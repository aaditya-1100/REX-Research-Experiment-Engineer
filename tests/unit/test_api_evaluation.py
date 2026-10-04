"""Unit tests for Evaluation API endpoints (REX-042 through REX-045)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from rex.api.app import create_app
from rex.config import RexSettings
from rex.persistence.database import Base


@pytest.fixture
def eval_client(tmp_path: Path) -> TestClient:
    """Create test FastAPI client with isolated SQLite database."""
    db_file = tmp_path / "test_eval_api.db"
    db_url = f"sqlite:///{db_file}"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    test_settings = RexSettings()
    test_settings.persistence.database_url = db_url
    test_settings.persistence.artifact_root = artifact_root

    app = create_app(
        engine=engine,
        session_factory=session_factory,
        rex_settings=test_settings,
    )
    return TestClient(app)


@pytest.mark.unit
def test_list_evaluation_suites(eval_client: TestClient) -> None:
    """GET /api/evaluation/suites returns all configured evaluation suites."""
    response = eval_client.get("/api/evaluation/suites")
    assert response.status_code == 200
    suites = response.json()
    assert len(suites) >= 10
    suite_ids = {s["id"] for s in suites}
    assert "all" in suite_ids
    assert "core_correctness" in suite_ids
    assert "security_corruption" in suite_ids
    assert "comparative" in suite_ids


@pytest.mark.unit
def test_trigger_evaluation_run_and_inspect(eval_client: TestClient) -> None:
    """POST /api/evaluation/runs executes a suite and GET /api/evaluation/runs/{id} returns details."""
    # 1. Trigger core_correctness evaluation
    res = eval_client.post("/api/evaluation/runs", json={"suite": "core_correctness"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "passed"
    assert data["total_cases"] == 2
    assert data["passed_cases"] == 2
    assert data["failed_cases"] == 0
    assert data["score"] == 100.0
    run_id = data["id"]

    # 2. Inspect run via GET /api/evaluation/runs/{id}
    res_get = eval_client.get(f"/api/evaluation/runs/{run_id}")
    assert res_get.status_code == 200
    run_data = res_get.json()
    assert run_data["id"] == run_id
    assert run_data["suite_name"] == "core_correctness"
    assert len(run_data["cases"]) == 2
    assert all(c["status"] == "passed" for c in run_data["cases"])

    # 3. List historical runs via GET /api/evaluation/runs
    res_list = eval_client.get("/api/evaluation/runs")
    assert res_list.status_code == 200
    runs = res_list.json()
    assert len(runs) >= 1
    assert any(r["id"] == run_id for r in runs)


@pytest.mark.unit
def test_get_quality_scorecard_and_gate_compliance(eval_client: TestClient) -> None:
    """GET /api/evaluation/scorecard and GET /api/evaluation/gates return valid metrics."""
    # Scorecard
    res_sc = eval_client.get("/api/evaluation/scorecard")
    assert res_sc.status_code == 200
    sc = res_sc.json()
    assert "overall_score" in sc
    assert "gate_compliance" in sc
    assert "domain_scores" in sc

    # Gates
    res_gates = eval_client.get("/api/evaluation/gates")
    assert res_gates.status_code == 200
    gates = res_gates.json()
    assert gates["total_gates"] == 18
    assert gates["passed_gates"] >= 1
    assert "X0_Scope_Integrity" in gates["gates"] or "X0" in gates["gates"]


@pytest.mark.unit
def test_trigger_invalid_suite_returns_400(eval_client: TestClient) -> None:
    """Triggering a non-existent suite returns HTTP 400."""
    res = eval_client.post("/api/evaluation/runs", json={"suite": "non_existent_suite_xyz"})
    assert res.status_code == 400
    assert "invalid evaluation suite" in res.json()["detail"].lower()


@pytest.mark.unit
def test_get_comparisons_endpoint(eval_client: TestClient) -> None:
    """GET /api/evaluation/comparisons returns comparative metrics after comparative run."""
    # Execute comparative suite
    res = eval_client.post("/api/evaluation/runs", json={"suite": "comparative"})
    assert res.status_code == 200

    # Query comparisons
    res_cmps = eval_client.get("/api/evaluation/comparisons")
    assert res_cmps.status_code == 200
    cmps = res_cmps.json()
    assert len(cmps) >= 1
    cmp = cmps[0]
    assert "baseline_metrics" in cmp
    assert "rex_metrics" in cmp
    assert "delta_metrics" in cmp
