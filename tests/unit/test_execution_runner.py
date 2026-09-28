"""Unit tests for Execution Runner Service and Lifecycle Coordinator (REX-009)."""

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from rex.controller.executions import create_execution
from rex.controller.experiments import create_experiment
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ArtifactType,
    ExecutionStatus,
    ExperimentStatus,
)
from rex.execution.backend import ExecutionBackend
from rex.execution.models import (
    ExecutionOutcome,
    ExecutionRequest,
    OutputArtifactMetadata,
)
from rex.execution.runner import run_execution_in_sandbox
from rex.observability.events import ActorType
from rex.persistence.database import create_db_engine, init_db
from rex.persistence.models import ArtifactModel, ExecutionModel, ExperimentModel


class MockBackend(ExecutionBackend):
    """Stub execution backend for controller integration testing."""

    def __init__(self, outcome: ExecutionOutcome | None = None) -> None:
        self.outcome = outcome
        self.executed_requests: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        self.executed_requests.append(request)
        if self.outcome is not None:
            return self.outcome
        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="run completed\n",
            stderr="",
            output_artifacts=[
                OutputArtifactMetadata(
                    path="metrics.json",
                    content_hash="a" * 64,
                    size_bytes=128,
                    artifact_type=ArtifactType.METRIC,
                )
            ],
            duration_seconds=1.5,
            cleaned_up=True,
        )

    def cancel(self, execution_id: str) -> bool:
        return True


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    db_path = tmp_path / "test_exec_runner.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_run_execution_in_sandbox_completes_and_persists_artifacts(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        run = create_research_run(
            session=session,
            research_question="Can model convergence be improved with adaptive learning rates?",
            title="Test Run",
            actor=ActorType.OWNER,
        )
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Evaluate baseline learning rate convergence.",
            actor=ActorType.RESEARCH_AGENT,
        )
        execution = create_execution(
            session=session,
            experiment_id=exp.id,
            command="python src/main.py",
            actor=ActorType.EXECUTION_WORKER,
        )
        session.commit()

        backend = MockBackend()
        code_files = {"main.py": "print('running')"}

        domain_exec, outcome = run_execution_in_sandbox(
            session=session,
            execution_id=execution.id,
            backend=backend,
            code_files=code_files,
        )
        session.commit()

        assert domain_exec.status == ExecutionStatus.COMPLETED
        assert outcome.status == ExecutionStatus.COMPLETED
        assert domain_exec.exit_code == 0

        # Verify artifacts were persisted
        artifacts = session.query(ArtifactModel).filter_by(execution_id=execution.id).all()
        # Should have metrics.json and stdout.log
        art_paths = {a.path: a.artifact_type for a in artifacts}
        assert "metrics.json" in art_paths
        assert art_paths["metrics.json"] == ArtifactType.METRIC.value
        assert "stdout.log" in art_paths
        assert art_paths["stdout.log"] == ArtifactType.LOG.value

        # Verify parent experiment status was transitioned to COMPLETED
        refreshed_exp = session.get(ExperimentModel, exp.id)
        assert refreshed_exp.status == ExperimentStatus.COMPLETED.value


def test_run_execution_in_sandbox_handles_timeout(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        run = create_research_run(
            session=session,
            research_question="Can timeout handling recover gracefully?",
            title="Test Run Timeout",
            actor=ActorType.OWNER,
        )
        exp = create_experiment(
            session=session,
            research_run_id=run.id,
            objective="Evaluate timeout recovery behavior.",
            actor=ActorType.RESEARCH_AGENT,
        )
        execution = create_execution(
            session=session,
            experiment_id=exp.id,
            command="python src/hang.py",
            actor=ActorType.EXECUTION_WORKER,
        )
        session.commit()

        timeout_outcome = ExecutionOutcome(
            execution_id=execution.id,
            experiment_id=exp.id,
            research_run_id=run.id,
            status=ExecutionStatus.TIMEOUT,
            exit_code=None,
            stdout="",
            stderr="Wall-clock timeout exceeded.\n",
            duration_seconds=300.0,
            failure_reason="Execution timed out after 300 seconds.",
            cleaned_up=True,
        )
        backend = MockBackend(outcome=timeout_outcome)

        domain_exec, outcome = run_execution_in_sandbox(
            session=session,
            execution_id=execution.id,
            backend=backend,
            code_files={"hang.py": "import time; time.sleep(999)"},
        )
        session.commit()

        assert domain_exec.status == ExecutionStatus.TIMEOUT
        assert outcome.status == ExecutionStatus.TIMEOUT

        refreshed_exec = session.get(ExecutionModel, execution.id)
        assert refreshed_exec.status == ExecutionStatus.TIMEOUT.value
