"""Unit tests for ExperimentDesignerAgent (REX-015)."""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from rex.agents.experiment_designer import ExperimentDesignerAgent
from rex.controller.hypotheses import create_hypothesis
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ExpectedDirection,
    Experiment,
    ExperimentSpecification,
    Hypothesis,
    MetricDirection,
    ResearchContext,
)
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
from rex.persistence.repositories import ExperimentRepository


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for experiment designer tests."""
    db_file = tmp_path / "test_agent_experiment_designer.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def sample_research_context():
    return ResearchContext(
        research_run_id="run-exp-1",
        problem_definition="Evaluate gradient clipping thresholds for transformer training stability.",
        task_domain="natural_language_processing",
        likely_baselines=("unclipped_adamw",),
        measurable_outcomes=("validation_perplexity", "max_grad_norm"),
        experiment_considerations=("fp16_training",),
    )


@pytest.fixture
def sample_hypothesis():
    return Hypothesis(
        research_run_id="run-exp-1",
        statement="Setting gradient clipping threshold to 1.0 reduces validation perplexity variance across seeds.",
        rationale="Prevents gradient spikes while retaining update direction.",
        expected_direction=ExpectedDirection.DECREASE,
        falsification_condition="Validation perplexity variance with threshold 1.0 >= unclipped baseline.",
    )


def test_cross_run_hypothesis_integrity_enforcement(sample_research_context):
    """Verify ExperimentDesignerAgent rejects a hypothesis belonging to a different research run."""
    agent = ExperimentDesignerAgent(provider=MockLLMProvider())
    foreign_hypothesis = Hypothesis(
        research_run_id="foreign-run-999",
        statement="Foreign hypothesis claim.",
        rationale="Some rationale.",
        expected_direction=ExpectedDirection.INCREASE,
        falsification_condition="Some condition.",
    )

    with pytest.raises(ValueError, match="does not match research context run"):
        agent.design_experiment(
            research_question="Does clipping help?",
            research_context=sample_research_context,
            hypothesis=foreign_hypothesis,
        )


def test_experiment_designer_generates_valid_specification(
    sample_research_context, sample_hypothesis
):
    """Verify ExperimentDesignerAgent produces a complete, strongly-typed ExperimentSpecification."""
    provider = MockLLMProvider()
    agent = ExperimentDesignerAgent(provider=provider)

    sample_proposal = {
        "name": "clip_norm_1_0_study",
        "description": "Compares gradient clipping threshold 1.0 against unclipped baseline across 3 seeds.",
        "method": "transformer_clipping_comparison",
        "variables": {"clip_threshold": 1.0},
        "controls": {"learning_rate": 0.001, "batch_size": 32, "architecture": "transformer_small"},
        "baseline": {"clip_threshold": None, "name": "unclipped_baseline"},
        "datasets": [{"name": "wikitext-2", "split": "validation"}],
        "metrics": [
            {"name": "validation_perplexity", "direction": "minimize"},
            {"name": "max_grad_norm", "direction": "minimize"},
        ],
        "parameters": {"optimizer": "AdamW", "warmup_steps": 100},
        "seeds": [42, 123, 999],
        "repetitions": 1,
        "analysis_methods": ["welch_t_test", "standard_deviation"],
        "success_criteria": "Perplexity variance across 3 seeds is strictly lower than baseline with p < 0.05.",
        "falsification_criteria": "Perplexity variance is greater than or equal to baseline.",
    }
    provider.enqueue_response(json.dumps(sample_proposal))

    spec, exp = agent.design_experiment(
        research_question="Does clipping help?",
        research_context=sample_research_context,
        hypothesis=sample_hypothesis,
    )

    assert isinstance(spec, ExperimentSpecification)
    assert exp is None  # No session provided
    assert spec.name == "clip_norm_1_0_study"
    assert spec.method == "transformer_clipping_comparison"
    assert spec.variables["clip_threshold"] == 1.0
    assert spec.seeds == (42, 123, 999)
    assert len(spec.metrics) == 2
    assert spec.metrics[0].name == "validation_perplexity"
    assert spec.metrics[0].direction == MetricDirection.MINIMIZE
    assert spec.success_criteria.startswith("Perplexity variance")


def test_experiment_designer_persists_via_controller_and_emits_event(
    session_factory: sessionmaker,
):
    """Verify ExperimentDesignerAgent persists Experiment entity and emits EXPERIMENT_CREATED event."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()
    agent = ExperimentDesignerAgent(provider=provider, event_sink=sink)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Does clipping help?",
        )
        run_id = run.id
        hyp = create_hypothesis(
            session=session,
            research_run_id=run_id,
            statement="Clipping helps.",
            rationale="Limits gradient explosions.",
            expected_direction=ExpectedDirection.DECREASE,
            falsification_condition="Loss is higher with clipping.",
            actor=ActorType.RESEARCH_AGENT,
        )

    context = ResearchContext(
        research_run_id=run_id,
        problem_definition="Study clipping.",
        task_domain="nlp",
    )

    sample_proposal = {
        "name": "clipping_persistence_test",
        "description": "Verify DB persistence via controller.",
        "method": "clipping_eval",
        "variables": {"clip": 1.0},
        "controls": {"lr": 1e-3},
        "baseline": {"clip": None},
        "datasets": [{"name": "mock_data"}],
        "metrics": [{"name": "loss", "direction": "minimize"}],
        "parameters": {},
        "seeds": [42],
        "repetitions": 1,
        "analysis_methods": [],
        "success_criteria": "Loss < 2.0",
        "falsification_criteria": "Loss >= 2.0",
    }
    provider.enqueue_response(json.dumps(sample_proposal))

    with get_db_session(session_factory) as session:
        spec, exp = agent.design_experiment(
            research_question="Does clipping help?",
            research_context=context,
            hypothesis=hyp,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )
        assert isinstance(spec, ExperimentSpecification)
        assert isinstance(exp, Experiment)
        exp_id = exp.id

    # Verify persisted in database
    with get_db_session(session_factory) as session:
        db_exp = ExperimentRepository(session).get_by_id(exp_id)
        assert db_exp is not None
        assert db_exp.research_run_id == run_id
        assert db_exp.specification_json["name"] == "clipping_persistence_test"

    # Verify event sink has experiment_created event
    exp_events = [e for e in sink.events if e.event_type == EventType.EXPERIMENT_CREATED]
    assert len(exp_events) == 1
    assert exp_events[0].experiment_id == exp_id
