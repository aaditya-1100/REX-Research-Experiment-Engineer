"""Comprehensive Concurrency and Budget Enforcement Test Suite (REX-010, REX-011).

Audits and verifies:
- Test A: Concurrent execution starts with max_executions limit
- Test B: Concurrent execution starts with max_concurrent_executions limit
- Test C: No double accounting (rejected executions & recovery do not double-count)
- Test D: Immediate backend failure releases concurrency capacity and permits subsequent runs
- Test E: Cancellation releases concurrency slot and pending cancellation does not consume budget
- Test F: Experiment budget enforcement (max_experiments cap)
- Test G: Post-flight cumulative artifact volume budget enforcement
- Test H: Cumulative runtime budget semantics vs per-execution timeout
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from rex.controller.budgets import compute_budget_usage
from rex.controller.exceptions import (
    BudgetExceededError,
    ConcurrencyLimitExceededError,
)
from rex.controller.execution_orchestrator import ExecutionOrchestrator
from rex.controller.executions import create_execution
from rex.controller.experiments import create_experiment
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ArtifactType,
    ExecutionStatus,
)
from rex.execution.backend import ExecutionBackend
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
)
from rex.persistence.repositories import EventRepository

SAMPLE_CODE = {"train.py": "print('training model')\n"}


class BlockingMockBackend(ExecutionBackend):
    """Mock backend that can block until signaled, allowing precise race orchestration."""

    def __init__(
        self,
        output_artifacts: list[OutputArtifactMetadata] | None = None,
        runtime_seconds: float = 1.0,
    ) -> None:
        self.output_artifacts = output_artifacts or []
        self.runtime_seconds = runtime_seconds
        self.started_event = threading.Event()
        self.continue_event = threading.Event()
        self.executed_requests: list[ExecutionRequest] = []
        self.lock = threading.Lock()

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        with self.lock:
            self.executed_requests.append(request)
        self.started_event.set()
        # Wait until test signals continuation
        self.continue_event.wait(timeout=10.0)
        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Completed",
            stderr="",
            output_artifacts=self.output_artifacts,
            duration_seconds=self.runtime_seconds,
            resource_usage={"runtime_seconds": self.runtime_seconds},
            cleaned_up=True,
        )


class ConfigurableMockBackend(ExecutionBackend):
    """Mock backend supporting per-call outcomes or exceptions."""

    def __init__(self) -> None:
        self.executed_requests: list[ExecutionRequest] = []
        self.exceptions_to_raise: list[Exception] = []
        self.outcomes: list[ExecutionOutcome] = []
        self.lock = threading.Lock()

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        with self.lock:
            self.executed_requests.append(request)
            if self.exceptions_to_raise:
                exc = self.exceptions_to_raise.pop(0)
                raise exc
            if self.outcomes:
                return self.outcomes.pop(0)
        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="OK",
            stderr="",
            output_artifacts=[],
            duration_seconds=1.0,
            resource_usage={"runtime_seconds": 1.0},
            cleaned_up=True,
        )


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    db_path = tmp_path / "test_concurrency.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _setup_run(
    session: Session,
    budget: dict[str, object] | None = None,
) -> tuple[str, str]:
    run = create_research_run(
        session=session,
        research_question="Concurrency and budget atomicity audit",
        title="Concurrency Study",
        budget=budget,
        actor=ActorType.OWNER,
    )
    exp = create_experiment(
        session=session,
        research_run_id=run.id,
        objective="Verify budget atomicity under concurrency",
        actor=ActorType.RESEARCH_AGENT,
    )
    session.commit()
    return run.id, exp.id


def test_concurrency_test_a_max_executions(session_factory: sessionmaker[Session]) -> None:
    """Test A — max executions under concurrent start attempts.

    Configure:
      max_executions = 1
      max_concurrent_executions >= 2
    Attempt two execution starts concurrently.
    Expected:
      - exactly one execution is admitted and executed
      - exactly one is rejected with BudgetExceededError
      - backend executes at most once
      - persisted usage never exceeds configured budget (1)
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_executions": 1, "max_concurrent_executions": 5},
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id, e2_id = e1.id, e2.id

    backend = ConfigurableMockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    results: list[object] = []
    errors: list[Exception] = []

    def _run(eid: str) -> None:
        try:
            res = orchestrator.run_execution(execution_id=eid, code_files=SAMPLE_CODE)
            results.append(res)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_run, e1_id)
        f2 = pool.submit(_run, e2_id)
        f1.result()
        f2.result()

    # Exactly one succeeded, exactly one was rejected with BudgetExceededError
    assert len(results) == 1, f"Expected exactly 1 successful execution, got {len(results)}"
    assert len(errors) == 1, f"Expected exactly 1 rejected execution, got {len(errors)}"
    assert isinstance(errors[0], BudgetExceededError)
    assert errors[0].dimension == "max_executions"

    # Backend was invoked exactly once
    assert len(backend.executed_requests) == 1

    # Persisted usage never exceeds the configured budget of 1
    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.executions_count == 1

        m1 = session.get(ExecutionModel, e1_id)
        m2 = session.get(ExecutionModel, e2_id)
        assert m1 is not None and m2 is not None
        statuses = {m1.status, m2.status}
        assert statuses == {ExecutionStatus.COMPLETED.value, ExecutionStatus.PENDING.value}


