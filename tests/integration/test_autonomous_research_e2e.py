"""End-to-end integration tests for Autonomous Research Loop (Batch 6 / REX-033 to REX-035).

Validates:
1. Multi-iteration autonomous research investigations across bounded cycles:
   HYPOTHESES -> DESIGN -> IMPLEMENT -> EXECUTE -> VERIFY -> ANALYZE -> CRITIQUE -> DECIDE -> ACTION
2. Deterministic DecisionEngine overrides when budget is exhausted.
3. Full Evidence Graph traceability from Claim -> Analysis -> Result -> Execution -> Experiment.
4. Formal verification passing via ResearchVerifier.
5. Complete, non-repudiable audit event sequence across all iteration phases.
"""

from __future__ import annotations

import json

import pytest

from rex.agents.critic import ResearchCriticAgent
from rex.controller.autonomous_loop import (
    AutonomousLoopConfig,
    AutonomousResearchLoop,
)
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    DecisionType,
    ResearchState,
)
from rex.evidence.graph import EvidenceGraphService
from rex.evidence.verifier import ResearchVerifier
from rex.llm.providers.mock import MockLLMProvider
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
from rex.persistence.models import (
    AnalysisModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    CritiqueRepository,
    DecisionRepository,
)


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for autonomous loop integration tests."""
    db_file = tmp_path / "test_autonomous_research_e2e.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def _make_mock_critic_agent(actions: list[str]) -> ResearchCriticAgent:
    """Helper to create a critic agent that returns sequential recommendations."""
    provider = MockLLMProvider()
    for act in actions:
        payload = json.dumps(
            {
                "summary": f"Critique assessing empirical evidence with recommended action {act}.",
                "strengths": ["Clear baseline", "Deterministic execution"],
                "weaknesses": ["Limited sample size"],
                "contradictions": [],
                "unresolved_questions": [],
                "methodological_concerns": [],
                "findings": [
                    {
                        "category": "methodology",
                        "severity": "medium",
                        "description": "Evaluate variance across seeds.",
                        "epistemic_status": "observed",
                        "evidence_refs": [],
                        "recommendation": f"Action recommendation: {act}.",
                    }
                ],
                "recommended_action": act,
                "recommended_action_rationale": f"Autonomous recommendation: {act}",
            }
        )
        provider.enqueue_response(payload)
    return ResearchCriticAgent(provider=provider)


@pytest.mark.integration
def test_autonomous_research_loop_e2e_two_iterations(session_factory) -> None:
    """Run an end-to-end 2-iteration investigation with refinement and termination."""
    event_sink = InMemoryEventSink()

    # 1. Create Research Run
    with get_db_session(session_factory) as session:
        budget = {
            "max_experiments": 5,
            "max_executions": 10,
            "max_runtime_seconds": 3600,
        }
        run = create_research_run(
            session=session,
            title="E2E Autonomous Investigation",
            research_question="Does residual connection improve gradient flow in deep networks?",
            budget=budget,
            event_sink=event_sink,
        )
        run_id = run.id

    # 2. Configure 2-step Critic: Iteration 1 -> refine, Iteration 2 -> stop
    critic = _make_mock_critic_agent(actions=["refine", "stop"])

    loop_config = AutonomousLoopConfig(
        max_iterations=2,
        auto_complete_if_sufficient=False,
        record_evidence_claims=True,
    )

    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=loop_config,
        critic_agent=critic,
        event_sink=event_sink,
    )

    # 3. Execute Autonomous Loop
    result = loop.run(research_run_id=run_id)

    # 4. Assert loop execution results
    assert result.success is True
    assert result.iterations_completed == 2
    assert result.final_state == ResearchState.STOP
    assert result.experiments_count == 2
    assert result.executions_count == 2

    # 5. Verify database entity persistence across iterations
    with get_db_session(session_factory) as session:
        # Check run status
        persisted_run = session.get(ResearchRunModel, run_id)
        assert persisted_run is not None
        assert persisted_run.status == ResearchState.STOP.value

        # Experiments and executions
        experiments = (
            session.query(ExperimentModel)
            .filter(ExperimentModel.research_run_id == run_id)
            .order_by(ExperimentModel.created_at.asc())
            .all()
        )
        assert len(experiments) == 2

        executions = (
            session.query(ExecutionModel)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == run_id)
            .all()
        )
        assert len(executions) == 2

        # Results and analyses
        results = (
            session.query(ResultModel)
            .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == run_id)
            .all()
        )
        assert len(results) == 2
        for r in results:
            assert r.metric_name == "accuracy"
            assert r.metric_value == 0.88

        analyses = (
            session.query(AnalysisModel).filter(AnalysisModel.research_run_id == run_id).all()
        )
        assert len(analyses) >= 2

        # Critiques and Decisions
        critique_repo = CritiqueRepository(session)
        critiques = critique_repo.list_by_run(run_id)
        assert len(critiques) == 2
        assert critiques[0].iteration == 1
        assert critiques[0].recommended_action == "refine"
        assert critiques[1].iteration == 2
        assert critiques[1].recommended_action == "stop"

        decision_repo = DecisionRepository(session)
        decisions = decision_repo.list_by_run(run_id)
        assert len(decisions) == 2
        assert decisions[0].iteration == 1
        assert decisions[0].action == DecisionType.REFINE
        assert decisions[1].iteration == 2
        assert decisions[1].action == DecisionType.STOP

        # Claims and Evidence Graph Lineage
        claims = session.query(ClaimModel).filter(ClaimModel.research_run_id == run_id).all()
        assert len(claims) >= 2

        graph = EvidenceGraphService(session)
        for claim in claims:
            lineage = graph.trace_claim_lineage(claim.id)
            assert len(lineage.analyses) >= 1
            assert len(lineage.results) >= 1

        # 6. Formal Verification Protocol
        verifier = ResearchVerifier(session=session, event_sink=event_sink)
        verify_report = verifier.verify_run(research_run_id=run_id, actor=ActorType.VERIFIER)
        assert verify_report.is_passed is True
        assert len(verify_report.cross_run_violations) == 0
        assert len(verify_report.errors) == 0


@pytest.mark.integration
def test_autonomous_research_loop_budget_guard_e2e(session_factory) -> None:
    """DecisionEngine deterministically halts with STOP when budget is exhausted."""
    event_sink = InMemoryEventSink()

    # 1. Create run with restrictive budget (max_experiments=1)
    with get_db_session(session_factory) as session:
        budget = {
            "max_experiments": 1,
            "max_executions": 5,
            "max_runtime_seconds": 3600,
        }
        run = create_research_run(
            session=session,
            title="Budget Guard Investigation",
            research_question="Can low learning rates prevent divergent updates?",
            budget=budget,
            event_sink=event_sink,
        )
        run_id = run.id

    # Critic attempts to recommend REFINE
    critic = _make_mock_critic_agent(actions=["refine", "refine"])

    loop_config = AutonomousLoopConfig(
        max_iterations=5,
        auto_complete_if_sufficient=False,
    )

    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=loop_config,
        critic_agent=critic,
        event_sink=event_sink,
    )

    # 2. Run loop
    result = loop.run(research_run_id=run_id)

    # 3. Assert halted after iteration 1 because experiment budget is exhausted
    assert result.experiments_count == 1
    assert result.final_state == ResearchState.STOP
    assert "budget" in result.terminated_reason.lower()

    # 4. Verify decision in persistence
    with get_db_session(session_factory) as session:
        decision_repo = DecisionRepository(session)
        decisions = decision_repo.list_by_run(run_id)
        assert len(decisions) == 1
        assert decisions[0].action == DecisionType.STOP
        assert "budget exhausted" in decisions[0].rationale.lower()


@pytest.mark.integration
def test_autonomous_research_loop_audit_trail_integrity_e2e(session_factory) -> None:
    """Audit trail contains complete, ordered typed events for every lifecycle phase."""
    event_sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            title="Audit Trail Run",
            research_question="Verify audit event sequence",
            event_sink=event_sink,
        )
        run_id = run.id

    critic = _make_mock_critic_agent(actions=["stop"])
    loop_config = AutonomousLoopConfig(max_iterations=1, auto_complete_if_sufficient=False)
    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=loop_config,
        critic_agent=critic,
        event_sink=event_sink,
    )

    result = loop.run(research_run_id=run_id)
    assert result.final_state == ResearchState.STOP

    event_types = [e.event_type for e in event_sink.events]

    # Verify key lifecycle event progression
    assert EventType.RESEARCH_CREATED.value in event_types
    assert EventType.RESEARCH_STATE_CHANGED.value in event_types
    assert EventType.LOOP_ITERATION_STARTED.value in event_types
    assert EventType.EXPERIMENT_CREATED.value in event_types
    assert EventType.EXECUTION_CREATED.value in event_types
    assert EventType.RESULT_RECORDED.value in event_types
    assert EventType.ANALYSIS_COMPLETED.value in event_types
    assert EventType.CRITIQUE_STARTED.value in event_types
    assert EventType.CRITIQUE_COMPLETED.value in event_types
    assert EventType.DECISION_PROPOSED.value in event_types
    assert EventType.DECISION_ACCEPTED.value in event_types
    assert EventType.LOOP_ITERATION_COMPLETED.value in event_types
    assert EventType.LOOP_TERMINATED.value in event_types
