"""REX Environment Sanitization and Metadata Capture (REX-009, REX-019).

Prevents host credential leakage into isolated sandboxes and records safe, deterministic
execution environment metadata for scientific reproducibility.
"""

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rex.domain.models import ArtifactType
from rex.execution.exceptions import SecretLeakageError
from rex.execution.models import OutputArtifactMetadata
from rex.execution.workspace import Workspace

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


logger = logging.getLogger(__name__)


def capture_installed_packages() -> dict[str, str]:
    """Capture currently installed Python package distributions deterministically."""
    packages: dict[str, str] = {}
    try:
        import importlib.metadata

        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name")
            version = dist.version
            if name and version:
                packages[name] = version
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed capturing installed packages: %s", exc)
    return dict(sorted(packages.items()))


def capture_git_commit() -> str | None:
    """Capture current git commit hash if running in a git repository."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
            if commit:
                return commit
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed capturing git commit: %s", exc)
    return None


class EnvironmentMetadata(BaseModel):
    """Deterministic environment, platform, and dependency metadata for reproducibility."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str = Field(description="Operating system kernel/platform name")
    platform_release: str = Field(description="OS release version")
    platform_machine: str = Field(description="Hardware CPU architecture")
    python_version: str = Field(description="Python runtime version")
    python_implementation: str = Field(
        default="", description="Python implementation (CPython, etc.)"
    )
    python_compiler: str = Field(default="", description="Compiler used for Python build")
    cpu_count: int = Field(default=1, ge=1, description="Logical CPU core count")
    image: str = Field(default="", description="Container image reference if containerized")
    injected_env_keys: list[str] = Field(
        default_factory=list, description="Non-sensitive environment variable names"
    )
    packages: dict[str, str] = Field(
        default_factory=dict, description="Installed Python packages and versions"
    )
    git_commit: str | None = Field(default=None, description="Repository commit SHA-1")
    command: list[str] = Field(default_factory=list, description="Execution command argv")
    seed: int | None = Field(default=None, description="Experiment random seed")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="UTC capture timestamp in ISO 8601 format",
    )

    def to_dict(self) -> dict[str, Any]:
        """Export as standard dictionary."""
        return self.model_dump()


def capture_safe_environment_metadata(
    env: Mapping[str, str] | None = None,
    image_reference: str = "",
    command: list[str] | None = None,
    seed: int | None = None,
    include_packages: bool = True,
) -> dict[str, Any]:
    """Capture host and execution environment metadata useful for provenance without leaking secrets."""
    safe_keys: list[str] = []
    if env:
        for k in sorted(env.keys()):
            if not is_sensitive_key(str(k)):
                safe_keys.append(str(k))

    packages = capture_installed_packages() if include_packages else {}
    git_commit = capture_git_commit()

    meta = EnvironmentMetadata(
        platform=platform.system(),
        platform_release=platform.release(),
        platform_machine=platform.machine(),
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        python_compiler=platform.python_compiler(),
        cpu_count=os.cpu_count() or 1,
        image=image_reference,
        injected_env_keys=safe_keys,
        packages=packages,
        git_commit=git_commit,
        command=command or [],
        seed=seed,
    )
    return meta.to_dict()


def save_environment_metadata_artifact(
    workspace: Workspace,
    metadata: EnvironmentMetadata | Mapping[str, Any],
) -> OutputArtifactMetadata:
    """Serialize and save environment metadata as a manifest artifact in workspace metadata directory."""
    raw_dict = (
        metadata.model_dump() if isinstance(metadata, EnvironmentMetadata) else dict(metadata)
    )
    content_str = json.dumps(raw_dict, indent=2, sort_keys=True)
    content_bytes = content_str.encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    size_bytes = len(content_bytes)

    meta_file = workspace.metadata_dir / "environment.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_bytes(content_bytes)

    return OutputArtifactMetadata(
        path="metadata/environment.json",
        content_hash=content_hash,
        size_bytes=size_bytes,
        artifact_type=ArtifactType.MANIFEST,
        metadata={"category": "environment", "filename": "environment.json"},
    )