def test_concurrency_test_b_max_concurrent_executions(
    session_factory: sessionmaker[Session],
) -> None:
    """Test B — max concurrent executions under concurrent start attempts.

    Configure:
      max_concurrent_executions = 1
    Attempt two starts concurrently.
    Expected:
      - exactly one execution becomes RUNNING and invokes backend
      - second execution is rejected with ConcurrencyLimitExceededError
      - second execution never invokes backend
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_executions": 10, "max_concurrent_executions": 1},
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id, e2_id = e1.id, e2.id

    backend = BlockingMockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    e1_result: list[object] = []
    e1_error: list[Exception] = []

    def _run_worker_1() -> None:
        try:
            res = orchestrator.run_execution(execution_id=e1_id, code_files=SAMPLE_CODE)
            e1_result.append(res)
        except Exception as exc:  # noqa: BLE001
            e1_error.append(exc)

    t1 = threading.Thread(target=_run_worker_1)
    t1.start()

    # Wait until execution 1 has entered backend (meaning it reserved the slot and is RUNNING)
    assert backend.started_event.wait(timeout=10.0), "Worker 1 did not reach backend"

    # Verify execution 1 is RUNNING in DB while in backend
    with session_factory() as session:
        m1 = session.get(ExecutionModel, e1_id)
        assert m1 is not None and m1.status == ExecutionStatus.RUNNING.value
        usage = compute_budget_usage(session, run_id)
        assert usage.concurrent_executions_count == 1

    # Worker 2 now attempts to start while Worker 1 occupies the only concurrent slot
    with pytest.raises(ConcurrencyLimitExceededError) as exc_info:
        orchestrator.run_execution(execution_id=e2_id, code_files=SAMPLE_CODE)

    assert exc_info.value.limit == 1
    assert exc_info.value.current_usage == 1

    # Now let Worker 1 finish
    backend.continue_event.set()
    t1.join(timeout=10.0)

    assert len(e1_error) == 0
    assert len(e1_result) == 1

    # Backend was invoked only once (by Worker 1)
    assert len(backend.executed_requests) == 1
    assert backend.executed_requests[0].execution_id == e1_id

    # Verify final states in DB
    with session_factory() as session:
        m1 = session.get(ExecutionModel, e1_id)
        m2 = session.get(ExecutionModel, e2_id)
        assert m1 is not None and m1.status == ExecutionStatus.COMPLETED.value
        assert m2 is not None and m2.status == ExecutionStatus.PENDING.value
        usage = compute_budget_usage(session, run_id)
        assert usage.concurrent_executions_count == 0
        assert usage.executions_count == 1


def test_concurrency_test_c_no_double_accounting(session_factory: sessionmaker[Session]) -> None:
    """Test C — no double accounting.

    Ensure:
      - A rejected or unstarted execution does NOT consume budget
      - Retries / recovery do not count the same execution twice
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_executions": 2, "max_concurrent_executions": 2},
        )
        # Create 3 draft pending executions
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        e3 = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id, e2_id, e3_id = e1.id, e2.id, e3.id

    backend = ConfigurableMockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    # 1. Verify that simply creating 3 PENDING drafts consumes ZERO execution budget
    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.executions_count == 0
        assert usage.concurrent_executions_count == 0

    # 2. Run e1 -> succeeds
    orchestrator.run_execution(execution_id=e1_id, code_files=SAMPLE_CODE)

    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.executions_count == 1

    # 3. Run e2 -> succeeds (reaches max_executions=2)
    orchestrator.run_execution(execution_id=e2_id, code_files=SAMPLE_CODE)

    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.executions_count == 2

    # 4. Attempting to run e3 must be rejected because budget is full
    with pytest.raises(BudgetExceededError):
        orchestrator.run_execution(execution_id=e3_id, code_files=SAMPLE_CODE)

    # 5. Verify e3 rejection did not increment executions_count beyond 2
    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.executions_count == 2

    # 6. Verify stale execution recovery does NOT double-count
    # Put e1 into RUNNING status with stale timestamp to simulate crash recovery
    with session_factory() as session:
        m1 = session.get(ExecutionModel, e1_id)
        assert m1 is not None
        m1.status = ExecutionStatus.RUNNING.value
        m1.started_at = datetime.now(UTC) - timedelta(hours=2)
        session.commit()

        usage_mid = compute_budget_usage(session, run_id)
        # While RUNNING: count is still 2 (e1 running + e2 completed)
        assert usage_mid.executions_count == 2
        assert usage_mid.concurrent_executions_count == 1

    reconciled = orchestrator.reconcile_stale_executions(stale_threshold_seconds=1800)
    assert e1_id in reconciled

    with session_factory() as session:
        m1 = session.get(ExecutionModel, e1_id)
        assert m1 is not None and m1.status == ExecutionStatus.FAILED.value
        usage_post = compute_budget_usage(session, run_id)
        # After reconciliation to FAILED: count remains exactly 2 (no double counting!)
        assert usage_post.executions_count == 2
        assert usage_post.concurrent_executions_count == 0


