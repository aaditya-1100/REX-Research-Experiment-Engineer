"""REX Environment Sanitization and Metadata Capture (REX-009).

Prevents host credential leakage into isolated sandboxes and records safe, deterministic
execution environment metadata for scientific reproducibility.
"""

import os
import platform
import re
from collections.abc import Mapping
from typing import Any

from rex.execution.exceptions import SecretLeakageError

# Patterns and prefixes that indicate sensitive credentials
SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "auth",
    "credential",
    "private_key",
    "privkey",
)

SENSITIVE_KEY_PREFIXES: tuple[str, ...] = (
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GITHUB_",
    "OPENAI_",
    "GROQ_",
    "GEMINI_",
    "ANTHROPIC_",
    "SLACK_",
    "STRIPE_",
    "SSH_",
)

SENSITIVE_KEY_EXACT: frozenset[str] = frozenset(
    {
        "DATABASE_URL",
        "REX_DATABASE_URL",
        "REX_LLM_API_KEY",
        "REX_LITERATURE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)


def is_sensitive_key(key: str) -> bool:
    """Determine whether an environment variable key represents a sensitive credential."""
    clean_key = key.strip()
    key_upper = clean_key.upper()
    key_lower = clean_key.lower()

    if key_upper in SENSITIVE_KEY_EXACT:
        return True

    for prefix in SENSITIVE_KEY_PREFIXES:
        if key_upper.startswith(prefix):
            return True

    for pattern in SENSITIVE_KEY_SUBSTRINGS:
        if pattern in key_lower:
            return True

    # Regex check for *_KEY, *_TOKEN, *_SECRET, *_PASSWORD, *_PASS
    return bool(re.search(r"(_KEY|_TOKEN|_SECRET|_PASSWORD|_PASS)$", key_upper))


def sanitize_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    """Sanitize and validate environment variables intended for sandbox container injection.

    Raises SecretLeakageError if any sensitive variable is present.
    Returns a clean dictionary of allowed environment variables.
    """
    if not env:
        return {}

    sanitized: dict[str, str] = {}
    for key, value in env.items():
        clean_key = str(key).strip()
        if is_sensitive_key(clean_key):
            raise SecretLeakageError(
                f"Prohibited sensitive environment variable detected: '{clean_key}'. "
                "Host secrets and credentials must never be injected into experiment containers."
            )
        sanitized[clean_key] = str(value)

    return sanitized


def capture_safe_environment_metadata(
    env: Mapping[str, str] | None = None,
    image_reference: str = "",
) -> dict[str, Any]:
    """Capture host and execution environment metadata useful for provenance without leaking secrets."""
    safe_keys: list[str] = []
    if env:
        for k in sorted(env.keys()):
            if not is_sensitive_key(str(k)):
                safe_keys.append(str(k))

    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count() or 1,
        "image": image_reference,
        "injected_env_keys": safe_keys,
    }
