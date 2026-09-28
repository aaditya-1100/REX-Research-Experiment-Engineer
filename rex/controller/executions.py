"""REX Execution, Result, and Artifact Controller and Provenance Management (REX-008).

Provides deterministic operations for creating executions, transitioning execution statuses,
recording empirical machine-generated results, tracking filesystem artifacts with cryptographic
hashes, enforcing actor permissions, and recording structured audit events.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    InvalidExecutionStateTransitionError,
    MissingExecutionError,
    MissingExperimentError,
    MissingResearchRunError,
    StateMachineError,
)
from rex.controller.experiments import update_experiment_status
from rex.domain.models import (
    EXECUTION_ARTIFACT_TYPES,
    TERMINAL_EXECUTION_STATUSES,
    Artifact,
    ArtifactType,
    Execution,
    ExecutionStatus,
    ExperimentStatus,
    Result,
)
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.database import get_db_session
from rex.persistence.models import ExecutionModel, ExperimentModel, ResearchRunModel
from rex.persistence.repositories import (
    ArtifactRepository,
    EventRepository,
    ExecutionRepository,
    ResultRepository,
)

# Actors permitted to launch/queue executions (03_Security_Access.md §3)
EXECUTION_CREATOR_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
        ActorType.EXECUTION_WORKER,
    }
)

# Actors permitted to report execution status updates
EXECUTION_STATUS_UPDATER_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
        ActorType.EXECUTION_WORKER,
    }
)

# Actors permitted to record authoritative empirical results (03_Security_Access.md §3)
# Note: RESEARCH_AGENT is intentionally excluded to prevent LLM hallucination of experimental results.
RESULT_CREATOR_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.EXECUTION_WORKER,
    }
)

# Actors permitted to register execution artifacts
ARTIFACT_CREATOR_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.EXECUTION_WORKER,
    }
)

# Deterministic execution lifecycle transition table
VALID_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.PENDING: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }
    ),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
}


def create_execution(
    session: Session,
    experiment_id: str,
    status: ExecutionStatus | str = ExecutionStatus.PENDING,
    command: str = "",
    git_commit: str = "",
    code_hash: str = "",
    dataset_hash: str = "",
    configuration_hash: str = "",
    seed: int | None = None,
    environment: Mapping[str, Any] | None = None,
    resource_usage: Mapping[str, Any] | None = None,
    exit_code: int | None = None,
    stdout_artifact_id: str | None = None,
    stderr_artifact_id: str | None = None,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    execution_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Execution:
    """Validate, persist, and record a concrete execution attempt within an active transaction.

    Enforces parent experiment existence, actor capability authorization, and atomic audit event emission.
    """
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in EXECUTION_CREATOR_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="EXECUTE",
            target_state="create_execution",
        )

    # 2. Enforce parent experiment existence and active status
    exp_model = session.get(ExperimentModel, experiment_id)
    if exp_model is None:
        raise MissingExperimentError(experiment_id)

    if exp_model.status in (
        ExperimentStatus.COMPLETED.value,
        ExperimentStatus.FAILED.value,
        ExperimentStatus.CANCELLED.value,
    ):
        raise StateMachineError(
            f"Cannot create execution for experiment '{experiment_id}' in terminal status '{exp_model.status}'."
        )

    status_enum = status if isinstance(status, ExecutionStatus) else ExecutionStatus(status)

    init_kwargs: dict[str, Any] = {
        "experiment_id": experiment_id,
        "status": status_enum,
        "command": command,
        "git_commit": git_commit,
        "code_hash": code_hash,
        "dataset_hash": dataset_hash,
        "configuration_hash": configuration_hash,
        "seed": seed,
        "environment": environment or {},
        "resource_usage": resource_usage or {},
        "exit_code": exit_code,
        "stdout_artifact_id": stdout_artifact_id,
        "stderr_artifact_id": stderr_artifact_id,
    }
    if execution_id is not None:
        init_kwargs["id"] = execution_id

    if status_enum == ExecutionStatus.RUNNING:
        init_kwargs["started_at"] = datetime.now(UTC)

    domain_execution = Execution(**init_kwargs)

    # 3. Persist to database
    repo = ExecutionRepository(session)
    repo.create(domain_execution.to_persistence())

    # 4. Synchronize parent experiment status with execution lifecycle
    if (
        status_enum == ExecutionStatus.PENDING
        and exp_model.status == ExperimentStatus.DESIGNED.value
    ):
        update_experiment_status(
            session=session,
            experiment_id=experiment_id,
            new_status=ExperimentStatus.PENDING,
            actor=actor_enum,
            reason=f"Execution '{domain_execution.id}' scheduled and pending.",
            event_sink=event_sink,
        )
    elif status_enum == ExecutionStatus.RUNNING and exp_model.status in (
        ExperimentStatus.DESIGNED.value,
        ExperimentStatus.PENDING.value,
    ):
        update_experiment_status(
            session=session,
            experiment_id=experiment_id,
            new_status=ExperimentStatus.RUNNING,
            actor=actor_enum,
            reason=f"Execution '{domain_execution.id}' launched in RUNNING status.",
            event_sink=event_sink,
        )

    # 5. Record structured audit event
    event_type = (
        EventType.EXECUTION_STARTED
        if status_enum == ExecutionStatus.RUNNING
        else EventType.EXECUTION_CREATED
    )

    event_payload: dict[str, Any] = {
        "execution_id": domain_execution.id,
        "experiment_id": domain_execution.experiment_id,
        "research_run_id": exp_model.research_run_id,
        "status": domain_execution.status.value,
        "command": domain_execution.command,
        "seed": domain_execution.seed,
        "code_hash": domain_execution.code_hash,
    }
    if context:
        event_payload.update(context)

    creation_event = create_event(
        event_type=event_type,
        actor=actor_enum,
        research_run_id=exp_model.research_run_id,
        experiment_id=domain_execution.experiment_id,
        execution_id=domain_execution.id,
        payload=event_payload,
    )

    EventRepository(session).record_event(creation_event)
    session.flush()

    # 6. Dispatch to sink if provided
    if event_sink is not None:
        event_sink.emit(creation_event)

    return domain_execution


def update_execution_status(
    session: Session,
    execution_id: str,
    new_status: ExecutionStatus | str,
    exit_code: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    resource_usage: Mapping[str, Any] | None = None,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    reason: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Execution:
    """Transition the lifecycle status of an execution with deterministic validation and audit event recording."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in EXECUTION_STATUS_UPDATER_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="EXECUTION_STATUS",
            target_state=f"update_status:{new_status}",
        )

    # 2. Fetch execution model
    repo = ExecutionRepository(session)
    model = repo.get_by_id(execution_id)
    if model is None:
        raise MissingExecutionError(execution_id)

    current_status = ExecutionStatus(model.status)
    target_status = (
        new_status if isinstance(new_status, ExecutionStatus) else ExecutionStatus(new_status)
    )

    # 3. Enforce deterministic state transitions
    if current_status in TERMINAL_EXECUTION_STATUSES:
        raise InvalidExecutionStateTransitionError(
            execution_id=execution_id,
            current_status=current_status.value,
            target_status=target_status.value,
            message=(
                f"Cannot transition execution '{execution_id}' from terminal status "
                f"'{current_status.value}' to '{target_status.value}'."
            ),
        )

    allowed_targets = VALID_EXECUTION_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed_targets:
        raise InvalidExecutionStateTransitionError(
            execution_id=execution_id,
            current_status=current_status.value,
            target_status=target_status.value,
        )

    # 4. Update status and timestamps in model
    model.status = target_status.value
    if exit_code is not None:
        model.exit_code = exit_code

    if started_at is not None:
        model.started_at = started_at
    elif target_status == ExecutionStatus.RUNNING and model.started_at is None:
        model.started_at = datetime.now(UTC)

    if finished_at is not None:
        model.finished_at = finished_at
    elif target_status in TERMINAL_EXECUTION_STATUSES and model.finished_at is None:
        model.finished_at = datetime.now(UTC)

    if resource_usage is not None:
        model.resource_usage_json = dict(resource_usage)

    # 5. Record structured audit event
    if target_status == ExecutionStatus.RUNNING:
        event_type = EventType.EXECUTION_STARTED
    elif target_status == ExecutionStatus.COMPLETED:
        event_type = EventType.EXECUTION_COMPLETED
    elif target_status == ExecutionStatus.FAILED:
        event_type = EventType.EXECUTION_FAILED
    elif target_status == ExecutionStatus.CANCELLED:
        event_type = EventType.EXECUTION_CANCELLED
    else:
        event_type = EventType.EXECUTION_FAILED

    exp_model = model.experiment
    run_id = exp_model.research_run_id if exp_model else "unknown"

    event_payload: dict[str, Any] = {
        "execution_id": execution_id,
        "experiment_id": model.experiment_id,
        "research_run_id": run_id,
        "previous_status": current_status.value,
        "new_status": target_status.value,
        "exit_code": model.exit_code,
        "reason": reason or "",
    }
    if context:
        event_payload.update(context)

    transition_event = create_event(
        event_type=event_type,
        actor=actor_enum,
        research_run_id=run_id,
        experiment_id=model.experiment_id,
        execution_id=execution_id,
        payload=event_payload,
    )

    EventRepository(session).record_event(transition_event)
    session.flush()

    # 6. Dispatch to sink if provided
    if event_sink is not None:
        event_sink.emit(transition_event)

    # 7. Synchronize parent experiment status with execution lifecycle
    parent_exp = session.get(ExperimentModel, model.experiment_id)
    if parent_exp is not None:
        if target_status == ExecutionStatus.RUNNING:
            if parent_exp.status in (
                ExperimentStatus.DESIGNED.value,
                ExperimentStatus.PENDING.value,
            ):
                update_experiment_status(
                    session=session,
                    experiment_id=parent_exp.id,
                    new_status=ExperimentStatus.RUNNING,
                    actor=actor_enum,
                    reason=f"Execution '{execution_id}' entered RUNNING status.",
                    event_sink=event_sink,
                )
        elif target_status in TERMINAL_EXECUTION_STATUSES:
            all_execs = repo.list_by_experiment(model.experiment_id)
            all_terminal = all(
                ExecutionStatus(e.status) in TERMINAL_EXECUTION_STATUSES for e in all_execs
            )
            if all_terminal and parent_exp.status == ExperimentStatus.RUNNING.value:
                any_completed = any(e.status == ExecutionStatus.COMPLETED.value for e in all_execs)
                all_cancelled = all(e.status == ExecutionStatus.CANCELLED.value for e in all_execs)
                if any_completed:
                    target_exp_status = ExperimentStatus.COMPLETED
                    term_reason = (
                        "All executions finished; at least one execution completed successfully."
                    )
                elif all_cancelled:
                    target_exp_status = ExperimentStatus.CANCELLED
                    term_reason = "All executions cancelled."
                else:
                    target_exp_status = ExperimentStatus.FAILED
                    term_reason = "All executions finished with failure."

                update_experiment_status(
                    session=session,
                    experiment_id=parent_exp.id,
                    new_status=target_exp_status,
                    actor=actor_enum,
                    reason=term_reason,
                    event_sink=event_sink,
                )

    return Execution.from_persistence(model)