def test_concurrency_test_d_failure_after_reservation(
    session_factory: sessionmaker[Session],
) -> None:
    """Test D — failure after reservation.

    If an execution is admitted and then the backend immediately fails:
      - the execution remains properly accounted for
      - concurrency capacity is released
      - the system does not leak a reservation
      - another valid execution can subsequently start
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_executions": 2, "max_concurrent_executions": 1},
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id, e2_id = e1.id, e2.id

    backend = ConfigurableMockBackend()
    # Configure backend to fail on first execution
    backend.exceptions_to_raise.append(RuntimeError("Hardware failure inside sandbox"))
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    # e1 is launched and fails in Phase 2
    with pytest.raises(RuntimeError, match="Hardware failure"):
        orchestrator.run_execution(execution_id=e1_id, code_files=SAMPLE_CODE)

    # Verify e1 is recorded as FAILED, concurrency is 0, execution count is 1
    with session_factory() as session:
        m1 = session.get(ExecutionModel, e1_id)
        assert m1 is not None and m1.status == ExecutionStatus.FAILED.value
        events = EventRepository(session).list_research_events_by_run(research_run_id=run_id)
        failed_events = [e for e in events if e.event_type == EventType.EXECUTION_FAILED]
        assert len(failed_events) >= 1
        assert "Hardware failure" in failed_events[-1].payload.get("reason", "")
        usage = compute_budget_usage(session, run_id)
        assert usage.concurrent_executions_count == 0  # Capacity released!
        assert usage.executions_count == 1  # Admitted and accounted for

    # e2 can now start and use the freed concurrency slot
    updated_e2, outcome_2 = orchestrator.run_execution(execution_id=e2_id, code_files=SAMPLE_CODE)
    assert outcome_2.status == ExecutionStatus.COMPLETED
    assert updated_e2.status == ExecutionStatus.COMPLETED

    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.concurrent_executions_count == 0
        assert usage.executions_count == 2


def test_concurrency_test_e_cancellation(session_factory: sessionmaker[Session]) -> None:
    """Test E — cancellation behavior.

    If an execution is cancelled:
      - concurrency capacity is released
      - cancellation does not create duplicate accounting
      - another execution can subsequently use the freed slot
      - cancelling an unstarted pending execution does not consume max_executions
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_executions": 2, "max_concurrent_executions": 1},
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        e_draft = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id, e2_id, ed_id = e1.id, e2.id, e_draft.id

    backend = BlockingMockBackend()
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    # 1. Test cancelling an unstarted PENDING execution
    cancelled_draft = orchestrator.cancel_execution(ed_id)
    assert cancelled_draft is True

    with session_factory() as session:
        md = session.get(ExecutionModel, ed_id)
        assert md is not None and md.status == ExecutionStatus.CANCELLED.value
        assert md.started_at is None
        # Unstarted cancelled execution does NOT consume max_executions budget
        usage = compute_budget_usage(session, run_id)
        assert usage.executions_count == 0

    # 2. Test cancelling an active RUNNING execution
    t1 = threading.Thread(
        target=lambda: orchestrator.run_execution(execution_id=e1_id, code_files=SAMPLE_CODE)
    )
    t1.start()
    assert backend.started_event.wait(timeout=10.0)

    # Concurrency is currently 1
    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.concurrent_executions_count == 1

    # Cancel e1 while running
    cancelled_active = orchestrator.cancel_execution(e1_id, reason="User cancelled active run")
    assert cancelled_active is True

    # Allow backend to finish
    backend.continue_event.set()
    t1.join(timeout=10.0)

    with session_factory() as session:
        m1 = session.get(ExecutionModel, e1_id)
        assert m1 is not None and m1.status == ExecutionStatus.CANCELLED.value
        usage = compute_budget_usage(session, run_id)
        # Concurrency capacity released!
        assert usage.concurrent_executions_count == 0
        # e1 was admitted and ran partially, so it counts as 1 execution
        assert usage.executions_count == 1

    # 3. Another execution (e2) can now start using the freed concurrency slot
    backend2 = ConfigurableMockBackend()
    orchestrator2 = ExecutionOrchestrator(session_factory=session_factory, backend=backend2)
    _updated_e2, outcome_2 = orchestrator2.run_execution(execution_id=e2_id, code_files=SAMPLE_CODE)
    assert outcome_2.status == ExecutionStatus.COMPLETED

    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.concurrent_executions_count == 0
        assert usage.executions_count == 2


