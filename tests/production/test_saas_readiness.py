"""Production SaaS Readiness Test Suite (REX-037, REX-038).

Validates deployment reproducibility, environment configuration precedence,
secret masking, SQLite WAL mode, dynamic health and readiness probes, diagnostic checks,
rex doctor CLI, IDOR run isolation, correlation ID tracing, and deterministic error envelopes.
"""

from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from typer.testing import CliRunner

from rex.api.app import create_app
from rex.api.routes.research import get_db
from rex.api.schemas import SystemDiagnosticsResponse
from rex.cli import cli
from rex.config import load_settings
from rex.domain.models import ResearchState
from rex.evidence.graph import CrossRunEvidenceError, EvidenceGraphService
from rex.observability.logging import JsonFormatter, set_correlation_id
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from rex.persistence.models import (
    ArtifactModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def file_db_engine(tmp_path: Path):
    """File-backed SQLite engine in WAL mode."""
    db_file = tmp_path / "saas_test.db"
    engine = create_db_engine(f"sqlite:///{db_file}")
    init_db(engine)
    return engine


@pytest.fixture
def saas_app(file_db_engine, tmp_path: Path):
    """FastAPI application configured with file-backed DB and custom artifacts."""
    session_factory = create_session_factory(file_db_engine)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)

    settings_override = load_settings(
        persistence={
            "database_url": str(file_db_engine.url),
            "artifact_root": artifact_root,
            "workspace_root": workspace_root,
        },
        app={"version": "1.0.0-prod"},
    )
    app = create_app(
        engine=file_db_engine,
        session_factory=session_factory,
        rex_settings=settings_override,
    )
    return app


@pytest.fixture
def saas_client(saas_app):
    return TestClient(saas_app)


# --- 1. Deployment & CLI ---


def test_deployment_reproducible_cli_and_imports():
    """Verify all core engine packages import cleanly and CLI help succeeds."""
    modules = [
        "rex.api",
        "rex.controller",
        "rex.evidence",
        "rex.execution",
        "rex.llm",
        "rex.observability",
        "rex.persistence",
    ]
    for mod in modules:
        imported = importlib.import_module(mod)
        assert imported is not None

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ["version", "verify", "reproduce", "report", "evaluate", "serve", "doctor"]:
        assert cmd in result.output


# --- 2. Environment Configuration & Secret Masking ---


