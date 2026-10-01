"""REX Configuration System (REX-002).

Provides typed application settings using Pydantic Settings.
Configuration is organized into logical sections with secure local-first defaults,
environment variable overrides, and strict secret masking via SecretStr.
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application-level configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    name: str = Field(
        default="rex",
        validation_alias=AliasChoices("REX_APP_NAME", "REX_APP__NAME", "name"),
        description="Application name",
    )
    version: str = Field(
        default="0.1.0",
        validation_alias=AliasChoices("REX_APP_VERSION", "REX_APP__VERSION", "version"),
        description="Application version",
    )
    environment: Literal["development", "production", "test"] = Field(
        default="development",
        validation_alias=AliasChoices("REX_ENV", "REX_APP__ENVIRONMENT", "environment"),
        description="Runtime environment mode",
    )
    debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("REX_DEBUG", "REX_APP__DEBUG", "debug"),
        description="Enable debug mode",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias=AliasChoices("REX_LOG_LEVEL", "REX_APP__LOG_LEVEL", "log_level"),
        description="Application logging level",
    )


class PersistenceSettings(BaseSettings):
    """Persistence and filesystem storage configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = Field(
        default="sqlite:///./data/database/rex.db",
        validation_alias=AliasChoices(
            "REX_DATABASE_URL", "REX_PERSISTENCE__DATABASE_URL", "database_url"
        ),
        description="Database connection URL (defaults to local SQLite file)",
    )
    artifact_root: Path = Field(
        default=Path("./data/runs"),
        validation_alias=AliasChoices(
            "REX_ARTIFACT_ROOT", "REX_PERSISTENCE__ARTIFACT_ROOT", "artifact_root"
        ),
        description="Root directory for storing run execution artifacts",
    )
    workspace_root: Path = Field(
        default=Path("./experiments"),
        validation_alias=AliasChoices(
            "REX_WORKSPACE_ROOT", "REX_PERSISTENCE__WORKSPACE_ROOT", "workspace_root"
        ),
        description="Root directory for experiment code and configurations",
    )


