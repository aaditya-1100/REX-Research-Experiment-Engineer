"""REX Research State Machine and Controller Lifecycle Guardrails (REX-005).

Enforces deterministic finite state machine (FSM) transitions, actor capability checks,
optimistic concurrency verification, and atomic event recording for research investigations.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    InvalidTransitionError,
    MissingResearchRunError,
    StaleStateError,
    TerminalStateError,
)
from rex.domain.models import TERMINAL_STATES, ResearchRun, ResearchState
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.database import get_db_session
from rex.persistence.models import ResearchRunModel
from rex.persistence.repositories import EventRepository

# Authoritative deterministic research lifecycle transitions table
LEGAL_TRANSITIONS: Mapping[ResearchState, frozenset[ResearchState]] = MappingProxyType(
    {
        ResearchState.INITIALIZE: frozenset(
            {
                ResearchState.UNDERSTAND,
                ResearchState.FAILED,
            }
        ),
        ResearchState.UNDERSTAND: frozenset(
            {
                ResearchState.LITERATURE,
                ResearchState.FAILED,
            }
        ),
        ResearchState.LITERATURE: frozenset(
            {
                ResearchState.HYPOTHESES,
                ResearchState.FAILED,
            }
        ),
        ResearchState.HYPOTHESES: frozenset(
            {
                ResearchState.DESIGN,
                ResearchState.FAILED,
            }
        ),
        ResearchState.DESIGN: frozenset(
            {
                ResearchState.IMPLEMENT,
                ResearchState.FAILED,
            }
        ),
        ResearchState.IMPLEMENT: frozenset(
            {
                ResearchState.EXECUTE,
                ResearchState.FAILED,
            }
        ),
        ResearchState.EXECUTE: frozenset(
            {
                ResearchState.VERIFY,
                ResearchState.FAILED,
            }
        ),
        ResearchState.VERIFY: frozenset(
            {
                ResearchState.ANALYZE,  # Execution verified intact
                ResearchState.IMPLEMENT,  # Retry/code fix on execution failure
                ResearchState.FAILED,  # Unrecoverable execution/verification failure
            }
        ),
        ResearchState.ANALYZE: frozenset(
            {
                ResearchState.CRITIQUE,
                ResearchState.FAILED,
            }
        ),
        ResearchState.CRITIQUE: frozenset(
            {
                ResearchState.DECIDE,
                ResearchState.FAILED,
            }
        ),
        ResearchState.DECIDE: frozenset(
            {
                ResearchState.REFINE,  # Branch: refine experiment
                ResearchState.REPLICATE,  # Branch: replicate result
                ResearchState.PIVOT,  # Branch: pivot hypothesis
                ResearchState.STOP,  # Branch: terminate research
                ResearchState.DESIGN,  # Direct loop: refine/replicate
                ResearchState.HYPOTHESES,  # Direct loop: pivot
                ResearchState.COMPLETE,  # Direct loop: stop/completed
                ResearchState.FAILED,  # Flaw or budget exhaustion
            }
        ),
        ResearchState.REFINE: frozenset(
            {
                ResearchState.DESIGN,
                ResearchState.FAILED,
            }
        ),
        ResearchState.REPLICATE: frozenset(
            {
                ResearchState.DESIGN,
                ResearchState.FAILED,
            }
        ),
        ResearchState.PIVOT: frozenset(
            {
                ResearchState.HYPOTHESES,
                ResearchState.UNDERSTAND,
                ResearchState.FAILED,
            }
        ),
        ResearchState.STOP: frozenset(),  # Terminal state
        ResearchState.COMPLETE: frozenset(),  # Terminal state
        ResearchState.FAILED: frozenset(),  # Terminal state
    }
)


# Specific actor capabilities per transition edge
# Owner and System possess administrative authority over all legal transitions
REASONING_PLANE_TRANSITIONS: frozenset[tuple[ResearchState, ResearchState]] = frozenset(
    {
        (ResearchState.INITIALIZE, ResearchState.UNDERSTAND),
        (ResearchState.UNDERSTAND, ResearchState.LITERATURE),
        (ResearchState.LITERATURE, ResearchState.HYPOTHESES),
        (ResearchState.HYPOTHESES, ResearchState.DESIGN),
        (ResearchState.DESIGN, ResearchState.IMPLEMENT),
        (ResearchState.ANALYZE, ResearchState.CRITIQUE),
        (ResearchState.CRITIQUE, ResearchState.DECIDE),
        (ResearchState.DECIDE, ResearchState.REFINE),
        (ResearchState.DECIDE, ResearchState.REPLICATE),
        (ResearchState.DECIDE, ResearchState.PIVOT),
        (ResearchState.DECIDE, ResearchState.STOP),
        (ResearchState.DECIDE, ResearchState.DESIGN),
        (ResearchState.DECIDE, ResearchState.HYPOTHESES),
        (ResearchState.DECIDE, ResearchState.COMPLETE),
        (ResearchState.DECIDE, ResearchState.FAILED),
        (ResearchState.REFINE, ResearchState.DESIGN),
        (ResearchState.REPLICATE, ResearchState.DESIGN),
        (ResearchState.PIVOT, ResearchState.HYPOTHESES),
        (ResearchState.PIVOT, ResearchState.UNDERSTAND),
    }
)

EXECUTION_WORKER_TRANSITIONS: frozenset[tuple[ResearchState, ResearchState]] = frozenset(
    {
        (ResearchState.IMPLEMENT, ResearchState.EXECUTE),
        (ResearchState.EXECUTE, ResearchState.VERIFY),
        (ResearchState.EXECUTE, ResearchState.FAILED),
    }
)

VERIFIER_TRANSITIONS: frozenset[tuple[ResearchState, ResearchState]] = frozenset(
    {
        (ResearchState.VERIFY, ResearchState.ANALYZE),
        (ResearchState.VERIFY, ResearchState.IMPLEMENT),
        (ResearchState.VERIFY, ResearchState.FAILED),
    }
)


class StateTransitionResult(BaseModel):
    """Immutable result of a validated research lifecycle state transition."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    research_run_id: str = Field(description="ID of the transitioned research run")
    previous_state: ResearchState = Field(description="State prior to transition")
    new_state: ResearchState = Field(description="State after transition")
    timestamp: datetime = Field(description="UTC timestamp of the transition")
    actor: ActorType = Field(description="Actor that authorized the transition")
    event_id: str = Field(description="Audit event ID recorded for the transition")
    version: int = Field(description="Post-transition monotonic version number")
    reason: str | None = Field(default=None, description="Optional transition rationale")


