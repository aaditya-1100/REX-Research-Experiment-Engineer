"""Unit and integration tests for state machine persistence, transaction boundaries, and event emission (REX-005)."""

import pytest
from sqlalchemy.orm import sessionmaker

from rex.controller.exceptions import (
    InvalidTransitionError,
    MissingResearchRunError,
    StaleStateError,
    TerminalStateError,
)
from rex.controller.state_machine import (
    ResearchStateMachine,
    create_research_run,
    transition_run,
)
from rex.domain.models import ResearchRun, ResearchState
from rex.observability.events import (
    ActorType,
    EventType,
    InMemoryEventSink,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import ResearchRunModel
from rex.persistence.repositories import EventRepository, ResearchRunRepository


@pytest.fixture
def session_factory(tmp_path):
    """Provide an isolated, file-based SQLite database with session factory."""
    db_file = tmp_path / "test_state_machine.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_create_research_run_persists_model_and_event(session_factory: sessionmaker):
    """Verify create_research_run persists ResearchRunModel and emits research_created event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        domain_run = create_research_run(
            session=session,
            research_question="Does learning rate warmup prevent early divergence?",
            title="Warmup Study",
            configuration={"warmup_steps": 500},
            budget={"max_hours": 1.0},
            actor=ActorType.OWNER,
            event_sink=sink,
        )
        run_id = domain_run.id

    assert run_id.startswith("run_")
    assert domain_run.state == ResearchState.INITIALIZE

    # Check event sink
    assert len(sink) == 1
    created_event = sink.events[0]
    assert created_event.event_type == EventType.RESEARCH_CREATED
    assert created_event.actor == ActorType.OWNER
    assert created_event.research_run_id == run_id
    assert created_event.payload["state"] == "INITIALIZE"

    # Check database persistence
    with get_db_session(session_factory) as session:
        repo = ResearchRunRepository(session)
        stored_model = repo.get_by_id(run_id)
        assert stored_model is not None
        assert stored_model.status == "INITIALIZE"
        assert stored_model.title == "Warmup Study"

        event_repo = EventRepository(session)
        events = event_repo.list_by_run(run_id)
        assert len(events) == 1
        assert events[0].event_type == "research_created"


def test_transition_run_atomic_update_and_event(session_factory: sessionmaker):
    """Verify transition_run updates model, version, updated_at, and emits research_state_changed event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Batch size scaling effect on generalisation",
            event_sink=sink,
        )
        run_id = run.id

    # Clear creation event from sink to test transition event isolation
    sink.clear()

    # Perform transition INITIALIZE -> UNDERSTAND
    result = transition_run(
        session_factory=session_factory,
        run_id=run_id,
        target_state=ResearchState.UNDERSTAND,
        actor=ActorType.RESEARCH_AGENT,
        reason="Research question parsed and confirmed",
        event_sink=sink,
    )

    assert result.research_run_id == run_id
    assert result.previous_state == ResearchState.INITIALIZE
    assert result.new_state == ResearchState.UNDERSTAND
    assert result.actor == ActorType.RESEARCH_AGENT
    assert result.version == 2
    assert result.reason == "Research question parsed and confirmed"

    # Verify event sink captured exactly 1 event
    assert len(sink) == 1
    event = sink.events[0]
    assert event.event_type == EventType.RESEARCH_STATE_CHANGED
    assert event.actor == ActorType.RESEARCH_AGENT
    assert event.research_run_id == run_id
    assert event.payload["previous_state"] == "INITIALIZE"
    assert event.payload["new_state"] == "UNDERSTAND"
    assert event.payload["version"] == 2
    assert event.payload["reason"] == "Research question parsed and confirmed"

    # Verify DB persistence of run and event
    with get_db_session(session_factory) as session:
        model = session.get(ResearchRunModel, run_id)
        assert model is not None
        assert model.status == "UNDERSTAND"
        assert model.configuration_json["_version"] == 2

        domain = ResearchRun.from_persistence(model)
        assert domain.state == ResearchState.UNDERSTAND
        assert domain.version == 2

        events = EventRepository(session).list_by_run(run_id)
        assert len(events) == 2  # creation + state changed


