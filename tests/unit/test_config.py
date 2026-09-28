"""Unit tests for REX Configuration System (REX-002)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rex.config import (
    AppSettings,
    BudgetSettings,
    DockerSettings,
    LiteratureSettings,
    LLMSettings,
    PersistenceSettings,
    RexSettings,
    get_settings,
    load_settings,
)


def test_default_configuration_loads_successfully():
    """1. Default configuration loads successfully without exceptions."""
    settings = load_settings()
    assert isinstance(settings, RexSettings)
    assert isinstance(settings.app, AppSettings)
    assert isinstance(settings.persistence, PersistenceSettings)
    assert isinstance(settings.docker, DockerSettings)
    assert isinstance(settings.llm, LLMSettings)
    assert isinstance(settings.literature, LiteratureSettings)
    assert isinstance(settings.budgets, BudgetSettings)


def test_environment_variables_override_defaults(monkeypatch):
    """2. Environment variables override default values."""
    monkeypatch.setenv("REX_ENV", "test")
    monkeypatch.setenv("REX_DEBUG", "true")
    monkeypatch.setenv("REX_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("REX_DATABASE_URL", "sqlite:///./custom_data.db")
    monkeypatch.setenv("REX_MAX_CPU", "4.0")
    monkeypatch.setenv("REX_MAX_MEMORY_MB", "4096")
    monkeypatch.setenv("REX_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("REX_MAX_EXPERIMENTS", "25")

    settings = load_settings()
    assert settings.app.environment == "test"
    assert settings.app.debug is True
    assert settings.app.log_level == "DEBUG"
    assert settings.persistence.database_url == "sqlite:///./custom_data.db"
    assert settings.docker.cpu_limit == 4.0
    assert settings.docker.memory_limit_mb == 4096
    assert settings.llm.provider == "gemini"
    assert settings.budgets.max_experiments == 25


def test_sqlite_and_local_artifact_paths_with_defaults():
    """3. SQLite and local artifact paths default to secure local-first architecture."""
    settings = load_settings()
    assert settings.persistence.database_url == "sqlite:///./data/database/rex.db"
    assert settings.persistence.artifact_root == Path("./data/runs")
    assert settings.persistence.workspace_root == Path("./experiments")

    # Verify convenience accessors
    assert settings.database_url == "sqlite:///./data/database/rex.db"
    assert settings.artifact_root == Path("./data/runs")
    assert settings.workspace_root == Path("./experiments")


def test_docker_security_defaults_are_correct():
    """4. Docker execution configuration has secure defaults."""
    settings = load_settings()
    assert settings.docker.enabled is True
    assert settings.docker.required is True
    assert settings.docker.image == "python:3.11-slim"
    assert settings.docker.workspace_isolation is True


def test_network_disabled_by_default(monkeypatch):
    """5. Network access inside sandbox is disabled by default."""
    settings = load_settings()
    assert settings.docker.network_disabled is True

    # Test policy string parsing from REX_NETWORK_POLICY
    monkeypatch.setenv("REX_NETWORK_POLICY", "disabled")
    s_disabled = load_settings()
    assert s_disabled.docker.network_disabled is True

    monkeypatch.setenv("REX_NETWORK_POLICY", "none")
    s_none = load_settings()
    assert s_none.docker.network_disabled is True


def test_non_root_execution_enabled_by_default():
    """6. Non-root user execution is enabled by default."""
    settings = load_settings()
    assert settings.docker.non_root_user is True


def test_read_only_root_filesystem_enabled_by_default():
    """7. Read-only root filesystem is enabled by default."""
    settings = load_settings()
    assert settings.docker.read_only_root_fs is True


def test_resource_limits_have_bounded_defaults():
    """8. Execution quotas and research budgets have conservative, bounded defaults."""
    settings = load_settings()
    # Docker quotas
    assert settings.docker.cpu_limit == 2.0
    assert settings.docker.memory_limit_mb == 2048
    assert settings.docker.timeout_seconds == 300
    assert settings.docker.max_output_size_bytes == 10 * 1024 * 1024

    # Research budgets
    assert settings.budgets.max_experiments == 10
    assert settings.budgets.max_executions == 30
    assert settings.budgets.max_runtime_seconds == 3600
    assert settings.budgets.max_llm_calls == 100
    assert settings.budgets.max_artifact_volume_bytes == 500 * 1024 * 1024
    assert settings.budgets.max_concurrent_executions == 1


def test_api_credentials_read_from_environment(monkeypatch):
    """9. API credentials are read from environment and protected via SecretStr."""
    dummy_key = "sk-rex-test-secret-value-xyz"
    monkeypatch.setenv("REX_LLM_API_KEY", dummy_key)
    monkeypatch.setenv("REX_OPENALEX_API_KEY", "openalex-test-key")

    settings = load_settings()
    assert settings.llm.api_key is not None
    assert settings.llm.api_key.get_secret_value() == dummy_key

    # Secret must be masked in string and repr representations
    assert str(settings.llm.api_key) == "**********"
    assert dummy_key not in repr(settings.llm.api_key)
    assert dummy_key not in repr(settings.llm)
    assert dummy_key not in repr(settings)

    # Literature credentials
    assert settings.literature.openalex_api_key is not None
    assert settings.literature.openalex_api_key.get_secret_value() == "openalex-test-key"
    assert "openalex-test-key" not in repr(settings)


def test_missing_optional_credentials_do_not_break_loading(monkeypatch):
    """10. Missing optional credentials do not prevent configuration loading."""
    monkeypatch.delenv("REX_LLM_API_KEY", raising=False)
    monkeypatch.delenv("REX_OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("REX_SEMANTIC_SCHOLAR_API_KEY", raising=False)

    settings = load_settings()
    assert settings.llm.api_key is None
    assert settings.literature.openalex_api_key is None
    assert settings.literature.semantic_scholar_api_key is None


def test_no_host_execution_fallback_exists_or_enabled():
    """11. Invariant: No silent host-execution fallback mechanism exists."""
    settings = load_settings()
    # Confirm Docker is required
    assert settings.docker.required is True

    # Assert no silent fallback attributes exist
    assert not hasattr(settings.docker, "fallback_to_subprocess")
    assert not hasattr(settings.docker, "allow_host_fallback")
    assert not hasattr(settings.docker, "host_fallback")
    assert not hasattr(settings, "fallback_to_host")


def test_invalid_configuration_fails_validation():
    """12. Invalid configuration values trigger clear Pydantic validation errors."""
    # Negative CPU limit
    with pytest.raises(ValidationError):
        DockerSettings(cpu_limit=-1.0)

    # Memory below minimum (128 MB)
    with pytest.raises(ValidationError):
        DockerSettings(memory_limit_mb=64)

    # Non-positive timeout
    with pytest.raises(ValidationError):
        DockerSettings(timeout_seconds=0)

    # Invalid environment mode
    with pytest.raises(ValidationError):
        AppSettings(environment="invalid_mode")  # type: ignore

    # Zero experiments budget
    with pytest.raises(ValidationError):
        BudgetSettings(max_experiments=0)

    # Zero runtime budget
    with pytest.raises(ValidationError):
        BudgetSettings(max_runtime_seconds=0)


def test_configuration_is_typed_without_global_mutable_state():
    """13. Configuration objects are typed and independent without global mutable state."""
    s1 = load_settings(app=AppSettings(name="instance-1"))
    s2 = load_settings(app=AppSettings(name="instance-2"))

    assert s1.app.name == "instance-1"
    assert s2.app.name == "instance-2"
    assert s1 is not s2

    # get_settings() also produces independent instances
    g1 = get_settings()
    g2 = get_settings()
    assert g1 is not g2
