"""REX Production Concurrency & Cost Controls Stress Suite (Track B Sec 20-34).

Validates:
1. Multi-run concurrency under SQLite WAL mode:
   - 2 concurrent research runs / loops writing to SQLite
   - 5 concurrent research runs / loops writing to SQLite
   - 10 concurrent research runs / loops writing to SQLite
   - Assert: 0 deadlocks, 0 OperationalError: database is locked, PRAGMA integrity_check == "ok"
2. Optimistic concurrency and race condition resilience.
3. Budget exhaustion limits:
   - max_executions limit exceeded -> halts run into ResearchState.STOP
   - max_runtime_seconds limit exceeded -> halts run into ResearchState.STOP
   - max_artifact_volume_bytes limit exceeded -> halts run into ResearchState.STOP
   - max_experiments limit exceeded -> raises BudgetExceededError
   - Audit event emission on budget breach (BUDGET_EXCEEDED)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from rex.controller.autonomous_loop import (
    AutonomousLoopConfig,
    AutonomousResearchLoop,
)
from rex.controller.budgets import (
    BudgetUsage,
    ResearchBudget,
    check_budget_limits,
    record_budget_exceeded_event,
)
from rex.controller.exceptions import (
    BudgetExceededError,
    StaleStateError,
)
from rex.controller.state_machine import ResearchStateMachine
from rex.domain.models import (
    ExecutionStatus,
    ResearchState,
)
from rex.observability.events import (
    ActorType,
    EventType,
    InMemoryEventSink,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import EventRepository


@pytest.fixture
def wal_db_engine(tmp_path: Path):
    """File-backed SQLite engine strictly configured in WAL mode with busy timeout."""
    db_file = tmp_path / "concurrency_stress.db"
    engine = create_db_engine(f"sqlite:///{db_file}")
    init_db(engine)
    return engine


@pytest.fixture
def session_factory(wal_db_engine):
    return create_session_factory(wal_db_engine)


def _worker_create_run_pipeline(
    factory: sessionmaker,
    worker_idx: int,
    run_prefix: str = "run_conc",
) -> str:
    """Helper simulating an autonomous research run lifecycle in a background thread."""
    run_id = f"{run_prefix}_{worker_idx}"

    with factory() as session:
        run = ResearchRunModel(
            id=run_id,
            title=f"Concurrent Run {worker_idx}",
            research_question=f"Question for run {worker_idx}?",
            status=ResearchState.INITIALIZE.value,
            configuration_json={"iteration": 1},
            budget_json={"max_executions": 10, "max_runtime_seconds": 3600},
        )
        hyp = HypothesisModel(
            id=f"hyp_{run_id}",
            research_run_id=run_id,
            statement=f"Hypothesis for worker {worker_idx}",
            rationale=f"Rationale {worker_idx}",
            expected_direction="increase",
            falsification_condition=f"Falsification condition {worker_idx}",
            status="proposed",
        )
        session.add_all([run, hyp])
        session.commit()

    # Step 2: Transition through state machine to DESIGN
    with factory() as session:
        ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=ResearchState.UNDERSTAND,
            actor=ActorType.CONTROLLER,
        )
        ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=ResearchState.LITERATURE,
            actor=ActorType.CONTROLLER,
        )
        ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=ResearchState.HYPOTHESES,
            actor=ActorType.CONTROLLER,
        )
        ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=ResearchState.DESIGN,
            actor=ActorType.CONTROLLER,
        )
        session.commit()

    # Step 3: Insert experiment and execution
    with factory() as session:
        exp = ExperimentModel(
            id=f"exp_{run_id}",
            research_run_id=run_id,
            hypothesis_id=f"hyp_{run_id}",
            objective=f"Objective {worker_idx}",
            status="success",
        )
        exec_model = ExecutionModel(
            id=f"exec_{run_id}",
            experiment_id=exp.id,
            status=ExecutionStatus.COMPLETED.value,
            command=f"python script_{worker_idx}.py",
        )
        res = ResultModel(
            id=f"res_{run_id}",
            execution_id=exec_model.id,
            metric_name="score",
            metric_value=0.88,
        )
        session.add_all([exp, exec_model, res])
        session.commit()

    return run_id


# =============================================================================
# 1. Multi-Run Concurrency Stress Tests (2, 5, 10 Parallel Loops)
# =============================================================================


def test_concurrent_research_runs_2_parallel(session_factory, wal_db_engine):
    """Stress test: 2 parallel research runs writing simultaneously to SQLite in WAL mode."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_worker_create_run_pipeline, session_factory, i, "run_2par")
            for i in range(2)
        ]
        created_runs = [f.result() for f in as_completed(futures)]

    assert len(created_runs) == 2

    # Verify database integrity and persisted records
    with wal_db_engine.connect() as conn:
        integrity = conn.execute(text("PRAGMA integrity_check")).scalar()
        assert str(integrity).lower() == "ok"

    with session_factory() as session:
        runs = session.scalars(
            select(ResearchRunModel).where(ResearchRunModel.id.like("run_2par_%"))
        ).all()
        assert len(runs) == 2
        for r in runs:
            assert r.status == ResearchState.DESIGN.value

        exps = session.scalars(
            select(ExperimentModel).where(ExperimentModel.id.like("exp_%"))
        ).all()
        assert len(exps) >= 2


