"""Unit tests for Execution, Result, and Artifact domain models and deep immutability (REX-008)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rex.domain.models import (
    EXECUTION_ARTIFACT_TYPES,
    TERMINAL_EXECUTION_STATUSES,
    Artifact,
    ArtifactType,
    Execution,
    ExecutionStatus,
    Result,
)
from rex.persistence.models import ArtifactModel, ExecutionModel, ResultModel

# ==============================================================================
# Execution Domain Tests
# ==============================================================================


def test_valid_execution_construction_and_defaults():
    """Verify default values and valid instantiation of Execution entity."""
    execution = Execution(
        experiment_id="exp_test_001",
    )

    assert execution.id.startswith("exec_")
    assert execution.experiment_id == "exp_test_001"
    assert execution.status == ExecutionStatus.PENDING
    assert execution.started_at is None
    assert execution.finished_at is None
    assert execution.command == ""
    assert execution.git_commit == ""
    assert execution.code_hash == ""
    assert execution.dataset_hash == ""
    assert execution.configuration_hash == ""
    assert execution.seed is None
    assert execution.exit_code is None
    assert not execution.is_terminal


def test_execution_validation_rejects_empty_ids():
    """Verify empty or whitespace-only identifiers are rejected."""
    with pytest.raises(ValidationError):
        Execution(experiment_id="")

    with pytest.raises(ValidationError):
        Execution(id="   ", experiment_id="exp_1")


def test_execution_top_level_and_nested_immutability():
    """Verify top-level and nested mapping immutability on Execution."""
    env = {"python": "3.11", "os": "linux", "packages": {"torch": "2.1.0"}}
    telemetry = {"peak_gpu_mem_mb": 4096, "cpu_percent": 85.5}

    execution = Execution(
        experiment_id="exp_1",
        environment=env,
        resource_usage=telemetry,
    )

    # Top-level frozen immutability
    with pytest.raises(ValidationError):
        execution.status = ExecutionStatus.RUNNING  # type: ignore[misc]

    # Nested mapping proxy immutability
    with pytest.raises(TypeError):
        execution.environment["python"] = "3.12"  # type: ignore[index]

    with pytest.raises(TypeError):
        execution.resource_usage["peak_gpu_mem_mb"] = 8192  # type: ignore[index]

    # Caller mutation isolation (defensive copy)
    env["python"] = "3.9"
    env["injected"] = "fail"
    telemetry["cpu_percent"] = 100.0

    assert execution.environment["python"] == "3.11"
    assert "injected" not in execution.environment
    assert execution.resource_usage["cpu_percent"] == 85.5


def test_execution_with_status_evolution():
    """Verify with_status returns a new immutable Execution instance."""
    now = datetime.now(UTC)
    execution = Execution(
        id="exec_evolve",
        experiment_id="exp_1",
        command="python train.py",
        seed=42,
    )

    running = execution.with_status(ExecutionStatus.RUNNING, started_at=now)
    assert running is not execution
    assert running.id == "exec_evolve"
    assert running.status == ExecutionStatus.RUNNING
    assert running.started_at == now
    assert execution.status == ExecutionStatus.PENDING

    completed = running.with_status(ExecutionStatus.COMPLETED, exit_code=0, finished_at=now)
    assert completed.status == ExecutionStatus.COMPLETED
    assert completed.exit_code == 0
    assert completed.is_terminal


def test_execution_persistence_roundtrip():
    """Verify exact roundtrip fidelity between Execution domain and ExecutionModel ORM."""
    started = datetime.now(UTC)
    finished = datetime.now(UTC)

    original = Execution(
        id="exec_roundtrip",
        experiment_id="exp_roundtrip",
        status=ExecutionStatus.COMPLETED,
        started_at=started,
        finished_at=finished,
        command="python evaluate.py --split test",
        git_commit="abcdef1234567890",
        code_hash="sha256:codehash",
        dataset_hash="sha256:datasethash",
        configuration_hash="sha256:confighash",
        seed=1234,
        environment={"cuda": "12.1", "torch": "2.2.0"},
        resource_usage={"duration_seconds": 124.5, "gpu_mem_gb": 7.8},
        exit_code=0,
        stdout_artifact_id="art_stdout",
        stderr_artifact_id="art_stderr",
    )

    model = original.to_persistence()
    assert isinstance(model, ExecutionModel)
    assert model.id == "exec_roundtrip"
    assert model.experiment_id == "exp_roundtrip"
    assert model.status == "completed"
    assert model.command == "python evaluate.py --split test"
    assert model.exit_code == 0
    assert model.environment_json["cuda"] == "12.1"
    assert model.resource_usage_json["gpu_mem_gb"] == 7.8

    restored = Execution.from_persistence(model)
    assert restored.id == original.id
    assert restored.experiment_id == original.experiment_id
    assert restored.status == original.status
    assert restored.started_at == original.started_at
    assert restored.finished_at == original.finished_at
    assert restored.command == original.command
    assert restored.git_commit == original.git_commit
    assert restored.seed == original.seed
    assert restored.environment == original.environment
    assert restored.resource_usage == original.resource_usage
    assert restored.exit_code == original.exit_code
    assert restored.stdout_artifact_id == original.stdout_artifact_id
    assert restored.stderr_artifact_id == original.stderr_artifact_id


def test_execution_naive_timestamp_timezone_conversion():
    """Verify naive datetime in ExecutionModel is safely converted to UTC."""
    naive_dt = datetime(2026, 9, 28, 14, 30, 0)  # noqa: DTZ001
    model = ExecutionModel(
        id="exec_naive",
        experiment_id="exp_1",
        status="running",
        command="python run.py",
        git_commit="abcdef0",
        code_hash="c" * 64,
        dataset_hash="d" * 64,
        configuration_hash="e" * 64,
        seed=42,
        started_at=naive_dt,
    )
    restored = Execution.from_persistence(model)
    assert restored.started_at is not None
    assert restored.started_at.tzinfo == UTC
    assert restored.started_at.hour == 14


def test_execution_terminal_statuses():
    """Verify is_terminal property behavior across statuses."""
    for terminal in TERMINAL_EXECUTION_STATUSES:
        e = Execution(experiment_id="exp_1", status=terminal)
        assert e.is_terminal

    for non_terminal in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
        e = Execution(experiment_id="exp_1", status=non_terminal)
        assert not e.is_terminal


# ==============================================================================
# Result Domain Tests
# ==============================================================================


def test_valid_result_construction_and_defaults():
    """Verify valid Result construction and attributes."""
    result = Result(
        execution_id="exec_101",
        metric_name="top1_accuracy",
        metric_value=0.945,
        metric_unit="%",
        result_data={"step": 100, "val_loss": 0.12},
    )

    assert result.id.startswith("res_")
    assert result.execution_id == "exec_101"
    assert result.metric_name == "top1_accuracy"
    assert result.metric_value == 0.945
    assert result.metric_unit == "%"
    assert result.result_data["step"] == 100
    assert result.created_at.tzinfo == UTC


def test_result_validation_rejects_empty():
    """Verify required field validation on Result."""
    with pytest.raises(ValidationError):
        Result(execution_id="", metric_name="acc")

    with pytest.raises(ValidationError):
        Result(execution_id="exec_1", metric_name="   ")


def test_result_metric_value_finite_validation():
    """Verify scalar metric_value rejects NaN, +inf, -inf, and accepts finite floats."""
    # NaN rejected
    with pytest.raises(ValidationError) as exc_info:
        Result(execution_id="exec_1", metric_name="acc", metric_value=float("nan"))
    assert "finite number" in str(exc_info.value)

    # +inf rejected
    with pytest.raises(ValidationError) as exc_info:
        Result(execution_id="exec_1", metric_name="acc", metric_value=float("inf"))
    assert "finite number" in str(exc_info.value)

    # -inf rejected
    with pytest.raises(ValidationError) as exc_info:
        Result(execution_id="exec_1", metric_name="acc", metric_value=float("-inf"))
    assert "finite number" in str(exc_info.value)

    # 0.0 accepted
    r0 = Result(execution_id="exec_1", metric_name="loss", metric_value=0.0)
    assert r0.metric_value == 0.0

    # normal positive/negative values accepted
    r_pos = Result(execution_id="exec_1", metric_name="acc", metric_value=98.5)
    assert r_pos.metric_value == 98.5

    r_neg = Result(execution_id="exec_1", metric_name="log_loss", metric_value=-0.042)
    assert r_neg.metric_value == -0.042

    # None accepted for optional metric_value
    r_none = Result(execution_id="exec_1", metric_name="unscored")
    assert r_none.metric_value is None


def test_result_immutability():
    """Verify immutability of top-level and nested result data."""
    data = {"confusion_matrix": [[10, 2], [1, 15]]}
    result = Result(
        execution_id="exec_1",
        metric_name="eval_matrix",
        result_data=data,
    )

    with pytest.raises(ValidationError):
        result.metric_value = 0.99  # type: ignore[misc]

    with pytest.raises(TypeError):
        result.result_data["confusion_matrix"] = []  # type: ignore[index]

    # Caller isolation
    data["confusion_matrix"] = "mutated"
    assert isinstance(result.result_data["confusion_matrix"], tuple)


def test_result_persistence_roundtrip():
    """Verify Result domain to persistence model roundtrip."""
    original = Result(
        id="res_001",
        execution_id="exec_001",
        metric_name="latency_ms",
        metric_value=12.4,
        metric_unit="ms",
        result_data={"p50": 11.2, "p99": 24.8},
    )

    model = original.to_persistence()
    assert isinstance(model, ResultModel)
    assert model.id == "res_001"
    assert model.metric_name == "latency_ms"
    assert model.metric_value == 12.4
    assert model.result_json["p99"] == 24.8

    restored = Result.from_persistence(model)
    assert restored.id == original.id
    assert restored.execution_id == original.execution_id
    assert restored.metric_name == original.metric_name
    assert restored.metric_value == original.metric_value
    assert restored.result_data == original.result_data
    assert restored.created_at == original.created_at


# ==============================================================================
# Artifact Domain Tests
# ==============================================================================


def test_valid_artifact_construction_and_defaults():
    """Verify valid Artifact construction and default attributes."""
    art = Artifact(
        research_run_id="run_101",
        execution_id="exec_101",
        artifact_type=ArtifactType.PLOT,
        path="plots/accuracy_curve.png",
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        size_bytes=45230,
        metadata={"format": "png", "dpi": 300},
    )

    assert art.id.startswith("art_")
    assert art.research_run_id == "run_101"
    assert art.execution_id == "exec_101"
    assert art.artifact_type == ArtifactType.PLOT
    assert art.path == "plots/accuracy_curve.png"
    assert art.size_bytes == 45230
    assert art.created_at.tzinfo == UTC


def test_artifact_validation_rejects_empty():
    """Verify required field validation on Artifact."""
    with pytest.raises(ValidationError):
        Artifact(
            research_run_id="",
            path="log.txt",
            content_hash="abc",
        )

    with pytest.raises(ValidationError):
        Artifact(
            research_run_id="run_1",
            path="   ",
            content_hash="abc",
        )

    with pytest.raises(ValidationError):
        Artifact(
            research_run_id="run_1",
            path="log.txt",
            content_hash="",
        )

    with pytest.raises(ValidationError):
        Artifact(
            research_run_id="run_1",
            path="log.txt",
            content_hash="abc",
            size_bytes=-1,
        )


def test_artifact_type_coercion():
    """Verify string coercion into ArtifactType."""
    art = Artifact(
        research_run_id="run_1",
        execution_id="exec_1",
        artifact_type="stdout",
        path="stdout.log",
        content_hash="hash123",
    )
    assert art.artifact_type == ArtifactType.STDOUT

    art_unknown = Artifact(
        research_run_id="run_1",
        artifact_type="nonexistent_type",
        path="unknown.dat",
        content_hash="hash456",
    )
    assert art_unknown.artifact_type == ArtifactType.OTHER


def test_artifact_execution_provenance_enforced():
    """Verify execution artifact types require execution_id, while standalone types do not."""
    for exec_type in EXECUTION_ARTIFACT_TYPES:
        with pytest.raises(ValidationError) as exc_info:
            Artifact(
                research_run_id="run_1",
                artifact_type=exec_type,
                path="output.dat",
                content_hash="hash123",
                execution_id=None,
            )
        assert "represent execution outputs" in str(exc_info.value)

        # Passes with execution_id
        art = Artifact(
            research_run_id="run_1",
            artifact_type=exec_type,
            path="output.dat",
            content_hash="hash123",
            execution_id="exec_1",
        )
        assert art.is_execution_artifact is True
        assert art.execution_id == "exec_1"

    # Standalone run-level artifacts do NOT require execution_id
    for standalone_type in (
        ArtifactType.OTHER,
        ArtifactType.DATASET,
        ArtifactType.CODE,
        ArtifactType.MODEL,
        ArtifactType.MANIFEST,
    ):
        art = Artifact(
            research_run_id="run_1",
            artifact_type=standalone_type,
            path="data/split.csv",
            content_hash="hash456",
            execution_id=None,
        )
        assert art.is_execution_artifact is False
        assert art.execution_id is None


def test_artifact_immutability():
    """Verify Artifact domain immutability and metadata isolation."""
    meta = {"epochs": 10}
    art = Artifact(
        research_run_id="run_1",
        path="model.pt",
        content_hash="hash",
        metadata=meta,
    )

    with pytest.raises(ValidationError):
        art.path = "new_path.pt"  # type: ignore[misc]

    with pytest.raises(TypeError):
        art.metadata["epochs"] = 20  # type: ignore[index]

    meta["epochs"] = 999
    assert art.metadata["epochs"] == 10


def test_artifact_persistence_roundtrip():
    """Verify Artifact domain to persistence model roundtrip."""
    original = Artifact(
        id="art_roundtrip",
        research_run_id="run_roundtrip",
        execution_id="exec_roundtrip",
        artifact_type=ArtifactType.CHECKPOINT,
        path="checkpoints/model_best.pt",
        content_hash="6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b",
        size_bytes=104857600,
        metadata={"framework": "pytorch", "parameters_m": 85},
    )

    model = original.to_persistence()
    assert isinstance(model, ArtifactModel)
    assert model.id == "art_roundtrip"
    assert model.research_run_id == "run_roundtrip"
    assert model.execution_id == "exec_roundtrip"
    assert model.artifact_type == "checkpoint"
    assert model.size_bytes == 104857600
    assert model.metadata_json["framework"] == "pytorch"

    restored = Artifact.from_persistence(model)
    assert restored.id == original.id
    assert restored.research_run_id == original.research_run_id
    assert restored.execution_id == original.execution_id
    assert restored.artifact_type == original.artifact_type
    assert restored.path == original.path
    assert restored.content_hash == original.content_hash
    assert restored.size_bytes == original.size_bytes
    assert restored.metadata == original.metadata
    assert restored.created_at == original.created_at
