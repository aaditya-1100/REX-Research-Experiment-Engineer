"""REX LLM Abstraction and Structured Generation Package (REX-012)."""

from rex.llm.accounting import (
    LLMReservation,
    check_llm_budget,
    finalize_llm_slot,
    record_llm_usage_event,
    release_llm_slot,
    reserve_llm_slot,
)
from rex.llm.base import LLMProvider, execute_with_retry
from rex.llm.models import (
    LLMConfigurationError,
    LLMError,
    LLMMalformedResponseError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMSchemaValidationError,
    LLMTimeoutError,
    LLMUnsupportedProviderError,
)
from rex.llm.providers import (
    GeminiLLMProvider,
    GroqLLMProvider,
    MockLLMProvider,
    OpenAILLMProvider,
    get_llm_provider,
)
from rex.llm.structured import StructuredGenerator, extract_json_candidate

__all__ = [
    "GeminiLLMProvider",
    "GroqLLMProvider",
    "LLMConfigurationError",
    "LLMError",
    "LLMMalformedResponseError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRequest",
    "LLMReservation",
    "LLMResponse",
    "LLMSchemaValidationError",
    "LLMTimeoutError",
    "LLMUnsupportedProviderError",
    "MockLLMProvider",
    "OpenAILLMProvider",
    "StructuredGenerator",
    "check_llm_budget",
    "execute_with_retry",
    "extract_json_candidate",
    "finalize_llm_slot",
    "get_llm_provider",
    "record_llm_usage_event",
    "release_llm_slot",
    "reserve_llm_slot",
]
