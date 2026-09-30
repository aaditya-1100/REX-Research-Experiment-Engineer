"""REX LLM Accounting and Budget Enforcement (REX-011, REX-012).

Integrates reasoning-plane LLM operations with the central research run budget system,
verifying call/token caps before dispatch and persisting structured audit events without secret leaks.
Provides atomic in-flight reservation and reconciliation to prevent concurrent oversubscription.
"""

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

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
from rex.persistence.models import EventModel, ResearchRunModel
from rex.persistence.repositories import EventRepository

logger = logging.getLogger(__name__)

_run_locks: dict[str, threading.Lock] = {}
_master_lock: threading.Lock = threading.Lock()


def get_llm_run_lock(research_run_id: str) -> threading.Lock:
    """Retrieve or create an in-process serialization lock for a research run's LLM budgeting."""
    with _master_lock:
        if research_run_id not in _run_locks:
            _run_locks[research_run_id] = threading.Lock()
        return _run_locks[research_run_id]


@dataclass(frozen=True)
class LLMReservation:
    """Strongly typed reservation token guaranteeing budget allocation before provider dispatch."""

    id: str
    research_run_id: str
    agent_name: str
    action_name: str
    estimated_cost: float
    reserved_at: datetime


def reserve_llm_slot(
    session: Session,
    research_run_id: str,
    agent_name: str = "unknown",
    action_name: str = "generate",
    estimated_cost: float = 0.0,
    actor: ActorType = ActorType.RESEARCH_AGENT,
    event_sink: EventSink | None = None,
) -> LLMReservation:
    """Atomically verify and reserve an LLM call slot before provider dispatch.

    Enforces that concurrent workers cannot oversubscribe max_llm_calls or max_token_cost.
    Short transaction: acquires run lock, checks budget usage including in-flight reservations,
    persists active reservation record to DB, commits, and returns.
    """
    run_lock = get_llm_run_lock(research_run_id)
    with run_lock:
        if session.bind and session.bind.dialect.name == "sqlite":
            try:
                session.execute(text("BEGIN IMMEDIATE"))
            except SQLAlchemyError:
                logger.debug("BEGIN IMMEDIATE skipped; transaction already started or locked.")

        run_model = session.get(ResearchRunModel, research_run_id)
        if run_model is None:
            reservation_id = f"resv_{uuid.uuid4().hex[:12]}"
            return LLMReservation(
                id=reservation_id,
                research_run_id=research_run_id,
                agent_name=agent_name,
                action_name=action_name,
                estimated_cost=estimated_cost,
                reserved_at=datetime.now(UTC),
            )

        budget = load_run_budget(run_model)
        usage = compute_budget_usage(session, research_run_id)

        # Check call limit: usage.llm_calls_count includes active reservations
        if usage.llm_calls_count + 1 > budget.max_llm_calls:
            record_budget_exceeded_event(
                session=session,
                research_run_id=research_run_id,
                dimension="max_llm_calls",
                limit=budget.max_llm_calls,
                current_usage=usage.llm_calls_count,
                actor=actor,
                event_sink=event_sink,
            )
            session.commit()
            raise BudgetExceededError(
                dimension="max_llm_calls",
                limit=budget.max_llm_calls,
                current_usage=usage.llm_calls_count,
                run_id=research_run_id,
            )

        # Check token cost limit
        if budget.max_token_cost is not None and (
            usage.token_cost + estimated_cost > budget.max_token_cost
        ):
            record_budget_exceeded_event(
                session=session,
                research_run_id=research_run_id,
                dimension="max_token_cost",
                limit=budget.max_token_cost,
                current_usage=usage.token_cost,
                actor=actor,
                event_sink=event_sink,
            )
            session.commit()
            raise BudgetExceededError(
                dimension="max_token_cost",
                limit=budget.max_token_cost,
                current_usage=usage.token_cost,
                run_id=research_run_id,
            )

        now = datetime.now(UTC)
        reservation_id = f"resv_{uuid.uuid4().hex[:12]}"
        payload = {
            "action": "llm_reservation",
            "reservation_id": reservation_id,
            "reservation_status": "active",
            "is_llm_reservation": True,
            "is_llm_call": False,
            "estimated_cost": estimated_cost,
            "agent": agent_name,
            "action_name": action_name,
            "reserved_at": now.isoformat(),
        }

        event = create_event(
            event_type=EventType.AGENT_ACTION,
            actor=actor,
            research_run_id=research_run_id,
            payload=payload,
        )

        EventRepository(session).record_event(event)
        session.commit()

        if event_sink is not None:
            event_sink.emit(event)

        return LLMReservation(
            id=event.event_id,
            research_run_id=research_run_id,
            agent_name=agent_name,
            action_name=action_name,
            estimated_cost=estimated_cost,
            reserved_at=now,
        )


def finalize_llm_slot(
    session: Session,
    reservation: LLMReservation | str,
    response: LLMResponse | None = None,
    error: Exception | None = None,
    actor: ActorType = ActorType.RESEARCH_AGENT,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Finalize an in-flight LLM reservation as completed or failed with full telemetry.

    Reconciles the reservation record in the database outside long-running locks.
    """
    res_id = reservation.id if isinstance(reservation, LLMReservation) else reservation
    run_id = reservation.research_run_id if isinstance(reservation, LLMReservation) else None

    run_lock = get_llm_run_lock(run_id) if run_id else _master_lock
    with run_lock:
        event_model = session.get(EventModel, res_id)
        if event_model is None:
            return

        payload = dict(event_model.payload_json or {})
        now = datetime.now(UTC)

        if error is not None:
            payload.update(
                {
                    "action": "llm_call_failed",
                    "reservation_status": "failed",
                    "is_llm_reservation": False,
                    "is_llm_call": True,
                    "error": str(error),
                    "error_type": type(error).__name__,
                    "failed_at": now.isoformat(),
                }
            )
        elif response is not None:
            payload.update(
                {
                    "action": "llm_call",
                    "reservation_status": "completed",
                    "is_llm_reservation": False,
                    "is_llm_call": True,
                    "model": response.model,
                    "provider": response.provider,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "total_tokens": response.total_tokens,
                    "cost": response.estimated_cost,
                    "latency_seconds": response.latency_seconds,
                    "request_id": response.request_id,
                    "completed_at": now.isoformat(),
                }
            )
        else:
            payload.update(
                {
                    "action": "llm_reservation_released",
                    "reservation_status": "released",
                    "is_llm_reservation": False,
                    "is_llm_call": False,
                    "released_at": now.isoformat(),
                }
            )

        if context:
            payload["context"] = sanitize_value(context)

        event_model.payload_json = payload
        flag_modified(event_model, "payload_json")
        session.commit()

        if event_sink is not None:
            event = EventRepository.to_research_event(event_model)
            event_sink.emit(event)


def release_llm_slot(
    session: Session,
    reservation: LLMReservation | str,
    actor: ActorType = ActorType.RESEARCH_AGENT,
    event_sink: EventSink | None = None,
) -> None:
    """Explicitly release an in-flight reservation without counting as a completed or failed call."""
    finalize_llm_slot(
        session=session,
        reservation=reservation,
        response=None,
        error=None,
        actor=actor,
        event_sink=event_sink,
    )


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
        "is_llm_reservation": False,
        "reservation_status": "completed",
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
    session.commit()

    if event_sink is not None:
        event_sink.emit(event)
