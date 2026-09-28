"""Unit and integration tests for execution, result, and artifact controller logic (REX-008)."""

import pytest
from sqlalchemy.orm import sessionmaker

from rex.controller.exceptions import (
    ActorAuthorizationError,
    InvalidExecutionStateTransitionError,
    MissingExecutionError,
    MissingExperimentError,
    StateMachineError,
)
from rex.controller.executions import (
    create_execution,
    create_execution_run,
    record_artifact,
    record_result,
    record_results_batch,
    update_execution_status,
)
from rex.controller.experiments import create_experiment
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ArtifactType,
    ExecutionStatus,
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
from rex.persistence.repositories import (
    ArtifactRepository,
    EventRepository,
    ExecutionRepository,
    ExperimentRepository,
    ResultRepository,
)


@pytest.fixture
def session_factory(tmp_path):
    """Provide an isolated, file-based SQLite database with session factory."""
    db_file = tmp_path / "test_exec_ctrl.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


# ==============================================================================
# Execution Controller Tests
# ==============================================================================


def test_create_execution_success_and_event(session_factory: sessionmaker):
    """Verify creating execution persists model and emits execution_created event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Execution question")
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Execution objective",
        )
        run_id = run.id
        exp_id = exp.id

    with get_db_session(session_factory) as session:
        execution = create_execution(
            session=session,
            experiment_id=exp_id,
            command="python main.py",
            seed=42,
            code_hash="sha256:code",
            dataset_hash="sha256:data",
            configuration_hash="sha256:cfg",
            actor=ActorType.EXECUTION_WORKER,
            event_sink=sink,
            context={"note": "first execution attempt"},
        )
        exec_id = execution.id

    assert execution.id.startswith("exec_")
    assert execution.experiment_id == exp_id
    assert execution.status == ExecutionStatus.PENDING
    assert execution.seed == 42

    # Verify persistence
    with get_db_session(session_factory) as session:
        stored = ExecutionRepository(session).get_by_id(exec_id)
        assert stored is not None
        assert stored.experiment_id == exp_id
        assert stored.command == "python main.py"
        assert stored.seed == 42

        # Verify audit event in DB
        events = EventRepository(session).list_by_run(run_id)
        created_events = [e for e in events if e.event_type == EventType.EXECUTION_CREATED.value]
        assert len(created_events) == 1
        db_event = created_events[0]
        assert db_event.payload_json["execution_id"] == exec_id
        assert db_event.payload_json["note"] == "first execution attempt"

        # Verify experiment transitioned DESIGNED -> PENDING
        exp_model = ExperimentRepository(session).get_by_id(exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.PENDING.value

    # Verify sink received both experiment status synchronization and execution creation
    assert any(e.event_type == EventType.EXECUTION_CREATED for e in sink.events)
    assert any(e.event_type == EventType.EXPERIMENT_STATUS_CHANGED for e in sink.events)
    created_evt = next(e for e in sink.events if e.event_type == EventType.EXECUTION_CREATED)
    assert created_evt.execution_id == exec_id


def test_create_execution_running_status_emits_started_event(session_factory: sessionmaker):
    """Verify creating execution directly in RUNNING status emits execution_started event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Running question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exp_id = exp.id

    with get_db_session(session_factory) as session:
        execution = create_execution(
            session=session,
            experiment_id=exp_id,
            status=ExecutionStatus.RUNNING,
            event_sink=sink,
        )

    assert execution.status == ExecutionStatus.RUNNING
    assert execution.started_at is not None
    assert any(e.event_type == EventType.EXECUTION_STARTED for e in sink.events)
    assert any(e.event_type == EventType.EXPERIMENT_STATUS_CHANGED for e in sink.events)

    with get_db_session(session_factory) as session:
        stored_exp = ExperimentRepository(session).get_by_id(exp_id)
        assert stored_exp is not None
        assert stored_exp.status == ExperimentStatus.RUNNING.value


def test_create_execution_missing_experiment_raises(session_factory: sessionmaker):
    """Verify referencing a non-existent experiment ID raises MissingExperimentError."""
    with (
        get_db_session(session_factory) as session,
        pytest.raises(MissingExperimentError),
    ):
        create_execution(
            session=session,
            experiment_id="exp_nonexistent_999",
        )


