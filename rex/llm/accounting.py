"""REX LLM Accounting and Budget Enforcement (REX-011, REX-012).

Integrates reasoning-plane LLM operations with the central research run budget system,
verifying call/token caps before dispatch and persisting structured audit events without secret leaks.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from rex.controller.budgets import (
    check_budget_limits,
    compute_budget_usage,
    load_run_budget,
    record_budget_exceeded_event,
)
from rex.controller.exceptions import (
    BudgetExceededError,
)
from rex.llm.models import (
    LLMRequest,
    LLMResponse,
)
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
    sanitize_value,
)
from rex.persistence.models import ResearchRunModel
from rex.persistence.repositories import EventRepository

logger = logging.getLogger(__name__)


def check_llm_budget(
    session: Session,
    research_run_id: str,
    actor: ActorType = ActorType.RESEARCH_AGENT,
    event_sink: EventSink | None = None,
) -> None:
    """Enforce pre-flight LLM call and token cost caps before an LLM invocation.

    Raises BudgetExceededError and logs an audit event if budget is exhausted.
    """
    run_model = session.get(ResearchRunModel, research_run_id)
    if run_model is None:
        return

    budget = load_run_budget(run_model)
    usage = compute_budget_usage(session, research_run_id)

    try:
        check_budget_limits(
            budget=budget,
            usage=usage,
            run_id=research_run_id,
            is_launching_execution=False,
        )
    except BudgetExceededError as err:
        record_budget_exceeded_event(
            session=session,
            research_run_id=research_run_id,
            dimension=err.dimension,
            limit=err.limit,
            current_usage=err.current_usage,
            actor=actor,
            event_sink=event_sink,
        )
        raise


def record_llm_usage_event(
    session: Session,
    request: LLMRequest,
    response: LLMResponse,
    actor: ActorType = ActorType.RESEARCH_AGENT,
    event_sink: EventSink | None = None,
) -> None:
    """Persist a structured AGENT_ACTION audit event capturing LLM usage telemetry.

    Strictly quarantines and masks secrets, auth headers, and raw prompts that could hold tokens.
    """
    if not request.research_run_id:
        return

    payload: dict[str, Any] = {
        "action": "llm_call",
        "is_llm_call": True,
        "agent": request.agent_name or "unknown",
        "action_name": request.action_name or "generate",
        "model": response.model,
        "provider": response.provider,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "total_tokens": response.total_tokens,
        "cost": response.estimated_cost,
        "latency_seconds": response.latency_seconds,
        "request_id": response.request_id,
        "context": sanitize_value(request.context),
    }

    event = create_event(
        event_type=EventType.AGENT_ACTION,
        actor=actor,
        research_run_id=request.research_run_id,
        payload=payload,
    )

    EventRepository(session).record_event(event)
    session.flush()

    if event_sink is not None:
        event_sink.emit(event)
