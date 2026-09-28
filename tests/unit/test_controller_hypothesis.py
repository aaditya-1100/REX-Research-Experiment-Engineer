"""Unit and integration tests for hypothesis controller, persistence, and audit events (REX-006)."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    MissingHypothesisError,
    MissingResearchRunError,
)
from rex.controller.hypotheses import (
    create_hypothesis,
    create_hypothesis_run,
    update_hypothesis_status,
)
from rex.controller.state_machine import create_research_run
from rex.domain.models import ExpectedDirection, HypothesisStatus
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
from rex.persistence.models import HypothesisModel
from rex.persistence.repositories import EventRepository, HypothesisRepository


@pytest.fixture
def session_factory(tmp_path):
    """Provide an isolated, file-based SQLite database with session factory."""
    db_file = tmp_path / "test_hypothesis_ctrl.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_create_hypothesis_persists_model_and_emits_event(session_factory: sessionmaker):
    """Verify create_hypothesis persists entity and emits structured hypothesis_created event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Does rotary position embedding improve extrapolation?",
        )
        run_id = run.id

    # Create hypothesis
    with get_db_session(session_factory) as session:
        hyp = create_hypothesis(
            session=session,
            research_run_id=run_id,
            statement="RoPE allows transformer context length extension with less perplexity degradation.",
            falsification_condition="Perplexity on 8k tokens is worse than sinusoidal embeddings with p > 0.05.",
            rationale="Relative rotation naturally encodes token distance without absolute position decay.",
            expected_direction=ExpectedDirection.INCREASE,
            status=HypothesisStatus.PROPOSED,
            actor=ActorType.RESEARCH_AGENT,
            event_sink=sink,
        )
        hyp_id = hyp.id

    assert hyp_id.startswith("hyp_")
    assert hyp.status == HypothesisStatus.PROPOSED

    # Verify event emission
    assert len(sink) == 1
    event = sink.events[0]
    assert event.event_type == EventType.HYPOTHESIS_CREATED
    assert event.actor == ActorType.RESEARCH_AGENT
    assert event.research_run_id == run_id
    assert event.payload["hypothesis_id"] == hyp_id
    assert "RoPE allows transformer" in event.payload["statement"]
    assert event.payload["expected_direction"] == "increase"
    assert event.payload["status"] == "proposed"

    # Verify database persistence
    with get_db_session(session_factory) as session:
        repo = HypothesisRepository(session)
        stored = repo.get_by_id(hyp_id)
        assert stored is not None
        assert stored.statement == hyp.statement
        assert stored.falsification_condition == hyp.falsification_condition
        assert stored.research_run_id == run_id

        # Verify event persistence in events table
        events = EventRepository(session).list_by_run(run_id)
        # 1 research_created + 1 hypothesis_created
        assert len(events) == 2
        assert events[1].event_type == "hypothesis_created"


def test_create_hypothesis_requires_existing_research_run(session_factory: sessionmaker):
    """Verify attempting to create a hypothesis for a non-existent run raises MissingResearchRunError."""
    sink = InMemoryEventSink()

    with (
        pytest.raises(MissingResearchRunError) as exc_info,
        get_db_session(session_factory) as session,
    ):
        create_hypothesis(
            session=session,
            research_run_id="non_existent_run_999",
            statement="Orphan hypothesis statement",
            falsification_condition="Orphan falsification condition",
            event_sink=sink,
        )

    assert exc_info.value.run_id == "non_existent_run_999"
    # Ensure zero events emitted
    assert len(sink) == 0


def test_direct_orphan_hypothesis_db_insert_fails_foreign_key(
    session_factory: sessionmaker,
):
    """Verify SQLite foreign key enforcement prevents direct insertion of orphan hypothesis."""
    orphan_model = HypothesisModel(
        research_run_id="missing_run_id",
        statement="Direct orphan insert",
        falsification_condition="Condition",
    )

    with pytest.raises(IntegrityError), get_db_session(session_factory) as session:
        session.add(orphan_model)
        session.flush()


