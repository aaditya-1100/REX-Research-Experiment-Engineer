"""REX Docker Execution Worker Domain Service (REX-017).

Coordinates the end-to-end experimental execution pipeline:
ExperimentSpecification -> Workspace Creation -> Safe Environment Capture ->
Isolated Container Execution (Orchestrator) -> Output Artifact Capture ->
Deterministic Metric Extraction -> Safe Workspace Cleanup -> Immutable ExecutionRecord.
"""

from __future__ import annotations

import hashlib
import logging
import shlex
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from rex.domain.models import ArtifactType, Execution, ExecutionStatus, Experiment

if TYPE_CHECKING:
    from rex.analysis.metrics import MetricExtractor
    from rex.controller.execution_orchestrator import ExecutionOrchestrator
from rex.execution.backend import ExecutionBackend
from rex.execution.docker_runner import DockerExecutionBackend
from rex.execution.environment import (
    capture_safe_environment_metadata,
    save_environment_metadata_artifact,
)
from rex.execution.exceptions import ExecutionError
from rex.execution.models import ExecutionRecord, ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.execution.workspace import Workspace, WorkspaceManager
from rex.observability.events import ActorType, EventSink
from rex.persistence.database import get_db_session
from rex.persistence.models import ExecutionModel, ExperimentModel

logger = logging.getLogger(__name__)


