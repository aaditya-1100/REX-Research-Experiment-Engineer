"""Unit tests for Deterministic Decision Engine (REX-034).

Tests:
- Action evaluation: REFINE, REPLICATE, PIVOT, STOP, COMPLETE, FAILED
- Budget validation: halts or converts to STOP when budgets are exhausted
- Evidence sufficiency criteria for COMPLETE
- Blocking COMPLETE when unresolved critical flaws exist
- Decision persistence and audit event emission
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.controller.decision_engine import DecisionEngine
from rex.domain.models import (
    CritiqueCategory,
    CritiqueFinding,
    CritiqueSeverity,
    DecisionType,
    EpistemicStatus,
    ExpectedDirection,
    ResearchCritique,
)
from rex.observability.events import EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import DecisionRepository


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def run_with_evidence(db_session: Session) -> tuple[str, str, str]:
    """Create a research run with complete evidence: hypothesis, experiment, execution, result, analysis."""
    run = ResearchRunModel(
        title="Decision Engine Test Run",
        research_question="Evaluate model performance with gradient clipping.",
        budget_json={"max_experiments": 3, "max_executions": 5, "max_runtime_seconds": 3600},
    )
    db_session.add(run)
    db_session.flush()

    hyp = HypothesisModel(
        research_run_id=run.id,
        statement="Gradient clipping stabilizes training and improves validation accuracy.",
        rationale="Prevents exploding gradients in recurrent connections.",
        expected_direction=ExpectedDirection.INCREASE.value,
        falsification_condition="Accuracy does not increase or loss diverges.",
    )
    db_session.add(hyp)
    db_session.flush()

    exp = ExperimentModel(
        research_run_id=run.id,
        hypothesis_id=hyp.id,
        objective="Train RNN with gradient clipping threshold 1.0",
        specification_json={"name": "exp_grad_clip_1", "method": "gradient_clipping"},
    )
    db_session.add(exp)
    db_session.flush()

    execution = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        exit_code=0,
    )
    db_session.add(execution)
    db_session.flush()

    res = ResultModel(
        execution_id=execution.id,
        metric_name="val_accuracy",
        metric_value=0.912,
        metric_unit="ratio",
    )
    db_session.add(res)
    db_session.flush()

    analysis = AnalysisModel(
        research_run_id=run.id,
        analysis_type="summary",
        method="sample_summary_statistics",
        output_json={"metric_name": "val_accuracy", "mean": 0.912, "sample_size": 1},
    )
    db_session.add(analysis)
    db_session.commit()

    return run.id, exp.id, execution.id


@pytest.mark.unit
def test_evaluate_decision_refine_valid(
    db_session: Session, run_with_evidence: tuple[str, str, str]
) -> None:
    """Proposing REFINE when experiments exist is accepted and targets the latest experiment."""
    run_id, exp_id, _ = run_with_evidence
    sink = InMemoryEventSink()
    engine = DecisionEngine(event_sink=sink)

    decision = engine.evaluate_decision(
        research_run_id=run_id,
        session=db_session,
        proposed_action=DecisionType.REFINE,
        iteration=1,
    )

    assert decision.action == DecisionType.REFINE
    assert decision.is_validated is True
    assert decision.target_entity_id == exp_id
    assert len(decision.validation_errors) == 0

    # Verify decision persistence
    repo = DecisionRepository(db_session)
    stored = repo.get_by_id(decision.id)
    assert stored is not None
    assert stored.action == "refine"

    # Verify event emission
    events = sink.get_by_type(EventType.DECISION_ACCEPTED)
    assert len(events) == 1
    assert events[0].payload["action"] == "refine"


@pytest.mark.unit
def test_evaluate_decision_replicate_with_and_without_results(
    db_session: Session, run_with_evidence: tuple[str, str, str]
) -> None:
    """REPLICATE is accepted if target has results, but adjusted to REFINE if target has no results."""
    run_id, exp_id, _ = run_with_evidence
    engine = DecisionEngine()

    # 1. Valid REPLICATE on experiment with results
    dec_valid = engine.evaluate_decision(
        research_run_id=run_id,
        session=db_session,
        proposed_action=DecisionType.REPLICATE,
        proposed_target_id=exp_id,
        iteration=1,
    )
    assert dec_valid.action == DecisionType.REPLICATE
    assert dec_valid.is_validated is True

    # 2. Create an experiment with NO results
    exp_no_res = ExperimentModel(
        research_run_id=run_id,
        objective="Unexecuted experiment",
        specification_json={"name": "exp_no_results"},
    )
    db_session.add(exp_no_res)
    db_session.commit()

    dec_invalid = engine.evaluate_decision(
        research_run_id=run_id,
        session=db_session,
        proposed_action=DecisionType.REPLICATE,
        proposed_target_id=exp_no_res.id,
        iteration=2,
    )
    # Adjusted to REFINE because cannot replicate without results
    assert dec_invalid.action == DecisionType.REFINE
    assert any("no recorded results" in err for err in dec_invalid.validation_errors)


@pytest.mark.unit
def test_evaluate_decision_budget_exhaustion(
    db_session: Session, run_with_evidence: tuple[str, str, str]
) -> None:
    """When budget is exhausted, candidate action requiring experiments is converted to STOP."""
    run_id, _, _ = run_with_evidence
    run = db_session.get(ResearchRunModel, run_id)
    # Set max_experiments to 1 (which matches current count of 1)
    run.budget_json = {"max_experiments": 1, "max_executions": 5, "max_runtime_seconds": 3600}
    db_session.commit()

    sink = InMemoryEventSink()
    engine = DecisionEngine(event_sink=sink)

    decision = engine.evaluate_decision(
        research_run_id=run_id,
        session=db_session,
        proposed_action=DecisionType.REFINE,
        iteration=1,
    )

    # Budget exhausted -> must convert to STOP
    assert decision.action == DecisionType.STOP
    assert any("budget exhausted" in err.lower() for err in decision.validation_errors)

    # Verify DECISION_REJECTED was emitted
    rejections = sink.get_by_type(EventType.DECISION_REJECTED)
    assert len(rejections) == 1
    assert rejections[0].payload["requested_action"] == "refine"
    assert rejections[0].payload["adjusted_action"] == "stop"


@pytest.mark.unit
def test_evaluate_decision_complete_evidence_sufficiency(
    db_session: Session, run_with_evidence: tuple[str, str, str]
) -> None:
    """COMPLETE is accepted only when hypotheses, experiments, results, and analyses exist."""
    run_id, _, _ = run_with_evidence
    engine = DecisionEngine()

    # All evidence exists -> COMPLETE is validated and accepted
    dec_complete = engine.evaluate_decision(
        research_run_id=run_id,
        session=db_session,
        proposed_action=DecisionType.COMPLETE,
        iteration=1,
    )
    assert dec_complete.action == DecisionType.COMPLETE
    assert dec_complete.is_validated is True

    # Create empty run with NO evidence
    empty_run = ResearchRunModel(title="Empty", research_question="Empty?")
    db_session.add(empty_run)
    db_session.commit()

    dec_empty = engine.evaluate_decision(
        research_run_id=empty_run.id,
        session=db_session,
        proposed_action=DecisionType.COMPLETE,
        iteration=1,
    )
    # Rejected because of evidence gaps
    assert dec_empty.action != DecisionType.COMPLETE
    assert any("incomplete evidence" in err.lower() for err in dec_empty.validation_errors)


@pytest.mark.unit
def test_evaluate_decision_complete_blocked_by_critical_flaw(
    db_session: Session, run_with_evidence: tuple[str, str, str]
) -> None:
    """COMPLETE cannot be accepted if critique contains unresolved critical findings."""
    run_id, _, _ = run_with_evidence
    engine = DecisionEngine()

    critique_with_flaw = ResearchCritique(
        research_run_id=run_id,
        iteration=1,
        summary="Fatal bug in preprocessing discovered.",
        findings=(
            CritiqueFinding(
                finding_id="fnd_leak_1",
                category=CritiqueCategory.LEAKAGE,
                severity=CritiqueSeverity.CRITICAL,
                description="Test split labels leaked into scaler.",
                epistemic_status=EpistemicStatus.OBSERVED,
            ),
        ),
        recommended_action="refine",
        recommended_action_rationale="Fix leakage before completing.",
    )

    decision = engine.evaluate_decision(
        research_run_id=run_id,
        session=db_session,
        critique=critique_with_flaw,
        proposed_action=DecisionType.COMPLETE,
        iteration=1,
    )

    # COMPLETE must be rejected due to unresolved critical flaw
    assert decision.action != DecisionType.COMPLETE
    assert any(
        "critical methodological findings" in err.lower() for err in decision.validation_errors
    )
