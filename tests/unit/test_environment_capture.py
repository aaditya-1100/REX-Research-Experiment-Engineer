"""Unit tests for Environment Sanitization, Metadata Capture, and Provenance (REX-019)."""

import hashlib
import json
from pathlib import Path

import pytest

from rex.domain.models import ArtifactType
from rex.execution.environment import (
    EnvironmentMetadata,
    capture_safe_environment_metadata,
    is_sensitive_key,
    sanitize_environment,
    save_environment_metadata_artifact,
)
from rex.execution.exceptions import SecretLeakageError
from rex.execution.models import ExecutionRequest
from rex.execution.workspace import WorkspaceManager


class TestEnvironmentCapture:
    """Tests for safe environment metadata extraction and artifact saving."""

    def test_capture_safe_environment_metadata_basic(self) -> None:
        meta = capture_safe_environment_metadata(
            env={"BATCH_SIZE": "32", "LEARNING_RATE": "0.001"},
            image_reference="python:3.11-slim",
            command=["python", "src/train.py"],
            seed=42,
            include_packages=True,
        )

        assert isinstance(meta, dict)
        assert "python_version" in meta
        assert "platform" in meta
        assert "platform_machine" in meta
        assert meta["image"] == "python:3.11-slim"
        assert meta["seed"] == 42
        assert meta["command"] == ["python", "src/train.py"]
        assert "BATCH_SIZE" in meta["injected_env_keys"]
        assert "LEARNING_RATE" in meta["injected_env_keys"]

        # Packages must be captured as a dict
        assert isinstance(meta["packages"], dict)
        assert len(meta["packages"]) > 0

    def test_sensitive_environment_variable_detection(self) -> None:
        assert is_sensitive_key("OPENAI_API_KEY") is True
        assert is_sensitive_key("REX_DATABASE_URL") is True
        assert is_sensitive_key("AWS_SECRET_ACCESS_KEY") is True
        assert is_sensitive_key("GITHUB_TOKEN") is True
        assert is_sensitive_key("AUTH_PASSWORD") is True
        assert is_sensitive_key("PRIVATE_KEY") is True
        assert is_sensitive_key("USER_SECRET") is True

        assert is_sensitive_key("BATCH_SIZE") is False
        assert is_sensitive_key("EPOCHS") is False
        assert is_sensitive_key("MODEL_NAME") is False

    def test_sanitize_environment_rejects_secrets(self) -> None:
        safe_env = {"LR": "0.01", "OPTIMIZER": "adam"}
        sanitized = sanitize_environment(safe_env)
        assert sanitized == safe_env

        leaky_env = {"LR": "0.01", "API_KEY": "super_secret_token"}
        with pytest.raises(SecretLeakageError):
            sanitize_environment(leaky_env)

    def test_save_environment_metadata_artifact(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-env-001",
            experiment_id="exp-env-001",
            research_run_id="run-env-001",
            command=["python", "main.py"],
        )
        ws = mgr.prepare_workspace(req)

        meta = EnvironmentMetadata(
            platform="Linux",
            platform_release="5.15.0",
            platform_machine="x86_64",
            python_version="3.11.8",
            python_implementation="CPython",
            python_compiler="GCC",
            cpu_count=8,
            image="python:3.11-slim",
            injected_env_keys=["EPOCHS"],
            packages={"numpy": "1.26.0"},
            command=["python", "main.py"],
            seed=123,
        )

        artifact_meta = save_environment_metadata_artifact(ws, meta)

        assert artifact_meta.artifact_type == ArtifactType.MANIFEST
        assert artifact_meta.path == "metadata/environment.json"

        # Check saved file on disk
        target_file = ws.metadata_dir / "environment.json"
        assert target_file.exists()

        content_bytes = target_file.read_bytes()
        assert hashlib.sha256(content_bytes).hexdigest() == artifact_meta.content_hash
        assert len(content_bytes) == artifact_meta.size_bytes

        # Verify parsed contents
        saved_dict = json.loads(content_bytes.decode("utf-8"))
        assert saved_dict["python_version"] == "3.11.8"
        assert saved_dict["packages"]["numpy"] == "1.26.0"
        assert saved_dict["seed"] == 123