class ResearchStateMachine:
    """Deterministic finite state machine controlling REX research lifecycle."""

    @staticmethod
    def is_actor_authorized(
        actor: ActorType,
        current_state: ResearchState,
        target_state: ResearchState,
    ) -> bool:
        """Check whether the actor has domain permission for the given transition edge."""
        if actor in (ActorType.OWNER, ActorType.SYSTEM):
            return True

        edge = (current_state, target_state)
        if actor == ActorType.RESEARCH_AGENT:
            return edge in REASONING_PLANE_TRANSITIONS
        if actor == ActorType.EXECUTION_WORKER:
            return edge in EXECUTION_WORKER_TRANSITIONS
        if actor == ActorType.VERIFIER:
            return edge in VERIFIER_TRANSITIONS

        return False

    @classmethod
    def can_transition(
        cls,
        current_state: ResearchState,
        target_state: ResearchState,
        actor: ActorType = ActorType.SYSTEM,
    ) -> bool:
        """Predicate checking whether a transition is legal and authorized."""
        if current_state in TERMINAL_STATES:
            return False
        if current_state == target_state:
            return False
        legal_targets = LEGAL_TRANSITIONS.get(current_state, frozenset())
        if target_state not in legal_targets:
            return False
        return cls.is_actor_authorized(actor, current_state, target_state)

    @classmethod
    def validate_transition(
        cls,
        current_state: ResearchState,
        target_state: ResearchState,
        actor: ActorType = ActorType.SYSTEM,
        run_id: str | None = None,
    ) -> None:
        """Validate a proposed transition, raising explicit domain exceptions on violation."""
        # 1. Terminal states cannot transition
        if current_state in TERMINAL_STATES:
            raise TerminalStateError(
                current_state=current_state.value,
                target_state=target_state.value,
                run_id=run_id,
            )

        # 2. Redundant transitions are rejected (idempotency rule)
        if current_state == target_state:
            raise InvalidTransitionError(
                current_state=current_state.value,
                target_state=target_state.value,
                run_id=run_id,
                message=(
                    f"Cannot transition run '{run_id or 'unknown'}' to its current state "
                    f"'{current_state.value}'. Redundant transitions are rejected."
                ),
            )

        # 3. Check legal transition table
        legal_targets = LEGAL_TRANSITIONS.get(current_state, frozenset())
        if target_state not in legal_targets:
            raise InvalidTransitionError(
                current_state=current_state.value,
                target_state=target_state.value,
                run_id=run_id,
            )

        # 4. Check actor authorization
        if not cls.is_actor_authorized(actor, current_state, target_state):
            raise ActorAuthorizationError(
                actor=actor.value,
                current_state=current_state.value,
                target_state=target_state.value,
            )

    @classmethod
    def transition(
        cls,
        session: Session,
        run_id: str,
        target_state: ResearchState | str,
        actor: ActorType | str = ActorType.SYSTEM,
        expected_state: ResearchState | str | None = None,
        reason: str | None = None,
        event_sink: EventSink | None = None,
        context: dict[str, Any] | None = None,
    ) -> StateTransitionResult:
        """Execute an atomic lifecycle transition within an active database transaction.

        Validates the transition, checks concurrency constraints, updates the run model,
        and records a persistent research_state_changed audit event.
        """
        target_state_enum = (
            target_state if isinstance(target_state, ResearchState) else ResearchState(target_state)
        )
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

        # 1. Fetch research run from database
        run_model = session.get(ResearchRunModel, run_id)
        if run_model is None:
            raise MissingResearchRunError(run_id)

        current_state_enum = ResearchState(run_model.status)

        # 2. Optimistic concurrency check: verify expected_state if supplied
        if expected_state is not None:
            expected_enum = (
                expected_state
                if isinstance(expected_state, ResearchState)
                else ResearchState(expected_state)
            )
            if current_state_enum != expected_enum:
                raise StaleStateError(
                    run_id=run_id,
                    expected_state=expected_enum.value,
                    actual_state=current_state_enum.value,
                )

        # 3. Validate transition rules and actor capabilities
        cls.validate_transition(
            current_state=current_state_enum,
            target_state=target_state_enum,
            actor=actor_enum,
            run_id=run_id,
        )

        # 4. Compute updated timestamp and version
        now = datetime.now(UTC)
        config_data = dict(run_model.configuration_json or {})
        current_version = config_data.get("_version", 1)
        new_version = current_version + 1
        config_data["_version"] = new_version

        # 5. Mutate persistence model
        run_model.status = target_state_enum.value
        run_model.updated_at = now
        run_model.configuration_json = config_data

        # 6. Create immutable structured research lifecycle event
        event_payload: dict[str, Any] = {
            "previous_state": current_state_enum.value,
            "new_state": target_state_enum.value,
            "version": new_version,
        }
        if reason:
            event_payload["reason"] = reason
        if context:
            event_payload.update(context)

        event = create_event(
            event_type=EventType.RESEARCH_STATE_CHANGED,
            actor=actor_enum,
            research_run_id=run_id,
            timestamp=now,
            payload=event_payload,
        )

        # 7. Persist event atomically in same database transaction
        event_repo = EventRepository(session)
        event_repo.record_event(event)
        session.flush()

        # 8. Dispatch event to optional event sink (e.g. logging sink)
        if event_sink is not None:
            event_sink.emit(event)

        return StateTransitionResult(
            research_run_id=run_id,
            previous_state=current_state_enum,
            new_state=target_state_enum,
            timestamp=now,
            actor=actor_enum,
            event_id=event.event_id,
            version=new_version,
            reason=reason,
        )