def test_create_execution_actor_authorization(session_factory: sessionmaker):
    """Verify allowed actors can create execution, while verifier is rejected."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Actor question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exp_id = exp.id

    for allowed in (
        ActorType.EXECUTION_WORKER,
        ActorType.RESEARCH_AGENT,
        ActorType.OWNER,
        ActorType.SYSTEM,
    ):
        with get_db_session(session_factory) as session:
            execution = create_execution(
                session=session,
                experiment_id=exp_id,
                actor=allowed,
            )
            assert execution is not None

    with (
        get_db_session(session_factory) as session,
        pytest.raises(ActorAuthorizationError),
    ):
        create_execution(
            session=session,
            experiment_id=exp_id,
            actor=ActorType.VERIFIER,
        )


def test_update_execution_status_lifecycle_and_events(session_factory: sessionmaker):
    """Verify execution lifecycle transitions and events (PENDING -> RUNNING -> COMPLETED)."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id
        exp_id = exp.id

    # 1. PENDING -> RUNNING
    with get_db_session(session_factory) as session:
        running = update_execution_status(
            session=session,
            execution_id=exec_id,
            new_status=ExecutionStatus.RUNNING,
            actor=ActorType.EXECUTION_WORKER,
            event_sink=sink,
        )
        assert running.status == ExecutionStatus.RUNNING
        assert running.started_at is not None

    assert any(e.event_type == EventType.EXECUTION_STARTED for e in sink.events)
    assert any(e.event_type == EventType.EXPERIMENT_STATUS_CHANGED for e in sink.events)

    with get_db_session(session_factory) as session:
        exp_model = ExperimentRepository(session).get_by_id(exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.RUNNING.value

    # 2. RUNNING -> COMPLETED
    with get_db_session(session_factory) as session:
        completed = update_execution_status(
            session=session,
            execution_id=exec_id,
            new_status=ExecutionStatus.COMPLETED,
            exit_code=0,
            resource_usage={"duration_s": 45.2},
            actor=ActorType.EXECUTION_WORKER,
            event_sink=sink,
        )
        assert completed.status == ExecutionStatus.COMPLETED
        assert completed.exit_code == 0
        assert completed.finished_at is not None
        assert completed.resource_usage["duration_s"] == 45.2

    assert any(e.event_type == EventType.EXECUTION_COMPLETED for e in sink.events)

    with get_db_session(session_factory) as session:
        exp_model = ExperimentRepository(session).get_by_id(exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.COMPLETED.value


def test_update_execution_status_invalid_transition_raises(session_factory: sessionmaker):
    """Verify invalid transitions raise InvalidExecutionStateTransitionError."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id

    # Cannot jump PENDING -> COMPLETED directly
    with (
        get_db_session(session_factory) as session,
        pytest.raises(InvalidExecutionStateTransitionError),
    ):
        update_execution_status(
            session=session,
            execution_id=exec_id,
            new_status=ExecutionStatus.COMPLETED,
        )


def test_update_execution_status_from_terminal_raises(session_factory: sessionmaker):
    """Verify transitioning out of terminal status raises error."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(
            session=session,
            experiment_id=exp.id,
            status=ExecutionStatus.FAILED,
        )
        exec_id = execution.id

    with (
        get_db_session(session_factory) as session,
        pytest.raises(InvalidExecutionStateTransitionError, match="terminal status"),
    ):
        update_execution_status(
            session=session,
            execution_id=exec_id,
            new_status=ExecutionStatus.RUNNING,
        )


# ==============================================================================
# Result Controller Tests
# ==============================================================================


def test_record_result_success_and_event(session_factory: sessionmaker):
    """Verify recording result persists model and emits result_recorded event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Result question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id
        run_id = run.id

    with get_db_session(session_factory) as session:
        result = record_result(
            session=session,
            execution_id=exec_id,
            metric_name="accuracy",
            metric_value=0.925,
            metric_unit="%",
            result_data={"test_samples": 1000},
            actor=ActorType.EXECUTION_WORKER,
            event_sink=sink,
            context={"split": "test"},
        )
        res_id = result.id

    assert result.id.startswith("res_")
    assert result.execution_id == exec_id
    assert result.metric_name == "accuracy"
    assert result.metric_value == 0.925

    with get_db_session(session_factory) as session:
        stored = ResultRepository(session).get_by_id(res_id)
        assert stored is not None
        assert stored.metric_name == "accuracy"
        assert stored.metric_value == 0.925
        assert stored.result_json["test_samples"] == 1000

        # Verify event
        events = EventRepository(session).list_by_run(run_id)
        res_events = [e for e in events if e.event_type == EventType.RESULT_RECORDED.value]
        assert len(res_events) == 1
        assert res_events[0].payload_json["metric_name"] == "accuracy"
        assert res_events[0].payload_json["split"] == "test"

    assert len(sink.events) == 1
    assert sink.events[0].event_type == EventType.RESULT_RECORDED
    assert sink.events[0].payload["metric_value"] == 0.925


def test_record_result_missing_execution_raises(session_factory: sessionmaker):
    """Verify recording result against non-existent execution raises MissingExecutionError."""
    with (
        get_db_session(session_factory) as session,
        pytest.raises(MissingExecutionError),
    ):
        record_result(
            session=session,
            execution_id="exec_nonexistent_999",
            metric_name="accuracy",
            metric_value=0.5,
        )


def test_record_result_rejects_research_agent(session_factory: sessionmaker):
    """CRITICAL SECURITY TEST: Verify research agent CANNOT fabricate or directly assert experimental results."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Security question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id

    # Research agent must be rejected!
    with (
        get_db_session(session_factory) as session,
        pytest.raises(ActorAuthorizationError),
    ):
        record_result(
            session=session,
            execution_id=exec_id,
            metric_name="accuracy",
            metric_value=0.999,
            actor=ActorType.RESEARCH_AGENT,
        )


def test_record_result_non_finite_metric_rejected(session_factory: sessionmaker):
    """Verify record_result rejects NaN and infinities, but accepts finite values."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Non-finite test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id

    for non_finite in (float("nan"), float("inf"), float("-inf")):
        with (
            get_db_session(session_factory) as session,
            pytest.raises(ValueError, match="finite number"),
        ):
            record_result(
                session=session,
                execution_id=exec_id,
                metric_name="acc",
                metric_value=non_finite,
            )

    with get_db_session(session_factory) as session:
        r0 = record_result(
            session=session,
            execution_id=exec_id,
            metric_name="zero_metric",
            metric_value=0.0,
        )
        assert r0.metric_value == 0.0

        r_normal = record_result(
            session=session,
            execution_id=exec_id,
            metric_name="normal_metric",
            metric_value=42.125,
        )
        assert r_normal.metric_value == 42.125


def test_record_results_batch(session_factory: sessionmaker):
    """Verify atomic registration of multiple results."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Batch question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id

    batch_data = [
        {"metric_name": "train_loss", "metric_value": 0.25},
        {"metric_name": "val_loss", "metric_value": 0.31},
        {"metric_name": "accuracy", "metric_value": 0.91, "metric_unit": "%"},
    ]

    with get_db_session(session_factory) as session:
        results = record_results_batch(
            session=session,
            execution_id=exec_id,
            results=batch_data,
        )
        assert len(results) == 3

    with get_db_session(session_factory) as session:
        stored = ResultRepository(session).list_by_execution(exec_id)
        assert len(stored) == 3


# ==============================================================================
# Artifact Controller Tests
# ==============================================================================


def test_record_artifact_success_and_event(session_factory: sessionmaker):
    """Verify recording execution artifact persists model and emits artifact_created event."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Artifact question")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id
        run_id = run.id

    with get_db_session(session_factory) as session:
        artifact = record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="logs/stdout.log",
            content_hash="sha256:loghash",
            size_bytes=1024,
            execution_id=exec_id,
            actor=ActorType.EXECUTION_WORKER,
            event_sink=sink,
            context={"tag": "stdout"},
        )
        art_id = artifact.id

    assert artifact.id.startswith("art_")
    assert artifact.research_run_id == run_id
    assert artifact.execution_id == exec_id
    assert artifact.artifact_type == ArtifactType.LOG

    with get_db_session(session_factory) as session:
        stored = ArtifactRepository(session).get_by_id(art_id)
        assert stored is not None
        assert stored.path == "logs/stdout.log"
        assert stored.size_bytes == 1024

        # Verify event
        events = EventRepository(session).list_by_run(run_id)
        art_events = [e for e in events if e.event_type == EventType.ARTIFACT_CREATED.value]
        assert len(art_events) == 1
        assert art_events[0].payload_json["artifact_id"] == art_id

    assert len(sink.events) == 1
    assert sink.events[0].event_type == EventType.ARTIFACT_CREATED


def test_record_artifact_missing_execution_raises(session_factory: sessionmaker):
    """Verify referencing non-existent execution raises MissingExecutionError."""
    with (
        get_db_session(session_factory) as session,
        pytest.raises(MissingExecutionError),
    ):
        record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="logs/test.log",
            content_hash="hash",
            execution_id="exec_nonexistent_999",
        )


def test_record_artifact_cross_run_mismatch_raises(session_factory: sessionmaker):
    """Verify specifying research_run_id that conflicts with execution's run raises StateMachineError."""
    with get_db_session(session_factory) as session:
        run1 = create_research_run(session=session, research_question="Run 1")
        run2 = create_research_run(session=session, research_question="Run 2")
        exp1 = create_experiment(session=session, research_run_id=run1.id, objective="Exp 1")
        execution = create_execution(session=session, experiment_id=exp1.id)
        exec_id = execution.id
        run2_id = run2.id

    with (
        get_db_session(session_factory) as session,
        pytest.raises(StateMachineError, match="does not match execution run"),
    ):
        record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="logs/test.log",
            content_hash="hash",
            execution_id=exec_id,
            research_run_id=run2_id,
        )


