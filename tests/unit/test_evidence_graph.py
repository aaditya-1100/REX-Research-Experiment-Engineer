"""Unit tests for REX-023 Evidence Graph Subsystem."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.domain.models import EvidenceNodeType, EvidenceRelationType
from rex.evidence.graph import (
    CrossRunEvidenceError,
    EvidenceCycleError,
    EvidenceGraphService,
    NodeNotFoundError,
)
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def populated_run(db_session: Session) -> tuple[str, str, str, str, str]:
    """Helper to populate a run with full empirical chain."""
    run = ResearchRunModel(title="Test Run", research_question="Does X cause Y?")
    db_session.add(run)
    db_session.flush()

    exp = ExperimentModel(research_run_id=run.id, title="Exp 1")
    db_session.add(exp)
    db_session.flush()

    exec_model = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        code_hash="code123",
        dataset_hash="data123",
        configuration_hash="cfg123",
    )
    db_session.add(exec_model)
    db_session.flush()

    res = ResultModel(
        execution_id=exec_model.id,
        metric_name="accuracy",
        metric_value=0.95,
    )
    db_session.add(res)
    db_session.flush()

    art = ArtifactModel(
        research_run_id=run.id,
        execution_id=exec_model.id,
        artifact_type="metric",
        path="metrics.json",
        content_hash="hash999",
    )
    db_session.add(art)
    db_session.flush()

    an = AnalysisModel(
        research_run_id=run.id,
        analysis_type="descriptive",
        method="sample_summary_statistics",
        input_result_ids=[res.id],
        output_json={"mean": 0.95, "sample_size": 1},
    )
    db_session.add(an)
    db_session.flush()

    claim = ClaimModel(
        research_run_id=run.id,
        statement="Treatment achieves 95% accuracy.",
        claim_type="observation",
        status="draft",
    )
    db_session.add(claim)
    db_session.commit()

    return run.id, claim.id, an.id, res.id, exec_model.id


@pytest.mark.unit
def test_create_link_valid(
    db_session: Session, populated_run: tuple[str, str, str, str, str]
) -> None:
    run_id, claim_id, an_id, _res_id, _exec_id = populated_run
    graph = EvidenceGraphService(db_session)

    # Link analysis -> claim
    link = graph.create_link(
        source_type=EvidenceNodeType.ANALYSIS,
        source_id=an_id,
        target_type=EvidenceNodeType.CLAIM,
        target_id=claim_id,
        relationship_type=EvidenceRelationType.SUPPORTED_BY,
    )

    assert link.source_id == an_id
    assert link.target_id == claim_id
    assert link.relationship_type == EvidenceRelationType.SUPPORTED_BY
    assert link.research_run_id == run_id

    # Check query
    inbound = graph.get_links_to(EvidenceNodeType.CLAIM, claim_id)
    assert len(inbound) == 1
    assert inbound[0].id == link.id


@pytest.mark.unit
def test_create_link_node_not_found(
    db_session: Session, populated_run: tuple[str, str, str, str, str]
) -> None:
    _, claim_id, _, _, _ = populated_run
    graph = EvidenceGraphService(db_session)

    with pytest.raises(NodeNotFoundError):
        graph.create_link(
            source_type=EvidenceNodeType.ANALYSIS,
            source_id="an-nonexistent",
            target_type=EvidenceNodeType.CLAIM,
            target_id=claim_id,
        )


@pytest.mark.unit
def test_create_link_cross_run_rejected(
    db_session: Session, populated_run: tuple[str, str, str, str, str]
) -> None:
    _run1_id, _claim_id, an_id, _, _ = populated_run
    graph = EvidenceGraphService(db_session)

    # Create run 2 and claim 2
    run2 = ResearchRunModel(title="Run 2", research_question="Another question")
    db_session.add(run2)
    db_session.flush()

    claim2 = ClaimModel(research_run_id=run2.id, statement="Unrelated claim")
    db_session.add(claim2)
    db_session.commit()

    # Try linking an_id (from run 1) to claim2 (from run 2)
    with pytest.raises(CrossRunEvidenceError):
        graph.create_link(
            source_type=EvidenceNodeType.ANALYSIS,
            source_id=an_id,
            target_type=EvidenceNodeType.CLAIM,
            target_id=claim2.id,
        )


@pytest.mark.unit
def test_cycle_detection(
    db_session: Session, populated_run: tuple[str, str, str, str, str]
) -> None:
    run_id, claim_id, _an_id, _, _ = populated_run
    graph = EvidenceGraphService(db_session)

    # Self-referential link
    with pytest.raises(EvidenceCycleError):
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=claim_id,
            target_type=EvidenceNodeType.CLAIM,
            target_id=claim_id,
        )

    # Multi-hop cycle: A -> B then B -> A
    claim2 = ClaimModel(research_run_id=run_id, statement="Claim 2")
    db_session.add(claim2)
    db_session.commit()

    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim_id,
        target_type=EvidenceNodeType.CLAIM,
        target_id=claim2.id,
        relationship_type=EvidenceRelationType.REFINES,
    )

    # Attempting claim2 -> claim_id creates cycle
    with pytest.raises(EvidenceCycleError):
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=claim2.id,
            target_type=EvidenceNodeType.CLAIM,
            target_id=claim_id,
            relationship_type=EvidenceRelationType.REFINES,
        )


@pytest.mark.unit
def test_trace_claim_lineage_complete(
    db_session: Session, populated_run: tuple[str, str, str, str, str]
) -> None:
    _, claim_id, an_id, res_id, exec_id = populated_run
    graph = EvidenceGraphService(db_session)

    # Link analysis -> claim
    graph.create_link(
        source_type=EvidenceNodeType.ANALYSIS,
        source_id=an_id,
        target_type=EvidenceNodeType.CLAIM,
        target_id=claim_id,
    )

    trace = graph.trace_claim_lineage(claim_id)
    assert trace.is_complete is True
    assert len(trace.gaps) == 0
    assert len(trace.analyses) == 1
    assert trace.analyses[0].id == an_id
    assert len(trace.results) == 1
    assert trace.results[0].id == res_id
    assert len(trace.executions) == 1
    assert trace.executions[0].id == exec_id
    assert len(trace.experiments) == 1


@pytest.mark.unit
def test_trace_claim_lineage_broken(
    db_session: Session, populated_run: tuple[str, str, str, str, str]
) -> None:
    _, claim_id, _, _, _ = populated_run
    graph = EvidenceGraphService(db_session)

    # Trace without any evidence links
    trace = graph.trace_claim_lineage(claim_id)
    assert trace.is_complete is False
    assert len(trace.gaps) > 0
    assert any("no supporting evidence" in g for g in trace.gaps)
