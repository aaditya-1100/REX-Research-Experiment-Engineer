"""REX LLM Provider Interface Protocol and Retry Policies (REX-012).

Defines the clean, provider-agnostic protocol for all LLM interactions, ensuring
no direct SDK dependencies exist outside isolated provider adapters.
"""

import logging
import threading
import time
from typing import Any, Protocol, runtime_checkable

from rex.llm.models import (
    LLMError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)

# Process-level thread-safe idempotency response cache
_IDEMPOTENCY_CACHE: dict[str, LLMResponse] = {}
_IDEMPOTENCY_LOCK = threading.Lock()


def clear_idempotency_cache() -> None:
    """Clear in-memory idempotency cache (used for test teardown)."""
    with _IDEMPOTENCY_LOCK:
        _IDEMPOTENCY_CACHE.clear()


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-neutral abstraction protocol for large language models."""

    @property
    def provider_name(self) -> str:
        """Canonical name of this provider (e.g. 'mock', 'openai', 'gemini', 'groq')."""
        ...

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Dispatch a strongly typed generation request and return a normalized response.

        Must raise LLMProviderError or subclasses on upstream failures.
        """
        ...


def execute_with_retry(
    provider: LLMProvider,
    request: LLMRequest,
    max_retries: int = 2,
    base_backoff_seconds: float = 0.25,
    session: Any | None = None,
    actor: Any = "research_agent",
    event_sink: Any | None = None,
) -> LLMResponse:
    """Execute an LLM generation request with bounded, deterministic retry policy for transient errors.

    Retries only on transient network failures, timeouts, and rate limits (429/503).
    Permanent errors (auth, schema, 400) fail immediately.
    Each attempt (initial and retries) consumes an LLM call budget slot when session and research_run_id are provided.
    Supports idempotency tokens to prevent duplicate executions across network retries.
    """
    if request.idempotency_token:
        with _IDEMPOTENCY_LOCK:
            if request.idempotency_token in _IDEMPOTENCY_CACHE:
                logger.info(
                    "Idempotency token '%s' hit; returning cached LLMResponse without duplicate execution.",
                    request.idempotency_token,
                )
                return _IDEMPOTENCY_CACHE[request.idempotency_token]

    attempt = 0
    last_error: Exception | None = None

    while attempt <= max_retries:
        reservation = None
        if session is not None and request.research_run_id is not None:
            from rex.llm.accounting import reserve_llm_slot
            from rex.observability.events import ActorType

            actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
            reservation = reserve_llm_slot(
                session=session,
                research_run_id=request.research_run_id,
                agent_name=request.agent_name or "unknown",
                action_name=f"{request.action_name or 'generate'}:attempt_{attempt}",
                estimated_cost=request.estimated_cost,
                actor=actor_enum,
                event_sink=event_sink,
            )

        try:
            response = provider.generate(request)
        except (LLMTimeoutError, LLMProviderError) as exc:
            last_error = exc
            if session is not None and reservation is not None:
                from rex.llm.accounting import finalize_llm_slot
                from rex.observability.events import ActorType

                actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
                finalize_llm_slot(
                    session=session,
                    reservation=reservation,
                    error=exc,
                    actor=actor_enum,
                    event_sink=event_sink,
                )

            if not getattr(exc, "is_transient", False) and not isinstance(exc, LLMTimeoutError):
                logger.warning(
                    "Non-transient provider error encountered from %s; failing immediately without retry: %s",
                    provider.provider_name,
                    exc,
                )
                raise

            if attempt >= max_retries:
                logger.error(
                    "Exhausted %d retries for provider %s: %s",
                    max_retries,
                    provider.provider_name,
                    exc,
                )
                raise

            delay = base_backoff_seconds * (2**attempt)
            logger.info(
                "Transient error from %s (attempt %d/%d). Retrying in %.2fs: %s",
                provider.provider_name,
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            time.sleep(delay)
            attempt += 1
        except LLMError as exc:
            if session is not None and reservation is not None:
                from rex.llm.accounting import finalize_llm_slot
                from rex.observability.events import ActorType

                actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
                finalize_llm_slot(
                    session=session,
                    reservation=reservation,
                    error=exc,
                    actor=actor_enum,
                    event_sink=event_sink,
                )
            raise
        except Exception as exc:
            if session is not None and reservation is not None:
                from rex.llm.accounting import finalize_llm_slot
                from rex.observability.events import ActorType

                actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
                finalize_llm_slot(
                    session=session,
                    reservation=reservation,
                    error=exc,
                    actor=actor_enum,
                    event_sink=event_sink,
                )
            raise LLMProviderError(
                f"Unexpected error executing LLM request: {exc}",
                provider=provider.provider_name,
                is_transient=False,
            ) from exc
        else:
            if session is not None and reservation is not None:
                from rex.llm.accounting import finalize_llm_slot
                from rex.observability.events import ActorType

                actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
                finalize_llm_slot(
                    session=session,
                    reservation=reservation,
                    response=response,
                    actor=actor_enum,
                    event_sink=event_sink,
                )
            if request.idempotency_token:
                with _IDEMPOTENCY_LOCK:
                    _IDEMPOTENCY_CACHE[request.idempotency_token] = response
            return response

    if last_error:
        raise last_error
    raise LLMProviderError("Execution failed without producing a response or error.")