def test_concurrent_research_runs_5_parallel(session_factory, wal_db_engine):
    """Stress test: 5 parallel research runs writing simultaneously to SQLite in WAL mode."""
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(_worker_create_run_pipeline, session_factory, i, "run_5par")
            for i in range(5)
        ]
        created_runs = [f.result() for f in as_completed(futures)]

    assert len(created_runs) == 5

    # Verify zero database lock corruption
    with wal_db_engine.connect() as conn:
        integrity = conn.execute(text("PRAGMA integrity_check")).scalar()
        assert str(integrity).lower() == "ok"

    with session_factory() as session:
        runs = session.scalars(
            select(ResearchRunModel).where(ResearchRunModel.id.like("run_5par_%"))
        ).all()
        assert len(runs) == 5
        assert all(r.status == ResearchState.DESIGN.value for r in runs)


def test_concurrent_research_runs_10_parallel(session_factory, wal_db_engine):
    """Stress test: 10 parallel research runs writing simultaneously to SQLite in WAL mode.

    Asserts zero deadlocks, zero 'database is locked' errors, and complete PRAGMA integrity.
    """
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(_worker_create_run_pipeline, session_factory, i, "run_10par")
            for i in range(10)
        ]
        created_runs = [f.result() for f in as_completed(futures)]

    assert len(created_runs) == 10

    # Strict check on SQLite WAL integrity
    with wal_db_engine.connect() as conn:
        integrity = conn.execute(text("PRAGMA integrity_check")).scalar()
        assert str(integrity).lower() == "ok"

    with session_factory() as session:
        runs = session.scalars(
            select(ResearchRunModel).where(ResearchRunModel.id.like("run_10par_%"))
        ).all()
        assert len(runs) == 10
        assert all(r.status == ResearchState.DESIGN.value for r in runs)

        executions = session.scalars(
            select(ExecutionModel).where(ExecutionModel.status == ExecutionStatus.COMPLETED.value)
        ).all()
        assert len(executions) >= 10


# =============================================================================
# 2. Concurrency Race Conditions & Optimistic Locking
# =============================================================================


def test_concurrent_optimistic_state_conflict(session_factory):
    """Verify concurrent conflicting transitions on the same run detect stale state."""
    with session_factory() as session:
        run = ResearchRunModel(
            id="run_conflict_test",
            title="Conflict Run",
            research_question="Can concurrent actors clash?",
            status=ResearchState.INITIALIZE.value,
        )
        session.add(run)
        session.commit()

    # Actor 1 executes transition from INITIALIZE -> UNDERSTAND
    with session_factory() as session1:
        ResearchStateMachine.transition(
            session=session1,
            run_id="run_conflict_test",
            target_state=ResearchState.UNDERSTAND,
            actor=ActorType.CONTROLLER,
            expected_state=ResearchState.INITIALIZE,
        )
        session1.commit()

    # Actor 2 attempts conflicting transition with stale expected_state=INITIALIZE
    with session_factory() as session2, pytest.raises(StaleStateError) as exc_info:
        ResearchStateMachine.transition(
            session=session2,
            run_id="run_conflict_test",
            target_state=ResearchState.UNDERSTAND,
            actor=ActorType.CONTROLLER,
            expected_state=ResearchState.INITIALIZE,
        )
    assert "Concurrency conflict" in str(exc_info.value)


# =============================================================================
# 3. Budget Exhaustion Limits & Halting Controls
# =============================================================================


def test_budget_exhaustion_max_executions_direct_check():
    """Verify check_budget_limits enforces max_executions cap."""
    budget = ResearchBudget(max_executions=3)
    usage = BudgetUsage(executions_count=3)

    # Launching execution when usage equals limit must raise BudgetExceededError
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(
            budget=budget,
            usage=usage,
            run_id="run_budget_1",
            is_launching_execution=True,
        )
    assert exc_info.value.dimension == "max_executions"
    assert exc_info.value.limit == 3