def transition_run(
    session_factory: sessionmaker[Session],
    run_id: str,
    target_state: ResearchState | str,
    actor: ActorType | str = ActorType.SYSTEM,
    expected_state: ResearchState | str | None = None,
    reason: str | None = None,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> StateTransitionResult:
    """Convenience helper to execute a validated state transition with managed session boundaries."""
    with get_db_session(session_factory) as session:
        return ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=target_state,
            actor=actor,
            expected_state=expected_state,
            reason=reason,
            event_sink=event_sink,
            context=context,
        )


def create_research_run(
    session: Session,
    research_question: str,
    title: str = "",
    configuration: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    actor: ActorType | str = ActorType.OWNER,
    event_sink: EventSink | None = None,
) -> ResearchRun:
    """Create and persist a new ResearchRun in INITIALIZE state, recording its creation event."""
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    domain_run = ResearchRun(
        title=title,
        research_question=research_question,
        configuration=MappingProxyType(configuration or {}),
        budget=MappingProxyType(budget or {}),
    )

    model = domain_run.to_persistence()
    session.add(model)
    session.flush()

    # Record research_created lifecycle event
    creation_event = create_event(
        event_type=EventType.RESEARCH_CREATED,
        actor=actor_enum,
        research_run_id=domain_run.id,
        payload={
            "title": domain_run.title,
            "research_question": domain_run.research_question,
            "state": domain_run.state.value,
        },
    )
    EventRepository(session).record_event(creation_event)
    session.flush()

    if event_sink is not None:
        event_sink.emit(creation_event)

    return domain_run
