"""Unit and integration tests for experiment controller, persistence, and audit events (REX-007)."""

import pytest
from sqlalchemy.orm import sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    ExperimentExecutionExistsError,
    InvalidExperimentStateTransitionError,
    MissingExperimentError,
    MissingHypothesisError,
    MissingResearchRunError,
    StateMachineError,
)
from rex.controller.experiments import (
    assert_experiment_mutable,
    create_experiment,
    create_experiment_run,
    update_experiment_status,
)
from rex.controller.hypotheses import create_hypothesis
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ExperimentSpecification,
    ExperimentStatus,
)
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
from rex.persistence.models import ExecutionModel
from rex.persistence.repositories import (
    EventRepository,
    ExecutionRepository,
    ExperimentRepository,
)


@pytest.fixture
def session_factory(tmp_path):
    """Provide an isolated, file-based SQLite database with session factory."""
    db_file = tmp_path / "test_experiment_ctrl.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_create_experiment_success_and_event_emission(session_factory: sessionmaker):
    """Verify successful experiment creation, persistence, and structured audit event emission."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Does weight decay reduce overfitting in small Transformer models?",
        )
        hyp = create_hypothesis(
            session=session,
            research_run_id=run.id,
            statement="Weight decay = 0.1 achieves lower test cross-entropy than weight decay = 0.0.",
            falsification_condition="Test loss with weight decay = 0.1 is >= test loss without weight decay.",
        )
        run_id = run.id
        hyp_id = hyp.id

    # Create experiment
    spec = ExperimentSpecification(
        name="wd_comparison",
        variables={"weight_decay": [0.0, 0.1]},
        parameters={"epochs": 50, "lr": 1e-3},
        seeds=(42, 43),
    )

    with get_db_session(session_factory) as session:
        exp = create_experiment(
            session=session,
            research_run_id=run_id,
            hypothesis_id=hyp_id,
            objective="Compare test loss across weight decay settings",
            specification=spec,
            actor=ActorType.RESEARCH_AGENT,
            event_sink=sink,
            context={"note": "Initial baseline experiment"},
        )
        exp_id = exp.id

    assert exp.id.startswith("exp_")
    assert exp.research_run_id == run_id
    assert exp.hypothesis_id == hyp_id
    assert exp.status == ExperimentStatus.DESIGNED
    assert exp.specification.name == "wd_comparison"

    # Verify database persistence
    with get_db_session(session_factory) as session:
        repo = ExperimentRepository(session)
        stored = repo.get_by_id(exp_id)
        assert stored is not None
        assert stored.id == exp_id
        assert stored.research_run_id == run_id
        assert stored.hypothesis_id == hyp_id
        assert stored.status == "designed"
        assert stored.specification_json["name"] == "wd_comparison"
        assert stored.specification_json["parameters"]["lr"] == 1e-3

        # Verify event in database
        event_repo = EventRepository(session)
        events = event_repo.list_by_run(run_id)
        exp_events = [e for e in events if e.event_type == EventType.EXPERIMENT_CREATED.value]
        assert len(exp_events) == 1
        db_event = exp_events[0]
        assert db_event.payload_json["experiment_id"] == exp_id
        assert (
            db_event.payload_json["objective"] == "Compare test loss across weight decay settings"
        )
        assert db_event.payload_json["note"] == "Initial baseline experiment"

    # Verify event sink emission
    assert len(sink.events) == 1
    sink_event = sink.events[0]
    assert sink_event.event_type == EventType.EXPERIMENT_CREATED
    assert sink_event.experiment_id == exp_id
    assert sink_event.research_run_id == run_id


def test_create_experiment_missing_run_raises(session_factory: sessionmaker):
    """Verify creating an experiment with non-existent research_run_id raises MissingResearchRunError."""
    with get_db_session(session_factory) as session, pytest.raises(MissingResearchRunError):
        create_experiment(
            session=session,
            research_run_id="run_nonexistent_999",
            objective="Test missing run",
        )


def test_create_experiment_missing_hypothesis_raises(session_factory: sessionmaker):
    """Verify referencing a non-existent hypothesis_id raises MissingHypothesisError."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Question")
        run_id = run.id

    with get_db_session(session_factory) as session, pytest.raises(MissingHypothesisError):
        create_experiment(
            session=session,
            research_run_id=run_id,
            hypothesis_id="hyp_missing_999",
            objective="Test missing hypothesis",
        )


def test_create_experiment_cross_run_hypothesis_raises(session_factory: sessionmaker):
    """Verify referencing a hypothesis belonging to another run raises StateMachineError."""
    with get_db_session(session_factory) as session:
        run1 = create_research_run(session=session, research_question="Run 1")
        run2 = create_research_run(session=session, research_question="Run 2")
        hyp1 = create_hypothesis(
            session=session,
            research_run_id=run1.id,
            statement="Statement 1",
            falsification_condition="Condition 1",
        )
        run2_id = run2.id
        hyp1_id = hyp1.id

    with (
        get_db_session(session_factory) as session,
        pytest.raises(StateMachineError, match="belongs to run"),
    ):
        create_experiment(
            session=session,
            research_run_id=run2_id,
            hypothesis_id=hyp1_id,
            objective="Test cross run hypothesis",
        )


