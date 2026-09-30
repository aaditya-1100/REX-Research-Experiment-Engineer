"""REX LLM Provider Interface Protocol and Retry Policies (REX-012).

Defines the clean, provider-agnostic protocol for all LLM interactions, ensuring
no direct SDK dependencies exist outside isolated provider adapters.
"""

import logging
import time
from typing import Protocol, runtime_checkable

from rex.llm.models import (
    LLMError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


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
) -> LLMResponse:
    """Execute an LLM generation request with bounded, deterministic retry policy for transient errors.

    Retries only on transient network failures, timeouts, and rate limits (429/503).
    Permanent errors (auth, schema, 400) fail immediately.
    """
    attempt = 0
    last_error: Exception | None = None

    while attempt <= max_retries:
        try:
            return provider.generate(request)
        except (LLMTimeoutError, LLMProviderError) as exc:
            last_error = exc
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
        except LLMError:
            # Domain / schema errors must not be retried by the provider retry loop
            raise
        except Exception as exc:
            raise LLMProviderError(
                f"Unexpected error executing LLM request: {exc}",
                provider=provider.provider_name,
                is_transient=False,
            ) from exc

    if last_error:
        raise last_error
    raise LLMProviderError("Execution failed without producing a response or error.")
