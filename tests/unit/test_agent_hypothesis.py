"""Unit tests for HypothesisAgent (REX-014)."""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from rex.agents.hypothesis import HypothesisAgent
from rex.controller.state_machine import create_research_run
from rex.domain.models import ExpectedDirection, Hypothesis, ResearchContext
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
from rex.persistence.repositories import HypothesisRepository


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for hypothesis agent tests."""
    db_file = tmp_path / "test_agent_hypothesis.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def sample_research_context():
    return ResearchContext(
        research_run_id="run-hyp-1",
        problem_definition="Study effects of weight decay on generalization in ResNets.",
        task_domain="computer_vision",
        relevant_terminology=("weight_decay", "l2_regularization", "generalization_gap"),
        methodological_approaches=("decoupled_weight_decay", "standard_l2"),
        likely_baselines=("adam_standard", "sgd_momentum"),
        measurable_outcomes=("test_accuracy", "generalization_gap", "final_loss"),
        important_assumptions=("fixed_learning_rate_schedule",),
        unresolved_questions=("optimal_weight_decay_coefficient",),
        experiment_considerations=("cifar10_dataset",),
    )


def test_hypothesis_agent_count_validation(sample_research_context):
    agent = HypothesisAgent(provider=MockLLMProvider())
    with pytest.raises(ValueError, match="Hypothesis count must be at least 1"):
        agent.generate_hypotheses(research_context=sample_research_context, count=0)


def test_hypothesis_agent_generates_in_memory_hypotheses(sample_research_context):
    """Verify HypothesisAgent generates pure domain Hypothesis without DB."""
    provider = MockLLMProvider()
    agent = HypothesisAgent(provider=provider)

    sample_proposals = {
        "hypotheses": [
            {
                "statement": "Decoupled weight decay reduces generalization gap compared to standard L2 regularization.",
                "rationale": "L2 regularization couples weight decay directly to gradient magnitude, causing smaller updates on frequently updated weights.",
                "expected_direction": "decrease",
                "falsification_condition": "Generalization gap on CIFAR-10 with decoupled weight decay is >= generalization gap with standard L2 (p > 0.05).",
            }
        ]
    }
    provider.enqueue_response(json.dumps(sample_proposals))

    hypotheses = agent.generate_hypotheses(sample_research_context, count=1)
    assert len(hypotheses) == 1
    hyp = hypotheses[0]
    assert isinstance(hyp, Hypothesis)
    assert hyp.research_run_id == sample_research_context.research_run_id
    assert "Decoupled weight decay" in hyp.statement
    assert hyp.expected_direction == ExpectedDirection.DECREASE
    assert "CIFAR-10" in hyp.falsification_condition


def test_hypothesis_agent_generates_multiple_hypotheses(sample_research_context):
    """Verify HypothesisAgent can generate multiple distinct hypotheses."""
    provider = MockLLMProvider()
    agent = HypothesisAgent(provider=provider)

    sample_proposals = {
        "hypotheses": [
            {
                "statement": "Hypothesis 1: Higher weight decay increases sparsity.",
                "rationale": "Stronger regularization suppresses weights.",
                "expected_direction": "increase",
                "falsification_condition": "Sparsity percentage does not increase monotonically.",
            },
            {
                "statement": "Hypothesis 2: Moderate weight decay improves test accuracy.",
                "rationale": "Prevents overfitting without underfitting.",
                "expected_direction": "increase",
                "falsification_condition": "Test accuracy does not exceed unregularized baseline.",
            },
        ]
    }
    provider.enqueue_response(json.dumps(sample_proposals))

    hypotheses = agent.generate_hypotheses(sample_research_context, count=2)
    assert len(hypotheses) == 2
    assert hypotheses[0].statement.startswith("Hypothesis 1")
    assert hypotheses[1].statement.startswith("Hypothesis 2")


def test_hypothesis_agent_persists_via_controller_and_emits_event(
    session_factory: sessionmaker,
    sample_research_context,
):
    """Verify HypothesisAgent persists entities via create_hypothesis controller and emits audit event."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()
    agent = HypothesisAgent(provider=provider, event_sink=sink)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Study effects of weight decay on generalization in ResNets.",
        )
        run_id = run.id

    context = ResearchContext(
        research_run_id=run_id,
        problem_definition="Study effects of weight decay.",
        task_domain="cv",
    )

    sample_proposals = {
        "hypotheses": [
            {
                "statement": "Decoupled weight decay improves top-1 accuracy on CIFAR-100.",
                "rationale": "Prevents excessive gradient dampening.",
                "expected_direction": "increase",
                "falsification_condition": "Top-1 accuracy with decoupled weight decay <= standard Adam.",
            }
        ]
    }
    provider.enqueue_response(json.dumps(sample_proposals))

    with get_db_session(session_factory) as session:
        hypotheses = agent.generate_hypotheses(
            research_context=context,
            count=1,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )
        assert len(hypotheses) == 1
        persisted_id = hypotheses[0].id

    # Verify persisted in DB
    with get_db_session(session_factory) as session:
        db_hyp = HypothesisRepository(session).get_by_id(persisted_id)
        assert db_hyp is not None
        assert db_hyp.research_run_id == run_id
        assert db_hyp.statement.startswith("Decoupled weight decay")

    # Verify event sink has hypothesis_created event
    hyp_events = [e for e in sink.events if e.event_type == EventType.HYPOTHESIS_CREATED]
    assert len(hyp_events) == 1
    assert hyp_events[0].payload["hypothesis_id"] == persisted_id