def test_record_artifact_run_level_without_execution(session_factory: sessionmaker):
    """Verify registering a run-level artifact without execution_id."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Run level artifact")
        run_id = run.id

    with get_db_session(session_factory) as session:
        artifact = record_artifact(
            session=session,
            artifact_type=ArtifactType.MANIFEST,
            path="manifests/environment.json",
            content_hash="sha256:manifesthash",
            research_run_id=run_id,
            actor=ActorType.SYSTEM,
        )
        assert artifact.research_run_id == run_id
        assert artifact.execution_id is None


# ==============================================================================
# Transaction Rollback Tests
# ==============================================================================


def test_transaction_rollback_execution(session_factory: sessionmaker):
    """Verify rollback leaves no execution or event in database on failure."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Rollback test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exp_id = exp.id
        run_id = run.id

    try:
        with get_db_session(session_factory) as session:
            create_execution(
                session=session,
                experiment_id=exp_id,
                execution_id="exec_rollback_001",
            )
            raise RuntimeError("Force execution rollback")
    except RuntimeError:
        pass

    with get_db_session(session_factory) as session:
        assert ExecutionRepository(session).get_by_id("exec_rollback_001") is None
        events = EventRepository(session).list_by_run(run_id)
        assert not any(e.payload_json.get("execution_id") == "exec_rollback_001" for e in events)


