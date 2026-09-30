"""Unit tests for Docker Execution Worker Domain Service (REX-017)."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rex.domain.models import ArtifactType, ExecutionStatus, ExperimentStatus, ResearchState
from rex.execution.backend import ExecutionBackend
from rex.execution.exceptions import ExecutionError
from rex.execution.models import (
    ExecutionOutcome,
    ExecutionRecord,
    ExecutionRequest,
)
from rex.execution.worker import DockerExecutionWorker
from rex.execution.workspace import WorkspaceManager
from rex.persistence.database import Base
from rex.persistence.models import (
    ArtifactModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class MockWorkerBackend(ExecutionBackend):
    """Mock execution backend simulating container execution and workspace outputs."""

    def __init__(
        self, workspace_manager: WorkspaceManager, outcome: ExecutionOutcome | None = None
    ) -> None:
        self.workspace_manager = workspace_manager
        self.outcome = outcome
        self.executed_requests: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        self.executed_requests.append(request)

        # Simulate container writing outputs into workspace
        ws = self.workspace_manager.prepare_workspace(request)
        (ws.output_dir / "metrics.json").write_text(
            '{"val_loss": 0.08, "val_accuracy": 0.96}', encoding="utf-8"
        )
        (ws.output_dir / "model.pt").write_bytes(b"mock model weights")

        artifacts = self.workspace_manager.collect_output_artifacts(ws)

        if self.outcome is not None:
            return self.outcome

        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Training completed successfully.\nFinal accuracy: 0.96",
            stderr="",
            output_artifacts=artifacts,
            duration_seconds=2.5,
            cleaned_up=True,
        )


class TestDockerExecutionWorker:
    """Tests for the DockerExecutionWorker service orchestrating the experimental pipeline."""

    def test_execute_experiment_end_to_end_success(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-w-001",
                title="Deep Learning Study",
                research_question="Does model X improve accuracy?",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-w-001",
                research_run_id=run.id,
                objective="Train baseline model",
                status=ExperimentStatus.DESIGNED.value,
            )
            session.add(exp)
            session.commit()

        wm = WorkspaceManager(base_root=tmp_path / "workspaces")
        backend = MockWorkerBackend(workspace_manager=wm)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            backend=backend,
            workspace_manager=wm,
        )

        code = {
            "train.py": "import torch\nprint('Training...')",
            "utils.py": "def helper(): pass",
        }

        record: ExecutionRecord = worker.execute_experiment(
            experiment="exp-w-001",
            code_files=code,
            command=["python", "src/train.py"],
            environment_variables={"LR": "0.001"},
            seed=42,
        )

        # 1. Verify ExecutionRecord properties
        assert isinstance(record, ExecutionRecord)
        assert record.experiment_id == "exp-w-001"
        assert record.research_run_id == "run-w-001"
        assert record.status == ExecutionStatus.COMPLETED
        assert record.exit_code == 0
        assert "Training completed successfully" in record.stdout
        assert record.duration_seconds == 2.5
        assert len(record.artifacts) >= 2  # metrics.json and model.pt
        assert len(record.results) == 2  # val_loss and val_accuracy

        # Verify extracted empirical metric values
        metric_map = {r["metric_name"]: r["metric_value"] for r in record.results}
        assert metric_map["val_loss"] == 0.08
        assert metric_map["val_accuracy"] == 0.96

        # Verify environment metadata
        assert "python_version" in record.environment_metadata
        assert record.environment_metadata["seed"] == 42
        assert "LR" in record.environment_metadata["injected_env_keys"]

        # 2. Verify database state
        with session_factory() as session:
            exec_db = session.get(ExecutionModel, record.execution_id)
            assert exec_db is not None
            assert exec_db.status == ExecutionStatus.COMPLETED.value
            assert exec_db.exit_code == 0
            assert exec_db.seed == 42
            assert exec_db.environment_json is not None

            # Verify persisted results
            results_db = (
                session.query(ResultModel)
                .filter(ResultModel.execution_id == record.execution_id)
                .all()
            )
            assert len(results_db) == 2

            # Verify persisted artifacts (environment manifest + outputs + stdout log)
            arts_db = (
                session.query(ArtifactModel)
                .filter(ArtifactModel.execution_id == record.execution_id)
                .all()
            )
            types = {a.artifact_type for a in arts_db}
            assert ArtifactType.MANIFEST.value in types
            assert ArtifactType.LOG.value in types

        # 3. Verify selective workspace cleanup
        ws_dir = wm.base_root / "run-w-001" / record.execution_id
        assert ws_dir.exists()
        # Temporary src should be cleaned up
        assert not (ws_dir / "src").exists()
        # Outputs, logs, and metadata should be preserved
        assert (ws_dir / "output").exists()
        assert (ws_dir / "metadata").exists()

    def test_execute_experiment_failure_handling(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-fail",
                title="Fail Test",
                research_question="Failure handling test",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-fail",
                research_run_id=run.id,
                objective="Fail experiment",
            )
            session.add(exp)
            session.commit()

        wm = WorkspaceManager(base_root=tmp_path / "workspaces")
        failed_outcome = ExecutionOutcome(
            execution_id="exec-failed-1",
            experiment_id="exp-fail",
            research_run_id="run-fail",
            status=ExecutionStatus.FAILED,
            exit_code=1,
            stdout="",
            stderr="MemoryError: out of memory",
            failure_reason="Process terminated with exit code 1",
            cleaned_up=True,
        )
        backend = MockWorkerBackend(workspace_manager=wm, outcome=failed_outcome)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            backend=backend,
            workspace_manager=wm,
        )

        record = worker.execute_experiment(
            experiment="exp-fail",
            code_files={"main.py": "raise MemoryError"},
        )

        assert record.status == ExecutionStatus.FAILED
        assert record.exit_code == 1
        assert "MemoryError" in record.stderr
        assert record.failure_reason == "Process terminated with exit code 1"

        with session_factory() as session:
            exec_db = session.get(ExecutionModel, record.execution_id)
            assert exec_db is not None
            assert exec_db.status == ExecutionStatus.FAILED.value

    def test_execute_non_existent_experiment_raises(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        wm = WorkspaceManager(base_root=tmp_path)
        backend = MockWorkerBackend(workspace_manager=wm)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            backend=backend,
            workspace_manager=wm,
        )

        with pytest.raises(ExecutionError, match="does not exist"):
            worker.execute_experiment(
                experiment="missing-exp-id",
                code_files={},
            )
