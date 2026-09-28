"""REX Experiment Controller and Specification Lifecycle Management (REX-007).

Provides deterministic operations for creating experiment specifications, transitioning
experiment statuses, enforcing actor capabilities, maintaining referential integrity,
and recording structured audit events.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    ExperimentExecutionExistsError,
    InvalidExperimentStateTransitionError,
    MissingExperimentError,
    MissingHypothesisError,
    MissingResearchRunError,
    StateMachineError,
)
from rex.domain.models import (
    TERMINAL_EXPERIMENT_STATUSES,
    Experiment,
    ExperimentSpecification,
    ExperimentStatus,
)
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.database import get_db_session
from rex.persistence.models import ExperimentModel, HypothesisModel, ResearchRunModel
from rex.persistence.repositories import (
    EventRepository,
    ExecutionRepository,
    ExperimentRepository,
)

# Actors permitted to define experiment specifications (03_Security_Access.md §3)
EXPERIMENT_CREATOR_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
    }
)

# Actors permitted to update experiment lifecycle status (03_Security_Access.md §3)
EXPERIMENT_STATUS_UPDATER_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
        ActorType.EXECUTION_WORKER,
        ActorType.VERIFIER,
    }
)

# Deterministic lifecycle state transition table
VALID_EXPERIMENT_TRANSITIONS: dict[ExperimentStatus, frozenset[ExperimentStatus]] = {
    ExperimentStatus.DESIGNED: frozenset(
        {
            ExperimentStatus.PENDING,
            ExperimentStatus.RUNNING,
            ExperimentStatus.FAILED,
            ExperimentStatus.CANCELLED,
        }
    ),
    ExperimentStatus.PENDING: frozenset(
        {
            ExperimentStatus.RUNNING,
            ExperimentStatus.FAILED,
            ExperimentStatus.CANCELLED,
        }
    ),
    ExperimentStatus.RUNNING: frozenset(
        {
            ExperimentStatus.ANALYZING,
            ExperimentStatus.COMPLETED,
            ExperimentStatus.FAILED,
            ExperimentStatus.CANCELLED,
        }
    ),
    ExperimentStatus.ANALYZING: frozenset(
        {
            ExperimentStatus.COMPLETED,
            ExperimentStatus.FAILED,
            ExperimentStatus.CANCELLED,
        }
    ),
    ExperimentStatus.COMPLETED: frozenset(),
    ExperimentStatus.FAILED: frozenset(),
    ExperimentStatus.CANCELLED: frozenset(),
}


def assert_experiment_mutable(session: Session, experiment_id: str) -> None:
    """Enforce the invariant that an experiment's specification cannot be modified once execution begins.

    Raises:
        MissingExperimentError: If the experiment does not exist.
        ExperimentExecutionExistsError: If any execution records are associated with the experiment.
    """
    exp_model = session.get(ExperimentModel, experiment_id)
    if exp_model is None:
        raise MissingExperimentError(experiment_id)

    exec_repo = ExecutionRepository(session)
    executions = exec_repo.list_by_experiment(experiment_id)
    if executions:
        raise ExperimentExecutionExistsError(experiment_id, len(executions))


def create_experiment(
    session: Session,
    research_run_id: str,
    objective: str,
    hypothesis_id: str | None = None,
    specification: ExperimentSpecification | Mapping[str, Any] | None = None,
    status: ExperimentStatus | str = ExperimentStatus.DESIGNED,
    parent_experiment_id: str | None = None,
    actor: ActorType | str = ActorType.RESEARCH_AGENT,
    experiment_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Experiment:
    """Validate, persist, and record an immutable experiment specification within an active transaction.

    Enforces parent entity referential integrity (run, hypothesis, parent experiment),
    actor capability authorization, schema validation, and atomic audit event emission.
    """
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in EXPERIMENT_CREATOR_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="DESIGN",
            target_state="create_experiment",
        )

    # 2. Enforce research run existence
    run_model = session.get(ResearchRunModel, research_run_id)
    if run_model is None:
        raise MissingResearchRunError(research_run_id)

    # 3. Enforce hypothesis referential integrity if provided
    if hypothesis_id is not None:
        hyp_model = session.get(HypothesisModel, hypothesis_id)
        if hyp_model is None:
            raise MissingHypothesisError(hypothesis_id)
        if hyp_model.research_run_id != research_run_id:
            raise StateMachineError(
                f"Referenced hypothesis '{hypothesis_id}' belongs to run '{hyp_model.research_run_id}', "
                f"not current run '{research_run_id}'."
            )

    # 4. Enforce parent experiment referential integrity if provided
    if parent_experiment_id is not None:
        parent_model = session.get(ExperimentModel, parent_experiment_id)
        if parent_model is None:
            raise MissingExperimentError(parent_experiment_id)
        if parent_model.research_run_id != research_run_id:
            raise StateMachineError(
                f"Referenced parent experiment '{parent_experiment_id}' belongs to run '{parent_model.research_run_id}', "
                f"not current run '{research_run_id}'."
            )

    # 5. Coerce specification
    if specification is None:
        spec_obj = ExperimentSpecification()
    elif isinstance(specification, ExperimentSpecification):
        spec_obj = specification
    elif isinstance(specification, Mapping):
        spec_obj = ExperimentSpecification.from_dict(specification)
    else:
        raise ValueError(
            f"Specification must be an ExperimentSpecification or mapping, got {type(specification).__name__}."
        )

    status_enum = status if isinstance(status, ExperimentStatus) else ExperimentStatus(status)

    init_kwargs: dict[str, Any] = {
        "research_run_id": research_run_id,
        "hypothesis_id": hypothesis_id,
        "objective": objective,
        "specification": spec_obj,
        "status": status_enum,
        "parent_experiment_id": parent_experiment_id,
    }
    if experiment_id is not None:
        init_kwargs["id"] = experiment_id

    domain_experiment = Experiment(**init_kwargs)

    # 6. Persist to database
    repo = ExperimentRepository(session)
    repo.create(domain_experiment.to_persistence())

    # 7. Record structured audit event
    event_payload: dict[str, Any] = {
        "experiment_id": domain_experiment.id,
        "research_run_id": domain_experiment.research_run_id,
        "hypothesis_id": domain_experiment.hypothesis_id,
        "objective": domain_experiment.objective,
        "status": domain_experiment.status.value,
        "parent_experiment_id": domain_experiment.parent_experiment_id,
        "specification": domain_experiment.specification.to_dict(),
    }
    if context:
        event_payload.update(context)

    creation_event = create_event(
        event_type=EventType.EXPERIMENT_CREATED,
        actor=actor_enum,
        research_run_id=research_run_id,
        experiment_id=domain_experiment.id,
        timestamp=domain_experiment.created_at,
        payload=event_payload,
    )

    EventRepository(session).record_event(creation_event)
    session.flush()

    # 8. Dispatch to sink if provided
    if event_sink is not None:
        event_sink.emit(creation_event)

    return domain_experiment


def update_experiment_status(
    session: Session,
    experiment_id: str,
    new_status: ExperimentStatus | str,
    actor: ActorType | str = ActorType.RESEARCH_AGENT,
    reason: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Experiment:
    """Transition the lifecycle status of an experiment with deterministic validation and audit event recording."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in EXPERIMENT_STATUS_UPDATER_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="EXPERIMENT_STATUS",
            target_state=f"update_status:{new_status}",
        )

    # 2. Fetch experiment model
    repo = ExperimentRepository(session)
    model = repo.get_by_id(experiment_id)
    if model is None:
        raise MissingExperimentError(experiment_id)

    current_status = ExperimentStatus(model.status)
    target_status = (
        new_status if isinstance(new_status, ExperimentStatus) else ExperimentStatus(new_status)
    )

    # 3. Enforce deterministic state transitions
    if current_status in TERMINAL_EXPERIMENT_STATUSES:
        raise InvalidExperimentStateTransitionError(
            experiment_id=experiment_id,
            current_status=current_status.value,
            target_status=target_status.value,
            message=(
                f"Cannot transition experiment '{experiment_id}' from terminal status "
                f"'{current_status.value}' to '{target_status.value}'."
            ),
        )

    allowed_targets = VALID_EXPERIMENT_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed_targets:
        raise InvalidExperimentStateTransitionError(
            experiment_id=experiment_id,
            current_status=current_status.value,
            target_status=target_status.value,
        )

    # 4. Update status in database
    repo.update_status(experiment_id, target_status.value)

    # 5. Record structured audit event
    event_payload: dict[str, Any] = {
        "experiment_id": experiment_id,
        "research_run_id": model.research_run_id,
        "previous_status": current_status.value,
        "new_status": target_status.value,
        "reason": reason or "",
    }
    if context:
        event_payload.update(context)

    transition_event = create_event(
        event_type=EventType.EXPERIMENT_STATUS_CHANGED,
        actor=actor_enum,
        research_run_id=model.research_run_id,
        experiment_id=experiment_id,
        payload=event_payload,
    )

    EventRepository(session).record_event(transition_event)
    session.flush()

    # 6. Dispatch to sink if provided
    if event_sink is not None:
        event_sink.emit(transition_event)

    return Experiment.from_persistence(model)


def create_experiment_run(
    session_factory: sessionmaker[Session],
    research_run_id: str,
    objective: str,
    hypothesis_id: str | None = None,
    specification: ExperimentSpecification | Mapping[str, Any] | None = None,
    status: ExperimentStatus | str = ExperimentStatus.DESIGNED,
    parent_experiment_id: str | None = None,
    actor: ActorType | str = ActorType.RESEARCH_AGENT,
    experiment_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Experiment:
    """Convenience helper to create and persist an experiment within a managed session."""
    with get_db_session(session_factory) as session:
        return create_experiment(
            session=session,
            research_run_id=research_run_id,
            objective=objective,
            hypothesis_id=hypothesis_id,
            specification=specification,
            status=status,
            parent_experiment_id=parent_experiment_id,
            actor=actor,
            experiment_id=experiment_id,
            event_sink=event_sink,
            context=context,
        )