def test_actor_authorization_for_hypothesis_creation(session_factory: sessionmaker):
    """Verify only authorized actors (owner, system, research_agent) can create hypotheses."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Actor test")
        run_id = run.id

    # Authorized: RESEARCH_AGENT, OWNER, SYSTEM
    for actor in (ActorType.RESEARCH_AGENT, ActorType.OWNER, ActorType.SYSTEM):
        with get_db_session(session_factory) as session:
            create_hypothesis(
                session=session,
                research_run_id=run_id,
                statement=f"Authorized hypothesis by {actor}",
                falsification_condition="Condition",
                actor=actor,
            )

    # Unauthorized: EXECUTION_WORKER, VERIFIER
    for unauthorized_actor in (ActorType.EXECUTION_WORKER, ActorType.VERIFIER):
        with (
            pytest.raises(ActorAuthorizationError),
            get_db_session(session_factory) as session,
        ):
            create_hypothesis(
                session=session,
                research_run_id=run_id,
                statement="Unauthorized hypothesis",
                falsification_condition="Condition",
                actor=unauthorized_actor,
                event_sink=sink,
            )

    # Ensure no events were emitted for unauthorized attempts
    assert len(sink) == 0


def test_update_hypothesis_status_lifecycle(session_factory: sessionmaker):
    """Verify update_hypothesis_status updates validation status while preserving scientific content."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Status test")
        hyp = create_hypothesis(
            session=session,
            research_run_id=run.id,
            statement="AdamW converges faster than SGD with momentum on ViT.",
            falsification_condition="SGD reaches lower validation loss within 100 epochs.",
            status=HypothesisStatus.PROPOSED,
        )
        hyp_id = hyp.id

    # Update to TESTING
    with get_db_session(session_factory) as session:
        updated = update_hypothesis_status(
            session=session,
            hypothesis_id=hyp_id,
            status=HypothesisStatus.TESTING,
            actor=ActorType.RESEARCH_AGENT,
        )
        assert updated.status == HypothesisStatus.TESTING
        assert updated.statement == hyp.statement

    # Update to VALIDATED by VERIFIER
    with get_db_session(session_factory) as session:
        validated = update_hypothesis_status(
            session=session,
            hypothesis_id=hyp_id,
            status=HypothesisStatus.VALIDATED,
            actor=ActorType.VERIFIER,
        )
        assert validated.status == HypothesisStatus.VALIDATED

    # Verify persistence has updated status
    with get_db_session(session_factory) as session:
        repo = HypothesisRepository(session)
        persisted = repo.get_by_id(hyp_id)
        assert persisted.status == "validated"


def test_update_hypothesis_status_unauthorized_and_missing(
    session_factory: sessionmaker,
):
    """Verify unauthorized actor or missing hypothesis ID raises appropriate domain exceptions."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Status test 2")
        hyp = create_hypothesis(
            session=session,
            research_run_id=run.id,
            statement="Statement",
            falsification_condition="Condition",
        )
        hyp_id = hyp.id

    # EXECUTION_WORKER cannot update hypothesis status
    with pytest.raises(ActorAuthorizationError), get_db_session(session_factory) as session:
        update_hypothesis_status(
            session=session,
            hypothesis_id=hyp_id,
            status=HypothesisStatus.VALIDATED,
            actor=ActorType.EXECUTION_WORKER,
        )

    # Missing hypothesis ID
    with pytest.raises(MissingHypothesisError), get_db_session(session_factory) as session:
        update_hypothesis_status(
            session=session,
            hypothesis_id="hyp_missing_123",
            status=HypothesisStatus.VALIDATED,
            actor=ActorType.RESEARCH_AGENT,
        )


def test_create_hypothesis_atomic_transaction_rollback(session_factory: sessionmaker):
    """Verify failure inside session rolls back both hypothesis and audit event."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Rollback test")
        run_id = run.id

    with pytest.raises(RuntimeError), get_db_session(session_factory) as session:
        create_hypothesis(
            session=session,
            research_run_id=run_id,
            statement="This hypothesis will be rolled back",
            falsification_condition="Falsification condition",
        )
        raise RuntimeError("Simulated failure after hypothesis creation")

    # Confirm neither hypothesis nor event persisted
    with get_db_session(session_factory) as session:
        repo = HypothesisRepository(session)
        hyps = repo.list_by_run(run_id)
        assert len(hyps) == 0

        event_repo = EventRepository(session)
        events = event_repo.list_by_run(run_id)
        assert len(events) == 1  # Only run creation event exists


def test_create_hypothesis_run_convenience_helper(session_factory: sessionmaker):
    """Verify create_hypothesis_run convenience helper manages session boundaries automatically."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Convenience test")
        run_id = run.id

    domain_hyp = create_hypothesis_run(
        session_factory=session_factory,
        research_run_id=run_id,
        statement="Convenience helper manages sessions cleanly.",
        falsification_condition="Falsification occurs.",
    )

    assert domain_hyp.id.startswith("hyp_")

    with get_db_session(session_factory) as session:
        stored = HypothesisRepository(session).get_by_id(domain_hyp.id)
        assert stored is not None
        assert stored.statement == domain_hyp.statement
