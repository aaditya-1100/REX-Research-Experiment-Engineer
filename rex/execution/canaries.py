"""REX Canary Token Generation, Detection, and Secret Scrubbing (REX-043 Track B).

Provides high-entropy canary token generation, environment insertion, detection,
and log scrubbing across execution stdout/stderr and artifact storage.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from rex.literature.base import sanitize_secret_values

# Global registry of active canary tokens issued during runtime
_ACTIVE_CANARY_TOKENS: set[str] = set()

# Regex to detect common canary token formats even if not registered
CANARY_TOKEN_PATTERN = re.compile(
    r"(?:rex_canary_[0-9a-fA-F]{16,64}|CANARY_SECRET_[A-Za-z0-9_\-]{8,})",
    re.IGNORECASE,
)


def generate_canary_token(prefix: str = "rex_canary_", num_bytes: int = 16) -> str:
    """Generate a cryptographically secure, high-entropy canary token."""
    token = f"{prefix}{secrets.token_hex(num_bytes)}"
    _ACTIVE_CANARY_TOKENS.add(token)
    return token


def register_canary_token(token: str) -> None:
    """Register an existing canary token for global leakage detection and scrubbing."""
    if token and token.strip():
        _ACTIVE_CANARY_TOKENS.add(token.strip())


def detect_canary_leakage(text: str, known_tokens: list[str] | set[str] | None = None) -> list[str]:
    """Scan text for known active canary tokens or standard canary patterns.

    Returns a list of detected canary tokens.
    """
    if not text:
        return []

    detected: list[str] = []
    tokens_to_check = set(_ACTIVE_CANARY_TOKENS)
    if known_tokens:
        tokens_to_check.update(known_tokens)

    for token in tokens_to_check:
        if token and token in text:
            detected.append(token)

    # Also detect pattern-based canaries
    for match in CANARY_TOKEN_PATTERN.finditer(text):
        matched_str = match.group(0)
        if matched_str not in detected:
            detected.append(matched_str)

    return detected


def scrub_logs_and_credentials(
    text: str,
    extra_secrets: list[Any] | None = None,
) -> str:
    """Scrub sensitive credentials, API keys, canary tokens, and passwords from logs and text.

    Replaces all detected sensitive tokens with asterisks ('********').
    """
    if not text:
        return ""

    secrets_to_mask: list[Any] = list(_ACTIVE_CANARY_TOKENS)
    if extra_secrets:
        secrets_to_mask.extend(extra_secrets)

    # 1. First pass: use sanitize_secret_values from literature base
    sanitized = sanitize_secret_values(text, secrets=secrets_to_mask)

    # 2. Second pass: scrub any remaining canary patterns
    sanitized = CANARY_TOKEN_PATTERN.sub("********", sanitized)

    # 3. Third pass: scrub common credential assignments: KEY=..., TOKEN=..., PASSWORD=...
    sanitized = re.sub(
        r"((?:API_KEY|SECRET|PASSWORD|TOKEN|AUTH_KEY)[=:\s]+)[A-Za-z0-9_\-\.]{8,}",
        r"\1********",
        sanitized,
        flags=re.IGNORECASE,
    )

    return sanitized
