"""REX Execution Models and Outcome Representations (REX-009).

Defines strongly typed, validated request and outcome models for isolated container execution.
"""

import shlex
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.domain.models import ArtifactType, ExecutionStatus
from rex.execution.exceptions import CommandValidationError
from rex.execution.resources import ResourceLimits

# Prohibited shell operators that indicate injection attempts
PROHIBITED_COMMAND_PATTERNS: tuple[str, ...] = (
    "&&",
    "||",
    ";",
    "|",
    ">",
    "<",
    "`",
    "$(",
    "${",
    "&",
)


class ExecutionRequest(BaseModel):
    """Specification of an execution attempt dispatched to an execution sandbox backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(description="Unique ID of execution attempt")
    experiment_id: str = Field(description="Parent experiment specification ID")
    research_run_id: str = Field(description="Parent research run ID")
    command: list[str] = Field(description="Argv-style command line arguments")
    code_files: Mapping[str, str] = Field(
        default_factory=dict,
        description="Relative file paths and code contents to populate in workspace",
    )
    image: str | None = Field(
        default=None,
        description="Explicit container image override (must obey trusted image policy)",
    )
    environment_variables: Mapping[str, str] = Field(
        default_factory=dict,
        description="Explicitly approved environment variables for container injection",
    )
    limits: ResourceLimits = Field(
        default_factory=ResourceLimits,
        description="Computational and disk limits for the container execution",
    )
    network_disabled: bool = Field(
        default=True,
        description="Whether container network access is disabled (default true)",
    )
    non_root_user: bool = Field(
        default=True,
        description="Whether container executes as a non-root user (default true)",
    )
    seed: int | None = Field(default=None, description="Random seed for reproducibility")

    @field_validator("execution_id", "experiment_id", "research_run_id")
    @classmethod
    def _validate_identifier(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier must be a non-empty string.")
        return cleaned

    @field_validator("command", mode="before")
    @classmethod
    def _coerce_and_validate_command(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            tokens = shlex.split(v.strip())
        elif isinstance(v, (list, tuple)):
            tokens = [str(item).strip() for item in v]
        else:
            raise TypeError(f"Command must be a string or list of strings, got {type(v).__name__}.")

        if not tokens:
            raise CommandValidationError("Execution command cannot be empty.")

        # Guard against host-level shell injection patterns in argv elements
        for token in tokens:
            for pattern in PROHIBITED_COMMAND_PATTERNS:
                if pattern in token:
                    raise CommandValidationError(
                        f"Command token '{token}' contains prohibited shell operator '{pattern}'. "
                        "Commands must be formatted as raw argv lists without shell chaining."
                    )

        return tokens


class OutputArtifactMetadata(BaseModel):
    """Metadata describing a concrete file artifact produced in the container output directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(description="Relative path of artifact inside output directory")
    content_hash: str = Field(description="Cryptographic SHA-256 digest of artifact contents")
    size_bytes: int = Field(ge=0, description="Size of artifact file in bytes")
    artifact_type: ArtifactType = Field(
        default=ArtifactType.OUTPUT,
        description="Categorization of the produced artifact",
    )
    metadata: Mapping[str, Any] = Field(
        default_factory=dict,
        description="Additional file metadata (MIME type, format, shape, etc.)",
    )


class ExecutionOutcome(BaseModel):
    """Structured, fact-based outcome reported by an execution backend after a sandbox run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(description="ID of associated execution attempt")
    experiment_id: str = Field(description="Parent experiment ID")
    research_run_id: str = Field(description="Parent research run ID")
    status: ExecutionStatus = Field(description="Terminal execution status outcome")
    exit_code: int | None = Field(
        default=None,
        description="Process exit code reported by container (None on timeout or cancellation)",
    )
    stdout: str = Field(default="", description="Captured standard output text")
    stderr: str = Field(default="", description="Captured standard error text")
    stdout_truncated: bool = Field(
        default=False,
        description="Whether captured stdout was truncated due to size limits",
    )
    stderr_truncated: bool = Field(
        default=False,
        description="Whether captured stderr was truncated due to size limits",
    )
    output_artifacts: list[OutputArtifactMetadata] = Field(
        default_factory=list,
        description="Metadata of files captured from container output directory",
    )
    environment_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Deterministic environment and platform metadata",
    )
    resource_usage: dict[str, Any] = Field(
        default_factory=dict,
        description="Measured runtime and resource consumption telemetry",
    )
    duration_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock execution duration in seconds",
    )
    failure_reason: str | None = Field(
        default=None,
        description="High-level description of failure if execution did not complete successfully",
    )
    cleaned_up: bool = Field(
        default=False,
        description="Whether sandbox container and temporary resources were cleaned up",
    )


class ExecutionRecord(BaseModel):
    """Immutable, strongly typed domain record of an executed experiment run.

    Provides a comprehensive snapshot linking specification, isolated execution,
    captured artifacts, empirical measurements, environment metadata, and telemetry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(description="Unique ID of execution attempt")
    experiment_id: str = Field(description="Parent experiment specification ID")
    research_run_id: str = Field(description="Parent research run ID")
    status: ExecutionStatus = Field(description="Terminal execution status outcome")
    exit_code: int | None = Field(
        default=None,
        description="Process exit code reported by container",
    )
    stdout: str = Field(default="", description="Captured standard output text")
    stderr: str = Field(default="", description="Captured standard error text")
    duration_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock execution duration in seconds",
    )
    artifacts: list[OutputArtifactMetadata] = Field(
        default_factory=list,
        description="Metadata of files captured from container output/metadata directories",
    )
    results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Empirical machine-measured metric results",
    )
    environment_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Deterministic environment, platform, and dependency metadata",
    )
    resource_usage: dict[str, Any] = Field(
        default_factory=dict,
        description="Measured runtime and resource consumption telemetry",
    )
    failure_reason: str | None = Field(
        default=None,
        description="High-level description of failure if execution did not complete successfully",
    )
    cleaned_up: bool = Field(
        default=False,
        description="Whether sandbox container and temporary resources were cleaned up",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when execution started",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when execution terminated",
    )
