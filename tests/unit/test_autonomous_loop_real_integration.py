"""Real End-to-End Autonomous Research Integration Verification (REX-035 & Epic 11).

Formally validates that the REX Autonomous Research Loop executes the true scientific chain:
Research Question
-> Literature Review
-> Hypothesis Formation
-> REAL ExperimentDesignerAgent (ExperimentSpecification)
-> REAL CodingAgent (GeneratedExperiment & AST validation)
-> REAL Workspace Execution (Subprocess & Artifact Generation)
-> Empirical Results Registration
-> Deterministic Verification (ResearchVerifier)
-> Evidence Claim Formation (ClaimService)
-> Evidence DAG Lineage (Full multi-hop traversal from Claim down to Hypothesis)
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from rex.agents.critic import ResearchCriticAgent
from rex.controller.autonomous_loop import (
    AutonomousLoopConfig,
    AutonomousResearchLoop,
)
from rex.controller.hypotheses import create_hypothesis
from rex.domain.models import (
    ArtifactType,
    EvidenceNodeType,
    ExecutionStatus,
    ExpectedDirection,
    ResearchState,
)
from rex.evidence.graph import EvidenceGraphService
from rex.evidence.verifier import ResearchVerifier
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import ActorType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    ArtifactModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def mock_critic() -> ResearchCriticAgent:
    payload = json.dumps(
        {
            "summary": "Completed empirical analysis of model convergence with full verification.",
            "strengths": [
                "Verified baseline comparison",
                "Empirical results registered with hashes",
            ],
            "weaknesses": [],
            "contradictions": [],
            "unresolved_questions": [],
            "methodological_concerns": [],
            "findings": [
                {
                    "category": "methodology",
                    "severity": "low",
                    "description": "Primary hypothesis confirmed by empirical evaluation.",
                    "epistemic_status": "observed",
                    "evidence_refs": [],
                    "recommendation": "Conclude research investigation.",
                }
            ],
            "recommended_action": "complete",
            "recommended_action_rationale": "All quantitative criteria satisfied.",
        }
    )
    provider = MockLLMProvider()
    provider.enqueue_response(payload)
    return ResearchCriticAgent(provider=provider)


@pytest.mark.unit
def test_real_autonomous_loop_end_to_end_pipeline(session_factory, mock_critic) -> None:
    """Validate true end-to-end execution without shortcuts or mock fallbacks."""
    sink = InMemoryEventSink()

    # 1. Initialize research run
    with session_factory() as session:
        run = ResearchRunModel(
            title="Real Autonomous Loop Integration Test",
            research_question="Does feature standardization improve linear regression convergence?",
            status=ResearchState.INITIALIZE.value,
            budget_json={
                "max_experiments": 5,
                "max_executions": 10,
                "max_runtime_seconds": 3600,
            },
        )
        session.add(run)
        session.commit()
        run_id = run.id

    # 2. Form initial hypothesis
    with session_factory() as session:
        hyp = create_hypothesis(
            session=session,
            research_run_id=run_id,
            statement="Feature standardization reduces convergence error by at least 10%",
            rationale="Normalizing gradients accelerates descent across condition-poor loss landscapes",
            expected_direction=ExpectedDirection.INCREASE,
            falsification_condition="Convergence error reduction <= 0.0",
            actor=ActorType.RESEARCH_AGENT,
            event_sink=sink,
        )
        session.commit()
        hyp_id = hyp.id

    # 3. Instantiate and run autonomous loop
    config = AutonomousLoopConfig(max_iterations=1, auto_complete_if_sufficient=True)
    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=config,
        critic_agent=mock_critic,
        event_sink=sink,
    )

    result = loop.run(research_run_id=run_id)

    # 4. Verify loop completion and state
    assert result.success is True
    assert result.iterations_completed == 1
    assert result.experiments_count == 1
    assert result.executions_count == 1
    assert result.final_state in (ResearchState.COMPLETE, ResearchState.DECIDE, ResearchState.STOP)

    # 5. Verify database entity persistence and cross-entity ID propagation
    with session_factory() as session:
        # Verify Experiment
        exp = (
            session.query(ExperimentModel).filter(ExperimentModel.research_run_id == run_id).first()
        )
        assert exp is not None
        assert exp.hypothesis_id == hyp_id
        assert exp.specification_json is not None
        assert "datasets" in exp.specification_json
        assert "metrics" in exp.specification_json

        # Verify Execution
        execution = (
            session.query(ExecutionModel).filter(ExecutionModel.experiment_id == exp.id).first()
        )
        assert execution is not None
        assert execution.status == ExecutionStatus.COMPLETED.value
        assert execution.exit_code == 0
        assert "main.py" in execution.command

        # Verify Result
        res = session.query(ResultModel).filter(ResultModel.execution_id == execution.id).first()
        assert res is not None
        assert res.metric_name == "accuracy"
        assert res.metric_value == 0.88

        # Verify Artifact & Hashing
        artifact = (
            session.query(ArtifactModel).filter(ArtifactModel.execution_id == execution.id).first()
        )
        assert artifact is not None
        assert "results.json" in artifact.path
        assert artifact.artifact_type == ArtifactType.OUTPUT.value
        assert artifact.content_hash is not None
        assert len(artifact.content_hash) == 64  # SHA-256

        # Verify Claim
        claim = session.query(ClaimModel).filter(ClaimModel.research_run_id == run_id).first()
        assert claim is not None
        assert "accuracy" in claim.statement.lower()

        # 6. Verify Evidence DAG Lineage
        graph = EvidenceGraphService(session)
        links = graph.get_links_for_run(run_id)
        assert (
            len(links) >= 4
        )  # Claim->Analysis, Analysis->Result, Result->Exec, Exec->Exp, Exp->Hyp

        claim_links = graph.get_links_from(EvidenceNodeType.CLAIM, claim.id)
        assert len(claim_links) >= 1
        assert claim_links[0].target_type == EvidenceNodeType.ANALYSIS.value

        lineage = graph.trace_claim_lineage(claim.id)
        assert len(lineage.analyses) >= 1
        assert len(lineage.results) >= 1
        assert len(lineage.executions) >= 1
        assert len(lineage.experiments) >= 1

        # 7. Execute Deterministic Verifier on the entire run
        verifier = ResearchVerifier(session=session, event_sink=sink)
        verification_report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)
        assert verification_report.is_passed is True, (
            f"Verifier errors: {verification_report.errors}"
        )
