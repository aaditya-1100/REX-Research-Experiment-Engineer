"""REX Literature Provider Interface & Typed Exceptions (REX-028).

Defines the provider-neutral abstraction, lifecycle protocol, and explicit error hierarchy
for scholarly metadata search, retrieval, and deterministic normalization.
"""

from abc import ABC, abstractmethod
from typing import Any

from rex.literature.models import (
    LiteratureSearchRequest,
    LiteratureSearchResult,
    LiteratureSource,
    ProviderCapabilities,
)
from rex.observability.events import SENSITIVE_KEY_PATTERNS


class LiteratureError(Exception):
    """Base exception for all scholarly literature subsystem failures."""


class LiteratureProviderError(LiteratureError):
    """Exception raised when an external literature provider encounters a failure."""

    def __init__(
        self,
        message: str,
        provider: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status_code = status_code
        self.details = details or {}


class ProviderUnavailableError(LiteratureProviderError):
    """Exception raised when a provider is offline, unreachable, or returns 5xx."""


class RateLimitError(LiteratureProviderError):
    """Exception raised when a provider rate limit (HTTP 429) is encountered."""

    def __init__(
        self,
        message: str,
        provider: str,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider, status_code=429, details=details)
        self.retry_after = retry_after


class MalformedResponseError(LiteratureProviderError):
    """Exception raised when a provider response cannot be safely parsed (invalid JSON/XML)."""


class LiteratureTimeoutError(LiteratureProviderError):
    """Exception raised when a provider request exceeds the configured timeout."""


class InvalidQueryError(LiteratureError):
    """Exception raised when a search query is syntactically invalid or exceeds query bounds."""


class LiteratureSecurityError(LiteratureError):
    """Base exception for security boundary violations in the literature plane."""


class PromptInjectionAttemptError(LiteratureSecurityError):
    """Exception raised when adversarial prompt injection is detected in untrusted literature text."""

    def __init__(self, message: str, pattern: str, snippet: str = "") -> None:
        super().__init__(message)
        self.pattern = pattern
        self.snippet = snippet


def mask_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of request/response headers with authentication secrets masked."""
    sanitized: dict[str, str] = {}
    for k, v in headers.items():
        lower_k = k.lower()
        if any(pat in lower_k for pat in SENSITIVE_KEY_PATTERNS) or lower_k in (
            "authorization",
            "x-api-key",
            "api-key",
        ):
            sanitized[k] = "********"
        else:
            sanitized[k] = v
    return sanitized


class LiteratureProvider(ABC):
    """Provider-neutral abstract base class for scholarly search and retrieval (REX-028)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Normalized unique identifier for the provider (e.g. 'openalex', 'semantic_scholar', 'arxiv')."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Provider feature capabilities and limitations."""
        return ProviderCapabilities(provider_name=self.provider_name)

    @abstractmethod
    def search(
        self, request: LiteratureSearchRequest, research_run_id: str = ""
    ) -> LiteratureSearchResult:
        """Execute a structured search query against the scholarly provider.

        Args:
            request: Typed search query parameters and pagination options.
            research_run_id: Optional research run identifier to associate with sources.

        Returns:
            Normalized LiteratureSearchResult containing typed LiteratureSource items.

        Raises:
            RateLimitError: If rate limited by the provider.
            ProviderUnavailableError: If provider is unreachable or returns 5xx.
            LiteratureTimeoutError: If request times out.
            MalformedResponseError: If response cannot be decoded.
            LiteratureProviderError: For other provider-level errors.
        """

    @abstractmethod
    def get_by_id(self, external_id: str, research_run_id: str = "") -> LiteratureSource | None:
        """Retrieve a specific publication by its provider-native identifier.

        Args:
            external_id: Provider identifier (DOI, arXiv ID, corpus ID, or work ID).
            research_run_id: Optional research run identifier to associate with the source.

        Returns:
            Normalized LiteratureSource if found, None if not found (HTTP 404).

        Raises:
            RateLimitError: If rate limited.
            ProviderUnavailableError: If provider is unreachable.
            LiteratureTimeoutError: If request times out.
            MalformedResponseError: If response cannot be decoded.
            LiteratureProviderError: For other provider errors.
        """

    def close(self) -> None:
        """Optional hook to release underlying HTTP connections or resources."""


__all__ = [
    "InvalidQueryError",
    "LiteratureError",
    "LiteratureProvider",
    "LiteratureProviderError",
    "LiteratureSecurityError",
    "LiteratureTimeoutError",
    "MalformedResponseError",
    "PromptInjectionAttemptError",
    "ProviderUnavailableError",
    "RateLimitError",
    "mask_sensitive_headers",
]
