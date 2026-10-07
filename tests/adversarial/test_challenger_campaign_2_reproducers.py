"""REX Empirical Challenger Campaign 2 Reproducers.

Adversarial stress-tests demonstrating active epistemic and state machine vulnerabilities:
1. Bug 1: Verifier Directional Mismatch Bypass with absolute_difference & 'better'/'worse' vocabulary.
2. Bug 2: Verifier Evidence Laundering via Unassociated / Fabricated Secondary Metrics.
3. Bug 3: State Machine Reset Bypass via /pause and /resume on active runs (EXECUTE -> UNDERSTAND).
4. Bug 4: Claim Authorization Gap allowing untrusted actors to untamper TAMPERED claims to SUPPORTED.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.api.app import create_app
from rex.domain.models import ClaimStatus, EvidenceNodeType, EvidenceRelationType
from rex.evidence.claims import ClaimService, UnauthorizedClaimError
from rex.evidence.graph import EvidenceGraphService
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.observability.events import ActorType
from rex.persistence.database import Base, create_session_factory
from rex.persistence.models import (
    AnalysisModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def test_directional_mismatch_with_absolute_difference_detected(db_session: Session) -> None:
    """Verifier must reject claims of improvement when analysis absolute_difference is negative."""
    run = ResearchRunModel(
        id="run_bug1_a", title="Directional Test", research_question="Q", status="verify"
    )
    exp = ExperimentModel(
        id="exp_bug1_a", research_run_id=run.id, objective="Obj", status="completed"
    )
    exec_rec = ExecutionModel(
        id="exec_bug1_a", experiment_id=exp.id, status="completed", exit_code=0
    )
    res = ResultModel(
        id="res_bug1_a", execution_id=exec_rec.id, metric_name="accuracy", metric_value=0.5
    )
    # Welch's t-test produces 'absolute_difference' in output_json
    ana = AnalysisModel(
        id="ana_bug1_a",
        research_run_id=run.id,
        analysis_type="comparison",
        input_result_ids=[res.id],
        method="welch_t_test_comparison",
        output_json={"metric_name": "accuracy", "absolute_difference": -0.4},
    )
    claim = ClaimModel(
        id="clm_bug1_a",
        research_run_id=run.id,
        statement="Model improved accuracy by 0.5",
        status="proposed",
    )
    db_session.add_all([run, exp, exec_rec, res, ana, claim])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        EvidenceNodeType.RESULT,
        res.id,
        EvidenceNodeType.EXECUTION,
        exec_rec.id,
        EvidenceRelationType.PRODUCED_BY,
        run.id,
    )
    graph.create_link(
        EvidenceNodeType.ANALYSIS,
        ana.id,
        EvidenceNodeType.RESULT,
        res.id,
        EvidenceRelationType.DERIVED_FROM,
        run.id,
    )
    graph.create_link(
        EvidenceNodeType.CLAIM,
        claim.id,
        EvidenceNodeType.ANALYSIS,
        ana.id,
        EvidenceRelationType.SUPPORTED_BY,
        run.id,
    )
    db_session.commit()

    verifier = ResearchVerifier(session=db_session)
    rep = verifier.verify_run(run.id)

    # MUST FAIL: claiming improvement when absolute_difference is negative is false verification
    assert rep.status == VerificationStatus.FAIL, (
        f"Vulnerability reproduced: claim was verified despite negative delta: {rep.status}"
    )


def test_directional_mismatch_with_better_vocabulary_detected(db_session: Session) -> None:
    """Verifier must reject claims of 'better' performance when empirical diff is negative."""
    run = ResearchRunModel(
        id="run_bug1_b", title="Directional Test", research_question="Q", status="verify"
    )
    exp = ExperimentModel(
        id="exp_bug1_b", research_run_id=run.id, objective="Obj", status="completed"
    )
    exec_rec = ExecutionModel(
        id="exec_bug1_b", experiment_id=exp.id, status="completed", exit_code=0
    )
    res = ResultModel(
        id="res_bug1_b", execution_id=exec_rec.id, metric_name="accuracy", metric_value=0.5
    )
    ana = AnalysisModel(
        id="ana_bug1_b",
        research_run_id=run.id,
        analysis_type="comparison",
        input_result_ids=[res.id],
        method="sample_summary_statistics",
        output_json={"metric_name": "accuracy", "diff": -0.4},
    )
    claim = ClaimModel(
        id="clm_bug1_b",
        research_run_id=run.id,
        statement="Model achieved 0.5 better accuracy than baseline",
        status="proposed",
    )
    db_session.add_all([run, exp, exec_rec, res, ana, claim])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        EvidenceNodeType.RESULT,
        res.id,
        EvidenceNodeType.EXECUTION,
        exec_rec.id,
        EvidenceRelationType.PRODUCED_BY,
        run.id,
    )
    graph.create_link(
        EvidenceNodeType.ANALYSIS,
        ana.id,
        EvidenceNodeType.RESULT,
        res.id,
        EvidenceRelationType.DERIVED_FROM,
        run.id,
    )
    graph.create_link(
        EvidenceNodeType.CLAIM,
        claim.id,
        EvidenceNodeType.ANALYSIS,
        ana.id,
        EvidenceRelationType.SUPPORTED_BY,
        run.id,
    )
    db_session.commit()

    verifier = ResearchVerifier(session=db_session)
    rep = verifier.verify_run(run.id)

    # MUST FAIL: claiming 'better' when diff is negative is false verification
    assert rep.status == VerificationStatus.FAIL, (
        f"Vulnerability reproduced: claim was verified despite diff=-0.4: {rep.status}"
    )


def test_evidence_laundering_unassociated_metric_detected(db_session: Session) -> None:
    """Verifier must reject claims asserting metrics not present in empirical evidence (even if one matches)."""
    run = ResearchRunModel(
        id="run_bug2", title="Unassociated Metric Test", research_question="Q", status="verify"
    )
    exp = ExperimentModel(
        id="exp_bug2", research_run_id=run.id, objective="Obj", status="completed"
    )
    exec_rec = ExecutionModel(id="exec_bug2", experiment_id=exp.id, status="completed", exit_code=0)
    # Evidence ONLY provides accuracy: 0.88
    res = ResultModel(
        id="res_bug2", execution_id=exec_rec.id, metric_name="accuracy", metric_value=0.88
    )
    ana = AnalysisModel(
        id="ana_bug2",
        research_run_id=run.id,
        analysis_type="summary",
        input_result_ids=[res.id],
        method="sample_summary_statistics",
        output_json={"metric_name": "accuracy", "mean": 0.88},
    )
    # Claim asserts accuracy (real) AND loss / latency (completely unmeasured/fake)
    claim = ClaimModel(
        id="clm_bug2",
        research_run_id=run.id,
        statement="We achieved accuracy of 0.88 and loss of 0.01",
        status="proposed",
    )
    db_session.add_all([run, exp, exec_rec, res, ana, claim])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        EvidenceNodeType.RESULT,
        res.id,
        EvidenceNodeType.EXECUTION,
        exec_rec.id,
        EvidenceRelationType.PRODUCED_BY,
        run.id,
    )
    graph.create_link(
        EvidenceNodeType.ANALYSIS,
        ana.id,
        EvidenceNodeType.RESULT,
        res.id,
        EvidenceRelationType.DERIVED_FROM,
        run.id,
    )
    graph.create_link(
        EvidenceNodeType.CLAIM,
        claim.id,
        EvidenceNodeType.ANALYSIS,
        ana.id,
        EvidenceRelationType.SUPPORTED_BY,
        run.id,
    )
    db_session.commit()

    verifier = ResearchVerifier(session=db_session)
    rep = verifier.verify_run(run.id)

    # MUST FAIL: asserting unmeasured loss=0.01 must be flagged as ungrounded
    assert rep.status == VerificationStatus.FAIL, (
        f"Vulnerability reproduced: unmeasured loss was laundered: {rep.status}"
    )


def test_pause_resume_cannot_bypass_fsm_to_reset_execute_state() -> None:
    """Pausing an active EXECUTE run and resuming it must not arbitrarily jump to UNDERSTAND."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    app = create_app(engine=engine, session_factory=factory)

    with factory() as session:
        session.add(
            ResearchRunModel(
                id="run_bug3", title="Exec Run", research_question="Q", status="EXECUTE"
            )
        )
        session.commit()

    client = TestClient(app)
    resp_pause = client.post("/api/research/run_bug3/pause")
    assert resp_pause.status_code == 200

    resp_resume = client.post("/api/research/run_bug3/resume")
    # Resuming should return 400 or return to EXECUTE, not illegally reset to UNDERSTAND
    assert resp_resume.status_code == 400 or resp_resume.json()["status"] == "EXECUTE", (
        f"Vulnerability reproduced: run in EXECUTE was illegally reset to UNDERSTAND: {resp_resume.json()}"
    )


def test_untrusted_actor_cannot_untamper_claim(db_session: Session) -> None:
    """An untrusted actor (EXECUTION_WORKER / RESEARCH_AGENT) cannot revert a TAMPERED claim to SUPPORTED."""
    run = ResearchRunModel(
        id="run_bug4", title="Tamper Test", research_question="Q", status="verify"
    )
    claim = ClaimModel(
        id="clm_bug4",
        research_run_id=run.id,
        statement="Tampered Claim",
        status=ClaimStatus.TAMPERED.value,
    )
    db_session.add_all([run, claim])
    db_session.commit()

    claim_service = ClaimService(session=db_session)
    # Untrusted actor attempts to launder TAMPERED claim to SUPPORTED
    with pytest.raises((UnauthorizedClaimError, ValueError)):
        claim_service.update_claim_status(
            claim_id="clm_bug4",
            new_status=ClaimStatus.SUPPORTED,
            actor=ActorType.EXECUTION_WORKER,
        )
