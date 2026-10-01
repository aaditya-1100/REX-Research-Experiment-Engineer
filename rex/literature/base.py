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


import re
from urllib.parse import urlparse

MAX_RETRY_AFTER_SECONDS: float = 60.0
MAX_RESPONSE_BYTES: int = 10 * 1024 * 1024  # 10 MB

FORBIDDEN_HOST_PATTERNS = [
    re.compile(r"^127\.", re.IGNORECASE),
    re.compile(r"^localhost$", re.IGNORECASE),
    re.compile(r"^0\.0\.0\.0$", re.IGNORECASE),
    re.compile(r"^169\.254\.", re.IGNORECASE),
    re.compile(r"^10\.", re.IGNORECASE),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[0-1])\.", re.IGNORECASE),
    re.compile(r"^192\.168\.", re.IGNORECASE),
    re.compile(r"^::1$", re.IGNORECASE),
]

FORBIDDEN_SCHEMES = frozenset({"file", "ftp", "data", "javascript", "vbscript"})


def validate_safe_url(url: str, allowed_base_url: str) -> None:
    """Validate that an outbound literature URL targets the configured provider endpoint safely.

    Raises:
        LiteratureSecurityError: If destination uses a forbidden scheme, loopback/private IP,
            or attempts to escape the configured provider host.
    """
    parsed = urlparse(url)
    allowed_parsed = urlparse(allowed_base_url)

    if parsed.scheme.lower() in FORBIDDEN_SCHEMES:
        raise LiteratureSecurityError(
            f"Forbidden URL scheme '{parsed.scheme}' in literature request: '{url}'"
        )

    if parsed.scheme.lower() not in ("http", "https"):
        raise LiteratureSecurityError(
            f"Unsupported URL scheme '{parsed.scheme}': must be http or https."
        )

    # Check host matching and forbidden destinations
    dest_host = (parsed.hostname or "").lower()
    allowed_host = (allowed_parsed.hostname or "").lower()

    # Check loopback / private IP first
    for pat in FORBIDDEN_HOST_PATTERNS:
        if pat.search(dest_host):
            raise LiteratureSecurityError(
                f"Literature requests to loopback, link-local, or private IP '{dest_host}' are strictly forbidden."
            )

    if not dest_host or dest_host != allowed_host:
        raise LiteratureSecurityError(
            f"URL destination host '{dest_host}' does not match allowed provider host '{allowed_host}'."
        )

    # Check path traversal
    if ".." in parsed.path:
        raise LiteratureSecurityError(
            f"Path traversal sequence '..' detected in literature URL: '{parsed.path}'"
        )


def sanitize_secret_values(text: str, secrets: list[Any] | None = None) -> str:
    """Mask known secret strings and common bearer/token patterns in error text."""
    if not text:
        return ""
    sanitized = text
    if secrets:
        for s in secrets:
            if s is None:
                continue
            val = s.get_secret_value() if hasattr(s, "get_secret_value") else str(s)
            if val and len(val) >= 4 and val in sanitized:
                sanitized = sanitized.replace(val, "********")

    # Regex mask Authorization and token headers/patterns
    sanitized = re.sub(
        r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}",
        r"\1********",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"((?:api[_-]?key|token|auth[_-]?token)[=:\s]+)[A-Za-z0-9_\-\.]{8,}",
        r"\1********",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


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
    "FORBIDDEN_HOST_PATTERNS",
    "FORBIDDEN_SCHEMES",
    "MAX_RESPONSE_BYTES",
    "MAX_RETRY_AFTER_SECONDS",
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
    "sanitize_secret_values",
    "validate_safe_url",
]