def record_result(
    session: Session,
    execution_id: str,
    metric_name: str,
    metric_value: float | None = None,
    metric_unit: str = "",
    result_data: Mapping[str, Any] | None = None,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    result_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Result:
    """Record an empirical, machine-generated metric observation tied to an execution."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization (guards against LLM fabrication of results)
    if actor_enum not in RESULT_CREATOR_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="RECORD_RESULT",
            target_state="create_result",
        )

    # 2. Enforce parent execution existence
    exec_model = session.get(ExecutionModel, execution_id)
    if exec_model is None:
        raise MissingExecutionError(execution_id)

    if metric_value is not None and not math.isfinite(metric_value):
        raise ValueError(f"Metric value must be a finite number, got {metric_value}.")

    init_kwargs: dict[str, Any] = {
        "execution_id": execution_id,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "metric_unit": metric_unit,
        "result_data": result_data or {},
    }
    if result_id is not None:
        init_kwargs["id"] = result_id

    domain_result = Result(**init_kwargs)

    # 3. Persist to database
    repo = ResultRepository(session)
    repo.create(domain_result.to_persistence())

    # 4. Record audit event
    exp_model = exec_model.experiment
    run_id = exp_model.research_run_id if exp_model else "unknown"

    event_payload: dict[str, Any] = {
        "result_id": domain_result.id,
        "execution_id": domain_result.execution_id,
        "experiment_id": exec_model.experiment_id,
        "research_run_id": run_id,
        "metric_name": domain_result.metric_name,
        "metric_value": domain_result.metric_value,
        "metric_unit": domain_result.metric_unit,
    }
    if context:
        event_payload.update(context)

    result_event = create_event(
        event_type=EventType.RESULT_RECORDED,
        actor=actor_enum,
        research_run_id=run_id,
        experiment_id=exec_model.experiment_id,
        execution_id=domain_result.execution_id,
        payload=event_payload,
    )

    EventRepository(session).record_event(result_event)
    session.flush()

    if event_sink is not None:
        event_sink.emit(result_event)

    return domain_result


def record_results_batch(
    session: Session,
    execution_id: str,
    results: Sequence[dict[str, Any]],
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    event_sink: EventSink | None = None,
) -> list[Result]:
    """Atomically record multiple empirical metric results for an execution."""
    recorded: list[Result] = []
    for item in results:
        res = record_result(
            session=session,
            execution_id=execution_id,
            metric_name=item["metric_name"],
            metric_value=item.get("metric_value"),
            metric_unit=item.get("metric_unit", ""),
            result_data=item.get("result_data"),
            actor=actor,
            result_id=item.get("id"),
            event_sink=event_sink,
        )
        recorded.append(res)
    return recorded


def record_artifact(
    session: Session,
    artifact_type: ArtifactType | str,
    path: str,
    content_hash: str,
    size_bytes: int = 0,
    execution_id: str | None = None,
    research_run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    artifact_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Artifact:
    """Register a persisted execution or run-level filesystem artifact with cryptographic hash verification."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in ARTIFACT_CREATOR_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="RECORD_ARTIFACT",
            target_state="create_artifact",
        )

    # 2. Coerce artifact type and enforce execution provenance
    type_enum = (
        artifact_type if isinstance(artifact_type, ArtifactType) else ArtifactType(artifact_type)
    )

    if type_enum in EXECUTION_ARTIFACT_TYPES and execution_id is None:
        raise ValueError(
            f"Artifacts of type '{type_enum.value}' represent execution outputs and must be "
            f"linked to an execution (execution_id cannot be None)."
        )

    # 3. Enforce referential integrity and lineage consistency:
    # artifact.execution_id -> Execution -> Experiment -> ResearchRun
    target_run_id: str
    target_exp_id: str | None = None

    if execution_id is not None:
        exec_model = session.get(ExecutionModel, execution_id)
        if exec_model is None:
            raise MissingExecutionError(execution_id)

        exp_model = session.get(ExperimentModel, exec_model.experiment_id)
        if exp_model is None:
            raise MissingExperimentError(exec_model.experiment_id)

        run_model = session.get(ResearchRunModel, exp_model.research_run_id)
        if run_model is None:
            raise MissingResearchRunError(exp_model.research_run_id)

        target_exp_id = exp_model.id
        derived_run_id = exp_model.research_run_id
        if research_run_id is not None and research_run_id != derived_run_id:
            raise StateMachineError(
                f"Referenced research run '{research_run_id}' does not match execution run '{derived_run_id}'."
            )
        target_run_id = derived_run_id
    else:
        if research_run_id is None:
            raise ValueError(
                "Either execution_id or research_run_id must be provided for artifact."
            )
        run_model = session.get(ResearchRunModel, research_run_id)
        if run_model is None:
            raise MissingResearchRunError(research_run_id)
        target_run_id = research_run_id

    # 4. Construct domain entity
    init_kwargs: dict[str, Any] = {
        "research_run_id": target_run_id,
        "execution_id": execution_id,
        "artifact_type": type_enum,
        "path": path,
        "content_hash": content_hash,
        "size_bytes": size_bytes,
        "metadata": metadata or {},
    }
    if artifact_id is not None:
        init_kwargs["id"] = artifact_id

    domain_artifact = Artifact(**init_kwargs)

    # 4. Persist to database
    repo = ArtifactRepository(session)
    repo.create(domain_artifact.to_persistence())

    # 5. Record audit event
    event_payload: dict[str, Any] = {
        "artifact_id": domain_artifact.id,
        "artifact_type": domain_artifact.artifact_type.value,
        "path": domain_artifact.path,
        "content_hash": domain_artifact.content_hash,
        "size_bytes": domain_artifact.size_bytes,
        "execution_id": domain_artifact.execution_id,
        "experiment_id": target_exp_id,
        "research_run_id": domain_artifact.research_run_id,
    }
    if context:
        event_payload.update(context)

    artifact_event = create_event(
        event_type=EventType.ARTIFACT_CREATED,
        actor=actor_enum,
        research_run_id=target_run_id,
        experiment_id=target_exp_id,
        execution_id=execution_id,
        payload=event_payload,
    )

    EventRepository(session).record_event(artifact_event)
    session.flush()

    if event_sink is not None:
        event_sink.emit(artifact_event)

    return domain_artifact


def create_execution_run(
    session_factory: sessionmaker[Session],
    experiment_id: str,
    status: ExecutionStatus | str = ExecutionStatus.PENDING,
    command: str = "",
    git_commit: str = "",
    code_hash: str = "",
    dataset_hash: str = "",
    configuration_hash: str = "",
    seed: int | None = None,
    environment: Mapping[str, Any] | None = None,
    resource_usage: Mapping[str, Any] | None = None,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    execution_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Execution:
    """Convenience helper to create and persist an execution within a managed session."""
    with get_db_session(session_factory) as session:
        return create_execution(
            session=session,
            experiment_id=experiment_id,
            status=status,
            command=command,
            git_commit=git_commit,
            code_hash=code_hash,
            dataset_hash=dataset_hash,
            configuration_hash=configuration_hash,
            seed=seed,
            environment=environment,
            resource_usage=resource_usage,
            actor=actor,
            execution_id=execution_id,
            event_sink=event_sink,
            context=context,
        )
