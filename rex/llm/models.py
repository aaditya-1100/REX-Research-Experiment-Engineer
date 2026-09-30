"""REX LLM Models, Request/Response Structures, and Exceptions (REX-012).

Defines strongly typed, provider-neutral models for prompting, token accounting,
and normalized error handling across all reasoning-plane operations.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.observability.events import sanitize_value


class LLMError(Exception):
    """Base exception for all reasoning plane LLM failures."""

    def __init__(self, message: str, provider: str | None = None, model: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model


class LLMProviderError(LLMError):
    """Upstream provider API error (e.g. 500, network failure, or API rejection)."""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        model: str | None = None,
        status_code: int | None = None,
        is_transient: bool = False,
    ) -> None:
        super().__init__(message, provider=provider, model=model)
        self.status_code = status_code
        self.is_transient = is_transient


class LLMTimeoutError(LLMProviderError):
    """Provider API request exceeded configured wall-clock timeout."""

    def __init__(
        self,
        message: str = "LLM request timed out",
        provider: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, model=model, is_transient=True)
        self.timeout_seconds = timeout_seconds


class LLMMalformedResponseError(LLMError):
    """Provider returned unparseable or completely malformed content."""


class LLMSchemaValidationError(LLMError):
    """Structured generation failed to validate against the expected Pydantic schema."""

    def __init__(
        self,
        message: str,
        raw_text: str = "",
        validation_errors: list[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message, provider=provider, model=model)
        self.raw_text = raw_text
        self.validation_errors = validation_errors or []


class LLMUnsupportedProviderError(LLMError):
    """Requested provider name is unknown or unconfigured."""


class LLMConfigurationError(LLMError):
    """Missing required credentials or invalid provider configuration."""


class LLMRequest(BaseModel):
    """Strongly typed, provider-neutral representation of an LLM generation request."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    user_prompt: str = Field(description="Core task or user prompt")
    system_prompt: str = Field(
        default="", description="Authoritative system/application instructions"
    )
    model: str | None = Field(default=None, description="Optional target model identifier")
    provider: str | None = Field(default=None, description="Optional provider identifier")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int | None = Field(
        default=None, gt=0, description="Maximum output tokens to generate"
    )
    timeout_seconds: float = Field(
        default=60.0, gt=0.0, description="Wall-clock timeout in seconds"
    )
    response_schema: type[BaseModel] | None = Field(
        default=None, description="Optional Pydantic schema for structured output"
    )
    research_run_id: str | None = Field(
        default=None, description="Associated research run ID for auditability"
    )
    agent_name: str | None = Field(
        default=None, description="Name of reasoning agent issuing the request"
    )
    action_name: str | None = Field(
        default=None, description="Specific action or purpose of generation"
    )
    context: Mapping[str, Any] = Field(
        default_factory=dict, description="Structured contextual parameters"
    )

    @field_validator("user_prompt")
    @classmethod
    def _validate_user_prompt(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_prompt must be a non-empty string.")
        return cleaned

    @field_validator("context", mode="before")
    @classmethod
    def _sanitize_context(cls, v: Any) -> Mapping[str, Any]:
        if not v or not isinstance(v, (dict, Mapping)):
            return {}
        return sanitize_value(dict(v))


class LLMResponse(BaseModel):
    """Strongly typed, provider-neutral representation of an LLM generation result."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    text: str = Field(description="Raw generated response text")
    model: str = Field(description="Model identifier that produced the response")
    provider: str = Field(description="Provider that handled the request")
    input_tokens: int | None = Field(default=None, ge=0, description="Prompt/input tokens consumed")
    output_tokens: int | None = Field(
        default=None, ge=0, description="Completion/output tokens produced"
    )
    total_tokens: int | None = Field(
        default=None, ge=0, description="Total tokens consumed by request"
    )
    estimated_cost: float | None = Field(
        default=None, ge=0.0, description="Estimated USD cost if determinable"
    )
    latency_seconds: float = Field(
        default=0.0, ge=0.0, description="Total round-trip latency in seconds"
    )
    request_id: str | None = Field(
        default=None, description="Unique upstream request or completion ID"
    )
    parsed: Any | None = Field(
        default=None, description="Parsed structured object if schema validation succeeded"
    )
    raw_metadata: Mapping[str, Any] = Field(
        default_factory=dict,
        description="Sanitized provider-specific telemetry (never contains secrets)",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of response creation",
    )

    @field_validator("raw_metadata", mode="before")
    @classmethod
    def _sanitize_metadata(cls, v: Any) -> Mapping[str, Any]:
        if not v or not isinstance(v, (dict, Mapping)):
            return {}
        return sanitize_value(dict(v))
