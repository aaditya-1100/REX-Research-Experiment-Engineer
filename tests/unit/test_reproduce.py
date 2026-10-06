"""Unit tests for REX-027 Experiment Reproducer Engine."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.evidence.reproduce import (
    ExperimentReproducer,
    ReproducibilityStatus,
    ReproductionOutcome,
)
from rex.observability.events import EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
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


@pytest.fixture
def exp_setup(db_session: Session) -> tuple[str, str, InMemoryEventSink]:
    sink = InMemoryEventSink()
    run = ResearchRunModel(title="Reproducibility Run", research_question="Testing Reproduction")
    db_session.add(run)
    db_session.flush()

    exp = ExperimentModel(
        research_run_id=run.id,
        title="Benchmark Experiment",
        parameters_json={"lr": 0.001, "batch_size": 32},
    )
    db_session.add(exp)
    db_session.flush()

    exec_m = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        command="python train.py",
        git_commit="git_abc123",
        code_hash="code_sha256",
        dataset_hash="dataset_sha256",
        configuration_hash="cfg_sha256",
        seed=42,
        environment_json={"python": "3.11", "torch": "2.1.0"},
    )
    db_session.add(exec_m)
    db_session.flush()

    res = ResultModel(
        execution_id=exec_m.id,
        metric_name="eval_loss",
        metric_value=0.250,
    )
    db_session.add(res)
    db_session.commit()

    return exp.id, exec_m.id, sink


@pytest.mark.unit
def test_assess_reproducibility_fully_reproducible(
    db_session: Session, exp_setup: tuple[str, str, InMemoryEventSink]
) -> None:
    exp_id, _, _ = exp_setup
    reproducer = ExperimentReproducer(session=db_session)

    assessment = reproducer.assess_reproducibility(exp_id)
    assert assessment.status == ReproducibilityStatus.REPRODUCIBLE
    assert assessment.is_executable is True
    assert assessment.has_code is True
    assert assessment.has_configuration is True
    assert assessment.has_dataset is True
    assert assessment.has_environment is True
    assert assessment.has_seed is True
    assert assessment.has_prior_execution is True
    assert len(assessment.missing_elements) == 0


@pytest.mark.unit
def test_assess_reproducibility_partially_reproducible(
    db_session: Session, exp_setup: tuple[str, str, InMemoryEventSink]
) -> None:
    exp_id, exec_id, _ = exp_setup
    # Remove seed from execution
    exec_m = db_session.get(ExecutionModel, exec_id)
    assert exec_m is not None
    exec_m.seed = None
    db_session.commit()

    reproducer = ExperimentReproducer(session=db_session)
    assessment = reproducer.assess_reproducibility(exp_id)
    assert assessment.status == ReproducibilityStatus.PARTIALLY_REPRODUCIBLE
    assert "random_seed" in assessment.missing_elements


@pytest.mark.unit
def test_assess_reproducibility_not_reproducible(db_session: Session) -> None:
    exp = ExperimentModel(title="Fresh Experiment Without Executions")
    # Need a run
    run = ResearchRunModel(title="R", research_question="Q")
    db_session.add(run)
    db_session.flush()
    exp.research_run_id = run.id
    db_session.add(exp)
    db_session.commit()

    reproducer = ExperimentReproducer(session=db_session)
    assessment = reproducer.assess_reproducibility(exp.id)
    assert assessment.status == ReproducibilityStatus.NOT_REPRODUCIBLE
    assert assessment.is_executable is False


@pytest.mark.unit
def test_reproduce_experiment_exact_match_preserves_original(
    db_session: Session, exp_setup: tuple[str, str, InMemoryEventSink]
) -> None:
    exp_id, orig_exec_id, sink = exp_setup
    reproducer = ExperimentReproducer(session=db_session, event_sink=sink)

    # Initial execution count
    orig_exec = db_session.get(ExecutionModel, orig_exec_id)
    assert orig_exec is not None
    orig_status = orig_exec.status

    report = reproducer.reproduce_experiment(
        experiment_id=exp_id,
        original_execution_id=orig_exec_id,
        tolerance=1e-3,
        allow_mock_fallback=True,
    )

    # 1. Assert original execution was NOT overwritten
    db_session.refresh(orig_exec)
    assert orig_exec.id == orig_exec_id
    assert orig_exec.status == orig_status

    # 2. Assert new execution was created and linked
    assert report.reproduction_execution_id != orig_exec_id
    new_exec = db_session.get(ExecutionModel, report.reproduction_execution_id)
    assert new_exec is not None
    assert new_exec.resource_usage_json.get("reproduction_of_execution_id") == orig_exec_id

    # 3. Assert reproduction outcome
    assert report.is_reproduced is True
    assert report.outcome == ReproductionOutcome.EXACT_MATCH
    assert len(report.metric_comparisons) == 1
    assert report.metric_comparisons[0].within_tolerance is True
    assert report.metric_comparisons[0].absolute_difference == 0.0

    # 4. Check events
    assert len(sink.get_by_type(EventType.REPRODUCTION_STARTED)) == 1
    assert len(sink.get_by_type(EventType.REPRODUCTION_COMPLETED)) == 1


@pytest.mark.unit
def test_reproduce_experiment_without_runner_fails_cleanly(
    db_session: Session, exp_setup: tuple[str, str, InMemoryEventSink]
) -> None:
    """Computational reproduction without a runner or explicit results must fail for epistemic integrity."""
    exp_id, orig_exec_id, sink = exp_setup
    reproducer = ExperimentReproducer(session=db_session, event_sink=sink)

    report = reproducer.reproduce_experiment(
        experiment_id=exp_id,
        original_execution_id=orig_exec_id,
        tolerance=1e-3,
        allow_mock_fallback=False,
    )

    assert report.is_reproduced is False
    assert report.outcome == ReproductionOutcome.FAILED
    assert "Automatic metric mirroring is disabled" in (report.error_message or "")
    assert len(sink.get_by_type(EventType.REPRODUCTION_FAILED)) == 1


@pytest.mark.unit
def test_reproduce_experiment_within_tolerance(
    db_session: Session, exp_setup: tuple[str, str, InMemoryEventSink]
) -> None:
    exp_id, _orig_exec_id, sink = exp_setup
    reproducer = ExperimentReproducer(session=db_session, event_sink=sink)

    # Simulated re-run with slight metric jitter (0.250 -> 0.2505, diff = 0.0005 < tol 0.001)
    simulated = [{"metric_name": "eval_loss", "metric_value": 0.2505}]

    report = reproducer.reproduce_experiment(
        experiment_id=exp_id,
        simulated_results=simulated,
        tolerance=0.001,
    )

    assert report.is_reproduced is True
    assert report.outcome == ReproductionOutcome.WITHIN_TOLERANCE
    assert report.metric_comparisons[0].within_tolerance is True
    assert report.metric_comparisons[0].absolute_difference == pytest.approx(0.0005)


@pytest.mark.unit
def test_reproduce_experiment_diverged(
    db_session: Session, exp_setup: tuple[str, str, InMemoryEventSink]
) -> None:
    exp_id, _, sink = exp_setup
    reproducer = ExperimentReproducer(session=db_session, event_sink=sink)

    # Simulated re-run with significant metric divergence (0.250 -> 0.400, diff = 0.150 > tol 0.001)
    simulated = [{"metric_name": "eval_loss", "metric_value": 0.400}]

    report = reproducer.reproduce_experiment(
        experiment_id=exp_id,
        simulated_results=simulated,
        tolerance=0.001,
    )

    assert report.is_reproduced is False
    assert report.outcome == ReproductionOutcome.DIVERGED
    assert report.metric_comparisons[0].within_tolerance is False

    # Event emitted is REPRODUCTION_FAILED
    assert len(sink.get_by_type(EventType.REPRODUCTION_FAILED)) == 1
