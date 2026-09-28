"""Unit tests for REX persistence repositories and event bridging (REX-004)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import sessionmaker

from rex.observability.events import (
    ActorType,
    EventType,
    ResearchEvent,
    create_event,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    LiteratureSourceModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    AnalysisRepository,
    ArtifactRepository,
    ClaimRepository,
    DatabaseEventSink,
    EventRepository,
    ExecutionRepository,
    ExperimentRepository,
    HypothesisRepository,
    LiteratureSourceRepository,
    ResearchRunRepository,
    ResultRepository,
)


@pytest.fixture
def session_factory(tmp_path):
    """Provide an isolated, file-based SQLite session factory."""
    db_file = tmp_path / "test_repos.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_research_run_repository_lifecycle(session_factory: sessionmaker):
    """Test full CRUD lifecycle in ResearchRunRepository."""
    with get_db_session(session_factory) as session:
        repo = ResearchRunRepository(session)
        run = repo.create(
            ResearchRunModel(
                title="Optimizer Benchmark",
                research_question="Does Lion outperform AdamW on small models?",
                configuration_json={"batch_size": 32},
            )
        )
        run_id = run.id
        assert run_id.startswith("run_")

    with get_db_session(session_factory) as session:
        repo = ResearchRunRepository(session)
        fetched = repo.get_by_id(run_id)
        assert fetched is not None
        assert fetched.title == "Optimizer Benchmark"
        assert fetched.status == "INITIALIZE"

        repo.update_status(run_id, "RUNNING")

    with get_db_session(session_factory) as session:
        repo = ResearchRunRepository(session)
        updated = repo.get_by_id(run_id)
        assert updated is not None
        assert updated.status == "RUNNING"
        all_runs = repo.list_all()
        assert len(all_runs) == 1

        deleted = repo.delete(run_id)
        assert deleted is True

    with get_db_session(session_factory) as session:
        repo = ResearchRunRepository(session)
        assert repo.get_by_id(run_id) is None


def test_hypothesis_and_experiment_repositories(session_factory: sessionmaker):
    """Test hypothesis and experiment repositories with parent/child linking."""
    with get_db_session(session_factory) as session:
        run_repo = ResearchRunRepository(session)
        run = run_repo.create(ResearchRunModel(research_question="Transformer scaling"))

        hyp_repo = HypothesisRepository(session)
        hyp = hyp_repo.create(
            HypothesisModel(
                research_run_id=run.id,
                statement="Doubling width improves performance more than doubling depth.",
                rationale="Wider layers capture more parallel feature combinations.",
                expected_direction="increase",
                falsification_condition="Depth outperforms width at equal FLOP budget.",
            )
        )
        hyp_id = hyp.id

        exp_repo = ExperimentRepository(session)
        exp1 = exp_repo.create(
            ExperimentModel(
                research_run_id=run.id,
                hypothesis_id=hyp.id,
                objective="Wide transformer baseline",
                specification_json={"width": 1024, "depth": 12},
            )
        )
        exp2 = exp_repo.create(
            ExperimentModel(
                research_run_id=run.id,
                hypothesis_id=hyp.id,
                parent_experiment_id=exp1.id,
                objective="Wide transformer variation with swiglu",
                specification_json={"width": 1024, "depth": 12, "activation": "swiglu"},
            )
        )
        exp1_id = exp1.id
        exp2_id = exp2.id

    with get_db_session(session_factory) as session:
        hyp_repo = HypothesisRepository(session)
        hyps = hyp_repo.list_by_run(run.id)
        assert len(hyps) == 1
        assert hyps[0].id == hyp_id

        hyp_repo.update_status(hyp_id, "validated")
        assert hyp_repo.get_by_id(hyp_id).status == "validated"

        exp_repo = ExperimentRepository(session)
        run_exps = exp_repo.list_by_run(run.id)
        assert len(run_exps) == 2

        hyp_exps = exp_repo.list_by_hypothesis(hyp_id)
        assert len(hyp_exps) == 2

        fetched_exp2 = exp_repo.get_by_id(exp2_id)
        assert fetched_exp2.parent_experiment_id == exp1_id


def test_execution_and_result_repositories(session_factory: sessionmaker):
    """Test execution tracking and result batching repositories."""
    with get_db_session(session_factory) as session:
        run = ResearchRunRepository(session).create(
            ResearchRunModel(research_question="Batch normalization test")
        )
        exp = ExperimentRepository(session).create(
            ExperimentModel(
                research_run_id=run.id,
                objective="Train with BatchNorm",
                specification_json={},
            )
        )
        exec_repo = ExecutionRepository(session)
        execution = exec_repo.create(
            ExecutionModel(
                experiment_id=exp.id,
                command="python train.py",
                code_hash="c123",
                dataset_hash="d123",
                configuration_hash="cfg123",
                seed=42,
            )
        )
        exec_id = execution.id

    with get_db_session(session_factory) as session:
        exec_repo = ExecutionRepository(session)
        res_repo = ResultRepository(session)

        # Update execution status
        exec_repo.update_status(
            exec_id,
            status="completed",
            exit_code=0,
            finished_at=datetime.now(UTC),
        )

        # Batch insert results
        results = [
            ResultModel(
                execution_id=exec_id,
                metric_name="train_loss",
                metric_value=0.15,
                metric_unit="loss",
            ),
            ResultModel(
                execution_id=exec_id,
                metric_name="val_loss",
                metric_value=0.22,
                metric_unit="loss",
            ),
            ResultModel(
                execution_id=exec_id,
                metric_name="accuracy",
                metric_value=0.945,
                metric_unit="ratio",
            ),
        ]
        res_repo.create_batch(results)

    with get_db_session(session_factory) as session:
        exec_repo = ExecutionRepository(session)
        res_repo = ResultRepository(session)

        updated_exec = exec_repo.get_by_id(exec_id)
        assert updated_exec.status == "completed"
        assert updated_exec.exit_code == 0
        assert updated_exec.finished_at is not None

        all_results = res_repo.list_by_execution(exec_id)
        assert len(all_results) == 3

        val_results = res_repo.list_by_metric(exec_id, "val_loss")
        assert len(val_results) == 1
        assert val_results[0].metric_value == 0.22


def test_analysis_artifact_and_literature_repositories(session_factory: sessionmaker):
    """Test analysis, artifact, and literature source repositories."""
    with get_db_session(session_factory) as session:
        run = ResearchRunRepository(session).create(
            ResearchRunModel(research_question="Literature and statistical evaluation")
        )
        exp = ExperimentRepository(session).create(
            ExperimentModel(research_run_id=run.id, objective="Obj", specification_json={})
        )
        execution = ExecutionRepository(session).create(
            ExecutionModel(
                experiment_id=exp.id,
                command="run",
                code_hash="c",
                dataset_hash="d",
                configuration_hash="cfg",
            )
        )

        an_repo = AnalysisRepository(session)
        analysis = an_repo.create(
            AnalysisModel(
                research_run_id=run.id,
                analysis_type="effect_size",
                input_result_ids=["res_1", "res_2"],
                method="cohen_d",
                output_json={"d": 0.85},
            )
        )

        art_repo = ArtifactRepository(session)
        artifact = art_repo.create(
            ArtifactModel(
                research_run_id=run.id,
                execution_id=execution.id,
                artifact_type="model_weights",
                path="runs/model.pt",
                content_hash="hash_art_123",
                size_bytes=50000000,
                metadata_json={"epochs": 10},
            )
        )

        lit_repo = LiteratureSourceRepository(session)
        lit = lit_repo.create(
            LiteratureSourceModel(
                research_run_id=run.id,
                provider="openalex",
                external_id="W123456789",
                title="Attention Is All You Need",
                authors_json=["Ashish Vaswani", "Noam Shazeer"],
                year=2017,
                url="https://arxiv.org/abs/1706.03762",
            )
        )

        run_id = run.id
        an_id = analysis.id
        art_id = artifact.id
        lit_id = lit.id
        exec_id = execution.id

    with get_db_session(session_factory) as session:
        assert AnalysisRepository(session).get_by_id(an_id) is not None
        assert len(AnalysisRepository(session).list_by_run(run_id)) == 1

        assert ArtifactRepository(session).get_by_id(art_id) is not None
        assert len(ArtifactRepository(session).list_by_run(run_id)) == 1
        assert len(ArtifactRepository(session).list_by_execution(exec_id)) == 1

        assert LiteratureSourceRepository(session).get_by_id(lit_id) is not None
        assert len(LiteratureSourceRepository(session).list_by_run(run_id)) == 1


def test_claim_repository_and_evidence_links(session_factory: sessionmaker):
    """Test claim creation, status updates, and provenance evidence graph edges."""
    with get_db_session(session_factory) as session:
        run = ResearchRunRepository(session).create(
            ResearchRunModel(research_question="Claim evidence linking")
        )
        claim_repo = ClaimRepository(session)
        claim = claim_repo.create(
            ClaimModel(
                research_run_id=run.id,
                text="Attention mechanism reduces perplexity across all seeds.",
                claim_type="empirical",
                confidence=0.99,
                status="proposed",
            )
        )
        claim_id = claim.id

    with get_db_session(session_factory) as session:
        claim_repo = ClaimRepository(session)
        claim_repo.update_status(claim_id, "verified")

        # Link claim to analysis and literature evidence
        link1 = claim_repo.add_evidence_link(
            claim_id=claim_id,
            source_type="analysis",
            source_id="an_test_456",
            relationship_type="supported_by",
        )
        link2 = claim_repo.add_evidence_link(
            claim_id=claim_id,
            source_type="literature",
            source_id="lit_test_789",
            relationship_type="corroborated_by",
        )
        assert link1.id.startswith("lnk_")
        assert link2.id.startswith("lnk_")

    with get_db_session(session_factory) as session:
        claim_repo = ClaimRepository(session)
        fetched_claim = claim_repo.get_by_id(claim_id)
        assert fetched_claim.status == "verified"

        links = claim_repo.get_evidence_links(claim_id)
        assert len(links) == 2
        link_types = {link.source_type for link in links}
        assert link_types == {"analysis", "literature"}


def test_event_repository_roundtrip_with_research_event(session_factory: sessionmaker):
    """Verify bidirectional conversion between REX-003 ResearchEvent and EventModel."""
    with get_db_session(session_factory) as session:
        run = ResearchRunRepository(session).create(
            ResearchRunModel(research_question="Audit trail event tracking")
        )
        run_id = run.id

    # Create immutable REX-003 ResearchEvent
    event = create_event(
        event_type=EventType.EXPERIMENT_CREATED,
        actor=ActorType.RESEARCH_AGENT,
        research_run_id=run_id,
        experiment_id="exp_test_777",
        execution_id="exec_test_888",
        payload={
            "parameters": {"lr": 0.001, "hidden_dim": 256},
            "notes": "initial exploration",
        },
    )

    with get_db_session(session_factory) as session:
        repo = EventRepository(session)
        model = repo.record_event(event)
        assert model.id == event.event_id
        assert model.event_type == "experiment_created"
        assert model.actor_type == "research_agent"

    with get_db_session(session_factory) as session:
        repo = EventRepository(session)
        # Fetch directly as ResearchEvent
        restored_event = repo.get_research_event(event.event_id)
        assert restored_event is not None
        assert isinstance(restored_event, ResearchEvent)
        assert restored_event.event_id == event.event_id
        assert restored_event.event_type == EventType.EXPERIMENT_CREATED
        assert restored_event.actor == ActorType.RESEARCH_AGENT
        assert restored_event.research_run_id == run_id
        assert restored_event.experiment_id == "exp_test_777"
        assert restored_event.execution_id == "exec_test_888"
        assert restored_event.payload["parameters"]["lr"] == 0.001
        assert restored_event.payload["notes"] == "initial exploration"

        # Verify deep immutability on the restored event
        with pytest.raises(TypeError):
            restored_event.payload["notes"] = "mutated"

        # Fetch list of events
        events_list = repo.list_research_events_by_run(run_id)
        assert len(events_list) == 1
        assert events_list[0].event_id == event.event_id


def test_database_event_sink(session_factory: sessionmaker):
    """Verify DatabaseEventSink integrates with session factory and persists events."""
    with get_db_session(session_factory) as session:
        run = ResearchRunRepository(session).create(
            ResearchRunModel(research_question="Sink integration test")
        )
        run_id = run.id

    sink = DatabaseEventSink(session_factory)
    event = create_event(
        event_type=EventType.EXECUTION_STARTED,
        actor=ActorType.EXECUTION_WORKER,
        research_run_id=run_id,
        payload={"worker_id": "worker_01"},
    )
    sink.emit(event)

    with get_db_session(session_factory) as session:
        repo = EventRepository(session)
        retrieved = repo.get_research_event(event.event_id)
        assert retrieved is not None
        assert retrieved.event_id == event.event_id
        assert retrieved.payload["worker_id"] == "worker_01"


def test_transaction_rollback_on_error(session_factory: sessionmaker):
    """Verify get_db_session context manager rolls back uncommitted changes on error."""
    with get_db_session(session_factory) as session:
        run_repo = ResearchRunRepository(session)
        run = run_repo.create(ResearchRunModel(research_question="Rollback test"))
        run_id = run.id

    # Raise exception in block after creating a hypothesis
    with pytest.raises(RuntimeError), get_db_session(session_factory) as session:
        hyp_repo = HypothesisRepository(session)
        hyp_repo.create(
            HypothesisModel(
                research_run_id=run_id,
                statement="This should be rolled back",
                falsification_condition="Error",
            )
        )
        raise RuntimeError("Simulated failure inside transaction")

    # Confirm hypothesis was rolled back and does not exist in DB
    with get_db_session(session_factory) as session:
        hyp_repo = HypothesisRepository(session)
        hyps = hyp_repo.list_by_run(run_id)
        assert len(hyps) == 0
