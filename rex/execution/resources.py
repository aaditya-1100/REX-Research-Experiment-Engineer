"""REX Execution Resource Limits and Constraints (REX-009).

Defines strongly typed, validated computational and disk quotas for isolated container runs.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.config import RexSettings, get_settings


class ResourceLimits(BaseModel):
    """Configurable resource quotas and limits enforced on sandbox executions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cpu_limit: float = Field(
        default=2.0,
        gt=0.0,
        description="Maximum CPU cores allocated to execution container",
    )
    memory_limit_mb: int = Field(
        default=2048,
        ge=128,
        description="Maximum RAM allocated to execution container in megabytes",
    )
    timeout_seconds: int = Field(
        default=300,
        gt=0,
        description="Maximum wall-clock execution duration in seconds before termination",
    )
    max_output_size_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Maximum captured stdout/stderr log output size in bytes (defaults to 10 MB)",
    )
    max_output_files: int = Field(
        default=1000,
        gt=0,
        description="Maximum number of output files collected from container output directory",
    )

    @field_validator("cpu_limit")
    @classmethod
    def _validate_cpu_limit(cls, v: float) -> float:
        if v <= 0.0 or v > 128.0:
            raise ValueError(f"CPU limit must be between 0.1 and 128.0 cores, got {v}.")
        return float(v)

    @field_validator("memory_limit_mb")
    @classmethod
    def _validate_memory_limit(cls, v: int) -> int:
        if v < 128 or v > 512 * 1024:
            raise ValueError(f"Memory limit must be between 128 MB and 512 GB, got {v}.")
        return int(v)

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        if v <= 0 or v > 86400:
            raise ValueError(f"Timeout must be between 1 and 86400 seconds (24h), got {v}.")
        return int(v)

    @classmethod
    def from_settings(cls, settings: RexSettings | None = None) -> "ResourceLimits":
        """Construct ResourceLimits using application configuration defaults."""
        app_settings = settings or get_settings()
        docker_cfg = app_settings.docker
        return cls(
            cpu_limit=docker_cfg.cpu_limit,
            memory_limit_mb=docker_cfg.memory_limit_mb,
            timeout_seconds=docker_cfg.timeout_seconds,
            max_output_size_bytes=docker_cfg.max_output_size_bytes,
        )