def test_create_experiment_missing_parent_raises(session_factory: sessionmaker):
    """Verify referencing a non-existent parent_experiment_id raises MissingExperimentError."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Question")
        run_id = run.id

    with get_db_session(session_factory) as session, pytest.raises(MissingExperimentError):
        create_experiment(
            session=session,
            research_run_id=run_id,
            objective="Test missing parent",
            parent_experiment_id="exp_missing_888",
        )


def test_create_experiment_cross_run_parent_raises(session_factory: sessionmaker):
    """Verify referencing a parent experiment from a different run raises StateMachineError."""
    with get_db_session(session_factory) as session:
        run1 = create_research_run(session=session, research_question="Run 1")
        run2 = create_research_run(session=session, research_question="Run 2")
        exp1 = create_experiment(
            session=session,
            research_run_id=run1.id,
            objective="Parent in run 1",
        )
        run2_id = run2.id
        exp1_id = exp1.id

    with (
        get_db_session(session_factory) as session,
        pytest.raises(StateMachineError, match="belongs to run"),
    ):
        create_experiment(
            session=session,
            research_run_id=run2_id,
            objective="Child in run 2",
            parent_experiment_id=exp1_id,
        )


def test_create_experiment_actor_authorization(session_factory: sessionmaker):
    """Verify allowed actors can create experiments, while worker and verifier are rejected."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Actor test")
        run_id = run.id

    # Allowed actors
    for actor in (ActorType.RESEARCH_AGENT, ActorType.OWNER, ActorType.SYSTEM):
        with get_db_session(session_factory) as session:
            exp = create_experiment(
                session=session,
                research_run_id=run_id,
                objective=f"Created by {actor.value}",
                actor=actor,
            )
            assert exp is not None

    # Disallowed actors (execution worker, verifier)
    for forbidden in (ActorType.EXECUTION_WORKER, ActorType.VERIFIER):
        with get_db_session(session_factory) as session, pytest.raises(ActorAuthorizationError):
            create_experiment(
                session=session,
                research_run_id=run_id,
                objective="Forbidden creation",
                actor=forbidden,
            )


def test_update_experiment_status_lifecycle_and_event(session_factory: sessionmaker):
    """Verify full sequential lifecycle flow and structured EXPERIMENT_STATUS_CHANGED event emission."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Lifecycle question")
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Lifecycle test experiment",
        )
        exp_id = exp.id
        run_id = run.id

    # Transition sequence: DESIGNED -> PENDING -> RUNNING -> ANALYZING -> COMPLETED
    transitions = [
        (ExperimentStatus.PENDING, ActorType.SYSTEM, "Queued for execution"),
        (ExperimentStatus.RUNNING, ActorType.EXECUTION_WORKER, "Sandbox container launched"),
        (
            ExperimentStatus.ANALYZING,
            ActorType.RESEARCH_AGENT,
            "Execution finished, running analysis",
        ),
        (ExperimentStatus.COMPLETED, ActorType.RESEARCH_AGENT, "Analysis completed successfully"),
    ]

    for target_status, actor, reason in transitions:
        with get_db_session(session_factory) as session:
            updated = update_experiment_status(
                session=session,
                experiment_id=exp_id,
                new_status=target_status,
                actor=actor,
                reason=reason,
                event_sink=sink,
            )
            assert updated.status == target_status

    # Verify events recorded in sink
    assert len(sink.events) == 4
    for idx, (target_status, actor, reason) in enumerate(transitions):
        event = sink.events[idx]
        assert event.event_type == EventType.EXPERIMENT_STATUS_CHANGED
        assert event.experiment_id == exp_id
        assert event.research_run_id == run_id
        assert event.actor == actor
        assert event.payload["new_status"] == target_status.value
        assert event.payload["reason"] == reason

    # Verify database persistence
    with get_db_session(session_factory) as session:
        repo = ExperimentRepository(session)
        final_exp = repo.get_by_id(exp_id)
        assert final_exp.status == "completed"

        event_repo = EventRepository(session)
        stored_events = event_repo.list_by_run(run_id)
        status_events = [
            e for e in stored_events if e.event_type == EventType.EXPERIMENT_STATUS_CHANGED.value
        ]
        assert len(status_events) == 4


def test_update_experiment_status_invalid_transition_raises(session_factory: sessionmaker):
    """Verify illegal transitions raise InvalidExperimentStateTransitionError."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Invalid transition test")
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Illegal transition test",
        )
        exp_id = exp.id

    # DESIGNED cannot transition directly to COMPLETED or ANALYZING
    with (
        get_db_session(session_factory) as session,
        pytest.raises(InvalidExperimentStateTransitionError),
    ):
        update_experiment_status(
            session=session,
            experiment_id=exp_id,
            new_status=ExperimentStatus.COMPLETED,
        )