def test_budget_exhaustion_max_runtime_seconds_direct_check():
    """Verify check_budget_limits enforces max_runtime_seconds cap."""
    budget = ResearchBudget(max_runtime_seconds=120)
    usage = BudgetUsage(total_runtime_seconds=121.5)

    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(
            budget=budget,
            usage=usage,
            run_id="run_budget_2",
            is_launching_execution=False,
        )
    assert exc_info.value.dimension == "max_runtime_seconds"
    assert exc_info.value.limit == 120


def test_budget_exhaustion_max_artifact_volume_bytes_direct_check():
    """Verify check_budget_limits enforces max_artifact_volume_bytes cap."""
    budget = ResearchBudget(max_artifact_volume_bytes=1000)
    usage = BudgetUsage(total_artifact_bytes=1500)

    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(
            budget=budget,
            usage=usage,
            run_id="run_budget_3",
            is_launching_execution=False,
        )
    assert exc_info.value.dimension == "max_artifact_volume_bytes"
    assert exc_info.value.limit == 1000


def test_budget_exhaustion_max_experiments_direct_check():
    """Verify check_budget_limits enforces max_experiments cap."""
    budget = ResearchBudget(max_experiments=2)
    usage = BudgetUsage(experiments_count=3)

    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(
            budget=budget,
            usage=usage,
            run_id="run_budget_4",
            is_launching_execution=False,
        )
    assert exc_info.value.dimension == "max_experiments"
    assert exc_info.value.limit == 2


def test_budget_exhaustion_halts_autonomous_loop(session_factory):
    """Verify AutonomousResearchLoop detects budget breach and halts run into ResearchState.STOP."""
    run_id = "run_loop_budget_stop"

    with session_factory() as session:
        # Configure run with very low max_executions = 1
        run = ResearchRunModel(
            id=run_id,
            title="Loop Budget Breach",
            research_question="Does budget halt the loop?",
            status=ResearchState.INITIALIZE.value,
            budget_json={"max_executions": 1, "max_runtime_seconds": 3600},
        )
        hyp = HypothesisModel(
            id="hyp_budget_stop",
            research_run_id=run_id,
            statement="Hypothesis for budget breach",
            rationale="R",
            expected_direction="increase",
            falsification_condition="FC",
            status="proposed",
        )
        exp = ExperimentModel(
            id="exp_budget_stop",
            research_run_id=run_id,
            hypothesis_id="hyp_budget_stop",
            objective="Exp",
            status="success",
        )
        # Create 2 executions so executions_count (2) > max_executions (1)
        exec1 = ExecutionModel(
            id="exec_budget_1",
            experiment_id=exp.id,
            status=ExecutionStatus.COMPLETED.value,
        )
        exec2 = ExecutionModel(
            id="exec_budget_2",
            experiment_id=exp.id,
            status=ExecutionStatus.COMPLETED.value,
        )
        session.add_all([run, hyp, exp, exec1, exec2])
        session.commit()

    sink = InMemoryEventSink()
    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=AutonomousLoopConfig(max_iterations=5),
        event_sink=sink,
    )

    # Executing the autonomous loop should immediately halt due to budget breach
    summary = loop.run(run_id)

    assert summary.final_state in (ResearchState.STOP, ResearchState.FAILED)
    assert "Budget exceeded" in (summary.terminated_reason or "")

    with session_factory() as session:
        updated_run = session.get(ResearchRunModel, run_id)
        assert updated_run is not None
        assert updated_run.status in ("STOP", "FAILED")


def test_budget_exceeded_audit_event_recorded(session_factory):
    """Verify record_budget_exceeded_event logs a BUDGET_EXCEEDED event in EventRepository."""
    run_id = "run_audit_event_budget"
    with session_factory() as session:
        run = ResearchRunModel(
            id=run_id,
            title="Audit Event Run",
            research_question="Audit question",
            status=ResearchState.INITIALIZE.value,
        )
        session.add(run)
        session.commit()

    sink = InMemoryEventSink()
    with session_factory() as session:
        record_budget_exceeded_event(
            session=session,
            research_run_id=run_id,
            dimension="max_executions",
            limit=5,
            current_usage=6,
            actor=ActorType.CONTROLLER,
            event_sink=sink,
        )
        session.commit()

    # Verify event sink and persistent database event
    emitted = [e for e in sink.events if e.event_type == EventType.BUDGET_EXCEEDED]
    assert len(emitted) == 1
    assert emitted[0].payload["dimension"] == "max_executions"
    assert emitted[0].payload["current_usage"] == 6

    with session_factory() as session:
        repo = EventRepository(session)
        events = repo.list_by_run(run_id)
        budget_events = [e for e in events if e.event_type == EventType.BUDGET_EXCEEDED.value]
        assert len(budget_events) == 1
        assert budget_events[0].payload_json["dimension"] == "max_executions"