class DockerSettings(BaseSettings):
    """Docker container execution sandboxing configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("REX_DOCKER_ENABLED", "REX_DOCKER__ENABLED", "enabled"),
        description="Whether Docker containerized execution is enabled",
    )
    required: bool = Field(
        default=True,
        validation_alias=AliasChoices("REX_DOCKER_REQUIRED", "REX_DOCKER__REQUIRED", "required"),
        description="Whether Docker is strictly required (fails closed if unavailable)",
    )
    image: str = Field(
        default="python:3.11-slim",
        validation_alias=AliasChoices("REX_DOCKER_IMAGE", "REX_DOCKER__IMAGE", "image"),
        description="Docker image for experiment execution sandbox",
    )
    network_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "REX_NETWORK_POLICY",
            "REX_DOCKER_NETWORK_DISABLED",
            "REX_DOCKER__NETWORK_DISABLED",
            "network_disabled",
        ),
        description="Disable container networking by default for security isolation",
    )
    non_root_user: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "REX_DOCKER_NON_ROOT_USER", "REX_DOCKER__NON_ROOT_USER", "non_root_user"
        ),
        description="Execute inside container as non-root user",
    )
    read_only_root_fs: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "REX_DOCKER_READ_ONLY_ROOT_FS", "REX_DOCKER__READ_ONLY_ROOT_FS", "read_only_root_fs"
        ),
        description="Enforce read-only root filesystem in container",
    )
    cpu_limit: float = Field(
        default=2.0,
        gt=0.0,
        validation_alias=AliasChoices(
            "REX_MAX_CPU", "REX_DOCKER_CPU_LIMIT", "REX_DOCKER__CPU_LIMIT", "cpu_limit"
        ),
        description="Maximum CPU cores allocated to container",
    )
    memory_limit_mb: int = Field(
        default=2048,
        ge=128,
        validation_alias=AliasChoices(
            "REX_MAX_MEMORY_MB",
            "REX_DOCKER_MEMORY_LIMIT_MB",
            "REX_DOCKER__MEMORY_LIMIT_MB",
            "memory_limit_mb",
        ),
        description="Maximum memory allocated to container in megabytes",
    )
    timeout_seconds: int = Field(
        default=300,
        gt=0,
        validation_alias=AliasChoices(
            "REX_MAX_EXPERIMENT_RUNTIME_SECONDS",
            "REX_DOCKER_TIMEOUT_SECONDS",
            "REX_DOCKER__TIMEOUT_SECONDS",
            "timeout_seconds",
        ),
        description="Wall-clock timeout in seconds for container execution",
    )
    max_output_size_bytes: int = Field(
        default=10 * 1024 * 1024,  # 10 MB
        gt=0,
        validation_alias=AliasChoices(
            "REX_MAX_OUTPUT_SIZE_BYTES",
            "REX_DOCKER__MAX_OUTPUT_SIZE_BYTES",
            "max_output_size_bytes",
        ),
        description="Maximum stdout/stderr log output size in bytes",
    )
    workspace_isolation: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "REX_DOCKER_WORKSPACE_ISOLATION",
            "REX_DOCKER__WORKSPACE_ISOLATION",
            "workspace_isolation",
        ),
        description="Mount only isolated run workspace into container",
    )

    @field_validator("network_disabled", mode="before")
    @classmethod
    def _validate_network_disabled(cls, v: Any) -> bool:
        if isinstance(v, str):
            if v.lower() in ("disabled", "none", "true", "1", "yes"):
                return True
            if v.lower() in ("enabled", "false", "0", "no"):
                return False
        return bool(v)


class LLMSettings(BaseSettings):
    """LLM provider configuration (provider-neutral)."""

    model_config = SettingsConfigDict(extra="ignore")

    provider: str = Field(
        default="mock",
        validation_alias=AliasChoices("REX_LLM_PROVIDER", "REX_LLM__PROVIDER", "provider"),
        description="LLM provider name (mock, gemini, openai, groq)",
    )
    model: str = Field(
        default="mock-model",
        validation_alias=AliasChoices("REX_LLM_MODEL", "REX_LLM__MODEL", "model"),
        description="Model identifier",
    )
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("REX_LLM_API_KEY", "REX_LLM__API_KEY", "api_key"),
        description="API key for LLM provider (quarantined as SecretStr)",
    )
    api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("REX_LLM_API_BASE", "REX_LLM__API_BASE", "api_base"),
        description="Base URL for LLM provider API endpoint",
    )
    request_timeout_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias=AliasChoices(
            "REX_LLM_REQUEST_TIMEOUT_SECONDS",
            "REX_LLM__REQUEST_TIMEOUT_SECONDS",
            "request_timeout_seconds",
        ),
        description="Request timeout in seconds for LLM calls",
    )
    max_token_cost: float | None = Field(
        default=None,
        ge=0.0,
        validation_alias=AliasChoices(
            "REX_LLM_MAX_TOKEN_COST", "REX_LLM__MAX_TOKEN_COST", "max_token_cost"
        ),
        description="Optional maximum budget cost in USD for LLM usage",
    )


class LiteratureSettings(BaseSettings):
    """Literature scholarly search and retrieval configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "REX_LITERATURE_ENABLED", "REX_LITERATURE__ENABLED", "enabled"
        ),
        description="Whether literature retrieval is enabled (default False for V1)",
    )
    request_timeout_seconds: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices(
            "REX_LITERATURE_REQUEST_TIMEOUT_SECONDS",
            "REX_LITERATURE__REQUEST_TIMEOUT_SECONDS",
            "request_timeout_seconds",
        ),
        description="Timeout in seconds for external literature API requests",
    )
    openalex_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REX_OPENALEX_API_KEY", "REX_LITERATURE__OPENALEX_API_KEY", "openalex_api_key"
        ),
        description="Optional API key for OpenAlex scholarly API",
    )
    semantic_scholar_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REX_SEMANTIC_SCHOLAR_API_KEY",
            "REX_LITERATURE__SEMANTIC_SCHOLAR_API_KEY",
            "semantic_scholar_api_key",
        ),
        description="Optional API key for Semantic Scholar Academic Graph API",
    )
    openalex_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REX_OPENALEX_EMAIL", "REX_LITERATURE__OPENALEX_EMAIL", "openalex_email"
        ),
        description="Optional contact email for OpenAlex polite pool API requests",
    )
    max_results_limit: int = Field(
        default=50,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "REX_LITERATURE_MAX_RESULTS",
            "REX_LITERATURE__MAX_RESULTS_LIMIT",
            "max_results_limit",
        ),
        description="Hard ceiling on maximum literature results returned per query",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        validation_alias=AliasChoices(
            "REX_LITERATURE_MAX_RETRIES", "REX_LITERATURE__MAX_RETRIES", "max_retries"
        ),
        description="Maximum retry attempts on transient network or 5xx provider failures",
    )
    rate_limit_delay_seconds: float = Field(
        default=0.5,
        ge=0.0,
        validation_alias=AliasChoices(
            "REX_LITERATURE_RATE_DELAY",
            "REX_LITERATURE__RATE_LIMIT_DELAY_SECONDS",
            "rate_limit_delay_seconds",
        ),
        description="Minimum inter-request delay in seconds for provider etiquette",
    )
    openalex_base_url: str = Field(
        default="https://api.openalex.org",
        validation_alias=AliasChoices(
            "REX_OPENALEX_BASE_URL", "REX_LITERATURE__OPENALEX_BASE_URL", "openalex_base_url"
        ),
        description="Base URL for OpenAlex API",
    )
    semantic_scholar_base_url: str = Field(
        default="https://api.semanticscholar.org/graph/v1",
        validation_alias=AliasChoices(
            "REX_SEMANTIC_SCHOLAR_BASE_URL",
            "REX_LITERATURE__SEMANTIC_SCHOLAR_BASE_URL",
            "semantic_scholar_base_url",
        ),
        description="Base URL for Semantic Scholar API",
    )
    arxiv_base_url: str = Field(
        default="https://export.arxiv.org/api/query",
        validation_alias=AliasChoices(
            "REX_ARXIV_BASE_URL", "REX_LITERATURE__ARXIV_BASE_URL", "arxiv_base_url"
        ),
        description="Base URL for arXiv API query endpoint",
    )


