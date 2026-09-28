"""REX Execution Runner Service and Lifecycle Coordinator (REX-009).

Coordinates between the Execution Domain Controller (REX-008) and the
Sandboxed Execution Backend (REX-009). Manages the transition to RUNNING,
sandbox invocation, artifact collection, result registration, and terminal status transition.
"""

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from rex.controller.executions import (
    record_artifact,
    update_execution_status,
)
from rex.domain.models import (
    ArtifactType,
    Execution,
    ExecutionStatus,
)
from rex.execution.backend import ExecutionBackend
from rex.execution.models import ExecutionOutcome, ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.observability.events import ActorType, EventSink
from rex.persistence.database import get_db_session
from rex.persistence.models import ExecutionModel, ExperimentModel

logger = logging.getLogger(__name__)


def run_execution_in_sandbox(
    session: Session,
    execution_id: str,
    backend: ExecutionBackend,
    code_files: Mapping[str, str],
    command: list[str] | None = None,
    limits: ResourceLimits | None = None,
    environment_variables: Mapping[str, str] | None = None,
    image: str | None = None,
    network_disabled: bool = True,
    non_root_user: bool = True,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[Execution, ExecutionOutcome]:
    """Execute a persisted execution record inside the provided isolated sandbox backend.

    Transitions the execution through its deterministic lifecycle:
    1. Validates execution existence and status.
    2. Transitions execution status to RUNNING.
    3. Builds validated ExecutionRequest and calls backend.execute().
    4. Records produced output artifacts with SHA-256 digests.
    5. Saves stdout/stderr log artifacts if present.
    6. Transitions execution status to COMPLETED, FAILED, TIMEOUT, or CANCELLED.
    7. Returns updated domain Execution and ExecutionOutcome.
    """
    actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

    # 1. Inspect execution record
    exec_model = session.get(ExecutionModel, execution_id)
    if exec_model is None:
        from rex.controller.executions import MissingExecutionError

        raise MissingExecutionError(execution_id)

    exp_model = session.get(ExperimentModel, exec_model.experiment_id)
    if exp_model is None:
        from rex.controller.executions import MissingExperimentError

        raise MissingExperimentError(exec_model.experiment_id)

    cmd_list: list[str]
    if command is not None:
        cmd_list = command
    elif exec_model.command:
        import shlex

        cmd_list = shlex.split(exec_model.command)
    else:
        cmd_list = ["python", "src/main.py"]

    # 2. Transition execution status to RUNNING
    if exec_model.status == ExecutionStatus.PENDING.value:
        update_execution_status(
            session=session,
            execution_id=execution_id,
            new_status=ExecutionStatus.RUNNING,
            actor=actor_enum,
            reason="Execution started in sandbox container.",
            event_sink=event_sink,
            context=context,
        )

    # 3. Construct validated ExecutionRequest
    request = ExecutionRequest(
        execution_id=exec_model.id,
        experiment_id=exp_model.id,
        research_run_id=exp_model.research_run_id,
        command=cmd_list,
        code_files=code_files,
        image=image,
        environment_variables=environment_variables or {},
        limits=limits or ResourceLimits.from_settings(),
        network_disabled=network_disabled,
        non_root_user=non_root_user,
        seed=exec_model.seed,
    )

    # 4. Dispatch to sandbox backend
    try:
        outcome = backend.execute(request)
    except Exception as exc:
        logger.exception(
            "Backend execution failed with unhandled exception for execution_id=%s",
            execution_id,
        )
        update_execution_status(
            session=session,
            execution_id=execution_id,
            new_status=ExecutionStatus.FAILED,
            reason=f"Sandbox execution backend error: {exc}",
            actor=actor_enum,
            event_sink=event_sink,
            context=context,
        )
        raise

    # 5. Persist stdout / stderr log artifacts if non-empty
    if outcome.stdout:
        stdout_bytes = outcome.stdout.encode("utf-8")
        stdout_hash = hashlib.sha256(stdout_bytes).hexdigest()
        stdout_art = record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="stdout.log",
            content_hash=stdout_hash,
            size_bytes=len(stdout_bytes),
            execution_id=execution_id,
            metadata={"stream": "stdout", "truncated": outcome.stdout_truncated},
            actor=actor_enum,
            event_sink=event_sink,
            context=context,
        )
        exec_model.stdout_artifact_id = stdout_art.id

    if outcome.stderr:
        stderr_bytes = outcome.stderr.encode("utf-8")
        stderr_hash = hashlib.sha256(stderr_bytes).hexdigest()
        stderr_art = record_artifact(
            session=session,
            artifact_type=ArtifactType.LOG,
            path="stderr.log",
            content_hash=stderr_hash,
            size_bytes=len(stderr_bytes),
            execution_id=execution_id,
            metadata={"stream": "stderr", "truncated": outcome.stderr_truncated},
            actor=actor_enum,
            event_sink=event_sink,
            context=context,
        )
        exec_model.stderr_artifact_id = stderr_art.id

    # 6. Persist captured output artifacts
    for art_meta in outcome.output_artifacts:
        record_artifact(
            session=session,
            artifact_type=art_meta.artifact_type,
            path=art_meta.path,
            content_hash=art_meta.content_hash,
            size_bytes=art_meta.size_bytes,
            execution_id=execution_id,
            metadata=art_meta.metadata,
            actor=actor_enum,
            event_sink=event_sink,
            context=context,
        )

    # 7. Transition execution status to terminal outcome
    updated_execution = update_execution_status(
        session=session,
        execution_id=execution_id,
        new_status=outcome.status,
        exit_code=outcome.exit_code,
        resource_usage=outcome.resource_usage,
        reason=outcome.failure_reason,
        actor=actor_enum,
        event_sink=event_sink,
        context=context,
    )

    return updated_execution, outcome


def execute_managed_sandbox_run(
    session_factory: sessionmaker[Session],
    execution_id: str,
    backend: ExecutionBackend,
    code_files: Mapping[str, str],
    command: list[str] | None = None,
    limits: ResourceLimits | None = None,
    environment_variables: Mapping[str, str] | None = None,
    image: str | None = None,
    network_disabled: bool = True,
    non_root_user: bool = True,
    actor: ActorType | str = ActorType.EXECUTION_WORKER,
    event_sink: EventSink | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[Execution, ExecutionOutcome]:
    """Execute a sandbox run inside a managed database session transaction."""
    with get_db_session(session_factory) as session:
        return run_execution_in_sandbox(
            session=session,
            execution_id=execution_id,
            backend=backend,
            code_files=code_files,
            command=command,
            limits=limits,
            environment_variables=environment_variables,
            image=image,
            network_disabled=network_disabled,
            non_root_user=non_root_user,
            actor=actor,
            event_sink=event_sink,
            context=context,
        )
