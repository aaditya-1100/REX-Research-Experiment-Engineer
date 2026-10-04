"""Unit tests for REX FastAPI HTTP Endpoints (REX-037 through REX-041)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from rex.api.app import create_app
from rex.config import RexSettings
from rex.domain.models import ClaimStatus, ExpectedDirection, ResearchState
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def test_app_and_session(tmp_path: Path):
    """Create a test FastAPI instance backed by an isolated SQLite database."""
    db_file = tmp_path / "test_api.db"
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
    client = TestClient(app)

    # Seed test data
    with session_factory() as session:
        run = ResearchRunModel(
            id="run_test01",
            title="Vision Robustness Test Investigation",
            research_question="Can data augmentation improve model robustness?",
            status=ResearchState.INITIALIZE.value,
        )
        session.add(run)

        hyp = HypothesisModel(
            id="hyp_test01",
            research_run_id=run.id,
            statement="Augmentation increases robustness by >3%",
            rationale="Diverse perturbations regularize feature space.",
            expected_direction=ExpectedDirection.INCREASE.value,
            falsification_condition="Accuracy delta <= 0.0%",
            status="active",
        )
        session.add(hyp)

        exp1 = ExperimentModel(
            id="exp_test01",
            research_run_id=run.id,
            hypothesis_id=hyp.id,
            objective="Baseline ResNet training on clean CIFAR",
            specification_json={
                "name": "Baseline",
                "method": "Standard SGD",
                "metrics": [{"name": "accuracy", "direction": "maximize"}],
            },
            status="completed",
        )
        exp2 = ExperimentModel(
            id="exp_test02",
            research_run_id=run.id,
            hypothesis_id=hyp.id,
            objective="Augmented ResNet training with CutMix",
            specification_json={
                "name": "Method CutMix",
                "method": "CutMix augmentation",
                "metrics": [{"name": "accuracy", "direction": "maximize"}],
            },
            status="completed",
        )
        session.add_all([exp1, exp2])

        # Executions
        exec1 = ExecutionModel(
            id="exec_test01",
            experiment_id=exp1.id,
            status="completed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            command="python train.py",
            git_commit="a1b2c3d",
            code_hash="sha256:code001",
            dataset_hash="sha256:data001",
            configuration_hash="sha256:cfg001",
            seed=42,
            exit_code=0,
            resource_usage_json={"duration_seconds": 120.0, "cost_estimate": 1.50},
        )
        exec2 = ExecutionModel(
            id="exec_test02",
            experiment_id=exp2.id,
            status="completed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            command="python train_cutmix.py",
            git_commit="d4e5f6a",
            code_hash="sha256:code002",
            dataset_hash="sha256:data001",
            configuration_hash="sha256:cfg002",
            seed=42,
            exit_code=0,
            resource_usage_json={"duration_seconds": 150.0, "cost_estimate": 2.10},
        )
        session.add_all([exec1, exec2])

        # Results
        res1 = ResultModel(
            id="res_test01",
            execution_id=exec1.id,
            metric_name="accuracy",
            metric_value=80.5,
            metric_unit="%",
            result_json={"step": 50, "loss": 0.35},
        )
        res2 = ResultModel(
            id="res_test02",
            execution_id=exec2.id,
            metric_name="accuracy",
            metric_value=84.7,
            metric_unit="%",
            result_json={"step": 50, "loss": 0.28},
        )
        session.add_all([res1, res2])

        # Analysis
        analysis = AnalysisModel(
            id="an_test01",
            research_run_id=run.id,
            analysis_type="hypothesis_testing",
            input_result_ids=[res1.id, res2.id],
            method="two_sample_t_test",
            output_json={"p_value": 0.002, "delta": 4.2, "significant": True},
        )
        session.add(analysis)

        # Artifact
        art_path = artifact_root / "metrics.json"
        art_path.write_text('{"accuracy": 84.7}', encoding="utf-8")
        import hashlib

        h = hashlib.sha256(art_path.read_bytes()).hexdigest()
        art = ArtifactModel(
            id="art_test01",
            research_run_id=run.id,
            execution_id=exec2.id,
            artifact_type="metric",
            path=str(art_path),
            content_hash=h,
            size_bytes=len(art_path.read_bytes()),
        )
        session.add(art)

        # Claim
        claim = ClaimModel(
            id="clm_test01",
            research_run_id=run.id,
            text="CutMix improves accuracy by 4.2%",
            claim_type="empirical",
            confidence=0.95,
            status=ClaimStatus.SUPPORTED.value,
        )
        session.add(claim)

        # Evidence links
        lnk1 = EvidenceLinkModel(
            id="lnk_test01",
            claim_id=claim.id,
            source_type="claim",
            source_id=claim.id,
            target_type="analysis",
            target_id=analysis.id,
            relationship_type="supported_by",
            research_run_id=run.id,
        )
        lnk2 = EvidenceLinkModel(
            id="lnk_test02",
            claim_id=claim.id,
            source_type="analysis",
            source_id=analysis.id,
            target_type="result",
            target_id=res2.id,
            relationship_type="derived_from",
            research_run_id=run.id,
        )
        lnk3 = EvidenceLinkModel(
            id="lnk_test03",
            claim_id=claim.id,
            source_type="claim",
            source_id=claim.id,
            target_type="result",
            target_id=res2.id,
            relationship_type="supported_by",
            research_run_id=run.id,
        )
        lnk4 = EvidenceLinkModel(
            id="lnk_test04",
            claim_id=claim.id,
            source_type="result",
            source_id=res2.id,
            target_type="execution",
            target_id=exec2.id,
            relationship_type="produced_by",
            research_run_id=run.id,
        )
        lnk5 = EvidenceLinkModel(
            id="lnk_test05",
            claim_id=claim.id,
            source_type="execution",
            source_id=exec2.id,
            target_type="experiment",
            target_id=exp2.id,
            relationship_type="instance_of",
            research_run_id=run.id,
        )
        lnk6 = EvidenceLinkModel(
            id="lnk_test06",
            claim_id=claim.id,
            source_type="execution",
            source_id=exec2.id,
            target_type="artifact",
            target_id=art.id,
            relationship_type="uses_artifact",
            research_run_id=run.id,
        )
        session.add_all([lnk1, lnk2, lnk3, lnk4, lnk5, lnk6])
        session.commit()

    return client, session_factory


@pytest.mark.unit
def test_system_health_and_status(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/system/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    res = client.get("/api/system/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "running"
    assert data["total_runs_count"] >= 1
    assert data["experiments_count"] >= 2
    assert data["claims_count"] >= 1


@pytest.mark.unit
def test_settings_endpoint(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert "app" in data
    assert "persistence" in data
    assert "docker" in data
    assert "budgets" in data
    assert "literature" in data
    assert "llm" in data


@pytest.mark.unit
def test_research_run_crud_and_lifecycle(test_app_and_session):
    client, _ = test_app_and_session

    # Create run
    create_payload = {
        "title": "New Attention Mechanisms",
        "research_question": "Does flash attention speed up training without loss?",
    }
    res = client.post("/api/research", json=create_payload)
    assert res.status_code == 201
    run_data = res.json()
    new_run_id = run_data["id"]
    assert run_data["status"] == "INITIALIZE"
    assert run_data["title"] == "New Attention Mechanisms"

    # List runs
    res = client.get("/api/research")
    assert res.status_code == 200
    runs = res.json()
    assert len(runs) >= 2

    # Get single run
    res = client.get(f"/api/research/{new_run_id}")
    assert res.status_code == 200
    assert res.json()["id"] == new_run_id

    # Start run (transitions to UNDERSTAND)
    res = client.post(f"/api/research/{new_run_id}/start")
    assert res.status_code == 200
    assert res.json()["status"] == "UNDERSTAND"

    # Pause run
    res = client.post(f"/api/research/{new_run_id}/pause", json={"reason": "User pause"})
    assert res.status_code == 200

    # Resume run
    res = client.post(f"/api/research/{new_run_id}/resume", json={"reason": "User resume"})
    assert res.status_code == 200

    # Events
    res = client.get(f"/api/research/{new_run_id}/events")
    assert res.status_code == 200
    events = res.json()
    assert len(events) >= 1


@pytest.mark.unit
def test_research_run_hypotheses_and_experiments(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/research/run_test01/hypotheses")
    assert res.status_code == 200
    hyps = res.json()
    assert len(hyps) == 1
    assert hyps[0]["id"] == "hyp_test01"

    res = client.get("/api/research/run_test01/experiments")
    assert res.status_code == 200
    exps = res.json()
    assert len(exps) == 2

    res = client.get("/api/research/run_test01/claims")
    assert res.status_code == 200
    claims = res.json()
    assert len(claims) == 1
    assert claims[0]["id"] == "clm_test01"


@pytest.mark.unit
def test_experiment_endpoints_and_comparison(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/experiments")
    assert res.status_code == 200
    exps = res.json()
    assert len(exps) >= 2

    res = client.get("/api/experiments/exp_test01")
    assert res.status_code == 200
    assert res.json()["objective"] == "Baseline ResNet training on clean CIFAR"

    res = client.get("/api/experiments/exp_test01/runs")
    assert res.status_code == 200
    runs = res.json()
    assert len(runs) == 1
    assert runs[0]["id"] == "exec_test01"

    # Compare
    res = client.get("/api/experiments/compare?ids=exp_test01,exp_test02")
    assert res.status_code == 200
    comp = res.json()
    assert len(comp["experiment_ids"]) == 2
    assert len(comp["experiments"]) == 2
    assert len(comp["metrics_summary"]) >= 1

    # Reproducibility
    res = client.get("/api/experiments/exp_test01/reproducibility")
    assert res.status_code == 200
    assert "is_reproducible" in res.json()


@pytest.mark.unit
def test_execution_endpoints(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/runs/exec_test01")
    assert res.status_code == 200
    data = res.json()
    assert data["git_commit"] == "a1b2c3d"

    res = client.get("/api/runs/exec_test01/results")
    assert res.status_code == 200
    results = res.json()
    assert len(results) == 1
    assert results[0]["metric_name"] == "accuracy"
    assert results[0]["metric_value"] == 80.5

    res = client.get("/api/runs/exec_test02/artifacts")
    assert res.status_code == 200
    arts = res.json()
    assert len(arts) == 1
    assert arts[0]["id"] == "art_test01"

    res = client.get("/api/runs/exec_test01/logs")
    assert res.status_code == 200
    assert "stdout" in res.json()


@pytest.mark.unit
def test_evidence_and_lineage_trace(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/evidence/claims")
    assert res.status_code == 200
    claims = res.json()
    assert len(claims) >= 1

    res = client.get("/api/evidence/claims/clm_test01")
    assert res.status_code == 200
    assert res.json()["id"] == "clm_test01"

    # Full lineage DAG
    res = client.get("/api/evidence/lineage/clm_test01")
    assert res.status_code == 200
    lineage = res.json()
    assert lineage["claim_id"] == "clm_test01"
    assert len(lineage["nodes"]) >= 4  # Claim, Analysis, Result, Execution, etc.
    assert len(lineage["edges"]) >= 3


@pytest.mark.unit
def test_verification_and_report_endpoints(test_app_and_session):
    client, _ = test_app_and_session

    # Verify run
    res = client.post("/api/research/run_test01/verify")
    assert res.status_code == 200
    report = res.json()
    assert "status" in report
    assert "checks" in report
    assert len(report["checks"]) >= 3

    # Generate Report
    res = client.get("/api/research/run_test01/report")
    assert res.status_code == 200
    rep_data = res.json()
    assert rep_data["research_run_id"] == "run_test01"
    assert "markdown" in rep_data
    assert len(rep_data["markdown"]) > 0

    # Reports list
    res = client.get("/api/reports")
    assert res.status_code == 200
    assert len(res.json()) >= 1


@pytest.mark.unit
def test_artifact_endpoints(test_app_and_session):
    client, _ = test_app_and_session

    res = client.get("/api/artifacts")
    assert res.status_code == 200
    arts = res.json()
    assert len(arts) >= 1

    res = client.get("/api/artifacts/art_test01")
    assert res.status_code == 200
    assert res.json()["id"] == "art_test01"

    # Verify artifact hash
    res = client.get("/api/artifacts/art_test01/verify")
    assert res.status_code == 200
    v = res.json()
    assert v["is_valid"] is True
    assert v["status"] == "verified"
