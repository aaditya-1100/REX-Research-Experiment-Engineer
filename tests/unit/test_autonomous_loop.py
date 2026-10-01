"""Unit tests for Autonomous Research Loop (REX-035).

Tests:
- Bounded loop iteration: terminates strictly at max_iterations
- Bounded budget: halts when resource budgets are exhausted
- State resilience: failed experiments do not crash or corrupt the research state
- Optimistic concurrency protection with StaleStateError
- Complete audit trail of typed events across iterations
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
from rex.controller.exceptions import StaleStateError
from rex.domain.models import (
    ExecutionStatus,
    ResearchState,
)
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import ActorType, EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    ExecutionModel,
    ResearchRunModel,
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def base_run(session_factory) -> str:
    with session_factory() as session:
        run = ResearchRunModel(
            title="Autonomous Loop Test Run",
            research_question="Can dropout prevent overfitting in shallow networks?",
            status=ResearchState.INITIALIZE.value,
            budget_json={"max_experiments": 5, "max_executions": 10, "max_runtime_seconds": 3600},
        )
        session.add(run)
        session.commit()
        return run.id


def _make_mock_critic(recommended_action: str = "refine") -> ResearchCriticAgent:
    """Helper to create a critic agent with a deterministic mock response."""
    payload = json.dumps(
        {
            "summary": "Completed empirical analysis of neural network training.",
            "strengths": ["Clear baseline setup"],
            "weaknesses": ["Single dataset tested"],
            "contradictions": [],
            "unresolved_questions": [],
            "methodological_concerns": [],
            "findings": [
                {
                    "category": "methodology",
                    "severity": "medium",
                    "description": "Evaluate on secondary benchmark.",
                    "epistemic_status": "observed",
                    "evidence_refs": [],
                    "recommendation": "Refine experiment on secondary dataset.",
                }
            ],
            "recommended_action": recommended_action,
            "recommended_action_rationale": "Proceeding with next research stage.",
        }
    )
    provider = MockLLMProvider()
    provider.enqueue_response(payload)
    # Queue extra identical responses for subsequent iterations
    for _ in range(10):
        provider.enqueue_response(payload)
    return ResearchCriticAgent(provider=provider)


@pytest.mark.unit
def test_loop_stops_at_max_iterations(session_factory, base_run: str) -> None:
    """Loop cannot run indefinitely; must halt when max_iterations is reached."""
    sink = InMemoryEventSink()
    config = AutonomousLoopConfig(max_iterations=2, auto_complete_if_sufficient=False)
    critic = _make_mock_critic(recommended_action="refine")

    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=config,
        critic_agent=critic,
        event_sink=sink,
    )

    result = loop.run(research_run_id=base_run)

    assert result.iterations_completed == 2
    assert result.experiments_count >= 1
    assert "max iterations" in result.terminated_reason.lower()
    assert result.final_state == ResearchState.STOP

    # Check loop terminated event
    term_events = sink.get_by_type(EventType.LOOP_TERMINATED)
    assert len(term_events) == 1
    assert term_events[0].payload["final_state"] == ResearchState.STOP.value


@pytest.mark.unit
def test_loop_stops_on_budget_exhaustion(session_factory) -> None:
    """Loop halts cleanly when configured budget limits are breached."""
    with session_factory() as session:
        run = ResearchRunModel(
            title="Budget Test Run",
            research_question="Test budget limits",
            status=ResearchState.INITIALIZE.value,
            budget_json={"max_experiments": 1, "max_executions": 5, "max_runtime_seconds": 3600},
        )
        session.add(run)
        session.commit()
        run_id = run.id

    sink = InMemoryEventSink()
    config = AutonomousLoopConfig(max_iterations=5, auto_complete_if_sufficient=False)
    critic = _make_mock_critic(recommended_action="refine")

    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=config,
        critic_agent=critic,
        event_sink=sink,
    )

    result = loop.run(research_run_id=run_id)

    # First iteration creates 1 experiment, exhausting budget for iteration 2
    assert result.experiments_count == 1
    assert result.final_state == ResearchState.STOP
    assert "budget" in result.terminated_reason.lower()


@pytest.mark.unit
def test_loop_failed_experiment_does_not_corrupt_state(session_factory, base_run: str) -> None:
    """Execution failures do not corrupt the research run state machine or evidence plane."""
    sink = InMemoryEventSink()
    config = AutonomousLoopConfig(max_iterations=1, auto_complete_if_sufficient=False)

    class FailingLoop(AutonomousResearchLoop):
        def _step_execute(self, research_run_id, experiment_id, code_files, command, actor):
            with self.session_factory() as session:
                exec_entity = ExecutionModel(
                    experiment_id=experiment_id,
                    status=ExecutionStatus.FAILED.value,
                    exit_code=1,
                )
                session.add(exec_entity)
                session.commit()
                exec_id = exec_entity.id

            # Transition EXECUTE -> VERIFY despite execution failure
            self._safe_transition(
                research_run_id=research_run_id,
                expected_state=ResearchState.EXECUTE,
                target_state=ResearchState.VERIFY,
                actor=actor,
                reason="Execution failed with exit code 1; continuing to verify.",
            )
            return exec_id, True

    critic = _make_mock_critic(recommended_action="refine")
    loop = FailingLoop(
        session_factory=session_factory,
        config=config,
        critic_agent=critic,
        event_sink=sink,
    )

    iter_result = loop.run_iteration(research_run_id=base_run, iteration=1)

    # Research run state successfully survived and progressed
    assert iter_result.execution_id is not None
    assert iter_result.state_after in (
        ResearchState.REFINE,
        ResearchState.DESIGN,
        ResearchState.STOP,
    )

    with session_factory() as session:
        run = session.get(ResearchRunModel, base_run)
        assert run.status == iter_result.state_after.value


@pytest.mark.unit
def test_loop_concurrency_race_raises_stale_state(session_factory, base_run: str) -> None:
    """State machine concurrency check raises StaleStateError if expected_state is stale."""
    sink = InMemoryEventSink()
    config = AutonomousLoopConfig(max_iterations=1)
    loop = AutonomousResearchLoop(session_factory=session_factory, config=config, event_sink=sink)

    # Intentionally pass an incorrect expected_state
    with pytest.raises(StaleStateError):
        loop._safe_transition(
            research_run_id=base_run,
            expected_state=ResearchState.EXECUTE,  # Actually INITIALIZE
            target_state=ResearchState.VERIFY,
            actor=ActorType.CONTROLLER,
        )


@pytest.mark.unit
def test_loop_audit_events_sequence(session_factory, base_run: str) -> None:
    """Verify that all expected lifecycle events are emitted across iteration phases."""
    sink = InMemoryEventSink()
    config = AutonomousLoopConfig(max_iterations=1, auto_complete_if_sufficient=False)
    critic = _make_mock_critic(recommended_action="stop")

    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=config,
        critic_agent=critic,
        event_sink=sink,
    )

    result = loop.run(research_run_id=base_run)
    assert result.final_state == ResearchState.STOP

    recorded_types = [e.event_type for e in sink.events]
    assert EventType.LOOP_ITERATION_STARTED.value in recorded_types
    assert EventType.AUTONOMOUS_ACTION_STARTED.value in recorded_types
    assert EventType.AUTONOMOUS_ACTION_COMPLETED.value in recorded_types
    assert EventType.DECISION_PROPOSED.value in recorded_types
    assert EventType.LOOP_ITERATION_COMPLETED.value in recorded_types
    assert EventType.LOOP_TERMINATED.value in recorded_types