def compute_code_hash(code_files: Mapping[str, str]) -> str:
    """Compute deterministic cryptographic SHA-256 digest over sorted experiment code files."""
    hasher = hashlib.sha256()
    for path in sorted(code_files.keys()):
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(code_files[path].encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


class DockerExecutionWorker:
    """Production service for managing isolated experiment execution runs and outcomes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        backend: ExecutionBackend | None = None,
        workspace_manager: WorkspaceManager | None = None,
        orchestrator: ExecutionOrchestrator | None = None,
        metric_extractor: MetricExtractor | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.backend = backend or DockerExecutionBackend(workspace_manager=self.workspace_manager)
        self.event_sink = event_sink
        if orchestrator is not None:
            self.orchestrator = orchestrator
        else:
            from rex.controller.execution_orchestrator import ExecutionOrchestrator

            self.orchestrator = ExecutionOrchestrator(
                session_factory=self.session_factory,
                backend=self.backend,
                event_sink=self.event_sink,
            )
        if metric_extractor is not None:
            self.metric_extractor = metric_extractor
        else:
            from rex.analysis.metrics import MetricExtractor

            self.metric_extractor = MetricExtractor()

    def execute_experiment(
        self,
        experiment: Experiment | ExperimentModel | str,
        code_files: Mapping[str, str],
        execution_id: str | None = None,
        command: list[str] | None = None,
        limits: ResourceLimits | None = None,
        environment_variables: Mapping[str, str] | None = None,
        image: str | None = None,
        network_disabled: bool = True,
        non_root_user: bool = True,
        actor: ActorType | str = ActorType.EXECUTION_WORKER,
        context: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> ExecutionRecord:
        """Run an experiment through the deterministic isolated sandbox pipeline.

        1. Validates experiment entity in database.
        2. Creates or locates pending Execution record.
        3. Prepares isolated filesystem workspace (src, input, output, logs, metadata, artifacts).
        4. Captures deterministic environment metadata and writes metadata/environment.json.
        5. Executes inside isolated sandbox via ExecutionOrchestrator (enforces budgets, concurrency).
        6. Extracts empirical metrics from workspace outputs (validating finite floats, rejecting NaN/Inf).
        7. Performs selective evidence-preserving cleanup of scratch files.
        8. Returns immutable, strongly typed ExecutionRecord.
        """
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
        from rex.controller.executions import create_execution, record_artifact

        # 1. Resolve experiment record
        exp_id: str
        run_id: str
        with get_db_session(self.session_factory) as session:
            exp_model: ExperimentModel | None
            if isinstance(experiment, str):
                exp_model = session.get(ExperimentModel, experiment)
            elif isinstance(experiment, (Experiment, ExperimentModel)):
                exp_model = session.get(ExperimentModel, experiment.id)
            else:
                raise TypeError(f"Unsupported experiment type: {type(experiment).__name__}")

            if exp_model is None:
                target_id = (
                    experiment if isinstance(experiment, str) else getattr(experiment, "id", "")
                )
                raise ExecutionError(f"Experiment '{target_id}' does not exist in database.")

            exp_id = exp_model.id
            run_id = exp_model.research_run_id

            # Determine command
            spec_dict = exp_model.specification_json or {}
            spec_cmd = spec_dict.get("command") or spec_dict.get("execution_command")
            if command is not None:
                cmd_list = command
            elif isinstance(spec_cmd, str) and spec_cmd.strip():
                cmd_list = shlex.split(spec_cmd.strip())
            elif isinstance(spec_cmd, list):
                cmd_list = [str(c) for c in spec_cmd]
            else:
                cmd_list = ["python", "src/main.py"]
            cmd_str = " ".join(cmd_list)

            # Determine seed
            effective_seed = seed if seed is not None else spec_dict.get("seed")

            # 2. Create pending Execution record if not already created
            active_exec_id = execution_id
            if active_exec_id is None:
                code_hash = compute_code_hash(code_files)
                created_exec: Execution = create_execution(
                    session=session,
                    experiment_id=exp_id,
                    status=ExecutionStatus.PENDING,
                    command=cmd_str,
                    code_hash=code_hash,
                    seed=effective_seed,
                    actor=actor_enum,
                    event_sink=self.event_sink,
                    context=context,
                )
                active_exec_id = created_exec.id
            else:
                existing_exec = session.get(ExecutionModel, active_exec_id)
                if existing_exec is None:
                    raise ExecutionError(f"Specified execution_id '{active_exec_id}' not found.")

        # 3. Prepare workspace with code files
        effective_limits = limits or ResourceLimits.from_settings()
        req = ExecutionRequest(
            execution_id=active_exec_id,
            experiment_id=exp_id,
            research_run_id=run_id,
            command=cmd_list,
            code_files=code_files,
            image=image,
            environment_variables=environment_variables or {},
            limits=effective_limits,
            network_disabled=network_disabled,
            non_root_user=non_root_user,
            seed=effective_seed,
        )
        workspace: Workspace = self.workspace_manager.prepare_workspace(req, raise_if_exists=True)

        # 4. Capture environment metadata and save manifest
        env_meta_dict = capture_safe_environment_metadata(
            env=environment_variables,
            image_reference=image or "",
            command=cmd_list,
            seed=effective_seed,
            include_packages=True,
        )
        env_art_meta = save_environment_metadata_artifact(workspace, env_meta_dict)

        with get_db_session(self.session_factory) as session:
            record_artifact(
                session=session,
                artifact_type=ArtifactType.MANIFEST,
                path=env_art_meta.path,
                content_hash=env_art_meta.content_hash,
                size_bytes=env_art_meta.size_bytes,
                execution_id=active_exec_id,
                metadata=env_art_meta.metadata,
                actor=actor_enum,
                event_sink=self.event_sink,
                context=context,
            )
            # Update environment_json in execution model
            exec_row = session.get(ExecutionModel, active_exec_id)
            if exec_row is not None:
                exec_row.environment_json = env_meta_dict
                session.flush()

        # 5. Dispatch execution to orchestrator (outside DB lock)
        exec_domain, outcome = self.orchestrator.run_execution(
            execution_id=active_exec_id,
            code_files=code_files,
            command=cmd_list,
            limits=effective_limits,
            environment_variables=environment_variables,
            image=image,
            network_disabled=network_disabled,
            non_root_user=non_root_user,
            actor=actor_enum,
            context=context,
        )

        # 6. Extract empirical metrics from workspace output directory
        results_list: list[dict[str, Any]] = []
        with get_db_session(self.session_factory) as session:
            recorded_results = self.metric_extractor.extract_and_record(
                session=session,
                execution_id=active_exec_id,
                workspace=workspace,
                actor=actor_enum,
                event_sink=self.event_sink,
                context=context,
            )
            for r in recorded_results:
                results_list.append(
                    {
                        "id": r.id,
                        "metric_name": r.metric_name,
                        "metric_value": r.metric_value,
                        "metric_unit": r.metric_unit,
                        "result_data": dict(r.result_data),
                    }
                )

        # 7. Evidence-preserving workspace cleanup
        self.workspace_manager.cleanup_workspace(workspace, retain_evidence=True)

        # 8. Assemble final immutable ExecutionRecord
        return ExecutionRecord(
            execution_id=active_exec_id,
            experiment_id=exp_id,
            research_run_id=run_id,
            status=exec_domain.status,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            duration_seconds=outcome.duration_seconds,
            artifacts=outcome.output_artifacts,
            results=results_list,
            environment_metadata=env_meta_dict,
            resource_usage=outcome.resource_usage,
            failure_reason=outcome.failure_reason,
            cleaned_up=outcome.cleaned_up,
            started_at=exec_domain.started_at,
            completed_at=exec_domain.finished_at,
        )
