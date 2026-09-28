"""REX Hypothesis Controller and Lifecycle Management (REX-006).

Provides deterministic operations for creating hypotheses, updating hypothesis statuses,
enforcing actor capabilities, and recording structured audit events.
"""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    MissingHypothesisError,
    MissingResearchRunError,
)
from rex.domain.models import ExpectedDirection, Hypothesis, HypothesisStatus
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.database import get_db_session
from rex.persistence.models import ResearchRunModel
from rex.persistence.repositories import EventRepository, HypothesisRepository

# Actors permitted to formulate hypotheses (03_Security_Access.md §3)
HYPOTHESIS_CREATOR_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
    }
)

# Actors permitted to update hypothesis validation status
HYPOTHESIS_STATUS_UPDATER_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
        ActorType.VERIFIER,
    }
)


def create_hypothesis(
    session: Session,
    research_run_id: str,
    statement: str,
    falsification_condition: str,
    rationale: str = "",
    expected_direction: ExpectedDirection | str = ExpectedDirection.INCREASE,
    status: HypothesisStatus | str = HypothesisStatus.PROPOSED,
    actor: ActorType | str = ActorType.RESEARCH_AGENT,
    hypothesis_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Hypothesis:
    """Validate, persist, and record a structured scientific hypothesis within an active transaction.

    Enforces research run existence, actor capabilities, schema validation, and atomic audit event emission.
    """
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in HYPOTHESIS_CREATOR_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="HYPOTHESES",
            target_state="create_hypothesis",
        )

    # 2. Enforce research run existence
    run_model = session.get(ResearchRunModel, research_run_id)
    if run_model is None:
        raise MissingResearchRunError(research_run_id)

    # 3. Construct domain entity (enforces validation)
    direction_enum = (
        expected_direction
        if isinstance(expected_direction, ExpectedDirection)
        else ExpectedDirection(expected_direction)
    )
    status_enum = status if isinstance(status, HypothesisStatus) else HypothesisStatus(status)

    init_kwargs: dict[str, Any] = {
        "research_run_id": research_run_id,
        "statement": statement,
        "falsification_condition": falsification_condition,
        "rationale": rationale,
        "expected_direction": direction_enum,
        "status": status_enum,
    }
    if hypothesis_id is not None:
        init_kwargs["id"] = hypothesis_id

    domain_hypothesis = Hypothesis(**init_kwargs)

    # 4. Persist to database
    repo = HypothesisRepository(session)
    repo.create(domain_hypothesis.to_persistence())

    # 5. Record structured audit event
    event_payload: dict[str, Any] = {
        "hypothesis_id": domain_hypothesis.id,
        "statement": domain_hypothesis.statement,
        "expected_direction": domain_hypothesis.expected_direction.value,
        "falsification_condition": domain_hypothesis.falsification_condition,
        "status": domain_hypothesis.status.value,
    }
    if context:
        event_payload.update(context)

    creation_event = create_event(
        event_type=EventType.HYPOTHESIS_CREATED,
        actor=actor_enum,
        research_run_id=research_run_id,
        timestamp=domain_hypothesis.created_at,
        payload=event_payload,
    )

    EventRepository(session).record_event(creation_event)
    session.flush()

    # 6. Dispatch to sink if provided
    if event_sink is not None:
        event_sink.emit(creation_event)

    return domain_hypothesis


def update_hypothesis_status(
    session: Session,
    hypothesis_id: str,
    status: HypothesisStatus | str,
    actor: ActorType | str = ActorType.RESEARCH_AGENT,
    reason: str | None = None,
    event_sink: EventSink | None = None,
) -> Hypothesis:
    """Update the verification status of an existing hypothesis without modifying its scientific statement."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Enforce actor authorization
    if actor_enum not in HYPOTHESIS_STATUS_UPDATER_ACTORS:
        raise ActorAuthorizationError(
            actor=actor_enum.value,
            current_state="HYPOTHESES",
            target_state=f"update_status:{status}",
        )

    # 2. Fetch hypothesis
    repo = HypothesisRepository(session)
    model = repo.get_by_id(hypothesis_id)
    if model is None:
        raise MissingHypothesisError(hypothesis_id)

    status_enum = status if isinstance(status, HypothesisStatus) else HypothesisStatus(status)

    # 3. Update status in persistence
    repo.update_status(hypothesis_id, status_enum.value)

    # 4. Return reconstructed domain object
    return Hypothesis.from_persistence(model)


def create_hypothesis_run(
    session_factory: sessionmaker[Session],
    research_run_id: str,
    statement: str,
    falsification_condition: str,
    rationale: str = "",
    expected_direction: ExpectedDirection | str = ExpectedDirection.INCREASE,
    status: HypothesisStatus | str = HypothesisStatus.PROPOSED,
    actor: ActorType | str = ActorType.RESEARCH_AGENT,
    hypothesis_id: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> Hypothesis:
    """Convenience helper to create and persist a hypothesis within a managed session."""
    with get_db_session(session_factory) as session:
        return create_hypothesis(
            session=session,
            research_run_id=research_run_id,
            statement=statement,
            falsification_condition=falsification_condition,
            rationale=rationale,
            expected_direction=expected_direction,
            status=status,
            actor=actor,
            hypothesis_id=hypothesis_id,
            event_sink=event_sink,
            context=context,
        )
