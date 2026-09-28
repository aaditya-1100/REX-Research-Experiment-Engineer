"""Unit tests for REX Environment Sanitization and Metadata Capture (REX-009)."""

import pytest

from rex.execution.environment import (
    capture_safe_environment_metadata,
    is_sensitive_key,
    sanitize_environment,
)
from rex.execution.exceptions import SecretLeakageError


class TestEnvironmentSanitization:
    """Tests for environment variable secret detection and sanitization."""

    @pytest.mark.parametrize(
        "key",
        [
            "API_KEY",
            "OPENAI_API_KEY",
            "REX_LLM_API_KEY",
            "DATABASE_URL",
            "REX_DATABASE_URL",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "AUTH_TOKEN",
            "DB_PASSWORD",
            "SSH_PRIVATE_KEY",
            "stripe_secret_key",
            "my_secret_token",
        ],
    )
    def test_sensitive_keys_identified(self, key: str) -> None:
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "BATCH_SIZE",
            "LEARNING_RATE",
            "EPOCHS",
            "SEED",
            "MODEL_NAME",
            "OPTIMIZER",
            "DEVICE",
            "PYTHONPATH",
        ],
    )
    def test_safe_keys_allowed(self, key: str) -> None:
        assert is_sensitive_key(key) is False

    def test_sanitize_clean_environment(self) -> None:
        env = {
            "BATCH_SIZE": "64",
            "LEARNING_RATE": "0.001",
            "DEVICE": "cpu",
        }
        sanitized = sanitize_environment(env)
        assert sanitized == env

    def test_sanitize_sensitive_environment_raises(self) -> None:
        env = {
            "BATCH_SIZE": "64",
            "OPENAI_API_KEY": "sk-secret-key-12345",
        }
        with pytest.raises(SecretLeakageError, match="Prohibited sensitive environment variable"):
            sanitize_environment(env)

    def test_capture_safe_metadata_never_includes_values(self) -> None:
        env = {
            "BATCH_SIZE": "64",
            "LEARNING_RATE": "0.001",
            "OPENAI_API_KEY": "sk-secret-12345",  # should be excluded from safe keys
        }
        meta = capture_safe_environment_metadata(env, image_reference="python:3.11-slim")
        assert meta["image"] == "python:3.11-slim"
        assert "BATCH_SIZE" in meta["injected_env_keys"]
        assert "LEARNING_RATE" in meta["injected_env_keys"]
        assert "OPENAI_API_KEY" not in meta["injected_env_keys"]
        # Ensure values are not in metadata
        assert "sk-secret-12345" not in str(meta)
