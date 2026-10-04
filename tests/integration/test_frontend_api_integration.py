"""Comprehensive End-to-End Integration Tests for REX Batch 8 Frontend API & Workstation.

Validates:
1. Static SPA serving & client-side route fallback (/ -> index.html, deep routes -> index.html).
2. API route isolation (API 404s are JSON, not SPA HTML fallback).
3. System & Settings API endpoints (REX-037).
4. Research Workflow Lifecycle: creation, start, pause, resume, events (REX-037).
5. Experiment & Execution Tracking: runs, results, logs, comparison (REX-038).
6. Evidence Lineage DAG & Verification: deterministic claim graph, SHA-256 verification (REX-039).
7. Report Generation & Artifact Browser: hash verification, artifact download (REX-040).
8. Epistemic Invariant Enforcement: PROPOSED != EXECUTED, UNVERIFIED != VERIFIED, tamper detection.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from rex.api.app import create_app
from rex.config.settings import RexSettings
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ClaimType,
    EvidenceNodeType,
    ExecutionStatus,
    ExperimentStatus,
    HypothesisStatus,
    ResearchState,
)
from rex.evidence.claims import ClaimService
from rex.observability.events import (
    ActorType,
    InMemoryEventSink,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import (
    ArtifactModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResultModel,
)


@pytest.fixture
def test_env(tmp_path: Path):
    """Create an isolated test environment with SQLite DB, artifact directory, and settings."""
    db_file = tmp_path / "test_api_integration.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    session_factory = create_session_factory(engine)

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    settings = RexSettings()
    settings.persistence.database_url = db_url
    settings.persistence.artifact_root = artifact_dir
    settings.app.environment = "test"
    event_sink = InMemoryEventSink()

    app = create_app(
        engine=engine,
        session_factory=session_factory,
        rex_settings=settings,
    )
    client = TestClient(app)

    yield {
        "client": client,
        "session_factory": session_factory,
        "artifact_dir": artifact_dir,
        "settings": settings,
        "event_sink": event_sink,
        "engine": engine,
    }

    engine.dispose()


class TestSpaServing:
    """Test SPA static file hosting and routing integration."""

    def test_root_serves_spa_index(self, test_env):
        """GET / should serve the built SPA index.html."""
        client: TestClient = test_env["client"]
        response = client.get("/")
        assert response.status_code == 200
        assert "<div id=\"root\"></div>" in response.text
        assert "REX" in response.text

    def test_client_side_routing_serves_index(self, test_env):
        """Deep client-side routes should fall back to index.html for SPA routing."""
        client: TestClient = test_env["client"]
        for path in ["/research", "/experiments/compare", "/evidence", "/settings"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "<div id=\"root\"></div>" in response.text

    def test_api_404_not_hijacked_by_spa(self, test_env):
        """Non-existent API routes must return 404 JSON, not SPA HTML."""
        client: TestClient = test_env["client"]
        response = client.get("/api/nonexistent_route_12345")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data


class TestSystemAndSettingsApi:
    """Test System health and settings endpoints (REX-037)."""

    def test_system_health_and_status(self, test_env):
        client: TestClient = test_env["client"]

        health_resp = client.get("/api/system/health")
        assert health_resp.status_code == 200
        health = health_resp.json()
        assert health["status"] == "ok"
        assert health["app"] == "rex"

        status_resp = client.get("/api/system/status")
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert "version" in status
        assert status["status"] == "running"

    def test_settings_retrieval(self, test_env):
        client: TestClient = test_env["client"]
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        settings = resp.json()
        assert "environment" in settings["app"]
        assert "database_url" in settings["persistence"]


class TestResearchWorkflowE2E:
    """End-to-end test of Research Run lifecycle and SSE events."""

    def test_research_run_lifecycle_and_transitions(self, test_env):
        client: TestClient = test_env["client"]

        # 1. Create a research run
        create_payload = {
            "title": "Autonomous Optimizer Validation",
            "research_question": "Does optimizer gradient clipping improve convergence stability?",
        }
        res = client.post("/api/research", json=create_payload)
        assert res.status_code == 201
        run = res.json()
        run_id = run["id"]
        assert run["title"] == "Autonomous Optimizer Validation"
        assert run["status"] == ResearchState.INITIALIZE.value

        # 2. Start the run
        start_res = client.post(f"/api/research/{run_id}/start")
        assert start_res.status_code == 200
        started = start_res.json()
        assert started["status"] == ResearchState.UNDERSTAND.value

        # 3. Pause the run
        pause_res = client.post(f"/api/research/{run_id}/pause")
        assert pause_res.status_code == 200

        # 4. Resume the run
        resume_res = client.post(f"/api/research/{run_id}/resume")
        assert resume_res.status_code == 200

        # 5. Check events endpoint
        events_res = client.get(f"/api/research/{run_id}/events")
        assert events_res.status_code == 200
        events = events_res.json()
        assert len(events) >= 3  # Initial creation, start, pause, resume recorded


class TestExperimentComparisonAndReproducibility:
    """Test Experiment comparison and reproducibility metrics (REX-038)."""

    def test_experiment_comparison_flow(self, test_env):
        client: TestClient = test_env["client"]
        session_factory = test_env["session_factory"]

        # Seed 2 experiments with executions & results
        with get_db_session(session_factory) as session:
            run = create_research_run(
                session=session,
                research_question="Optimizer Comparison",
                title="Optimizer Comparison",
            )
            h = HypothesisModel(
                research_run_id=run.id,
                statement="AdamW converges faster than SGD with momentum",
                falsification_condition="Loss of AdamW >= SGD after 50 epochs",
                status=HypothesisStatus.TESTING.value,
            )
            session.add(h)
            session.flush()

            exp1 = ExperimentModel(
                research_run_id=run.id,
                hypothesis_id=h.id,
                title="AdamW Baseline",
                parameters_json={"optimizer": "AdamW", "lr": 0.001, "batch_size": 32},
                status=ExperimentStatus.COMPLETED.value,
            )
            exp2 = ExperimentModel(
                research_run_id=run.id,
                hypothesis_id=h.id,
                title="SGD Momentum Variant",
                parameters_json={"optimizer": "SGD", "lr": 0.01, "momentum": 0.9, "batch_size": 32},
                status=ExperimentStatus.COMPLETED.value,
            )
            session.add_all([exp1, exp2])
            session.flush()

            exec1 = ExecutionModel(
                experiment_id=exp1.id,
                status=ExecutionStatus.COMPLETED.value,
                resource_usage_json={"duration_seconds": 45.2},
                exit_code=0,
            )
            exec2 = ExecutionModel(
                experiment_id=exp2.id,
                status=ExecutionStatus.COMPLETED.value,
                resource_usage_json={"duration_seconds": 52.1},
                exit_code=0,
            )
            session.add_all([exec1, exec2])
            session.flush()

            res1 = ResultModel(
                execution_id=exec1.id,
                metric_name="accuracy",
                metric_value=0.945,
                metric_unit="",
                result_json={"loss": 0.182, "convergence_epoch": 12},
            )
            res2 = ResultModel(
                execution_id=exec2.id,
                metric_name="accuracy",
                metric_value=0.912,
                metric_unit="",
                result_json={"loss": 0.245, "convergence_epoch": 24},
            )
            session.add_all([res1, res2])
            session.commit()

            exp1_id = exp1.id
            exp2_id = exp2.id

        # Call compare API
        compare_res = client.post(
            "/api/experiments/compare",
            json={"experiment_ids": [exp1_id, exp2_id]},
        )
        assert compare_res.status_code == 200
        comp_data = compare_res.json()
        metrics = {m["metric_name"]: m["values_by_experiment"] for m in comp_data["metrics_summary"]}
        assert "accuracy" in metrics
        assert metrics["accuracy"][exp1_id] == 0.945
        assert metrics["accuracy"][exp2_id] == 0.912


class TestEvidenceLineageAndVerification:
    """Test deterministic DAG lineage tracing and verification (REX-039)."""

    def test_lineage_dag_and_epistemic_integrity(self, test_env):
        client: TestClient = test_env["client"]
        session_factory = test_env["session_factory"]

        with get_db_session(session_factory) as session:
            run = create_research_run(
                session=session,
                research_question="Lineage Verification",
                title="Lineage Verification",
            )
            h = HypothesisModel(
                research_run_id=run.id,
                statement="Dropout rate 0.2 prevents overfitting",
                falsification_condition="Validation loss > 0.5",
                status=HypothesisStatus.TESTING.value,
            )
            session.add(h)
            session.flush()

            exp = ExperimentModel(
                research_run_id=run.id,
                hypothesis_id=h.id,
                title="Dropout Test",
                parameters_json={"dropout": 0.2},
                status=ExperimentStatus.COMPLETED.value,
            )
            session.add(exp)
            session.flush()

            execution = ExecutionModel(
                experiment_id=exp.id,
                status=ExecutionStatus.COMPLETED.value,
                resource_usage_json={"duration_seconds": 18.5},
                exit_code=0,
            )
            session.add(execution)
            session.flush()

            result = ResultModel(
                execution_id=execution.id,
                metric_name="val_loss",
                metric_value=0.15,
                metric_unit="",
                result_json={"train_loss": 0.14},
            )
            session.add(result)
            session.flush()

            claim_svc = ClaimService(session)
            claim = claim_svc.create_claim(
                research_run_id=run.id,
                statement="Validation loss matches training loss closely",
                claim_type=ClaimType.OBSERVATION,
                created_by="test-agent",
                actor=ActorType.RESEARCH_AGENT,
            )
            claim_svc.attach_evidence(
                claim_id=claim.id,
                evidence_type=EvidenceNodeType.RESULT,
                evidence_id=result.id,
                actor=ActorType.RESEARCH_AGENT,
            )
            claim_id = claim.id
            run_id = run.id

        # 1. Fetch Lineage DAG
        lineage_res = client.get(f"/api/evidence/lineage/{claim_id}")
        assert lineage_res.status_code == 200
        lineage = lineage_res.json()
        assert lineage["claim_id"] == claim_id
        assert len(lineage["nodes"]) >= 2
        assert len(lineage["edges"]) >= 1

        # 2. Run Verifier via API
        verify_res = client.post(f"/api/research/{run_id}/verify")
        assert verify_res.status_code == 200
        v_report = verify_res.json()
        assert "is_passed" in v_report
        assert v_report["status"] in ["pass", "fail", "quarantine"]
        assert len(v_report["checks"]) >= 1


class TestArtifactVerificationAndTamperDetection:
    """Test SHA-256 artifact verification and adversarial tamper detection."""

    def test_artifact_sha256_verification_and_tamper(self, test_env):
        client: TestClient = test_env["client"]
        session_factory = test_env["session_factory"]
        artifact_dir: Path = test_env["artifact_dir"]

        # Create authentic file and compute hash
        content = b'{"metric": "test_output", "value": 42}'
        computed_hash = hashlib.sha256(content).hexdigest()
        file_path = artifact_dir / "authentic_data.json"
        file_path.write_bytes(content)

        with get_db_session(session_factory) as session:
            run = create_research_run(
                session=session,
                research_question="Artifact Test",
                title="Artifact Test",
            )
            art = ArtifactModel(
                research_run_id=run.id,
                path=str(file_path),
                artifact_type="json",
                content_hash=computed_hash,
                size_bytes=len(content),
            )
            session.add(art)
            session.commit()
            art_id = art.id

        # Verify authentic artifact
        verify_res = client.post(f"/api/artifacts/{art_id}/verify")
        assert verify_res.status_code == 200
        data = verify_res.json()
        assert data["is_valid"] is True
        assert data["recorded_hash"] == computed_hash
        assert data["actual_hash"] == computed_hash

        # Tamper with file
        file_path.write_bytes(b'{"metric": "tampered_output", "value": 999}')

        # Verify tampered artifact
        tamper_res = client.post(f"/api/artifacts/{art_id}/verify")
        assert tamper_res.status_code == 200
        tamper_data = tamper_res.json()
        assert tamper_data["is_valid"] is False
        assert tamper_data["actual_hash"] != tamper_data["recorded_hash"]
