"""Unit tests for REX persistence models and foreign-key constraints (REX-004)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rex.persistence.database import (
    Base,
    create_db_engine,
    create_session_factory,
    init_db,
)
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    EventModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    LiteratureSourceModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def test_db_session(tmp_path):
    """Provide an isolated, file-based SQLite session with foreign key enforcement."""
    db_file = tmp_path / "test_persistence.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    session_factory = create_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_schema_contains_all_eleven_authoritative_tables():
    """Verify that Base.metadata defines all 11 tables required by 02_Technical_Architecture.md §6."""
    expected_tables = {
        "research_runs",
        "hypotheses",
        "experiments",
        "executions",
        "results",
        "analyses",
        "artifacts",
        "literature_sources",
        "claims",
        "evidence_links",
        "events",
    }
    actual_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(actual_tables), (
        f"Missing schema tables: {expected_tables - actual_tables}"
    )


def test_sqlite_enforces_foreign_key_constraints(test_db_session: Session):
    """Verify that SQLite raises IntegrityError when foreign key integrity is violated."""
    # Attempt to insert an experiment with a non-existent research_run_id
    orphan_experiment = ExperimentModel(
        research_run_id="non_existent_run_id",
        objective="Test orphan constraint",
        specification_json={"type": "test"},
    )
    test_db_session.add(orphan_experiment)
    with pytest.raises(IntegrityError):
        test_db_session.flush()
    test_db_session.rollback()

    # Attempt to insert an execution with a non-existent experiment_id
    orphan_execution = ExecutionModel(
        experiment_id="non_existent_experiment_id",
        command="python run.py",
        code_hash="dummy_hash",
        dataset_hash="dummy_hash",
        configuration_hash="dummy_hash",
    )
    test_db_session.add(orphan_execution)
    with pytest.raises(IntegrityError):
        test_db_session.flush()
    test_db_session.rollback()

    # Attempt to insert a result with a non-existent execution_id
    orphan_result = ResultModel(
        execution_id="non_existent_execution_id",
        metric_name="accuracy",
        metric_value=0.95,
    )
    test_db_session.add(orphan_result)
    with pytest.raises(IntegrityError):
        test_db_session.flush()
    test_db_session.rollback()

    # Attempt to insert an evidence link with a non-existent claim_id
    orphan_link = EvidenceLinkModel(
        claim_id="non_existent_claim_id",
        source_type="result",
        source_id="res_123",
    )
    test_db_session.add(orphan_link)
    with pytest.raises(IntegrityError):
        test_db_session.flush()
    test_db_session.rollback()

    # Attempt to insert an event with a non-existent research_run_id
    orphan_event = EventModel(
        id="evt_orphan",
        research_run_id="non_existent_run_id",
        event_type="agent_action",
        actor_type="system",
        payload_json={"action": "test"},
    )
    test_db_session.add(orphan_event)
    with pytest.raises(IntegrityError):
        test_db_session.flush()
    test_db_session.rollback()


def test_full_provenance_graph_and_cascades(test_db_session: Session):
    """Verify model creation, relationship traversal, and cascade deletion across all 11 tables."""
    # 1. Create ResearchRun
    run = ResearchRunModel(
        title="Learning Rate Optimization",
        research_question="Does cosine annealing improve transformer convergence?",
        status="RUNNING",
        configuration_json={"seed": 42, "lr": 1e-4},
        budget_json={"max_experiments": 10, "max_hours": 2.0},
    )
    test_db_session.add(run)
    test_db_session.flush()
    assert run.id.startswith("run_")

    # 2. Create Hypothesis
    hyp = HypothesisModel(
        research_run_id=run.id,
        statement="Cosine annealing yields lower cross-entropy loss than fixed schedule.",
        rationale="Gradual decay prevents late oscillation.",
        expected_direction="decrease",
        falsification_condition="Validation loss is equal or worse with p > 0.05.",
        status="active",
    )
    test_db_session.add(hyp)
    test_db_session.flush()
    assert hyp.id.startswith("hyp_")

    # 3. Create Experiment
    exp = ExperimentModel(
        research_run_id=run.id,
        hypothesis_id=hyp.id,
        objective="Train transformer with cosine annealing schedule",
        specification_json={"schedule": "cosine", "epochs": 5},
        status="completed",
    )
    test_db_session.add(exp)
    test_db_session.flush()
    assert exp.id.startswith("exp_")

    # 4. Create Execution
    execution = ExecutionModel(
        experiment_id=exp.id,
        status="success",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        command="python train.py --schedule cosine",
        git_commit="abcdef0123456789",
        code_hash="sha256:1111",
        dataset_hash="sha256:2222",
        configuration_hash="sha256:3333",
        seed=42,
        exit_code=0,
        environment_json={"python": "3.11"},
        resource_usage_json={"gpu_memory_mb": 4096},
    )
    test_db_session.add(execution)
    test_db_session.flush()
    assert execution.id.startswith("exec_")

    # 5. Create Result
    result = ResultModel(
        execution_id=execution.id,
        metric_name="val_loss",
        metric_value=0.234,
        metric_unit="cross_entropy",
        result_json={"epoch_losses": [0.5, 0.4, 0.3, 0.25, 0.234]},
    )
    test_db_session.add(result)
    test_db_session.flush()
    assert result.id.startswith("res_")

    # 6. Create Analysis
    analysis = AnalysisModel(
        research_run_id=run.id,
        analysis_type="hypothesis_testing",
        input_result_ids=[result.id],
        method="paired_t_test",
        output_json={"p_value": 0.012, "effect_size": -0.15},
    )
    test_db_session.add(analysis)
    test_db_session.flush()
    assert analysis.id.startswith("an_")

    # 7. Create Artifact
    artifact = ArtifactModel(
        research_run_id=run.id,
        execution_id=execution.id,
        artifact_type="plot",
        path="./data/runs/plots/loss_curve.png",
        content_hash="sha256:plot123",
        size_bytes=1048576,
        metadata_json={"dpi": 300, "format": "png"},
    )
    test_db_session.add(artifact)
    test_db_session.flush()
    assert artifact.id.startswith("art_")

    # 8. Create Literature Source
    literature = LiteratureSourceModel(
        research_run_id=run.id,
        provider="arxiv",
        external_id="1608.03983",
        title="SGDR: Stochastic Gradient Descent with Warm Restarts",
        authors_json=["Ilya Loshchilov", "Frank Hutter"],
        year=2016,
        abstract="We investigate restarts for SGD...",
        url="https://arxiv.org/abs/1608.03983",
        raw_metadata_json={"venue": "ICLR 2017"},
    )
    test_db_session.add(literature)
    test_db_session.flush()
    assert literature.id.startswith("lit_")

    # 9. Create Claim
    claim = ClaimModel(
        research_run_id=run.id,
        text="Cosine annealing statistically significantly reduces validation loss.",
        claim_type="empirical",
        confidence=0.95,
        status="verified",
    )
    test_db_session.add(claim)
    test_db_session.flush()
    assert claim.id.startswith("clm_")

    # 10. Create Evidence Link
    link = EvidenceLinkModel(
        claim_id=claim.id,
        source_type="analysis",
        source_id=analysis.id,
        relationship_type="supported_by",
    )
    test_db_session.add(link)
    test_db_session.flush()
    assert link.id.startswith("lnk_")

    # 11. Create Event
    event = EventModel(
        id="evt_test_123",
        research_run_id=run.id,
        event_type="claim_created",
        timestamp=datetime.now(UTC),
        actor_type="research_agent",
        actor_id="agent_alpha",
        payload_json={"claim_id": claim.id},
    )
    test_db_session.add(event)
    test_db_session.commit()

    # Verify relationship traversal
    refreshed_run = test_db_session.get(ResearchRunModel, run.id)
    assert refreshed_run is not None
    assert len(refreshed_run.hypotheses) == 1
    assert len(refreshed_run.experiments) == 1
    assert len(refreshed_run.analyses) == 1
    assert len(refreshed_run.artifacts) == 1
    assert len(refreshed_run.literature_sources) == 1
    assert len(refreshed_run.claims) == 1
    assert len(refreshed_run.events) == 1
    assert len(refreshed_run.claims[0].evidence_links) == 1

    # Verify Cascade Deletion of ResearchRun drops all associated child records
    test_db_session.delete(refreshed_run)
    test_db_session.commit()

    assert test_db_session.get(HypothesisModel, hyp.id) is None
    assert test_db_session.get(ExperimentModel, exp.id) is None
    assert test_db_session.get(ExecutionModel, execution.id) is None
    assert test_db_session.get(ResultModel, result.id) is None
    assert test_db_session.get(AnalysisModel, analysis.id) is None
    assert test_db_session.get(ArtifactModel, artifact.id) is None
    assert test_db_session.get(LiteratureSourceModel, literature.id) is None
    assert test_db_session.get(ClaimModel, claim.id) is None
    assert test_db_session.get(EvidenceLinkModel, link.id) is None
    assert test_db_session.get(EventModel, event.id) is None


def test_experiment_hierarchy_parent_child(test_db_session: Session):
    """Verify self-referencing parent/child experiment hierarchy with SET NULL on parent deletion."""
    run = ResearchRunModel(
        title="Branching Experiment",
        research_question="Evaluating architecture variations",
    )
    test_db_session.add(run)
    test_db_session.flush()

    parent_exp = ExperimentModel(
        research_run_id=run.id,
        objective="Baseline ResNet-18",
        specification_json={"depth": 18},
    )
    test_db_session.add(parent_exp)
    test_db_session.flush()

    child_exp = ExperimentModel(
        research_run_id=run.id,
        parent_experiment_id=parent_exp.id,
        objective="ResNet-18 with attention heads",
        specification_json={"depth": 18, "attention": True},
    )
    test_db_session.add(child_exp)
    test_db_session.commit()

    assert child_exp.parent_experiment_id == parent_exp.id
    assert parent_exp.child_experiments == [child_exp]

    # Deleting parent experiment should set parent_experiment_id to NULL on child
    test_db_session.delete(parent_exp)
    test_db_session.commit()

    refreshed_child = test_db_session.get(ExperimentModel, child_exp.id)
    assert refreshed_child is not None
    assert refreshed_child.parent_experiment_id is None
