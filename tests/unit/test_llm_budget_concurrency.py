"""REX LLM Budget Concurrency and Atomicity Audit Test Suite.

Verifies that the LLM budget system strictly prevents oversubscription under
concurrent execution, enforces token cost limits, accounts for transient retries
and structured repair attempts, reconciles failures, and avoids deadlocks.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from rex.controller import create_research_run
from rex.controller.budgets import compute_budget_usage
from rex.controller.exceptions import BudgetExceededError
from rex.llm.accounting import (
    release_llm_slot,
    reserve_llm_slot,
)
from rex.llm.base import execute_with_retry
from rex.llm.models import (
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)
from rex.llm.providers.mock import MockLLMProvider
from rex.llm.structured import StructuredGenerator
from rex.observability.events import EventType, InMemoryEventSink
from rex.persistence.database import create_db_engine, get_db_session, init_db
from rex.persistence.models import EventModel


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    db_path = tmp_path / "test_llm_budget_concurrency.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class SimpleModel(BaseModel):
    summary: str
    score: int


class SlowMockLLMProvider:
    """Mock provider with simulated latency to induce concurrency race conditions."""

    def __init__(self, delay_seconds: float = 0.05, cost_per_call: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self.cost_per_call = cost_per_call
        self.call_count = 0
        self._lock = threading.Lock()

    @property
    def provider_name(self) -> str:
        return "slow_mock"

    def generate(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.call_count += 1
            current_call = self.call_count

        time.sleep(self.delay_seconds)

        return LLMResponse(
            text=json.dumps({"summary": f"Result {current_call}", "score": 42}),
            provider=self.provider_name,
            model="mock-v1",
            input_tokens=50,
            output_tokens=25,
            total_tokens=75,
            estimated_cost=self.cost_per_call,
        )


def test_concurrent_calls_max_calls_1_exactly_one_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 1: Under high concurrency, max_llm_calls=1 admits exactly 1 call and rejects all others."""
    sink = InMemoryEventSink()
    provider = SlowMockLLMProvider(delay_seconds=0.08)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Concurrent Budget Test",
            budget={"max_llm_calls": 1},
            event_sink=sink,
        )
        run_id = run.id

    generator = StructuredGenerator(provider)
    num_threads = 10
    successes: list[Any] = []
    rejections: list[Exception] = []

    def worker(worker_id: int) -> None:
        with get_db_session(session_factory) as session:
            req = LLMRequest(
                user_prompt=f"Task from worker {worker_id}",
                research_run_id=run_id,
                agent_name="TestAgent",
            )
            try:
                res, _ = generator.generate_structured(
                    request=req,
                    response_model=SimpleModel,
                    session=session,
                    event_sink=sink,
                )
                successes.append(res)
            except BudgetExceededError as exc:
                rejections.append(exc)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    # Exact assertions: NO oversubscription
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(rejections) == num_threads - 1
    assert provider.call_count == 1, (
        f"Provider should have received exactly 1 call, got {provider.call_count}"
    )

    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 1

    # Verify BUDGET_EXCEEDED audit events were emitted for all rejections
    budget_events = [e for e in sink.events if e.event_type == EventType.BUDGET_EXCEEDED]
    assert len(budget_events) == num_threads - 1


def test_concurrent_calls_max_calls_5_exactly_five_succeed(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 1: max_llm_calls=5 admits exactly 5 concurrent calls and rejects remaining."""
    sink = InMemoryEventSink()
    provider = SlowMockLLMProvider(delay_seconds=0.05)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Concurrent Budget Test 5",
            budget={"max_llm_calls": 5},
            event_sink=sink,
        )
        run_id = run.id

    generator = StructuredGenerator(provider)
    num_threads = 12
    successes: list[Any] = []
    rejections: list[Exception] = []

    def worker(worker_id: int) -> None:
        with get_db_session(session_factory) as session:
            req = LLMRequest(
                user_prompt=f"Task {worker_id}",
                research_run_id=run_id,
                agent_name="TestAgent",
            )
            try:
                res, _ = generator.generate_structured(
                    request=req,
                    response_model=SimpleModel,
                    session=session,
                    event_sink=sink,
                )
                successes.append(res)
            except BudgetExceededError as exc:
                rejections.append(exc)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    assert len(successes) == 5
    assert len(rejections) == num_threads - 5
    assert provider.call_count == 5

    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 5


def test_token_cost_limit_enforcement_under_concurrency(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 2: max_token_cost is strictly enforced without oversubscription under concurrency."""
    sink = InMemoryEventSink()
    # Cost per call = $0.02, limit = $0.05 -> Max 2 calls can be admitted (2 * 0.02 = 0.04 <= 0.05; 3 * 0.02 = 0.06 > 0.05)
    provider = SlowMockLLMProvider(delay_seconds=0.05, cost_per_call=0.02)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Token Cost Concurrency",
            budget={"max_llm_calls": 50, "max_token_cost": 0.05},
            event_sink=sink,
        )
        run_id = run.id

    generator = StructuredGenerator(provider)
    num_threads = 8
    successes: list[Any] = []
    rejections: list[Exception] = []

    def worker(worker_id: int) -> None:
        with get_db_session(session_factory) as session:
            req = LLMRequest(
                user_prompt=f"Task {worker_id}",
                research_run_id=run_id,
                agent_name="TestAgent",
                estimated_cost=0.02,
            )
            try:
                res, _ = generator.generate_structured(
                    request=req,
                    response_model=SimpleModel,
                    session=session,
                    event_sink=sink,
                )
                successes.append(res)
            except BudgetExceededError as exc:
                rejections.append(exc)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    assert len(successes) == 2, f"Expected 2 successes under $0.05 limit, got {len(successes)}"
    assert len(rejections) == num_threads - 2
    assert provider.call_count == 2

    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.token_cost <= 0.05
        assert round(usage.token_cost, 2) == 0.04


