"""Unit tests for REX Execution Models and Resource Constraints (REX-009)."""

import pytest
from pydantic import ValidationError

from rex.config import RexSettings
from rex.domain.models import ArtifactType, ExecutionStatus
from rex.execution.exceptions import CommandValidationError
from rex.execution.models import (
    ExecutionOutcome,
    ExecutionRequest,
    OutputArtifactMetadata,
)
from rex.execution.resources import ResourceLimits


class TestExecutionRequest:
    """Tests for ExecutionRequest creation and validation."""

    def test_valid_request(self) -> None:
        req = ExecutionRequest(
            execution_id="exec-123",
            experiment_id="exp-456",
            research_run_id="run-789",
            command=["python", "src/train.py", "--epochs", "5"],
            code_files={"main.py": "print('hello')"},
            seed=42,
        )
        assert req.execution_id == "exec-123"
        assert req.experiment_id == "exp-456"
        assert req.research_run_id == "run-789"
        assert req.command == ["python", "src/train.py", "--epochs", "5"]
        assert req.code_files == {"main.py": "print('hello')"}
        assert req.seed == 42
        assert req.network_disabled is True
        assert req.non_root_user is True

    def test_string_command_coerced_to_list(self) -> None:
        req = ExecutionRequest(
            execution_id="exec-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            command="python main.py --batch-size 32",
        )
        assert req.command == ["python", "main.py", "--batch-size", "32"]

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(CommandValidationError, match="cannot be empty"):
            ExecutionRequest(
                execution_id="exec-1",
                experiment_id="exp-1",
                research_run_id="run-1",
                command=[],
            )

    @pytest.mark.parametrize(
        "operator",
        ["&&", "||", ";", "|", ">", "<", "`", "$(", "${", "&"],
    )
    def test_shell_injection_operators_rejected(self, operator: str) -> None:
        with pytest.raises(CommandValidationError, match="prohibited shell operator"):
            ExecutionRequest(
                execution_id="exec-1",
                experiment_id="exp-1",
                research_run_id="run-1",
                command=["python", f"train.py {operator} malicious.sh"],
            )

    def test_empty_identifier_rejected(self) -> None:
        with pytest.raises(ValueError, match="Identifier must be a non-empty string"):
            ExecutionRequest(
                execution_id="",
                experiment_id="exp-1",
                research_run_id="run-1",
                command=["python", "main.py"],
            )


class TestResourceLimits:
    """Tests for ResourceLimits validation and defaults."""

    def test_default_resource_limits(self) -> None:
        limits = ResourceLimits()
        assert limits.cpu_limit == 2.0
        assert limits.memory_limit_mb == 2048
        assert limits.timeout_seconds == 300
        assert limits.max_output_size_bytes == 10 * 1024 * 1024

    def test_invalid_cpu_limit(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            ResourceLimits(cpu_limit=0.0)
        with pytest.raises((ValueError, ValidationError)):
            ResourceLimits(cpu_limit=256.0)

    def test_invalid_memory_limit(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            ResourceLimits(memory_limit_mb=64)

    def test_invalid_timeout(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            ResourceLimits(timeout_seconds=0)

    def test_from_settings(self) -> None:
        settings = RexSettings()
        limits = ResourceLimits.from_settings(settings)
        assert limits.cpu_limit == settings.docker.cpu_limit
        assert limits.memory_limit_mb == settings.docker.memory_limit_mb
        assert limits.timeout_seconds == settings.docker.timeout_seconds


class TestExecutionOutcome:
    """Tests for ExecutionOutcome representation."""

    def test_successful_outcome(self) -> None:
        outcome = ExecutionOutcome(
            execution_id="exec-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Training completed successfully.\n",
            stderr="",
            output_artifacts=[
                OutputArtifactMetadata(
                    path="metrics.json",
                    content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    size_bytes=42,
                    artifact_type=ArtifactType.METRIC,
                )
            ],
            duration_seconds=5.2,
            cleaned_up=True,
        )
        assert outcome.status == ExecutionStatus.COMPLETED
        assert outcome.exit_code == 0
        assert outcome.failure_reason is None
        assert outcome.cleaned_up is True
        assert len(outcome.output_artifacts) == 1
        assert outcome.output_artifacts[0].artifact_type == ArtifactType.METRIC