def test_config_environment_precedence_and_secret_masking(
    monkeypatch: pytest.MonkeyPatch, saas_app, saas_client: TestClient
):
    """Verify environment variable overrides and strict SecretStr masking."""
    monkeypatch.setenv("REX_DATABASE_URL", "sqlite:///./custom_saas.db")
    monkeypatch.setenv("REX_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("REX_LLM_API_KEY", "super-secret-production-token-12345")

    cfg = load_settings()
    assert cfg.persistence.database_url == "sqlite:///./custom_saas.db"
    assert cfg.app.log_level == "DEBUG"
    assert str(cfg.llm.api_key) == "**********"
    assert cfg.llm.api_key.get_secret_value() == "super-secret-production-token-12345"

    # API check: verify secrets never leak via settings endpoint
    saas_app.state.settings = cfg
    resp = saas_client.get("/api/settings")
    assert resp.status_code == 200
    settings_data = resp.json()
    raw_text = resp.text
    assert "super-secret-production-token-12345" not in raw_text
    assert settings_data["llm"]["api_key"] == "**********"


# --- 3. SQLite WAL Mode & Pragmas ---


def test_database_init_wal_mode_and_pragmas(file_db_engine):
    """Verify production SQLite pragmas: WAL journal mode, busy_timeout >= 5000, foreign keys."""
    with file_db_engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert str(journal_mode).lower() == "wal"

        busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert int(busy_timeout) >= 5000

        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert int(foreign_keys) == 1


# --- 4. Health & Readiness Probes with Active Checks ---


def test_health_and_readiness_endpoints_with_active_probes(saas_app, saas_client: TestClient):
    """Verify /health, /health/live, /health/ready dynamic resolution and 503 fail-closed probe."""
    # Healthy state
    res_health = saas_client.get("/api/system/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"
    assert res_health.json()["version"] == "1.0.0-prod"

    res_live = saas_client.get("/api/system/health/live")
    assert res_live.status_code == 200
    assert res_live.json()["status"] == "ok"

    res_ready = saas_client.get("/api/system/health/ready")
    assert res_ready.status_code == 200
    assert res_ready.json()["status"] == "ready"
    assert res_ready.json()["database"] == "connected"

    # Simulated DB connection failure -> 503 fail-closed probe
    broken_session = MagicMock()
    broken_session.execute.side_effect = RuntimeError("Database connection lost")
    saas_app.dependency_overrides[get_db] = lambda: broken_session

    try:
        res_fail_health = saas_client.get("/api/system/health")
        assert res_fail_health.status_code == 503
        assert "Database connectivity probe failed" in res_fail_health.text

        res_fail_ready = saas_client.get("/api/system/health/ready")
        assert res_fail_ready.status_code == 503
        assert "Database connectivity probe failed" in res_fail_ready.text

        # Liveness probe still returns 200 (process is alive)
        res_live_ok = saas_client.get("/api/system/health/live")
        assert res_live_ok.status_code == 200
    finally:
        saas_app.dependency_overrides.clear()


# --- 5. Dynamic System Status Telemetry ---


def test_system_status_dynamic_metrics_and_masking(file_db_engine, saas_client: TestClient):
    """Verify /api/system/status dynamic counts and masked database URL."""
    session_factory = create_session_factory(file_db_engine)
    with session_factory() as session:
        run1 = ResearchRunModel(
            id="run_status_1",
            title="SaaS Status Test",
            research_question="Question 1",
            status="INITIALIZE",
            configuration_json={},
            budget_json={},
        )
        session.add(run1)
        session.commit()

    resp = saas_client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["total_runs_count"] >= 1
    assert data["active_runs_count"] >= 1
    assert data["version"] == "1.0.0-prod"
    assert "sqlite" in data["database_url_masked"]
    assert "password" not in data["database_url_masked"]


# --- 6. Diagnostics Endpoint & rex doctor CLI ---


def test_diagnostic_checks_environment_inspection(saas_client: TestClient):
    """Verify /api/system/diagnostics returns structured environment diagnostics."""
    resp = saas_client.get("/api/system/diagnostics")
    assert resp.status_code == 200
    diag = SystemDiagnosticsResponse(**resp.json())
    assert diag.python_version_ok is True
    assert diag.sqlite_wal_enabled is True
    assert diag.workspace_writable is True
    assert diag.artifact_writable is True
    assert diag.database_connected is True
    assert diag.status in ["healthy", "degraded"]


def test_cli_doctor_command(file_db_engine):
    """Verify rex doctor CLI executes all probes and reports PASS."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--db", str(file_db_engine.url)])
    assert result.exit_code == 0
    assert "REX Doctor" in result.output
    assert "Python Runtime" in result.output
    assert "Database Probe" in result.output
    assert "SQLite WAL Mode" in result.output
    assert "Workspace Root" in result.output
    assert "Artifact Storage" in result.output
    assert "All critical system diagnostics passed" in result.output


# --- 7. Run Isolation & IDOR Protection ---


def test_run_isolation_api_idor_protection(file_db_engine, saas_client: TestClient, tmp_path: Path):
    """Verify cross-run query isolation across experiments, artifacts, and claims."""
    session_factory = create_session_factory(file_db_engine)
    with session_factory() as session:
        run_a = ResearchRunModel(
            id="run_iso_A",
            title="Run A",
            research_question="Question A",
            status="DESIGN",
            configuration_json={},
            budget_json={},
        )
        run_b = ResearchRunModel(
            id="run_iso_B",
            title="Run B",
            research_question="Question B",
            status="DESIGN",
            configuration_json={},
            budget_json={},
        )
        hyp_a = HypothesisModel(
            id="hyp_A",
            research_run_id="run_iso_A",
            statement="Hypothesis A",
            rationale="Rationale A",
            expected_direction="increase",
            falsification_condition="Condition A",
            status="proposed",
        )
        session.add_all([run_a, run_b, hyp_a])
        session.flush()

        exp_a = ExperimentModel(
            id="exp_iso_A",
            research_run_id="run_iso_A",
            hypothesis_id="hyp_A",
            objective="Objective A",
            status="pending",
        )
        session.add(exp_a)
        session.flush()

        exec_a = ExecutionModel(
            id="exec_iso_A",
            experiment_id="exp_iso_A",
            status="COMPLETED",
            command="python run.py",
        )
        session.add(exec_a)
        session.flush()

        art_file = tmp_path / "artifacts" / "iso_A.txt"
        art_file.parent.mkdir(parents=True, exist_ok=True)
        art_file.write_text("artifact content A", encoding="utf-8")

        art_a = ArtifactModel(
            id="art_iso_A",
            research_run_id="run_iso_A",
            execution_id="exec_iso_A",
            artifact_type="log",
            path=str(art_file),
            content_hash="mock_hash",
            size_bytes=len("artifact content A"),
        )
        claim_a = ClaimModel(
            id="claim_iso_A",
            research_run_id="run_iso_A",
            statement="Statement A",
            status="unverified",
        )
        session.add_all([art_a, claim_a])
        session.commit()

    # Query with correct run_id -> 200
    assert saas_client.get("/api/experiments/exp_iso_A?run_id=run_iso_A").status_code == 200
    assert saas_client.get("/api/experiments/exp_iso_A/runs?run_id=run_iso_A").status_code == 200
    assert saas_client.get("/api/artifacts/art_iso_A?run_id=run_iso_A").status_code == 200
    assert saas_client.get("/api/artifacts/art_iso_A/content?run_id=run_iso_A").status_code == 200
    assert saas_client.get("/api/evidence/claims/claim_iso_A?run_id=run_iso_A").status_code == 200
    assert saas_client.get("/api/runs/exec_iso_A?run_id=run_iso_A").status_code == 200

    # Cross-run IDOR query with run_iso_B -> 404 blocked
    assert saas_client.get("/api/experiments/exp_iso_A?run_id=run_iso_B").status_code == 404
    assert saas_client.get("/api/experiments/exp_iso_A/runs?run_id=run_iso_B").status_code == 404
    assert saas_client.get("/api/artifacts/art_iso_A?run_id=run_iso_B").status_code == 404
    assert saas_client.get("/api/artifacts/art_iso_A/content?run_id=run_iso_B").status_code == 404
    assert saas_client.get("/api/evidence/claims/claim_iso_A?run_id=run_iso_B").status_code == 404
    assert saas_client.get("/api/evidence/lineage/claim_iso_A?run_id=run_iso_B").status_code == 404
    assert saas_client.get("/api/runs/exec_iso_A?run_id=run_iso_B").status_code == 404


def test_run_isolation_cross_run_evidence_linking_blocked(file_db_engine):
    """Verify EvidenceGraphService rejects cross-run evidence links."""
    session_factory = create_session_factory(file_db_engine)
    with session_factory() as session:
        run_a = ResearchRunModel(
            id="run_cross_A",
            title="A",
            research_question="QA",
            status="EXECUTE",
            configuration_json={},
            budget_json={},
        )
        run_b = ResearchRunModel(
            id="run_cross_B",
            title="B",
            research_question="QB",
            status="EXECUTE",
            configuration_json={},
            budget_json={},
        )
        hyp_cross_a = HypothesisModel(
            id="hyp_cross_A",
            research_run_id="run_cross_A",
            statement="HA",
            rationale="R",
            expected_direction="increase",
            falsification_condition="FC A",
            status="proposed",
        )
        hyp_cross_b = HypothesisModel(
            id="hyp_cross_B",
            research_run_id="run_cross_B",
            statement="HB",
            rationale="R",
            expected_direction="increase",
            falsification_condition="FC B",
            status="proposed",
        )
        session.add_all([run_a, run_b, hyp_cross_a, hyp_cross_b])
        session.flush()

        exp_a = ExperimentModel(
            id="exp_cross_A",
            research_run_id="run_cross_A",
            hypothesis_id="hyp_cross_A",
            objective="Obj A",
            status="success",
        )
        exp_b = ExperimentModel(
            id="exp_cross_B",
            research_run_id="run_cross_B",
            hypothesis_id="hyp_cross_B",
            objective="Obj B",
            status="success",
        )
        session.add_all([exp_a, exp_b])
        session.flush()

        exec_a = ExecutionModel(
            id="exec_cross_A", experiment_id="exp_cross_A", status="COMPLETED", command="cmd A"
        )
        session.add(exec_a)
        session.flush()

        res_a = ResultModel(
            id="res_cross_A", execution_id="exec_cross_A", metric_name="accuracy", metric_value=0.95
        )
        claim_b = ClaimModel(
            id="claim_cross_B",
            research_run_id="run_cross_B",
            statement="Claim in Run B",
            status="unverified",
        )
        session.add_all([res_a, claim_b])
        session.commit()

        graph_service = EvidenceGraphService(session=session)
        with pytest.raises(CrossRunEvidenceError) as exc_info:
            graph_service.create_link(
                source_type="claim",
                source_id="claim_cross_B",
                target_type="result",
                target_id="res_cross_A",
                relationship_type="supported_by",
            )
        assert "Cross-run evidence link rejected" in str(exc_info.value)


# --- 8. Correlation ID Propagation & Structured JSON Logging ---


def test_observability_correlation_id_propagation_and_logging(saas_client: TestClient):
    """Verify X-Correlation-ID is extracted/generated, set on response, and emitted in JSON logs."""
    # 1. Incoming custom correlation ID
    res = saas_client.get(
        "/api/system/health", headers={"X-Correlation-ID": "test-custom-corr-777"}
    )
    assert res.status_code == 200
    assert res.headers.get("X-Correlation-ID") == "test-custom-corr-777"

    # 2. Auto-generated correlation ID if absent
    res_auto = saas_client.get("/api/system/health")
    assert res_auto.status_code == 200
    auto_corr = res_auto.headers.get("X-Correlation-ID")
    assert auto_corr is not None
    assert len(auto_corr) > 10

    # 3. JsonFormatter output verification
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="rex.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=100,
        msg="Structured logging correlation test",
        args=(),
        exc_info=None,
    )
    set_correlation_id("ctx-corr-id-12345")
    formatted = formatter.format(record)
    assert '"correlation_id": "ctx-corr-id-12345"' in formatted
    assert '"message": "Structured logging correlation test"' in formatted


# --- 9. Deterministic Error Envelope ---


def test_api_deterministic_error_envelope(saas_client: TestClient):
    """Verify standard error response structure: {"error": {"code", "message", "details", "correlation_id"}}."""
    # 404 Resource Not Found
    res_404 = saas_client.get(
        "/api/research/non_existent_run_9999", headers={"X-Correlation-ID": "err-corr-404"}
    )
    assert res_404.status_code == 404
    data_404 = res_404.json()
    assert "error" in data_404
    assert data_404["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert "not found" in data_404["error"]["message"].lower()
    assert data_404["error"]["correlation_id"] == "err-corr-404"

    # 422 Validation Error
    res_422 = saas_client.post("/api/research", json={"research_question": ""})
    assert res_422.status_code == 422
    data_422 = res_422.json()
    assert "error" in data_422
    assert data_422["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(data_422["error"]["details"], list)

    # 400 Bad Request (pausing an already paused run)
    create_res = saas_client.post(
        "/api/research", json={"research_question": "What is the speed of light in vacuum?"}
    )
    assert create_res.status_code == 201
    test_run_id = create_res.json()["id"]
    saas_client.post(f"/api/research/{test_run_id}/pause")
    res_400 = saas_client.post(f"/api/research/{test_run_id}/pause")
    assert res_400.status_code == 400
    data_400 = res_400.json()
    assert "error" in data_400
    assert data_400["error"]["code"] == "BAD_REQUEST"


# --- 10. Frontend / Backend Schema Parity ---


def test_frontend_backend_schema_parity():
    """Verify frontend TypeScript ResearchStatus parity with backend ResearchState."""
    ts_file = (
        Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "types" / "index.ts"
    )
    if not ts_file.exists():
        pytest.skip("frontend/src/types/index.ts not found")

    content = ts_file.read_text(encoding="utf-8")
    status_block_match = re.search(r"export type ResearchStatus\s*=\s*([^;]+);", content)
    assert status_block_match is not None, "Could not find ResearchStatus union type in index.ts"

    ts_statuses = {
        s.strip().strip('"').strip("'") for s in status_block_match.group(1).split("|") if s.strip()
    }

    for state in ResearchState:
        assert state.value in ts_statuses, (
            f"Backend ResearchState '{state.value}' missing from frontend ResearchStatus!"
        )
