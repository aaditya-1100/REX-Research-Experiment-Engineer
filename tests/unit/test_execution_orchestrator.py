"""Unit tests for Execution Orchestrator Service and Resource Budgets (REX-010, REX-011)."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from rex.controller.exceptions import (
    BudgetExceededError,
    ConcurrencyLimitExceededError,
    ExecutionAlreadyRunningError,
    ExecutionAlreadyTerminalError,
    StateMachineError,
)
from rex.controller.execution_orchestrator import ExecutionOrchestrator
from rex.controller.executions import create_execution, update_execution_status
from rex.controller.experiments import create_experiment
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ArtifactType,
    ExecutionStatus,
    ExperimentStatus,
)
from rex.execution.backend import ExecutionBackend
from rex.execution.exceptions import DockerUnavailableError
from rex.execution.models import (
    ExecutionOutcome,
    ExecutionRequest,
    OutputArtifactMetadata,
)
from rex.observability.events import ActorType, EventType
from rex.persistence.database import create_db_engine, init_db
from rex.persistence.models import (
    ArtifactModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import EventRepository


class MockBackend(ExecutionBackend):
    """Configurable mock execution backend for orchestrator unit testing."""

    def __init__(
        self,
        outcome: ExecutionOutcome | None = None,
        exc_to_raise: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.exc_to_raise = exc_to_raise
        self.executed_requests: list[ExecutionRequest] = []
        self.cancelled_executions: list[str] = []

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        self.executed_requests.append(request)
        if self.exc_to_raise is not None:
            raise self.exc_to_raise
        if self.outcome is not None:
            return self.outcome
        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Training completed successfully.\n",
            stderr="Warning: low disk space\n",
            output_artifacts=[
                OutputArtifactMetadata(
                    path="metrics.json",
                    content_hash="m" * 64,
                    size_bytes=512,
                    artifact_type=ArtifactType.METRIC,
                ),
                OutputArtifactMetadata(
                    path="checkpoint.pt",
                    content_hash="c" * 64,
                    size_bytes=2048,
                    artifact_type=ArtifactType.CHECKPOINT,
                ),
            ],
            duration_seconds=3.2,
            resource_usage={"runtime_seconds": 3.2, "peak_memory_mb": 256},
            cleaned_up=True,
        )

    def cancel(self, execution_id: str) -> bool:
        self.cancelled_executions.append(execution_id)
        return True


class MockEventSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    db_path = tmp_path / "test_orchestrator.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _setup_run_and_experiment(
    session: Session,
    budget: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    run = create_research_run(
        session=session,
        research_question="Does dropout rate 0.2 outperform 0.5?",
        title="Dropout Rate Study",
        budget=dict(budget) if budget else None,
        actor=ActorType.OWNER,
    )
    exp = create_experiment(
        session=session,
        research_run_id=run.id,
        objective="Compare test loss with dropout 0.2",
        actor=ActorType.RESEARCH_AGENT,
    )
    session.commit()
    return run.id, exp.id


def test_orchestrator_successful_execution_and_persistence(
    session_factory: sessionmaker[Session],
) -> None:
    sink = MockEventSink()
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(
        session_factory=session_factory,
        backend=backend,
        event_sink=sink,
    )

    with session_factory() as session:
        run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(
            session=session,
            experiment_id=exp_id,
            command="python train.py --dropout 0.2",
            actor=ActorType.EXECUTION_WORKER,
        )
        session.commit()
        exec_id = exec_domain.id

    code_files = {"train.py": "print('done')"}
    domain_exec, outcome = orchestrator.run_execution(
        execution_id=exec_id,
        code_files=code_files,
        actor=ActorType.EXECUTION_WORKER,
    )

    assert domain_exec.status == ExecutionStatus.COMPLETED
    assert outcome.status == ExecutionStatus.COMPLETED
    assert len(backend.executed_requests) == 1
    assert backend.executed_requests[0].execution_id == exec_id

    # Verify database persistence
    with session_factory() as session:
        exec_model = session.get(ExecutionModel, exec_id)
        assert exec_model is not None
        assert exec_model.status == ExecutionStatus.COMPLETED.value
        assert exec_model.exit_code == 0
        assert exec_model.stdout_artifact_id is not None
        assert exec_model.stderr_artifact_id is not None

        # Verify parent experiment status updated to COMPLETED
        exp_model = session.get(ExperimentModel, exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.COMPLETED.value

        # Verify artifacts
        artifacts = session.query(ArtifactModel).filter(ArtifactModel.execution_id == exec_id).all()
        assert len(artifacts) == 4  # stdout, stderr, metrics.json, checkpoint.pt
        art_paths = {a.path for a in artifacts}
        assert "stdout.log" in art_paths
        assert "stderr.log" in art_paths
        assert "metrics.json" in art_paths
        assert "checkpoint.pt" in art_paths

        # Verify audit events
        events = EventRepository(session).list_by_run(run_id)
        event_types = [e.event_type for e in events]
        assert EventType.EXECUTION_STARTED.value in event_types
        assert EventType.EXECUTION_COMPLETED.value in event_types


def test_orchestrator_records_empirical_results(
    session_factory: sessionmaker[Session],
) -> None:
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(
        session_factory=session_factory,
        backend=backend,
    )

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(
            session=session,
            experiment_id=exp_id,
            actor=ActorType.EXECUTION_WORKER,
        )
        session.commit()
        exec_id = exec_domain.id

    results_data = [
        {"metric_name": "final_loss", "metric_value": 0.245, "metric_unit": "nll"},
        {"metric_name": "accuracy", "metric_value": 0.942, "metric_unit": "ratio"},
    ]

    orchestrator.run_execution(
        execution_id=exec_id,
        code_files={"train.py": "print('done')"},
        results=results_data,
        actor=ActorType.EXECUTION_WORKER,
    )

    with session_factory() as session:
        results = session.query(ResultModel).filter(ResultModel.execution_id == exec_id).all()
        assert len(results) == 2
        metrics = {r.metric_name: r.metric_value for r in results}
        assert metrics["final_loss"] == 0.245
        assert metrics["accuracy"] == 0.942


def test_orchestrator_failed_execution_handling(
    session_factory: sessionmaker[Session],
) -> None:
    fail_outcome = ExecutionOutcome(
        execution_id="any",
        experiment_id="any",
        research_run_id="any",
        status=ExecutionStatus.FAILED,
        exit_code=1,
        stdout="",
        stderr="RuntimeError: CUDA out of memory\n",
        duration_seconds=1.0,
        failure_reason="Process exited with code 1",
        cleaned_up=True,
    )
    backend = MockBackend(outcome=fail_outcome)
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        exec_id = exec_domain.id

    domain_exec, outcome = orchestrator.run_execution(
        execution_id=exec_id,
        code_files={"train.py": "print('fail')"},
    )

    assert domain_exec.status == ExecutionStatus.FAILED
    assert outcome.status == ExecutionStatus.FAILED
    assert domain_exec.exit_code == 1

    with session_factory() as session:
        exec_model = session.get(ExecutionModel, exec_id)
        assert exec_model is not None
        assert exec_model.status == ExecutionStatus.FAILED.value

        exp_model = session.get(ExperimentModel, exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.FAILED.value


def test_orchestrator_timeout_execution_handling(
    session_factory: sessionmaker[Session],
) -> None:
    timeout_outcome = ExecutionOutcome(
        execution_id="any",
        experiment_id="any",
        research_run_id="any",
        status=ExecutionStatus.TIMEOUT,
        exit_code=None,
        stdout="running loop...\n",
        stderr="",
        duration_seconds=30.0,
        failure_reason="Execution timed out after 30.0s",
        cleaned_up=True,
    )
    backend = MockBackend(outcome=timeout_outcome)
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        exec_id = exec_domain.id

    domain_exec, outcome = orchestrator.run_execution(
        execution_id=exec_id,
        code_files={"train.py": "while True: pass"},
    )

    assert domain_exec.status == ExecutionStatus.TIMEOUT
    assert outcome.status == ExecutionStatus.TIMEOUT

    with session_factory() as session:
        exec_model = session.get(ExecutionModel, exec_id)
        assert exec_model is not None
        assert exec_model.status == ExecutionStatus.TIMEOUT.value


def test_orchestrator_rejects_already_running_execution(
    session_factory: sessionmaker[Session],
) -> None:
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=exec_domain.id,
            new_status=ExecutionStatus.RUNNING,
        )
        session.commit()
        exec_id = exec_domain.id

    with pytest.raises(ExecutionAlreadyRunningError):
        orchestrator.run_execution(
            execution_id=exec_id,
            code_files={"train.py": "print('dup')"},
        )

    assert len(backend.executed_requests) == 0


def test_orchestrator_rejects_already_terminal_execution(
    session_factory: sessionmaker[Session],
) -> None:
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=exec_domain.id,
            new_status=ExecutionStatus.RUNNING,
        )
        update_execution_status(
            session=session,
            execution_id=exec_domain.id,
            new_status=ExecutionStatus.COMPLETED,
        )
        session.commit()
        exec_id = exec_domain.id

    with pytest.raises(ExecutionAlreadyTerminalError):
        orchestrator.run_execution(
            execution_id=exec_id,
            code_files={"train.py": "print('dup')"},
        )

    assert len(backend.executed_requests) == 0


def test_orchestrator_rejects_in_terminal_or_paused_run(
    session_factory: sessionmaker[Session],
) -> None:
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(session=session, experiment_id=exp_id)
        run_model = session.get(ResearchRunModel, run_id)
        assert run_model is not None
        run_model.status = "COMPLETED"
        session.commit()
        exec_id = exec_domain.id

    with pytest.raises(StateMachineError) as exc_info:
        orchestrator.run_execution(
            execution_id=exec_id,
            code_files={"train.py": "print('noop')"},
        )
    assert "terminal state" in str(exc_info.value)
    assert len(backend.executed_requests) == 0


def test_orchestrator_budget_concurrency_limit_enforcement(
    session_factory: sessionmaker[Session],
) -> None:
    sink = MockEventSink()
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(
        session_factory=session_factory,
        backend=backend,
        event_sink=sink,
    )

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(
            session, budget={"max_concurrent_executions": 1}
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        # Put e1 into RUNNING
        update_execution_status(
            session=session,
            execution_id=e1.id,
            new_status=ExecutionStatus.RUNNING,
        )
        session.commit()
        e2_id = e2.id

    # Starting e2 should be rejected because 1 active execution equals max_concurrent_executions
    with pytest.raises(ConcurrencyLimitExceededError) as exc_info:
        orchestrator.run_execution(
            execution_id=e2_id,
            code_files={"train.py": "print('blocked')"},
        )

    assert exc_info.value.limit == 1
    assert exc_info.value.current_usage == 1
    assert len(backend.executed_requests) == 0

    # Verify BUDGET_EXCEEDED event was emitted
    budget_events = [
        e for e in sink.events if getattr(e, "event_type", None) == EventType.BUDGET_EXCEEDED
    ]
    assert len(budget_events) == 1


def test_orchestrator_budget_runtime_limit_enforcement(
    session_factory: sessionmaker[Session],
) -> None:
    sink = MockEventSink()
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(
        session_factory=session_factory,
        backend=backend,
        event_sink=sink,
    )

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session, budget={"max_runtime_seconds": 60})
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=e1.id,
            new_status=ExecutionStatus.RUNNING,
        )
        update_execution_status(
            session=session,
            execution_id=e1.id,
            new_status=ExecutionStatus.COMPLETED,
            resource_usage={"runtime_seconds": 65.0},
        )
        session.commit()
        e2_id = e2.id

    with pytest.raises(BudgetExceededError) as exc_info:
        orchestrator.run_execution(
            execution_id=e2_id,
            code_files={"train.py": "print('blocked')"},
        )

    assert exc_info.value.dimension == "max_runtime_seconds"
    assert len(backend.executed_requests) == 0


def test_orchestrator_cancellation(
    session_factory: sessionmaker[Session],
) -> None:
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        e_pending = create_execution(session=session, experiment_id=exp_id)
        e_running = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=e_running.id,
            new_status=ExecutionStatus.RUNNING,
        )
        e_completed = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=e_completed.id,
            new_status=ExecutionStatus.RUNNING,
        )
        update_execution_status(
            session=session,
            execution_id=e_completed.id,
            new_status=ExecutionStatus.COMPLETED,
        )
        session.commit()
        pending_id = e_pending.id
        running_id = e_running.id
        completed_id = e_completed.id

    # 1. Cancel pending
    assert orchestrator.cancel_execution(pending_id) is True
    # 2. Cancel running
    assert orchestrator.cancel_execution(running_id) is True
    assert running_id in backend.cancelled_executions
    # 3. Cancel completed (should return False)
    assert orchestrator.cancel_execution(completed_id) is False

    with session_factory() as session:
        m_pending = session.get(ExecutionModel, pending_id)
        m_running = session.get(ExecutionModel, running_id)
        m_completed = session.get(ExecutionModel, completed_id)
        assert m_pending is not None and m_pending.status == ExecutionStatus.CANCELLED.value
        assert m_running is not None and m_running.status == ExecutionStatus.CANCELLED.value
        assert m_completed is not None and m_completed.status == ExecutionStatus.COMPLETED.value


def test_orchestrator_reconcile_stale_executions(
    session_factory: sessionmaker[Session],
) -> None:
    backend = MockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        stale_time = datetime.now(UTC) - timedelta(seconds=7200)
        fresh_time = datetime.now(UTC) - timedelta(seconds=10)

        e_stale = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=e_stale.id,
            new_status=ExecutionStatus.RUNNING,
            started_at=stale_time,
        )

        e_fresh = create_execution(session=session, experiment_id=exp_id)
        update_execution_status(
            session=session,
            execution_id=e_fresh.id,
            new_status=ExecutionStatus.RUNNING,
            started_at=fresh_time,
        )
        session.commit()
        stale_id = e_stale.id
        fresh_id = e_fresh.id

    reconciled = orchestrator.reconcile_stale_executions(stale_threshold_seconds=3600)

    assert stale_id in reconciled
    assert fresh_id not in reconciled

    with session_factory() as session:
        m_stale = session.get(ExecutionModel, stale_id)
        m_fresh = session.get(ExecutionModel, fresh_id)
        assert m_stale is not None and m_stale.status == ExecutionStatus.FAILED.value
        assert m_fresh is not None and m_fresh.status == ExecutionStatus.RUNNING.value


def test_orchestrator_fail_closed_backend_error(
    session_factory: sessionmaker[Session],
) -> None:
    """Demonstrate fail-closed behavior when Docker daemon is unavailable."""
    backend = MockBackend(
        exc_to_raise=DockerUnavailableError(
            "Docker daemon ping failed. Untrusted execution fails closed."
        )
    )
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    with session_factory() as session:
        _run_id, exp_id = _setup_run_and_experiment(session)
        exec_domain = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        exec_id = exec_domain.id

    # Must raise DockerUnavailableError, NOT fall back to host execution
    with pytest.raises(DockerUnavailableError):
        orchestrator.run_execution(
            execution_id=exec_id,
            code_files={"train.py": "import os; os.system('echo dangerous')"},
        )

    # Verify execution was transitioned to FAILED in database rather than left RUNNING
    with session_factory() as session:
        exec_model = session.get(ExecutionModel, exec_id)
        assert exec_model is not None
        assert exec_model.status == ExecutionStatus.FAILED.value