class BudgetSettings(BaseSettings):
    """Resource budget caps preventing runaway autonomous execution."""

    model_config = SettingsConfigDict(extra="ignore")

    max_experiments: int = Field(
        default=10,
        ge=1,
        validation_alias=AliasChoices(
            "REX_MAX_EXPERIMENTS", "REX_BUDGETS__MAX_EXPERIMENTS", "max_experiments"
        ),
        description="Maximum number of experiments permitted in a research run",
    )
    max_executions: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices(
            "REX_MAX_EXECUTIONS", "REX_BUDGETS__MAX_EXECUTIONS", "max_executions"
        ),
        description="Maximum number of total execution runs permitted",
    )
    max_runtime_seconds: int = Field(
        default=3600,
        gt=0,
        validation_alias=AliasChoices(
            "REX_MAX_BUDGET_RUNTIME_SECONDS",
            "REX_BUDGETS__MAX_RUNTIME_SECONDS",
            "max_runtime_seconds",
        ),
        description="Maximum aggregate execution runtime in seconds for research run",
    )
    max_llm_calls: int = Field(
        default=100,
        ge=1,
        validation_alias=AliasChoices(
            "REX_MAX_LLM_CALLS", "REX_BUDGETS__MAX_LLM_CALLS", "max_llm_calls"
        ),
        description="Maximum number of LLM API requests permitted",
    )
    max_token_cost: float | None = Field(
        default=None,
        ge=0.0,
        validation_alias=AliasChoices(
            "REX_BUDGET_MAX_TOKEN_COST", "REX_BUDGETS__MAX_TOKEN_COST", "max_token_cost"
        ),
        description="Maximum token spend limit in USD",
    )
    max_artifact_volume_bytes: int = Field(
        default=500 * 1024 * 1024,  # 500 MB
        gt=0,
        validation_alias=AliasChoices(
            "REX_MAX_ARTIFACT_VOLUME_BYTES",
            "REX_BUDGETS__MAX_ARTIFACT_VOLUME_BYTES",
            "max_artifact_volume_bytes",
        ),
        description="Maximum total disk volume for artifacts in bytes",
    )
    max_concurrent_executions: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices(
            "REX_MAX_CONCURRENT_EXECUTIONS",
            "REX_BUDGETS__MAX_CONCURRENT_EXECUTIONS",
            "max_concurrent_executions",
        ),
        description="Maximum number of concurrent executions permitted (default 1 for local)",
    )


class RexSettings(BaseSettings):
    """Root configuration object composing all REX subsystem settings.

    Can be loaded directly from environment variables or initialized with explicit overrides.
    Does not use global mutable state.
    """

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    persistence: PersistenceSettings = Field(default_factory=PersistenceSettings)
    docker: DockerSettings = Field(default_factory=DockerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    literature: LiteratureSettings = Field(default_factory=LiteratureSettings)
    budgets: BudgetSettings = Field(default_factory=BudgetSettings)

    # Convenience properties for common accessors
    @property
    def environment(self) -> str:
        return self.app.environment

    @property
    def database_url(self) -> str:
        return self.persistence.database_url

    @property
    def artifact_root(self) -> Path:
        return self.persistence.artifact_root

    @property
    def workspace_root(self) -> Path:
        return self.persistence.workspace_root


def load_settings(**overrides: Any) -> RexSettings:
    """Load and validate REX settings from environment variables with optional keyword overrides.

    This function produces a fresh, independent RexSettings instance each time,
    avoiding global mutable configuration state.
    """
    return RexSettings(**overrides)


def get_settings() -> RexSettings:
    """Convenience accessor returning default settings loaded from the current environment."""
    return load_settings()