def test_structured_repair_budget_consumption_fails_closed_when_exhausted(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 7: Structured repair attempt checks budget and fails closed if max_llm_calls=1."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Repair Budget Test",
            budget={"max_llm_calls": 1},
            event_sink=sink,
        )
        run_id = run.id

    # Queue invalid JSON for initial call, valid JSON for repair
    provider.enqueue_response("NOT VALID JSON")
    provider.enqueue_response(json.dumps({"summary": "repaired", "score": 99}))

    generator = StructuredGenerator(provider)

    with (
        get_db_session(session_factory) as session,
        pytest.raises(BudgetExceededError, match="Research budget exceeded"),
    ):
        req = LLMRequest(
            user_prompt="Produce structured data",
            research_run_id=run_id,
            agent_name="TestAgent",
        )
        # max_repair_attempts=1, but budget is only 1 call
        generator.generate_structured(
            request=req,
            response_model=SimpleModel,
            max_repair_attempts=1,
            session=session,
            event_sink=sink,
        )

    # Initial call was dispatched and recorded, but repair was blocked
    assert provider.call_count == 1, (
        f"Provider should have been called only once, got {provider.call_count}"
    )

    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 1


def test_structured_repair_budget_consumption_succeeds_when_budget_allows(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 7: Structured repair consumes 2 calls when max_llm_calls=2, recording both."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Repair Budget Success Test",
            budget={"max_llm_calls": 2},
            event_sink=sink,
        )
        run_id = run.id

    provider.enqueue_response("NOT VALID JSON")
    provider.enqueue_response(json.dumps({"summary": "repaired", "score": 99}))

    generator = StructuredGenerator(provider)

    with get_db_session(session_factory) as session:
        req = LLMRequest(
            user_prompt="Produce structured data",
            research_run_id=run_id,
            agent_name="TestAgent",
        )
        parsed, _response = generator.generate_structured(
            request=req,
            response_model=SimpleModel,
            max_repair_attempts=1,
            session=session,
            event_sink=sink,
        )

    assert parsed.summary == "repaired"
    assert provider.call_count == 2

    # Exactly 2 calls accounted in the database
    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 2


def test_execute_with_retry_budget_enforcement(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 4: Each retry attempt in execute_with_retry reserves and consumes a budget slot."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Retry Budget Test",
            budget={"max_llm_calls": 1},  # Only 1 attempt permitted!
            event_sink=sink,
        )
        run_id = run.id

    # Queue transient timeout error on attempt 0
    provider.queue_exception(LLMTimeoutError("Gateway timeout"))

    req = LLMRequest(
        user_prompt="Retry prompt",
        research_run_id=run_id,
        agent_name="RetryAgent",
    )

    with (
        get_db_session(session_factory) as session,
        pytest.raises(BudgetExceededError, match="Research budget exceeded"),
    ):
        execute_with_retry(
            provider=provider,
            request=req,
            max_retries=2,
            base_backoff_seconds=0.01,
            session=session,
            event_sink=sink,
        )

    # Provider was called only 1 time (the retry attempt was blocked by budget)
    assert provider.call_count == 1

    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 1


def test_failed_call_accounting_records_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 6: A failed provider call attempt is recorded and increments llm_calls_count."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Failed Call Test",
            budget={"max_llm_calls": 1},
            event_sink=sink,
        )
        run_id = run.id

    provider.queue_exception(LLMProviderError("Upstream server 500 error", status_code=500))

    generator = StructuredGenerator(provider)

    with (
        get_db_session(session_factory) as session,
        pytest.raises(LLMProviderError, match="Upstream server 500 error"),
    ):
        req = LLMRequest(
            user_prompt="Doomed prompt",
            research_run_id=run_id,
            agent_name="FailAgent",
        )
        generator.generate_structured(
            request=req,
            response_model=SimpleModel,
            session=session,
            event_sink=sink,
        )

    # Verify that the failed attempt was persisted in database
    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 1

        failed_event = (
            session.query(EventModel)
            .filter(
                EventModel.research_run_id == run_id,
                EventModel.payload_json["action"].as_string() == "llm_call_failed",
            )
            .first()
        )
        assert failed_event is not None
        assert failed_event.payload_json["is_llm_call"] is True
        assert "500" in failed_event.payload_json["error"]