def test_concurrency_max_experiments_budget(session_factory: sessionmaker[Session]) -> None:
    """Test F — experiment creation respects max_experiments budget."""
    with session_factory() as session:
        run = create_research_run(
            session=session,
            research_question="Does dropout rate 0.2 outperform 0.5?",
            title="Experiment Budget Test",
            budget={"max_experiments": 1},
            actor=ActorType.OWNER,
        )
        session.commit()
        run_id = run.id

    # First experiment succeeds
    with session_factory() as session:
        exp1 = create_experiment(
            session=session,
            research_run_id=run_id,
            objective="First allowed experiment",
            actor=ActorType.RESEARCH_AGENT,
        )
        session.commit()
        assert exp1.id is not None

    # Second experiment breaches max_experiments cap
    with session_factory() as session:
        with pytest.raises(BudgetExceededError) as exc_info:
            create_experiment(
                session=session,
                research_run_id=run_id,
                objective="Second experiment should be rejected",
                actor=ActorType.RESEARCH_AGENT,
            )
        assert exc_info.value.dimension == "max_experiments"
        assert exc_info.value.limit == 1
        assert exc_info.value.current_usage == 1

        # Verify BUDGET_EXCEEDED audit event was recorded
        events = EventRepository(session).list_research_events_by_run(research_run_id=run_id)
        budget_events = [e for e in events if e.event_type == EventType.BUDGET_EXCEEDED]
        assert len(budget_events) >= 1
        assert budget_events[-1].payload["dimension"] == "max_experiments"


