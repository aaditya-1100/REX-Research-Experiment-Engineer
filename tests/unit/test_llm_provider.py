"""Unit tests for LLM provider interface, models, mock provider, retries, and accounting (REX-012)."""

import pytest
from sqlalchemy.orm import sessionmaker

from rex.controller.budgets import ResearchBudget
from rex.controller.exceptions import BudgetExceededError
from rex.controller.state_machine import create_research_run
from rex.llm.accounting import check_llm_budget, record_llm_usage_event
from rex.llm.base import execute_with_retry
from rex.llm.exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnsupportedProviderError,
)
from rex.llm.models import LLMRequest, LLMResponse
from rex.llm.providers.factory import get_llm_provider
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
    """Isolated SQLite database for provider accounting tests."""
    db_file = tmp_path / "test_llm_provider.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_llm_request_and_response_models():
    """Verify request and response validation, immutability, and defaults."""
    req = LLMRequest(
        system_prompt="You are a research assistant.",
        user_prompt="Analyze this dataset.",
        temperature=0.5,
        max_tokens=1000,
        research_run_id="run-123",
        agent_name="TestAgent",
    )
    assert req.system_prompt == "You are a research assistant."
    assert req.temperature == 0.5
    assert req.max_tokens == 1000
    assert req.research_run_id == "run-123"

    res = LLMResponse(
        text='{"key": "value"}',
        provider="mock",
        model="mock-model",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        estimated_cost=0.0005,
        latency_seconds=0.1,
    )
    assert res.text == '{"key": "value"}'
    assert res.total_tokens == 30
    assert res.estimated_cost == 0.0005


def test_mock_llm_provider_programmable_responses():
    """Verify MockLLMProvider yields programmed responses and records calls."""
    provider = MockLLMProvider()
    provider.enqueue_response('{"answer": 42}')

    req = LLMRequest(user_prompt="What is the answer?")
    res = provider.generate(req)

    assert res.text == '{"answer": 42}'
    assert provider.call_count == 1
    assert len(provider.history) == 1
    assert provider.history[0][0].user_prompt == "What is the answer?"


def test_mock_llm_provider_latency_and_usage():
    """Verify MockLLMProvider latency simulation and custom usage parameters."""
    provider = MockLLMProvider(
        default_latency=0.01,
        default_prompt_tokens=50,
        default_completion_tokens=25,
        cost_per_1k_tokens=0.01,
    )
    req = LLMRequest(user_prompt="Calculate something.")
    res = provider.generate(req)

    assert res.latency_seconds >= 0.009
    assert res.input_tokens == 50
    assert res.output_tokens == 25
    assert res.total_tokens == 75
    assert res.estimated_cost is not None and res.estimated_cost > 0


def test_mock_llm_provider_error_injection():
    """Verify MockLLMProvider can inject synthetic errors."""
    provider = MockLLMProvider()
    provider.enqueue_error(LLMTimeoutError("Request timed out after 30s"))

    req = LLMRequest(user_prompt="Hello")
    with pytest.raises(LLMTimeoutError, match="timed out"):
        provider.generate(req)


def test_execute_with_retry_succeeds_after_transient_failure():
    """Verify execute_with_retry recovers after transient errors within max_retries."""
    provider = MockLLMProvider()
    provider.enqueue_error(LLMTimeoutError("First attempt timed out"))
    provider.enqueue_response("Success on second attempt")

    req = LLMRequest(user_prompt="Test retry")
    res = execute_with_retry(
        provider=provider,
        request=req,
        max_retries=2,
        base_backoff_seconds=0.01,
    )

    assert res.text == "Success on second attempt"
    assert provider.call_count == 2


def test_execute_with_retry_fails_immediately_on_non_transient_error():
    """Verify non-transient error (e.g. 401 Unauthorized) is not retried."""
    provider = MockLLMProvider()
    provider.enqueue_error(LLMProviderError("Invalid API key", status_code=401))

    req = LLMRequest(user_prompt="Unauthorized call")
    with pytest.raises(LLMProviderError) as exc_info:
        execute_with_retry(
            provider=provider,
            request=req,
            max_retries=3,
            base_backoff_seconds=0.01,
        )
    assert exc_info.value.status_code == 401
    assert provider.call_count == 1  # No retry performed


def test_execute_with_retry_exhausts_retries():
    """Verify exception is raised when retries are exhausted."""
    provider = MockLLMProvider()
    provider.enqueue_error(LLMTimeoutError("Timeout 1"))
    provider.enqueue_error(LLMTimeoutError("Timeout 2"))
    provider.enqueue_error(LLMTimeoutError("Timeout 3"))

    req = LLMRequest(user_prompt="Exhaust retries")
    with pytest.raises(LLMTimeoutError, match="Timeout 3"):
        execute_with_retry(
            provider=provider,
            request=req,
            max_retries=2,
            base_backoff_seconds=0.01,
        )
    assert provider.call_count == 3


def test_provider_factory():
    """Verify get_llm_provider retrieves correct provider and handles unknown providers."""
    mock_prov = get_llm_provider("mock")
    assert isinstance(mock_prov, MockLLMProvider)

    with pytest.raises(
        LLMUnsupportedProviderError, match="Unsupported or unrecognized LLM provider"
    ):
        get_llm_provider("unknown_vendor")


def test_llm_budget_enforcement_and_accounting(session_factory: sessionmaker):
    """Verify pre-flight check_llm_budget raises BudgetExceededError when limit reached."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        budget = ResearchBudget(max_llm_calls=2).model_dump()
        run = create_research_run(
            session=session,
            research_question="Testing LLM budget enforcement",
            budget=budget,
        )
        run_id = run.id

    # 1. First call check passes
    with get_db_session(session_factory) as session:
        check_llm_budget(session, run_id, ActorType.RESEARCH_AGENT, sink)
        # Record first usage
        req = LLMRequest(user_prompt="Prompt 1", research_run_id=run_id, agent_name="AgentA")
        res = LLMResponse(
            text="Answer 1",
            provider="mock",
            model="mock-model",
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            estimated_cost=0.01,
        )
        record_llm_usage_event(session, req, res, ActorType.RESEARCH_AGENT, sink)

    # 2. Second call check passes
    with get_db_session(session_factory) as session:
        check_llm_budget(session, run_id, ActorType.RESEARCH_AGENT, sink)
        req = LLMRequest(user_prompt="Prompt 2", research_run_id=run_id, agent_name="AgentA")
        res = LLMResponse(
            text="Answer 2",
            provider="mock",
            model="mock-model",
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            estimated_cost=0.01,
        )
        record_llm_usage_event(session, req, res, ActorType.RESEARCH_AGENT, sink)

    # 3. Third call check MUST fail pre-flight
    with (
        get_db_session(session_factory) as session,
        pytest.raises(BudgetExceededError, match="Research budget exceeded"),
    ):
        check_llm_budget(session, run_id, ActorType.RESEARCH_AGENT, sink)

    # Verify event sink has recorded the usage events
    usage_events = [e for e in sink.events if e.payload.get("action") == "llm_call"]
    assert len(usage_events) == 2
    assert usage_events[0].payload["cost"] == 0.01
    assert (
        usage_events[0].payload["total_tokens"] == "[REDACTED]"
    )  # Secret sanitizer redacts token fields
