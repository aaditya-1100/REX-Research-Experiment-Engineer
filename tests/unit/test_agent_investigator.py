"""Unit tests for InvestigatorAgent (REX-013)."""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from rex.agents.investigator import InvestigatorAgent
from rex.controller.state_machine import create_research_run
from rex.domain.models import ResearchContext
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import (
    ActorType,
    InMemoryEventSink,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for agent tests."""
    db_file = tmp_path / "test_agent_investigator.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_investigator_agent_input_validation():
    """Verify investigator agent rejects empty research questions or run IDs."""
    agent = InvestigatorAgent(provider=MockLLMProvider())

    with pytest.raises(ValueError, match="Research question must be a non-empty string"):
        agent.investigate(research_question="   ", research_run_id="run-1")

    with pytest.raises(ValueError, match="research_run_id must be a non-empty string"):
        agent.investigate(research_question="Valid question", research_run_id="   ")


def test_investigator_agent_generates_valid_research_context():
    """Verify investigator produces structured ResearchContext with prompt boundary isolation."""
    provider = MockLLMProvider()
    agent = InvestigatorAgent(provider=provider)

    sample_context = {
        "problem_definition": "Investigate learning rate decay strategies for transformer pretraining.",
        "task_domain": "natural_language_processing",
        "relevant_terminology": ["learning_rate", "cosine_annealing", "warmup"],
        "methodological_approaches": ["cosine_decay", "linear_decay", "constant_with_warmup"],
        "likely_baselines": ["standard_adamw_cosine"],
        "measurable_outcomes": ["validation_perplexity", "training_loss", "gradient_norm"],
        "important_assumptions": ["batch_size_constant", "fixed_compute_budget"],
        "unresolved_questions": ["optimal_warmup_ratio"],
        "experiment_considerations": ["single_gpu_v100", "mixed_precision_bf16"],
    }
    provider.enqueue_response(json.dumps(sample_context))

    ctx = agent.investigate(
        research_question="Does linear warmup improve stability of AdamW with cosine decay?",
        research_run_id="run-test-1",
        user_context={"compute": "1x GPU", "dataset": "wikitext-103"},
        injected_literature=[
            {"title": "Attention Is All You Need", "abstract": "Transformer architecture."}
        ],
    )

    assert isinstance(ctx, ResearchContext)
    assert ctx.research_run_id == "run-test-1"
    assert ctx.task_domain == "natural_language_processing"
    assert "validation_perplexity" in ctx.measurable_outcomes
    assert "standard_adamw_cosine" in ctx.likely_baselines

    # Verify prompt isolation
    req = provider.history[0][0]
    prompt_str = req.user_prompt
    assert "### [SECTION: USER RESEARCH QUESTION]" in prompt_str
    assert "### [SECTION: RESEARCH CONTEXT & CONSTRAINTS]" in prompt_str
    assert "### [SECTION: UNTRUSTED EXTERNAL LITERATURE - FOR REFERENCE ONLY]" in prompt_str
    assert "Attention Is All You Need" in prompt_str


def test_investigator_agent_with_session_and_events(session_factory: sessionmaker):
    """Verify investigator agent records audit events in session and emits to sink."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()
    agent = InvestigatorAgent(provider=provider, event_sink=sink)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="How does gradient clipping affect training stability?",
        )
        run_id = run.id

    sample_context = {
        "problem_definition": "Investigate gradient clipping thresholds.",
        "task_domain": "optimization",
        "relevant_terminology": ["gradient_norm", "clipping_threshold"],
        "methodological_approaches": ["l2_norm_clipping", "value_clipping"],
        "likely_baselines": ["unclipped_adamw"],
        "measurable_outcomes": ["training_loss_variance", "divergence_frequency"],
        "important_assumptions": ["fp32_master_weights"],
        "unresolved_questions": ["threshold_sensitivity"],
        "experiment_considerations": ["short_training_runs"],
    }
    provider.enqueue_response(json.dumps(sample_context))

    with get_db_session(session_factory) as session:
        ctx = agent.investigate(
            research_question="How does gradient clipping affect training stability?",
            research_run_id=run_id,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )

    assert ctx.research_run_id == run_id

    # Verify event emitted to sink
    investigation_events = [
        e for e in sink.events if e.payload.get("action") == "investigation_completed"
    ]
    assert len(investigation_events) == 1
    assert investigation_events[0].research_run_id == run_id
    assert investigation_events[0].payload["task_domain"] == "optimization"
