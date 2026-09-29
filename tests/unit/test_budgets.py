"""Unit tests for Research Run Budgets and Resource Accounting Subsystem (REX-011)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from rex.controller.budgets import (
    BudgetUsage,
    ResearchBudget,
    check_budget_limits,
    compute_budget_usage,
    load_run_budget,
    record_budget_exceeded_event,
)
from rex.controller.exceptions import (
    BudgetExceededError,
    ConcurrencyLimitExceededError,
)
from rex.controller.executions import (
    create_execution,
    record_artifact,
    update_execution_status,
)
from rex.controller.experiments import create_experiment
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ArtifactType,
    ExecutionStatus,
)
from rex.observability.events import (
    ActorType,
    EventType,
    create_event,
)
from rex.persistence.database import create_db_engine, init_db
from rex.persistence.models import ResearchRunModel
from rex.persistence.repositories import EventRepository


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    db_path = tmp_path / "test_budgets.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class MockEventSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


def test_research_budget_defaults() -> None:
    budget = ResearchBudget()
    assert budget.max_experiments == 10
    assert budget.max_executions == 30
    assert budget.max_concurrent_executions == 1
    assert budget.max_runtime_seconds == 3600
    assert budget.max_artifact_volume_bytes == 500 * 1024 * 1024
    assert budget.max_llm_calls == 100
    assert budget.max_token_cost is None


def test_research_budget_overrides() -> None:
    overrides = {
        "max_experiments": 5,
        "max_executions": 15,
        "max_concurrent_executions": 3,
        "max_runtime_seconds": 1800,
        "max_token_cost": 25.5,
    }
    budget = ResearchBudget.from_settings(overrides=overrides)
    assert budget.max_experiments == 5
    assert budget.max_executions == 15
    assert budget.max_concurrent_executions == 3
    assert budget.max_runtime_seconds == 1800
    assert budget.max_token_cost == 25.5
    # Non-overridden retain defaults
    assert budget.max_llm_calls == 100
    assert budget.max_artifact_volume_bytes == 500 * 1024 * 1024


def test_research_budget_validation_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ResearchBudget(max_experiments=0)

    with pytest.raises(ValidationError):
        ResearchBudget(max_executions=-1)

    with pytest.raises(ValidationError):
        ResearchBudget(max_runtime_seconds=0)

    with pytest.raises(ValidationError):
        ResearchBudget(max_token_cost=-5.0)

    with pytest.raises(ValidationError):
        ResearchBudget(unknown_dimension=10)  # type: ignore[call-arg]


def test_budget_usage_defaults() -> None:
    usage = BudgetUsage()
    assert usage.experiments_count == 0
    assert usage.executions_count == 0
    assert usage.concurrent_executions_count == 0
    assert usage.total_runtime_seconds == 0.0
    assert usage.total_artifact_bytes == 0
    assert usage.llm_calls_count == 0
    assert usage.token_cost == 0.0


def test_compute_budget_usage_aggregates_correctly(
    session_factory: sessionmaker[Session],
) -> None:
    sink = MockEventSink()
    with session_factory() as session:
        # Create research run with custom budget in budget_json
        run = create_research_run(
            session=session,
            research_question="Does gradient clipping stabilize training?",
            title="Clipping Study",
            budget={"max_experiments": 5, "max_executions": 10},
            actor=ActorType.OWNER,
            event_sink=sink,
        )

        # 1. Create two experiments
        exp1 = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Evaluate norm 1.0 clipping",
            actor=ActorType.RESEARCH_AGENT,
        )
        exp2 = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Evaluate norm 0.5 clipping",
            actor=ActorType.RESEARCH_AGENT,
        )

        # 2. Create executions
        start1 = datetime.now(UTC) - timedelta(seconds=120)
        fin1 = datetime.now(UTC) - timedelta(seconds=60)
        e1 = create_execution(
            session=session,
            experiment_id=exp1.id,
            status=ExecutionStatus.PENDING,
            actor=ActorType.EXECUTION_WORKER,
        )
        update_execution_status(
            session=session,
            execution_id=e1.id,
            new_status=ExecutionStatus.RUNNING,
            started_at=start1,
            actor=ActorType.EXECUTION_WORKER,
        )
        update_execution_status(
            session=session,
            execution_id=e1.id,
            new_status=ExecutionStatus.COMPLETED,
            finished_at=fin1,
            resource_usage={"runtime_seconds": 60.0},
            actor=ActorType.EXECUTION_WORKER,
        )

        e2 = create_execution(
            session=session,
            experiment_id=exp2.id,
            status=ExecutionStatus.PENDING,
            actor=ActorType.EXECUTION_WORKER,
        )
        update_execution_status(
            session=session,
            execution_id=e2.id,
            new_status=ExecutionStatus.RUNNING,
            actor=ActorType.EXECUTION_WORKER,
        )

        # 3. Create artifacts
        record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="stdout1.log",
            content_hash="1" * 64,
            size_bytes=2048,
            execution_id=e1.id,
            actor=ActorType.EXECUTION_WORKER,
        )
        record_artifact(
            session=session,
            artifact_type=ArtifactType.PLOT,
            path="loss_curve.png",
            content_hash="2" * 64,
            size_bytes=8192,
            execution_id=e1.id,
            actor=ActorType.EXECUTION_WORKER,
        )

        # 4. Record LLM action events
        ev1 = create_event(
            event_type=EventType.AGENT_ACTION,
            actor=ActorType.RESEARCH_AGENT,
            research_run_id=run.id,
            payload={"is_llm_call": True, "cost": 0.05},
        )
        ev2 = create_event(
            event_type=EventType.AGENT_ACTION,
            actor=ActorType.RESEARCH_AGENT,
            research_run_id=run.id,
            payload={"is_llm_call": True, "cost": 0.12},
        )
        EventRepository(session).record_event(ev1)
        EventRepository(session).record_event(ev2)
        session.commit()

        # Compute usage
        usage = compute_budget_usage(session, run.id)

        assert usage.experiments_count == 2
        assert usage.executions_count == 2
        assert usage.concurrent_executions_count == 1  # e2 is RUNNING
        assert usage.total_runtime_seconds >= 60.0
        assert usage.total_artifact_bytes == 2048 + 8192
        assert usage.llm_calls_count == 2
        assert round(usage.token_cost, 2) == 0.17

        # Check budget loaded from run model
        run_model = session.get(ResearchRunModel, run.id)
        assert run_model is not None
        loaded_budget = load_run_budget(run_model)
        assert loaded_budget.max_experiments == 5
        assert loaded_budget.max_executions == 10


def test_check_budget_limits_concurrency_exceeded() -> None:
    budget = ResearchBudget(max_concurrent_executions=1)
    usage = BudgetUsage(concurrent_executions_count=1)

    with pytest.raises(ConcurrencyLimitExceededError) as exc_info:
        check_budget_limits(budget, usage, run_id="run_123", is_launching_execution=True)

    assert exc_info.value.dimension == "max_concurrent_executions"
    assert exc_info.value.limit == 1
    assert exc_info.value.current_usage == 1
    assert "Concurrency limit exceeded" in str(exc_info.value)

    # If not launching, concurrent executions limit is not triggered
    check_budget_limits(budget, usage, run_id="run_123", is_launching_execution=False)


def test_check_budget_limits_dimension_breaches() -> None:
    # 1. Executions count breach
    budget = ResearchBudget(max_executions=10)
    usage = BudgetUsage(executions_count=11)
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(budget, usage, is_launching_execution=False)
    assert exc_info.value.dimension == "max_executions"

    # 2. Experiments count breach
    budget = ResearchBudget(max_experiments=5)
    usage = BudgetUsage(experiments_count=6)
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(budget, usage, is_launching_execution=False)
    assert exc_info.value.dimension == "max_experiments"

    # 3. Runtime breach
    budget = ResearchBudget(max_runtime_seconds=100)
    usage = BudgetUsage(total_runtime_seconds=100.0)
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(budget, usage, is_launching_execution=False)
    assert exc_info.value.dimension == "max_runtime_seconds"

    # 4. Artifact volume breach
    budget = ResearchBudget(max_artifact_volume_bytes=1000)
    usage = BudgetUsage(total_artifact_bytes=1000)
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(budget, usage, is_launching_execution=False)
    assert exc_info.value.dimension == "max_artifact_volume_bytes"

    # 5. LLM calls breach
    budget = ResearchBudget(max_llm_calls=50)
    usage = BudgetUsage(llm_calls_count=50)
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(budget, usage, is_launching_execution=False)
    assert exc_info.value.dimension == "max_llm_calls"

    # 6. Token cost breach
    budget = ResearchBudget(max_token_cost=5.0)
    usage = BudgetUsage(token_cost=5.01)
    with pytest.raises(BudgetExceededError) as exc_info:
        check_budget_limits(budget, usage, is_launching_execution=False)
    assert exc_info.value.dimension == "max_token_cost"


def test_record_budget_exceeded_event_records_and_emits(
    session_factory: sessionmaker[Session],
) -> None:
    sink = MockEventSink()
    with session_factory() as session:
        run = create_research_run(
            session=session,
            research_question="Does weight decay reduce overfitting?",
            title="Weight Decay Study",
            actor=ActorType.OWNER,
        )
        session.commit()

        record_budget_exceeded_event(
            session=session,
            research_run_id=run.id,
            dimension="max_runtime_seconds",
            limit=3600,
            current_usage=3650.0,
            actor=ActorType.SYSTEM,
            event_sink=sink,
            context={"attempted_command": "python train.py"},
        )
        session.commit()

        # Verify event was persisted
        events = EventRepository(session).list_by_run(run.id)
        budget_events = [e for e in events if e.event_type == EventType.BUDGET_EXCEEDED]
        assert len(budget_events) == 1
        ev = budget_events[0]
        assert ev.payload_json["dimension"] == "max_runtime_seconds"
        assert ev.payload_json["limit"] == 3600
        assert ev.payload_json["current_usage"] == 3650.0
        assert ev.payload_json["attempted_command"] == "python train.py"

        # Verify emitted to sink
        assert len(sink.events) == 1
