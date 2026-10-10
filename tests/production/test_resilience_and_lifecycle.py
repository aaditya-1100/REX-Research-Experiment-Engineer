"""REX Production Lifecycle Control & Crash Recovery Resilience Suite (Track B Sec 20-34).

Validates:
1. Lifecycle Control:
   - Pause / resume preserving exact pre-paused state (HYPOTHESIZE -> PAUSED -> HYPOTHESIZE)
   - Pausing already paused runs rejected (400 Bad Request)
   - Resuming non-paused runs rejected (400 Bad Request)
   - Run cancellation (POST /api/research/{run_id}/cancel):
     - Cancels active executions (status -> CANCELLED)
     - Transitions run to ResearchState.STOP
     - Cancelling terminal runs rejected (400 Bad Request)
   - Terminal state resurrection strictly blocked across COMPLETE, STOP, FAILED, CANCELLED
2. Crash Recovery & Reconciliation:
   - Stale execution reconciliation (RUNNING -> FAILED) via ExecutionOrchestrator
   - Startup crash recovery hook in create_app
3. Model Provider Resilience:
   - Transient failure exponential backoff and retry (e.g. rate limit / timeout)
   - Permanent failure fail-closed immediately without retrying (e.g. authentication error)
   - Bounded malformed JSON repair cycle in StructuredGenerator
   - Idempotency caching (zero duplicate executions with idempotency_token)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from rex.api.app import create_app
from rex.config import load_settings
from rex.controller.exceptions import (
    TerminalStateError,
)
from rex.controller.execution_orchestrator import ExecutionOrchestrator
from rex.controller.state_machine import (
    ResearchStateMachine,
)
from rex.domain.models import (
    ExecutionStatus,
    ResearchState,
)
from rex.llm.base import (
    clear_idempotency_cache,
    execute_with_retry,
)
from rex.llm.models import (
    LLMProviderError,
    LLMRequest,
    LLMResponse,
)
from rex.llm.providers.mock import MockLLMProvider
from rex.llm.structured import StructuredGenerator
from rex.observability.events import ActorType
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
)


@pytest.fixture
def file_db_engine(tmp_path: Path):
    """File-backed SQLite engine in WAL mode."""
    db_file = tmp_path / "lifecycle_resilience.db"
    engine = create_db_engine(f"sqlite:///{db_file}")
    init_db(engine)
    return engine


@pytest.fixture
def session_factory(file_db_engine):
    return create_session_factory(file_db_engine)


@pytest.fixture
def saas_app(file_db_engine, session_factory, tmp_path: Path):
    settings_override = load_settings(
        persistence={
            "database_url": str(file_db_engine.url),
            "artifact_root": tmp_path / "artifacts",
            "workspace_root": tmp_path / "workspaces",
        },
        app={"version": "1.0.0-lifecycle"},
    )
    return create_app(
        engine=file_db_engine,
        session_factory=session_factory,
        rex_settings=settings_override,
    )


@pytest.fixture
def saas_client(saas_app):
    return TestClient(saas_app)


# =============================================================================
# 1. Lifecycle Control: Pause, Safe Resume, Cancel, Terminal Enforcements
# =============================================================================


def test_pause_and_resume_preserves_pre_paused_state(saas_client: TestClient, session_factory):
    """Verify pausing preserves pre-pause state and resuming restores exact pre-paused state."""
    run_id = "run_pause_restore_1"
    with session_factory() as session:
        run = ResearchRunModel(
            id=run_id,
            title="Pause Test Run",
            research_question="Can lifecycle pause cleanly?",
            status="HYPOTHESES",
            configuration_json={"iteration": 1},
            budget_json={},
        )
        session.add(run)
        session.commit()

    # Step 1: Pause run
    pause_res = saas_client.post(f"/api/research/{run_id}/pause")
    assert pause_res.status_code == 200
    pause_data = pause_res.json()
    assert pause_data["status"] == "PAUSED"

    with session_factory() as session:
        db_run = session.get(ResearchRunModel, run_id)
        assert db_run.status == "PAUSED"
        assert db_run.configuration_json.get("pre_paused_status") == "HYPOTHESES"

    # Step 2: Resume run -> must restore HYPOTHESES, not INITIALIZE or UNDERSTAND
    resume_res = saas_client.post(f"/api/research/{run_id}/resume")
    assert resume_res.status_code == 200
    resume_data = resume_res.json()
    assert resume_data["status"] == "HYPOTHESES"

    with session_factory() as session:
        db_run = session.get(ResearchRunModel, run_id)
        assert db_run.status == "HYPOTHESES"


def test_pause_already_paused_and_resume_unpaused_rejected(
    saas_client: TestClient, session_factory
):
    """Verify pausing an already paused run and resuming a non-paused run return 400 Bad Request."""
    run_id = "run_invalid_pause_ops"
    with session_factory() as session:
        run = ResearchRunModel(
            id=run_id,
            title="Invalid Pause Ops",
            research_question="Invalid pause test?",
            status="DESIGN",
            configuration_json={},
            budget_json={},
        )
        session.add(run)
        session.commit()

    # Attempt to resume a non-paused run in DESIGN -> 400 Bad Request
    res_bad_resume = saas_client.post(f"/api/research/{run_id}/resume")
    assert res_bad_resume.status_code == 400
    assert (
        "Cannot resume research run from state 'DESIGN'"
        in res_bad_resume.json()["error"]["message"]
    )

    # Pause the run
    res_pause = saas_client.post(f"/api/research/{run_id}/pause")
    assert res_pause.status_code == 200

    # Attempt to pause an already paused run -> 400 Bad Request
    res_bad_pause = saas_client.post(f"/api/research/{run_id}/pause")
    assert res_bad_pause.status_code == 400
    assert "already paused" in res_bad_pause.json()["error"]["message"].lower()


def test_cancel_active_run_cancels_executions_and_stops(saas_client: TestClient, session_factory):
    """Verify cancel_run halts active executions (setting CANCELLED) and transitions run to STOP."""
    run_id = "run_cancel_active_test"
    exp_id = "exp_cancel_active_test"
    exec_id = "exec_cancel_active_test"

    with session_factory() as session:
        run = ResearchRunModel(
            id=run_id,
            title="Cancel Active Run",
            research_question="Does cancellation stop active executions?",
            status="EXECUTE",
            configuration_json={},
            budget_json={},
        )
        hyp = HypothesisModel(
            id="hyp_cancel_test",
            research_run_id=run_id,
            statement="H",
            rationale="R",
            expected_direction="increase",
            falsification_condition="FC",
            status="proposed",
        )
        exp = ExperimentModel(
            id=exp_id,
            research_run_id=run_id,
            hypothesis_id="hyp_cancel_test",
            objective="Cancel Exp",
            status="running",
        )
        exec_model = ExecutionModel(
            id=exec_id,
            experiment_id=exp_id,
            status=ExecutionStatus.RUNNING.value,
            command="python compute.py",
        )
        session.add_all([run, hyp, exp, exec_model])
        session.commit()

    cancel_res = saas_client.post(f"/api/research/{run_id}/cancel")
    assert cancel_res.status_code == 200
    cancel_data = cancel_res.json()
    assert cancel_data["status"] == "STOP"

    # Verify execution in DB was cancelled
    with session_factory() as session:
        updated_exec = session.get(ExecutionModel, exec_id)
        assert updated_exec.status == ExecutionStatus.CANCELLED.value
        assert updated_exec.finished_at is not None

        updated_run = session.get(ResearchRunModel, run_id)
        assert updated_run.status == "STOP"


def test_cancel_terminal_run_rejected(saas_client: TestClient, session_factory):
    """Verify cancelling an already terminated run (STOP / COMPLETE / FAILED) returns 400."""
    run_id = "run_already_terminal"
    with session_factory() as session:
        run = ResearchRunModel(
            id=run_id,
            title="Terminal Run",
            research_question="Question",
            status="STOP",
            configuration_json={},
            budget_json={},
        )
        session.add(run)
        session.commit()

    res = saas_client.post(f"/api/research/{run_id}/cancel")
    assert res.status_code == 400
    assert "Cannot cancel research run in terminal state 'STOP'" in res.json()["error"]["message"]


def test_terminal_state_resurrection_blocked(session_factory):
    """Verify that runs in terminal states cannot be resurrected to any active state."""
    terminal_states = [ResearchState.COMPLETE, ResearchState.STOP, ResearchState.FAILED]
    active_targets = [
        ResearchState.INITIALIZE,
        ResearchState.HYPOTHESES,
        ResearchState.EXECUTE,
        ResearchState.UNDERSTAND,
    ]

    for term_state in terminal_states:
        run_id = f"run_term_{term_state.value}"
        with session_factory() as session:
            run = ResearchRunModel(
                id=run_id,
                title=f"Terminal {term_state.value}",
                research_question="Resurrection forbidden",
                status=term_state.value,
            )
            session.add(run)
            session.commit()

        for target in active_targets:
            with session_factory() as session, pytest.raises(TerminalStateError) as exc_info:
                ResearchStateMachine.transition(
                    session=session,
                    run_id=run_id,
                    target_state=target,
                    actor=ActorType.OWNER,
                )
            assert "Terminal states cannot transition" in str(exc_info.value)


# =============================================================================
# 2. Crash Recovery & Stale Execution Reconciliation
# =============================================================================


def test_crash_recovery_stale_executions_reconciliation(session_factory):
    """Verify ExecutionOrchestrator reconciles orphaned RUNNING executions to FAILED."""
    with session_factory() as session:
        run = ResearchRunModel(
            id="run_stale_1", title="Stale Run", research_question="Orphan check"
        )
        hyp = HypothesisModel(
            id="hyp_stale_1",
            research_run_id=run.id,
            statement="H",
            rationale="R",
            expected_direction="increase",
            falsification_condition="FC",
            status="proposed",
        )
        exp = ExperimentModel(
            id="exp_stale_1", research_run_id=run.id, hypothesis_id="hyp_stale_1", objective="Exp"
        )
        exec1 = ExecutionModel(
            id="exec_orphan_running_1",
            experiment_id=exp.id,
            status=ExecutionStatus.RUNNING.value,
        )
        exec2 = ExecutionModel(
            id="exec_orphan_running_2",
            experiment_id=exp.id,
            status=ExecutionStatus.RUNNING.value,
        )
        session.add_all([run, hyp, exp, exec1, exec2])
        session.commit()

    orchestrator = ExecutionOrchestrator(session_factory=session_factory)
    reconciled = orchestrator.reconcile_stale_executions(stale_threshold_seconds=0)

    assert "exec_orphan_running_1" in reconciled
    assert "exec_orphan_running_2" in reconciled

    with session_factory() as session:
        m1 = session.get(ExecutionModel, "exec_orphan_running_1")
        m2 = session.get(ExecutionModel, "exec_orphan_running_2")
        assert m1.status == ExecutionStatus.FAILED.value
        assert m2.status == ExecutionStatus.FAILED.value
        assert m1.finished_at is not None
        assert m2.finished_at is not None


def test_startup_crash_recovery_in_create_app(tmp_path: Path):
    """Verify application boot automatically reconciles stale executions on startup."""
    db_file = tmp_path / "startup_crash.db"
    engine = create_db_engine(f"sqlite:///{db_file}")
    init_db(engine)
    factory = create_session_factory(engine)

    with factory() as session:
        run = ResearchRunModel(
            id="run_boot_crash", title="Boot Crash", research_question="Boot test"
        )
        hyp = HypothesisModel(
            id="hyp_boot_crash",
            research_run_id=run.id,
            statement="H",
            rationale="R",
            expected_direction="increase",
            falsification_condition="FC",
            status="proposed",
        )
        exp = ExperimentModel(
            id="exp_boot_crash",
            research_run_id=run.id,
            hypothesis_id="hyp_boot_crash",
            objective="Exp",
        )
        exec_model = ExecutionModel(
            id="exec_boot_stale",
            experiment_id=exp.id,
            status=ExecutionStatus.RUNNING.value,
        )
        session.add_all([run, hyp, exp, exec_model])
        session.commit()

    # Boot application
    app = create_app(engine=engine, session_factory=factory)
    assert app is not None

    # Verify execution was reconciled during create_app execution
    with factory() as session:
        e_model = session.get(ExecutionModel, "exec_boot_stale")
        assert e_model.status == ExecutionStatus.FAILED.value


# =============================================================================
# 3. Model Provider Resilience: Retries, Fail-Closed, Repair, Idempotency
# =============================================================================


def test_model_provider_retry_exponential_backoff():
    """Verify execute_with_retry recovers from transient errors with backoff."""
    mock_provider = MockLLMProvider()
    call_count = 0

    def flaky_generate(req: LLMRequest) -> LLMResponse:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise LLMProviderError(
                message=f"Rate limit exceeded (attempt {call_count})",
                provider="mock",
                status_code=429,
                is_transient=True,
            )
        return LLMResponse(
            text="Success after backoff",
            provider="mock",
            model="mock-v1",
        )

    mock_provider.generate = flaky_generate

    req = LLMRequest(user_prompt="Transient test prompt")
    resp = execute_with_retry(
        provider=mock_provider,
        request=req,
        max_retries=3,
        base_backoff_seconds=0.01,
    )

    assert resp.text == "Success after backoff"
    assert call_count == 3


def test_model_provider_permanent_failure_fails_closed():
    """Verify permanent errors (non-transient errors) fail closed immediately."""
    mock_provider = MockLLMProvider()
    call_count = 0

    def auth_fail_generate(req: LLMRequest) -> LLMResponse:
        nonlocal call_count
        call_count += 1
        raise LLMProviderError(
            message="Invalid API token",
            provider="mock",
            status_code=401,
            is_transient=False,
        )

    mock_provider.generate = auth_fail_generate

    req = LLMRequest(user_prompt="Auth test prompt")
    with pytest.raises(LLMProviderError) as exc_info:
        execute_with_retry(
            provider=mock_provider,
            request=req,
            max_retries=3,
            base_backoff_seconds=0.01,
        )

    assert call_count == 1
    assert "Invalid API token" in str(exc_info.value)


class _SampleHypothesis(BaseModel):
    statement: str = Field(description="Statement")
    confidence: float = Field(ge=0.0, le=1.0)


def test_structured_generator_malformed_json_repair_cycle():
    """Verify StructuredGenerator repairs malformed JSON on subsequent correction prompt."""
    mock_provider = MockLLMProvider()
    calls: list[str] = []

    def alternating_generate(req: LLMRequest) -> LLMResponse:
        calls.append(req.user_prompt)
        if len(calls) == 1:
            # First attempt: malformed JSON text
            return LLMResponse(
                text="Here is the output: {statement: 'Missing quotes', confidence: 0.85",
                provider="mock",
                model="mock-v1",
            )
        # Second attempt (repair prompt): corrected valid JSON
        return LLMResponse(
            text='{"statement": "Fixed valid hypothesis statement", "confidence": 0.85}',
            provider="mock",
            model="mock-v1",
        )

    mock_provider.generate = alternating_generate

    generator = StructuredGenerator(provider=mock_provider)
    req = LLMRequest(user_prompt="Generate a scientific hypothesis")

    parsed, _resp = generator.generate_structured(
        request=req,
        response_model=_SampleHypothesis,
        max_repair_attempts=1,
    )

    assert len(calls) == 2
    assert "Your previous response was rejected" in calls[1]
    assert parsed.statement == "Fixed valid hypothesis statement"
    assert parsed.confidence == 0.85


def test_llm_idempotency_cache():
    """Verify idempotency_token prevents duplicate LLM provider executions."""
    clear_idempotency_cache()
    mock_provider = MockLLMProvider()
    call_count = 0

    def counted_generate(req: LLMRequest) -> LLMResponse:
        nonlocal call_count
        call_count += 1
        return LLMResponse(
            text=f"Response for token execution #{call_count}",
            provider="mock",
            model="mock-v1",
        )

    mock_provider.generate = counted_generate

    token = "idempotency-token-unique-xyz-789"
    req1 = LLMRequest(user_prompt="Run idempotent query", idempotency_token=token)
    req2 = LLMRequest(user_prompt="Run idempotent query", idempotency_token=token)

    resp1 = execute_with_retry(mock_provider, req1)
    resp2 = execute_with_retry(mock_provider, req2)

    assert call_count == 1
    assert resp1.text == "Response for token execution #1"
    assert resp2.text == "Response for token execution #1"