def test_transaction_rollback_result(session_factory: sessionmaker):
    """Verify rollback leaves no result or event in database on failure."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Rollback result test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(session=session, experiment_id=exp.id)
        exec_id = execution.id
        run_id = run.id

    try:
        with get_db_session(session_factory) as session:
            record_result(
                session=session,
                execution_id=exec_id,
                metric_name="acc",
                result_id="res_rollback_001",
            )
            raise RuntimeError("Force result rollback")
    except RuntimeError:
        pass

    with get_db_session(session_factory) as session:
        assert ResultRepository(session).get_by_id("res_rollback_001") is None
        events = EventRepository(session).list_by_run(run_id)
        assert not any(e.payload_json.get("result_id") == "res_rollback_001" for e in events)


# ==============================================================================
# Repository Listing Queries Tests
# ==============================================================================


def test_repository_listing_queries(session_factory: sessionmaker):
    """Verify list_by_run and list_by_experiment across Execution, Result, and Artifact repositories."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Listing test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exec1 = create_execution(session=session, experiment_id=exp.id, seed=1)
        exec2 = create_execution(session=session, experiment_id=exp.id, seed=2)

        record_result(session=session, execution_id=exec1.id, metric_name="acc", metric_value=0.90)
        record_result(session=session, execution_id=exec2.id, metric_name="acc", metric_value=0.92)

        record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="log1.txt",
            content_hash="h1",
            execution_id=exec1.id,
        )
        record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="log2.txt",
            content_hash="h2",
            execution_id=exec2.id,
        )

        run_id = run.id
        exp_id = exp.id
        exec1_id = exec1.id

    with get_db_session(session_factory) as session:
        # Execution listing
        exec_repo = ExecutionRepository(session)
        assert len(exec_repo.list_by_experiment(exp_id)) == 2
        assert len(exec_repo.list_by_run(run_id)) == 2

        # Result listing
        res_repo = ResultRepository(session)
        assert len(res_repo.list_by_execution(exec1_id)) == 1
        assert len(res_repo.list_by_run(run_id)) == 2
        assert len(res_repo.list_by_metric(exec1_id, "acc")) == 1

        # Artifact listing
        art_repo = ArtifactRepository(session)
        assert len(art_repo.list_by_execution(exec1_id)) == 1
        assert len(art_repo.list_by_run(run_id)) == 2