def test_reservation_lifecycle_and_explicit_release(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 8 & 6: Reservation lifecycle: reserve -> active (counts) -> release (0 calls)."""
    sink = InMemoryEventSink()

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Release Test",
            budget={"max_llm_calls": 1},
            event_sink=sink,
        )
        run_id = run.id

    with get_db_session(session_factory) as session:
        # 1. Acquire reservation
        resv = reserve_llm_slot(
            session=session,
            research_run_id=run_id,
            agent_name="AgentX",
            action_name="test_resv",
        )

    # 2. While in flight, usage counts the active reservation
    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 1

        # Second reservation must fail
        with pytest.raises(BudgetExceededError):
            reserve_llm_slot(
                session=session,
                research_run_id=run_id,
                agent_name="AgentY",
            )

    # 3. Explicitly release reservation without provider call
    with get_db_session(session_factory) as session:
        release_llm_slot(session=session, reservation=resv)

    # 4. Usage resets to 0, slot is freed
    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count == 0

        # Now reservation succeeds again
        new_resv = reserve_llm_slot(
            session=session,
            research_run_id=run_id,
            agent_name="AgentZ",
        )
        assert new_resv is not None


def test_database_isolation_no_locks_held_during_external_call(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 10: Database transactions are NOT held open during external LLM API calls."""
    sink = InMemoryEventSink()

    class LongRunningMockProvider:
        def __init__(self, session_factory: sessionmaker[Session], run_id: str) -> None:
            self.session_factory = session_factory
            self.run_id = run_id
            self.parallel_db_write_succeeded = False

        @property
        def provider_name(self) -> str:
            return "long_mock"

        def generate(self, request: LLMRequest) -> LLMResponse:
            # While the provider is generating (simulating long network call),
            # another database transaction MUST be able to write freely to SQLite without database lock!
            with get_db_session(self.session_factory) as other_session:
                other_run = create_research_run(
                    session=other_session,
                    research_question="Parallel Run Created During LLM Call",
                )
                assert other_run.id is not None
                self.parallel_db_write_succeeded = True

            return LLMResponse(
                text=json.dumps({"summary": "done", "score": 100}),
                provider="long_mock",
                model="mock-v1",
            )

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Isolation Test",
            budget={"max_llm_calls": 5},
            event_sink=sink,
        )
        run_id = run.id

    provider = LongRunningMockProvider(session_factory, run_id)
    generator = StructuredGenerator(provider)

    with get_db_session(session_factory) as session:
        req = LLMRequest(
            user_prompt="Run query",
            research_run_id=run_id,
            agent_name="IsolationAgent",
        )
        parsed, _ = generator.generate_structured(
            request=req,
            response_model=SimpleModel,
            session=session,
            event_sink=sink,
        )

    assert parsed.summary == "done"
    assert provider.parallel_db_write_succeeded is True, (
        "Parallel database write should have succeeded without being blocked by an open transaction during LLM call"
    )


def test_database_deadlock_resistance_under_high_concurrency(
    session_factory: sessionmaker[Session],
) -> None:
    """Invariant 10 & 11: High concurrent contention across threads does not cause deadlocks or lock corruption."""
    sink = InMemoryEventSink()
    provider = SlowMockLLMProvider(delay_seconds=0.01)

    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            research_question="Deadlock Stress Test",
            budget={"max_llm_calls": 10},
            event_sink=sink,
        )
        run_id = run.id

    generator = StructuredGenerator(provider)
    num_threads = 20
    errors: list[Exception] = []
    admitted = 0
    rejected = 0

    def mixed_worker(worker_id: int) -> None:
        nonlocal admitted, rejected
        try:
            with get_db_session(session_factory) as session:
                if worker_id % 3 == 0:
                    # Thread queries budget usage repeatedly
                    for _ in range(5):
                        compute_budget_usage(session, run_id)
                        time.sleep(0.005)
                elif worker_id % 3 == 1:
                    # Thread attempts structured generation
                    req = LLMRequest(
                        user_prompt=f"Task {worker_id}",
                        research_run_id=run_id,
                        agent_name="StressAgent",
                    )
                    try:
                        generator.generate_structured(
                            request=req,
                            response_model=SimpleModel,
                            session=session,
                            event_sink=sink,
                        )
                        admitted += 1
                    except BudgetExceededError:
                        rejected += 1
                else:
                    # Thread does manual reserve and release
                    try:
                        resv = reserve_llm_slot(
                            session=session,
                            research_run_id=run_id,
                            agent_name="ManualAgent",
                        )
                        time.sleep(0.005)
                        release_llm_slot(session=session, reservation=resv)
                    except BudgetExceededError:
                        rejected += 1
        except (SQLAlchemyError, RuntimeError) as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(mixed_worker, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    # Zero unexpected exceptions or deadlocks
    assert len(errors) == 0, f"Expected 0 unexpected errors, encountered: {errors}"

    with get_db_session(session_factory) as session:
        usage = compute_budget_usage(session, run_id)
        assert usage.llm_calls_count <= 10