def test_post_flight_artifact_volume_enforcement(
    session_factory: sessionmaker[Session],
) -> None:
    """Test G — post-flight cumulative artifact volume budget enforcement.

    If an execution generates artifacts that collectively exceed max_artifact_volume_bytes:
      - The artifacts are persisted in DB as empirical evidence (per 03_Security_Access.md)
      - The execution status is overridden to FAILED
      - A BUDGET_EXCEEDED event is emitted
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_artifact_volume_bytes": 1000},
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id = e1.id

    large_artifact = OutputArtifactMetadata(
        path="huge_model.bin",
        content_hash="h" * 64,
        size_bytes=1500,  # Exceeds 1000 byte limit
        artifact_type=ArtifactType.MODEL,
    )
    backend = ConfigurableMockBackend()
    backend.outcomes.append(
        ExecutionOutcome(
            execution_id=e1_id,
            experiment_id=exp_id,
            research_run_id=run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Trained huge model",
            stderr="",
            output_artifacts=[large_artifact],
            duration_seconds=2.0,
            resource_usage={"runtime_seconds": 2.0},
            cleaned_up=True,
        )
    )
    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    updated_exec, _outcome = orchestrator.run_execution(
        execution_id=e1_id,
        code_files=SAMPLE_CODE,
    )

    # Execution status must be overridden to FAILED because run artifact budget was breached
    assert updated_exec.status == ExecutionStatus.FAILED

    # Evidence is preserved in database
    with session_factory() as session:
        artifacts = session.query(ArtifactModel).filter(ArtifactModel.execution_id == e1_id).all()
        assert len(artifacts) >= 1
        assert any(a.path == "huge_model.bin" and a.size_bytes == 1500 for a in artifacts)

        # BUDGET_EXCEEDED event was recorded
        events = EventRepository(session).list_research_events_by_run(research_run_id=run_id)
        budget_events = [e for e in events if e.event_type == EventType.BUDGET_EXCEEDED]
        assert len(budget_events) >= 1
        assert budget_events[-1].payload["dimension"] == "max_artifact_volume_bytes"

        # EXECUTION_FAILED event records the failure reason
        failed_events = [e for e in events if e.event_type == EventType.EXECUTION_FAILED]
        assert len(failed_events) >= 1
        assert (
            "artifact volume budget exceeded" in failed_events[-1].payload.get("reason", "").lower()
        )


def test_runtime_budget_semantics(session_factory: sessionmaker[Session]) -> None:
    """Test H — runtime budget semantics.

    Validates that max_runtime_seconds represents cumulative execution runtime
    across the entire research run, distinct from per-execution timeout.
    """
    with session_factory() as session:
        run_id, exp_id = _setup_run(
            session,
            budget={"max_runtime_seconds": 100, "max_executions": 5},
        )
        e1 = create_execution(session=session, experiment_id=exp_id)
        e2 = create_execution(session=session, experiment_id=exp_id)
        e3 = create_execution(session=session, experiment_id=exp_id)
        session.commit()
        e1_id, e2_id, e3_id = e1.id, e2.id, e3.id

    backend = ConfigurableMockBackend()
    backend.outcomes.append(
        ExecutionOutcome(
            execution_id=e1_id,
            experiment_id=exp_id,
            research_run_id=run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Run 1",
            stderr="",
            output_artifacts=[],
            duration_seconds=60.0,
            resource_usage={"runtime_seconds": 60.0},
            cleaned_up=True,
        )
    )
    backend.outcomes.append(
        ExecutionOutcome(
            execution_id=e2_id,
            experiment_id=exp_id,
            research_run_id=run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Run 2",
            stderr="",
            output_artifacts=[],
            duration_seconds=50.0,
            resource_usage={"runtime_seconds": 50.0},
            cleaned_up=True,
        )
    )

    orchestrator = ExecutionOrchestrator(session_factory=session_factory, backend=backend)

    # Run 1 consumes 60 seconds (budget remaining: 40s)
    orchestrator.run_execution(execution_id=e1_id, code_files=SAMPLE_CODE)

    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.total_runtime_seconds == 60.0

    # Run 2 is admitted (current usage 60s < 100s limit) and consumes 50s
    orchestrator.run_execution(execution_id=e2_id, code_files=SAMPLE_CODE)

    with session_factory() as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.total_runtime_seconds == 110.0

    # Run 3 attempts pre-flight validation with cumulative runtime = 110s >= 100s
    with pytest.raises(BudgetExceededError) as exc_info:
        orchestrator.run_execution(execution_id=e3_id, code_files=SAMPLE_CODE)

    assert exc_info.value.dimension == "max_runtime_seconds"
    assert exc_info.value.limit == 100
    assert exc_info.value.current_usage == 110.0
