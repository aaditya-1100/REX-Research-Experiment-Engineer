"""Unit tests for Docker Execution Backend and Container Sandbox (REX-009)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rex.domain.models import ExecutionStatus
from rex.execution.docker_runner import (
    DockerExecutionBackend,
    is_docker_available,
)
from rex.execution.exceptions import (
    DockerUnavailableError,
    ImagePolicyError,
    SecretLeakageError,
)
from rex.execution.models import ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.execution.workspace import WorkspaceManager


class TestDockerAvailability:
    """Tests for fail-closed behavior when Docker daemon is not available."""

    def test_is_docker_available_returns_false_on_error(self) -> None:
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("Daemon connection refused")
        assert is_docker_available(mock_client) is False

    def test_fails_closed_when_docker_unavailable(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("Cannot connect to Docker daemon")

        backend = DockerExecutionBackend(
            docker_client=mock_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        req = ExecutionRequest(
            execution_id="exec-fail-closed",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "main.py"],
        )

        with pytest.raises(DockerUnavailableError, match="fails closed"):
            backend.execute(req)


class TestDockerExecutionBackend:
    """Tests for Docker execution lifecycle with stubbed Docker client."""

    @pytest.fixture
    def mock_docker_client(self) -> MagicMock:
        client = MagicMock()
        client.ping.return_value = True
        return client

    def test_image_policy_rejection(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
            allowed_images=["python:3.11-slim"],
        )

        req = ExecutionRequest(
            execution_id="exec-img-policy",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "main.py"],
            image="malicious/unapproved-rootkit:latest",
        )

        with pytest.raises(ImagePolicyError, match="violates image security policy"):
            backend.execute(req)

        # Ensure container was never created
        mock_docker_client.containers.run.assert_not_called()

    def test_secret_leakage_prevented_before_container_launch(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        req = ExecutionRequest(
            execution_id="exec-secret",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "main.py"],
            environment_variables={"AWS_SECRET_ACCESS_KEY": "super-secret-key"},
        )

        with pytest.raises(SecretLeakageError, match="Prohibited sensitive environment variable"):
            backend.execute(req)

        mock_docker_client.containers.run.assert_not_called()

    def test_successful_container_execution(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.logs.side_effect = lambda stdout, stderr: (
            b"Experiment epoch 1 completed\n" if stdout else b""
        )
        mock_docker_client.containers.run.return_value = mock_container

        wm = WorkspaceManager(base_root=tmp_path)
        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=wm,
        )

        req = ExecutionRequest(
            execution_id="exec-ok",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "src/train.py"],
            code_files={"train.py": "print('hello')"},
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert outcome.exit_code == 0
        assert "Experiment epoch 1 completed" in outcome.stdout
        assert outcome.failure_reason is None
        assert outcome.cleaned_up is True
        mock_container.remove.assert_called_once_with(force=True)

    def test_failed_container_execution(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 1}}
        mock_container.logs.side_effect = lambda stdout, stderr: (
            b"" if stdout else b"ZeroDivisionError: division by zero\n"
        )
        mock_docker_client.containers.run.return_value = mock_container

        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        req = ExecutionRequest(
            execution_id="exec-fail",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "src/train.py"],
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.FAILED
        assert outcome.exit_code == 1
        assert "ZeroDivisionError" in outcome.stderr
        assert outcome.cleaned_up is True
        mock_container.remove.assert_called_once_with(force=True)

    def test_log_truncation_enforced(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        # 2 MB logs with 1 MB limit
        big_log = b"A" * (2 * 1024 * 1024)
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.logs.side_effect = lambda stdout, stderr: big_log if stdout else b""
        mock_docker_client.containers.run.return_value = mock_container

        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        limits = ResourceLimits(max_output_size_bytes=1024 * 1024)
        req = ExecutionRequest(
            execution_id="exec-trunc",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "src/train.py"],
            limits=limits,
        )

        outcome = backend.execute(req)

        assert outcome.stdout_truncated is True
        assert len(outcome.stdout.encode("utf-8")) == 1024 * 1024
        assert outcome.cleaned_up is True

    def test_container_cleanup_guaranteed_on_exception(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_container = MagicMock()
        # Simulate unexpected reload error
        mock_container.reload.side_effect = RuntimeError("Fatal socket error")
        mock_docker_client.containers.run.return_value = mock_container

        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        req = ExecutionRequest(
            execution_id="exec-exc",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "main.py"],
        )

        outcome = backend.execute(req)
        assert outcome.status == ExecutionStatus.FAILED
        assert outcome.cleaned_up is True
        mock_container.remove.assert_called_once_with(force=True)

    def test_timeout_container_execution(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.attrs = {"State": {"ExitCode": None}}
        mock_docker_client.containers.run.return_value = mock_container

        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        limits = ResourceLimits(timeout_seconds=1)
        req = ExecutionRequest(
            execution_id="exec-timeout",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "src/train.py"],
            limits=limits,
        )

        outcome = backend.execute(req)
        assert outcome.status == ExecutionStatus.TIMEOUT
        assert outcome.cleaned_up is True
        mock_container.kill.assert_called_once()
        mock_container.remove.assert_called_once_with(force=True)

    def test_cancellation_execution(
        self,
        mock_docker_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker_client.containers.run.return_value = mock_container

        backend = DockerExecutionBackend(
            docker_client=mock_docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        # Cancel ahead of execution launch
        backend.cancel("exec-cancel")

        req = ExecutionRequest(
            execution_id="exec-cancel",
            experiment_id="exp-1",
            research_run_id="run-1",
            command=["python", "src/train.py"],
            limits=ResourceLimits(timeout_seconds=5),
        )

        outcome = backend.execute(req)
        assert outcome.status == ExecutionStatus.CANCELLED
        assert outcome.cleaned_up is True
        mock_container.kill.assert_called_once()
        mock_container.remove.assert_called_once_with(force=True)