def test_update_experiment_status_from_terminal_raises(session_factory: sessionmaker):
    """Verify attempting to transition out of a terminal status raises InvalidExperimentStateTransitionError."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Terminal transition test")
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Terminal experiment",
            status=ExperimentStatus.FAILED,
        )
        exp_id = exp.id

    with (
        get_db_session(session_factory) as session,
        pytest.raises(InvalidExperimentStateTransitionError, match="terminal status"),
    ):
        update_experiment_status(
            session=session,
            experiment_id=exp_id,
            new_status=ExperimentStatus.RUNNING,
        )


def test_update_experiment_status_actor_authorization(session_factory: sessionmaker):
    """Verify execution worker and verifier can update status, while unknown/disallowed actors are rejected."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Actor test")
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Actor test experiment",
        )
        exp_id = exp.id

    # EXECUTION_WORKER can update to RUNNING
    with get_db_session(session_factory) as session:
        updated = update_experiment_status(
            session=session,
            experiment_id=exp_id,
            new_status=ExperimentStatus.RUNNING,
            actor=ActorType.EXECUTION_WORKER,
        )
        assert updated.status == ExperimentStatus.RUNNING


def test_assert_experiment_mutable_boundary(session_factory: sessionmaker):
    """Verify assert_experiment_mutable succeeds when no execution exists, but raises when executions exist."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Boundary test")
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Boundary experiment",
        )
        exp_id = exp.id

    # 1. No execution exists -> succeeds
    with get_db_session(session_factory) as session:
        assert_experiment_mutable(session, exp_id)

    # 2. Attach an execution
    with get_db_session(session_factory) as session:
        exec_repo = ExecutionRepository(session)
        exec_model = ExecutionModel(
            experiment_id=exp_id,
            status="pending",
        )
        exec_repo.create(exec_model)

    # 3. Execution exists -> raises ExperimentExecutionExistsError
    with (
        get_db_session(session_factory) as session,
        pytest.raises(ExperimentExecutionExistsError, match="already exist"),
    ):
        assert_experiment_mutable(session, exp_id)


def test_create_experiment_rollback_on_failure(session_factory: sessionmaker):
    """Verify atomic transaction rollback: on failure, neither experiment nor event is persisted."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Rollback test")
        run_id = run.id

    # Simulate an error inside the transaction after experiment creation
    try:
        with get_db_session(session_factory) as session:
            create_experiment(
                session=session,
                research_run_id=run_id,
                objective="Will be rolled back",
                experiment_id="exp_rollback_01",
            )
            # Force intentional transaction rollback
            raise RuntimeError("Intentional error triggering rollback")
    except RuntimeError:
        pass

    # Verify that nothing was persisted
    with get_db_session(session_factory) as session:
        exp_repo = ExperimentRepository(session)
        assert exp_repo.get_by_id("exp_rollback_01") is None

        event_repo = EventRepository(session)
        events = event_repo.list_by_run(run_id)
        assert not any(e.payload_json.get("experiment_id") == "exp_rollback_01" for e in events)


def test_experiment_repository_list_queries(session_factory: sessionmaker):
    """Verify ExperimentRepository list_by_run, list_by_hypothesis, and list_by_parent methods."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Listing test")
        hyp = create_hypothesis(
            session=session,
            research_run_id=run.id,
            statement="Listing hyp",
            falsification_condition="Listing condition",
        )

        parent_exp = create_experiment(
            session=session,
            research_run_id=run.id,
            hypothesis_id=hyp.id,
            objective="Parent experiment",
        )

        child_exp = create_experiment(
            session=session,
            research_run_id=run.id,
            hypothesis_id=hyp.id,
            objective="Child experiment",
            parent_experiment_id=parent_exp.id,
        )

        run_id = run.id
        hyp_id = hyp.id
        parent_id = parent_exp.id
        child_id = child_exp.id

    with get_db_session(session_factory) as session:
        repo = ExperimentRepository(session)

        by_run = repo.list_by_run(run_id)
        assert len(by_run) == 2
        assert {e.id for e in by_run} == {parent_id, child_id}

        by_hyp = repo.list_by_hypothesis(hyp_id)
        assert len(by_hyp) == 2

        by_parent = repo.list_by_parent(parent_id)
        assert len(by_parent) == 1
        assert by_parent[0].id == child_id


def test_create_experiment_run_convenience_helper(session_factory: sessionmaker):
    """Verify create_experiment_run managed session helper."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Helper test")
        run_id = run.id

    exp = create_experiment_run(
        session_factory=session_factory,
        research_run_id=run_id,
        objective="Created via convenience helper",
    )

    assert exp.id.startswith("exp_")
    with get_db_session(session_factory) as session:
        stored = ExperimentRepository(session).get_by_id(exp.id)
        assert stored is not None
        assert stored.objective == "Created via convenience helper"