def test_create_execution_run_helper(session_factory: sessionmaker):
    """Verify create_execution_run convenience helper with managed session."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Helper test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exp_id = exp.id

    execution = create_execution_run(
        session_factory=session_factory,
        experiment_id=exp_id,
        command="python eval.py",
        seed=100,
    )

    assert execution.id.startswith("exec_")
    with get_db_session(session_factory) as session:
        stored = ExecutionRepository(session).get_by_id(execution.id)
        assert stored is not None
        assert stored.seed == 100


def test_record_artifact_execution_evidence_without_execution_id_raises(
    session_factory: sessionmaker,
):
    """Verify execution artifact types (LOG, STDOUT, OUTPUT, etc.) cannot be registered without execution_id."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Artifact test")
        run_id = run.id

    for exec_type in (
        ArtifactType.LOG,
        ArtifactType.STDOUT,
        ArtifactType.STDERR,
        ArtifactType.METRIC,
        ArtifactType.CHECKPOINT,
        ArtifactType.OUTPUT,
    ):
        with (
            get_db_session(session_factory) as session,
            pytest.raises(ValueError, match="represent execution outputs"),
        ):
            record_artifact(
                session=session,
                artifact_type=exec_type,
                path="output.log",
                content_hash="hash123",
                execution_id=None,
                research_run_id=run_id,
            )


def test_execution_cancellation_emits_cancelled_event_and_updates_experiment(
    session_factory: sessionmaker,
):
    """Verify cancelling an execution emits EXECUTION_CANCELLED and updates parent experiment."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Cancellation test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        execution = create_execution(
            session=session,
            experiment_id=exp.id,
            status=ExecutionStatus.RUNNING,
        )
        exec_id = execution.id
        exp_id = exp.id

    with get_db_session(session_factory) as session:
        cancelled = update_execution_status(
            session=session,
            execution_id=exec_id,
            new_status=ExecutionStatus.CANCELLED,
            event_sink=sink,
            reason="Aborted by user",
        )
        assert cancelled.status == ExecutionStatus.CANCELLED

    # Check EXECUTION_CANCELLED event
    assert any(e.event_type == EventType.EXECUTION_CANCELLED for e in sink.events)

    with get_db_session(session_factory) as session:
        exp_model = ExperimentRepository(session).get_by_id(exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.CANCELLED.value


def test_cannot_create_execution_on_terminal_experiment(session_factory: sessionmaker):
    """Verify creating execution on a terminal experiment is rejected with StateMachineError."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Terminal exp test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exp_id = exp.id

        # Run an execution to completion to make the experiment COMPLETED
        exec1 = create_execution(
            session=session, experiment_id=exp_id, status=ExecutionStatus.RUNNING
        )
        update_execution_status(
            session=session, execution_id=exec1.id, new_status=ExecutionStatus.COMPLETED
        )

        exp_model = ExperimentRepository(session).get_by_id(exp_id)
        assert exp_model.status == ExperimentStatus.COMPLETED.value

    # Attempting to create a new execution on completed experiment must fail
    with (
        get_db_session(session_factory) as session,
        pytest.raises(StateMachineError, match="terminal status"),
    ):
        create_execution(session=session, experiment_id=exp_id)


def test_experiment_failed_when_all_executions_fail(session_factory: sessionmaker):
    """Verify parent experiment transitions to FAILED when all executions fail."""
    with get_db_session(session_factory) as session:
        run = create_research_run(session=session, research_question="Fail test")
        exp = create_experiment(session=session, research_run_id=run.id, objective="Obj")
        exec1 = create_execution(
            session=session, experiment_id=exp.id, status=ExecutionStatus.RUNNING
        )
        exp_id = exp.id
        exec1_id = exec1.id

    with get_db_session(session_factory) as session:
        update_execution_status(
            session=session,
            execution_id=exec1_id,
            new_status=ExecutionStatus.FAILED,
            exit_code=1,
            reason="CUDA Out of Memory",
        )

    with get_db_session(session_factory) as session:
        exp_model = ExperimentRepository(session).get_by_id(exp_id)
        assert exp_model is not None
        assert exp_model.status == ExperimentStatus.FAILED.value
