"""REX Research Run Budgets and Resource Accounting Subsystem (REX-011).

Provides typed budget constraints, aggregate resource consumption accounting,
pre-flight validation before sandbox dispatch, concurrency slot enforcement,
and structured budget breach event emission.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from rex.config.settings import BudgetSettings, get_settings
from rex.controller.exceptions import (
    BudgetExceededError,
    ConcurrencyLimitExceededError,
    MissingResearchRunError,
)
from rex.domain.models import ExecutionStatus
from rex.execution.resources import ResourceLimits
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.models import (
    ArtifactModel,
    EventModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
)
from rex.persistence.repositories import EventRepository


class ResearchBudget(BaseModel):
    """Strongly typed research run budget limits across all resource dimensions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_experiments: int = Field(
        default=10,
        ge=1,
        description="Maximum number of experiments permitted in a research run",
    )
    max_executions: int = Field(
        default=30,
        ge=1,
        description="Maximum number of total execution runs permitted",
    )
    max_concurrent_executions: int = Field(
        default=1,
        ge=1,
        description="Maximum number of concurrent executions permitted simultaneously",
    )
    max_runtime_seconds: int = Field(
        default=3600,
        gt=0,
        description="Maximum aggregate execution runtime in seconds for the research run",
    )
    max_artifact_volume_bytes: int = Field(
        default=500 * 1024 * 1024,  # 500 MB
        gt=0,
        description="Maximum total disk volume for artifacts in bytes",
    )
    max_llm_calls: int = Field(
        default=100,
        ge=1,
        description="Maximum number of LLM API requests permitted",
    )
    max_token_cost: float | None = Field(
        default=None,
        ge=0.0,
        description="Maximum token spend limit in USD (None for unconstrained token budget)",
    )

    @classmethod
    def from_settings(
        cls,
        settings: BudgetSettings | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> "ResearchBudget":
        """Construct a ResearchBudget merging global config with optional run-specific overrides."""
        base_settings = settings or get_settings().budgets
        data: dict[str, Any] = {
            "max_experiments": base_settings.max_experiments,
            "max_executions": base_settings.max_executions,
            "max_concurrent_executions": base_settings.max_concurrent_executions,
            "max_runtime_seconds": base_settings.max_runtime_seconds,
            "max_artifact_volume_bytes": base_settings.max_artifact_volume_bytes,
            "max_llm_calls": base_settings.max_llm_calls,
            "max_token_cost": base_settings.max_token_cost,
        }
        if overrides:
            for k, v in overrides.items():
                if v is not None and k in data:
                    data[k] = v
        return cls(**data)

    @classmethod
    def from_run_model(cls, run: ResearchRunModel) -> "ResearchBudget":
        """Construct a ResearchBudget from a persisted ResearchRunModel record."""
        return cls.from_settings(overrides=run.budget_json or {})


class BudgetUsage(BaseModel):
    """Aggregate resource consumption measured across an active research investigation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiments_count: int = Field(
        default=0, ge=0, description="Total experiments created under this run"
    )
    executions_count: int = Field(
        default=0, ge=0, description="Total execution attempts created under this run"
    )
    concurrent_executions_count: int = Field(
        default=0,
        ge=0,
        description="Current number of active executions with RUNNING status",
    )
    total_runtime_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Aggregate execution wall-clock runtime in seconds",
    )
    total_artifact_bytes: int = Field(
        default=0, ge=0, description="Total size in bytes of all registered artifacts"
    )
    llm_calls_count: int = Field(default=0, ge=0, description="Number of completed LLM API calls")
    token_cost: float = Field(default=0.0, ge=0.0, description="Total token spend accrued in USD")


def compute_budget_usage(session: Session, research_run_id: str) -> BudgetUsage:
    """Calculate exact aggregate resource consumption from persistent database records."""
    # 1. Count experiments for this research run
    experiments_count = (
        session.query(func.count(ExperimentModel.id))
        .filter(ExperimentModel.research_run_id == research_run_id)
        .scalar()
        or 0
    )

    # 2. Query all executions belonging to experiments in this research run
    executions = (
        session.query(ExecutionModel)
        .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
        .filter(ExperimentModel.research_run_id == research_run_id)
        .all()
    )

    ADMITTED_EXECUTION_STATUSES = frozenset(
        {
            ExecutionStatus.RUNNING.value,
            ExecutionStatus.COMPLETED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.TIMEOUT.value,
        }
    )

    admitted_count = 0
    concurrent_count = 0
    total_runtime = 0.0
    now = datetime.now(UTC)

    for ex in executions:
        is_admitted = ex.status in ADMITTED_EXECUTION_STATUSES or (
            ex.status == ExecutionStatus.CANCELLED.value and ex.started_at is not None
        )
        if is_admitted:
            admitted_count += 1

        if ex.status == ExecutionStatus.RUNNING.value:
            concurrent_count += 1

        # Calculate execution duration
        duration: float = 0.0
        if ex.resource_usage_json and "runtime_seconds" in ex.resource_usage_json:
            try:
                duration = float(ex.resource_usage_json["runtime_seconds"])
            except (ValueError, TypeError):
                duration = 0.0
        elif ex.started_at is not None and ex.finished_at is not None:
            st = (
                ex.started_at
                if ex.started_at.tzinfo is not None
                else ex.started_at.replace(tzinfo=UTC)
            )
            fin = (
                ex.finished_at
                if ex.finished_at.tzinfo is not None
                else ex.finished_at.replace(tzinfo=UTC)
            )
            duration = max(0.0, (fin - st).total_seconds())
        elif ex.started_at is not None and ex.status == ExecutionStatus.RUNNING.value:
            st = (
                ex.started_at
                if ex.started_at.tzinfo is not None
                else ex.started_at.replace(tzinfo=UTC)
            )
            duration = max(0.0, (now - st).total_seconds())

        total_runtime += duration

    # 3. Sum artifact volume
    artifact_bytes = (
        session.query(func.sum(ArtifactModel.size_bytes))
        .filter(ArtifactModel.research_run_id == research_run_id)
        .scalar()
        or 0
    )

    # 4. Aggregate LLM calls and token cost from events or configuration
    llm_calls_count = 0
    token_cost = 0.0

    events = (
        session.query(EventModel)
        .filter(
            EventModel.research_run_id == research_run_id,
            EventModel.event_type.in_(
                [
                    EventType.AGENT_ACTION.value,
                    "llm_call",
                    "llm_response",
                    "llm_reservation",
                ]
            ),
        )
        .all()
    )

    for ev in events:
        payload = ev.payload_json or {}
        is_completed_call = bool(
            payload.get("is_llm_call") or payload.get("llm_call") or ev.event_type == "llm_call"
        )
        is_active_reservation = bool(
            payload.get("is_llm_reservation") and payload.get("reservation_status") == "active"
        )

        if is_active_reservation:
            reserved_at_str = payload.get("reserved_at")
            if reserved_at_str:
                try:
                    res_time = datetime.fromisoformat(reserved_at_str)
                    if res_time.tzinfo is None:
                        res_time = res_time.replace(tzinfo=UTC)
                    if (now - res_time).total_seconds() > 300:
                        is_active_reservation = False
                except (ValueError, TypeError):
                    pass

        if is_completed_call or is_active_reservation:
            llm_calls_count += 1

        if is_completed_call:
            for key in ("cost", "llm_cost", "cost_usd", "spend", "token_cost"):
                if key in payload:
                    try:
                        token_cost += float(payload[key])
                        break
                    except (ValueError, TypeError):
                        pass
        elif is_active_reservation:
            for key in ("estimated_cost", "cost", "llm_cost"):
                if key in payload:
                    try:
                        token_cost += float(payload[key])
                        break
                    except (ValueError, TypeError):
                        pass

    return BudgetUsage(
        experiments_count=experiments_count,
        executions_count=admitted_count,
        concurrent_executions_count=concurrent_count,
        total_runtime_seconds=total_runtime,
        total_artifact_bytes=artifact_bytes,
        llm_calls_count=llm_calls_count,
        token_cost=token_cost,
    )


def load_run_budget(run: ResearchRunModel) -> ResearchBudget:
    """Load the effective budget for a research run record."""
    return ResearchBudget.from_run_model(run)


def check_budget_limits(
    budget: ResearchBudget,
    usage: BudgetUsage,
    run_id: str | None = None,
    is_launching_execution: bool = True,
    requested_limits: ResourceLimits | None = None,
) -> None:
    """Validate resource consumption against budget constraints.

    Raises:
        ConcurrencyLimitExceededError: When active execution slots are exhausted.
        BudgetExceededError: When any other resource dimension is exhausted.
    """
    # 1. Check concurrent executions slot availability
    if (
        is_launching_execution
        and usage.concurrent_executions_count >= budget.max_concurrent_executions
    ):
        raise ConcurrencyLimitExceededError(
            limit=budget.max_concurrent_executions,
            current_usage=usage.concurrent_executions_count,
            run_id=run_id,
        )

    # 2. Check total executions cap
    if is_launching_execution:
        if usage.executions_count >= budget.max_executions:
            raise BudgetExceededError(
                dimension="max_executions",
                limit=budget.max_executions,
                current_usage=usage.executions_count,
                run_id=run_id,
            )
    else:
        if usage.executions_count > budget.max_executions:
            raise BudgetExceededError(
                dimension="max_executions",
                limit=budget.max_executions,
                current_usage=usage.executions_count,
                run_id=run_id,
            )

    # 3. Check total experiments cap
    if usage.experiments_count > budget.max_experiments:
        raise BudgetExceededError(
            dimension="max_experiments",
            limit=budget.max_experiments,
            current_usage=usage.experiments_count,
            run_id=run_id,
        )

    # 4. Check aggregate runtime
    if usage.total_runtime_seconds >= budget.max_runtime_seconds:
        raise BudgetExceededError(
            dimension="max_runtime_seconds",
            limit=budget.max_runtime_seconds,
            current_usage=usage.total_runtime_seconds,
            run_id=run_id,
        )

    # 5. Check artifact volume
    if usage.total_artifact_bytes >= budget.max_artifact_volume_bytes:
        raise BudgetExceededError(
            dimension="max_artifact_volume_bytes",
            limit=budget.max_artifact_volume_bytes,
            current_usage=usage.total_artifact_bytes,
            run_id=run_id,
        )

    # 6. Check LLM call cap
    if usage.llm_calls_count >= budget.max_llm_calls:
        raise BudgetExceededError(
            dimension="max_llm_calls",
            limit=budget.max_llm_calls,
            current_usage=usage.llm_calls_count,
            run_id=run_id,
        )

    # 7. Check token cost cap if defined
    if budget.max_token_cost is not None and usage.token_cost >= budget.max_token_cost:
        raise BudgetExceededError(
            dimension="max_token_cost",
            limit=budget.max_token_cost,
            current_usage=usage.token_cost,
            run_id=run_id,
        )


def record_budget_exceeded_event(
    session: Session,
    research_run_id: str,
    dimension: str,
    limit: object,
    current_usage: object,
    actor: ActorType | str = ActorType.SYSTEM,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Record and emit a structured BUDGET_EXCEEDED audit event within an active session."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    run_model = session.get(ResearchRunModel, research_run_id)
    if run_model is None:
        raise MissingResearchRunError(research_run_id)

    payload: dict[str, Any] = {
        "research_run_id": research_run_id,
        "dimension": dimension,
        "limit": limit,
        "current_usage": current_usage,
    }
    if context:
        payload.update(context)

    event = create_event(
        event_type=EventType.BUDGET_EXCEEDED,
        actor=actor_enum,
        research_run_id=research_run_id,
        payload=payload,
    )

    EventRepository(session).record_event(event)
    session.flush()

    if event_sink is not None:
        event_sink.emit(event)


calculate_budget_usage = compute_budget_usage