def test_optimistic_concurrency_stale_state_error(session_factory: sessionmaker):
    """Verify that expected_state mismatch raises StaleStateError and leaves DB untouched."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Concurrency test")
        run_id = run.id

    # Advance run to UNDERSTAND
    transition_run(
        session_factory=session_factory,
        run_id=run_id,
        target_state=ResearchState.UNDERSTAND,
        actor=ActorType.SYSTEM,
    )

    # Caller attempts to transition with stale expected_state=INITIALIZE
    with pytest.raises(StaleStateError) as exc_info:
        transition_run(
            session_factory=session_factory,
            run_id=run_id,
            target_state=ResearchState.LITERATURE,
            expected_state=ResearchState.INITIALIZE,  # Stale!
            actor=ActorType.RESEARCH_AGENT,
        )

    assert exc_info.value.run_id == run_id
    assert exc_info.value.expected_state == "INITIALIZE"
    assert exc_info.value.actual_state == "UNDERSTAND"

    # Confirm DB remains at UNDERSTAND
    with get_db_session(session_factory) as session:
        model = session.get(ResearchRunModel, run_id)
        assert model.status == "UNDERSTAND"


def test_missing_research_run_error(session_factory: sessionmaker):
    """Verify that transitioning a non-existent run raises MissingResearchRunError."""
    with pytest.raises(MissingResearchRunError) as exc_info:
        transition_run(
            session_factory=session_factory,
            run_id="non_existent_run_999",
            target_state=ResearchState.UNDERSTAND,
        )
    assert exc_info.value.run_id == "non_existent_run_999"


def test_invalid_transition_rolls_back_transaction(session_factory: sessionmaker):
    """Verify that an illegal transition inside a session rolls back without mutating DB or events."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Rollback test",
            event_sink=sink,
        )
        run_id = run.id

    sink.clear()

    # Attempt illegal transition: INITIALIZE -> EXECUTE
    with pytest.raises(InvalidTransitionError), get_db_session(session_factory) as session:
        ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=ResearchState.EXECUTE,
            actor=ActorType.OWNER,
            event_sink=sink,
        )

    # Ensure zero events emitted to sink
    assert len(sink) == 0

    # Ensure DB remained at INITIALIZE
    with get_db_session(session_factory) as session:
        model = session.get(ResearchRunModel, run_id)
        assert model.status == "INITIALIZE"
        events = EventRepository(session).list_by_run(run_id)
        assert len(events) == 1  # Only creation event exists


def test_multi_step_decide_branch_and_stop_termination(session_factory: sessionmaker):
    """Test full multi-step lifecycle with iteration loop, DECIDE -> REFINE -> DESIGN, and DECIDE -> STOP."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Iterative architecture refinement",
            event_sink=sink,
        )
        run_id = run.id

    steps = [
        (ResearchState.UNDERSTAND, ActorType.RESEARCH_AGENT),
        (ResearchState.LITERATURE, ActorType.RESEARCH_AGENT),
        (ResearchState.HYPOTHESES, ActorType.RESEARCH_AGENT),
        (ResearchState.DESIGN, ActorType.RESEARCH_AGENT),
        (ResearchState.IMPLEMENT, ActorType.RESEARCH_AGENT),
        (ResearchState.EXECUTE, ActorType.EXECUTION_WORKER),
        (ResearchState.VERIFY, ActorType.EXECUTION_WORKER),
        (ResearchState.ANALYZE, ActorType.VERIFIER),
        (ResearchState.CRITIQUE, ActorType.RESEARCH_AGENT),
        (ResearchState.DECIDE, ActorType.RESEARCH_AGENT),
        # Iteration 1: Refine
        (ResearchState.REFINE, ActorType.RESEARCH_AGENT),
        (ResearchState.DESIGN, ActorType.RESEARCH_AGENT),
        (ResearchState.IMPLEMENT, ActorType.RESEARCH_AGENT),
        (ResearchState.EXECUTE, ActorType.EXECUTION_WORKER),
        (ResearchState.VERIFY, ActorType.EXECUTION_WORKER),
        (ResearchState.ANALYZE, ActorType.VERIFIER),
        (ResearchState.CRITIQUE, ActorType.RESEARCH_AGENT),
        (ResearchState.DECIDE, ActorType.RESEARCH_AGENT),
        # Final Decision: STOP (terminal)
        (ResearchState.STOP, ActorType.RESEARCH_AGENT),
    ]

    for target_state, actor in steps:
        transition_run(
            session_factory=session_factory,
            run_id=run_id,
            target_state=target_state,
            actor=actor,
            event_sink=sink,
        )

    # Verify run is now in terminal state STOP
    with get_db_session(session_factory) as session:
        model = session.get(ResearchRunModel, run_id)
        assert model.status == "STOP"
        domain = ResearchRun.from_persistence(model)
        assert domain.is_terminal

        events = EventRepository(session).list_by_run(run_id)
        # 1 creation + len(steps) state transitions
        assert len(events) == 1 + len(steps)

    # Attempting any further transition out of STOP must fail
    with pytest.raises(TerminalStateError):
        transition_run(
            session_factory=session_factory,
            run_id=run_id,
            target_state=ResearchState.DESIGN,
            actor=ActorType.OWNER,
        )
