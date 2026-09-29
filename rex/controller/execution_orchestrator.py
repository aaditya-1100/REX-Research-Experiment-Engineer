"""REX Execution Orchestrator Service (REX-010).

Coordinates the end-to-end execution lifecycle across research runs:
1. Pre-flight checks: entity existence, lifecycle state, permissions, budget validation,
   and atomic concurrency slot reservation.
2. Sandboxed execution: dispatches untrusted code to isolated ExecutionBackend outside of
   any database transaction locks.
3. Post-flight persistence: captures stdout/stderr logs, output artifacts, empirical results,
   and deterministic terminal status updates with parent experiment synchronization.
4. Concurrency protection, cancellation handling, and stale execution recovery.
"""

import hashlib
import logging
import shlex
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from rex.controller.budgets import (
    BudgetUsage,
    ResearchBudget,
    check_budget_limits,
    compute_budget_usage,
    load_run_budget,
    record_budget_exceeded_event,
)
from rex.controller.exceptions import (
    ActorAuthorizationError,
    BudgetExceededError,
    ConcurrencyLimitExceededError,
    ExecutionAlreadyRunningError,
    ExecutionAlreadyTerminalError,
    InvalidExecutionStateTransitionError,
    MissingExecutionError,
    MissingExperimentError,
    MissingResearchRunError,
    StateMachineError,
)
from rex.controller.executions import (
    EXECUTION_STATUS_UPDATER_ACTORS,
    record_artifact,
    record_results_batch,
    update_execution_status,
)
from rex.controller.experiments import update_experiment_status
from rex.domain.models import (
    TERMINAL_EXECUTION_STATUSES,
    TERMINAL_STATES,
    ArtifactType,
    Execution,
    ExecutionStatus,
    ExperimentStatus,
    ResearchState,
)
from rex.execution.backend import ExecutionBackend
from rex.execution.models import ExecutionOutcome, ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.database import get_db_session
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
)
from rex.persistence.repositories import EventRepository

logger = logging.getLogger(__name__)


class ExecutionOrchestrator:
    """Production execution orchestration service managing sandbox lifecycles and budgets."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        backend: ExecutionBackend,
        event_sink: EventSink | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.backend = backend
        self.event_sink = event_sink

    def get_budget_usage(self, research_run_id: str) -> BudgetUsage:
        """Query and compute current aggregate resource usage for a research run."""
        with get_db_session(self.session_factory) as session:
            run = session.get(ResearchRunModel, research_run_id)
            if run is None:
                raise MissingResearchRunError(research_run_id)
            return compute_budget_usage(session, research_run_id)

    def get_run_budget(self, research_run_id: str) -> ResearchBudget:
        """Retrieve the configured resource budget for a research run."""
        with get_db_session(self.session_factory) as session:
            run = session.get(ResearchRunModel, research_run_id)
            if run is None:
                raise MissingResearchRunError(research_run_id)
            return load_run_budget(run)

    def run_execution(
        self,
        execution_id: str,
        code_files: Mapping[str, str],
        command: list[str] | None = None,
        limits: ResourceLimits | None = None,
        environment_variables: Mapping[str, str] | None = None,
        image: str | None = None,
        network_disabled: bool = True,
        non_root_user: bool = True,
        actor: ActorType | str = ActorType.EXECUTION_WORKER,
        results: Sequence[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[Execution, ExecutionOutcome]:
        """Orchestrate an execution run through pre-flight, sandbox, and post-flight phases.

        Enforces:
        - Actor capability authorization.
        - Research run state checks (terminal and paused runs rejected).
        - Research run resource budgets (pre-flight check).
        - Atomic transition from PENDING to RUNNING with concurrency protection.
        - Transaction isolation: sandbox backend is invoked strictly outside DB transactions.
        - Fail-closed exception handling with database status synchronization.
        - Output artifact, log, and empirical result persistence.
        """
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
        if actor_enum not in EXECUTION_STATUS_UPDATER_ACTORS:
            raise ActorAuthorizationError(
                actor=actor_enum.value,
                current_state="EXECUTE",
                target_state="run_execution",
            )

        # ---------------------------------------------------------------------
        # PHASE 1: Pre-flight Validation & Status Reservation (Short Transaction)
        # ---------------------------------------------------------------------
        with get_db_session(self.session_factory) as session:
            exec_model = session.get(ExecutionModel, execution_id)
            if exec_model is None:
                raise MissingExecutionError(execution_id)

            # Check execution status invariants
            if exec_model.status == ExecutionStatus.RUNNING.value:
                raise ExecutionAlreadyRunningError(execution_id)
            if exec_model.status in [s.value for s in TERMINAL_EXECUTION_STATUSES]:
                raise ExecutionAlreadyTerminalError(execution_id, exec_model.status)
            if exec_model.status != ExecutionStatus.PENDING.value:
                raise InvalidExecutionStateTransitionError(
                    execution_id=execution_id,
                    current_status=exec_model.status,
                    target_status=ExecutionStatus.RUNNING.value,
                )

            exp_model = session.get(ExperimentModel, exec_model.experiment_id)
            if exp_model is None:
                raise MissingExperimentError(exec_model.experiment_id)

            run_model = session.get(ResearchRunModel, exp_model.research_run_id)
            if run_model is None:
                raise MissingResearchRunError(exp_model.research_run_id)

            # Enforce research run lifecycle constraints
            is_terminal = False
            try:
                run_state = ResearchState(run_model.status)
                if run_state in TERMINAL_STATES:
                    is_terminal = True
            except ValueError:
                if run_model.status in ("COMPLETE", "COMPLETED", "FAILED", "STOP", "ARCHIVED"):
                    is_terminal = True

            if is_terminal:
                raise StateMachineError(
                    f"Cannot execute in research run '{run_model.id}' with terminal state '{run_model.status}'."
                )
            if run_model.status in ("PAUSED", "STOPPED"):
                raise StateMachineError(
                    f"Cannot execute in research run '{run_model.id}' with inactive state '{run_model.status}'."
                )

            # Enforce research run budget limits
            budget = load_run_budget(run_model)
            usage = compute_budget_usage(session, run_model.id)

            effective_limits = limits or ResourceLimits.from_settings()

            try:
                check_budget_limits(
                    budget=budget,
                    usage=usage,
                    run_id=run_model.id,
                    is_launching_execution=True,
                    requested_limits=effective_limits,
                )
            except (BudgetExceededError, ConcurrencyLimitExceededError) as budget_err:
                record_budget_exceeded_event(
                    session=session,
                    research_run_id=run_model.id,
                    dimension=budget_err.dimension,
                    limit=budget_err.limit,
                    current_usage=budget_err.current_usage,
                    actor=actor_enum,
                    event_sink=self.event_sink,
                    context=context,
                )
                raise

            # Atomic transition PENDING -> RUNNING with optimistic concurrency protection
            now = datetime.now(UTC)
            updated_count = (
                session.query(ExecutionModel)
                .filter(
                    ExecutionModel.id == execution_id,
                    ExecutionModel.status == ExecutionStatus.PENDING.value,
                )
                .update(
                    {
                        ExecutionModel.status: ExecutionStatus.RUNNING.value,
                        ExecutionModel.started_at: now,
                    },
                    synchronize_session=False,
                )
            )

            if updated_count == 0:
                # Concurrency conflict detected: another worker transitioned this record
                rechecked = session.get(ExecutionModel, execution_id)
                if rechecked and rechecked.status == ExecutionStatus.RUNNING.value:
                    raise ExecutionAlreadyRunningError(execution_id)
                if rechecked and rechecked.status in [s.value for s in TERMINAL_EXECUTION_STATUSES]:
                    raise ExecutionAlreadyTerminalError(execution_id, rechecked.status)
                status_str = rechecked.status if rechecked else "unknown"
                raise InvalidExecutionStateTransitionError(
                    execution_id=execution_id,
                    current_status=status_str,
                    target_status=ExecutionStatus.RUNNING.value,
                )

            # Sync parent experiment status to RUNNING if pending or designed
            if exp_model.status in (
                ExperimentStatus.DESIGNED.value,
                ExperimentStatus.PENDING.value,
            ):
                update_experiment_status(
                    session=session,
                    experiment_id=exp_model.id,
                    new_status=ExperimentStatus.RUNNING,
                    actor=actor_enum,
                    reason=f"Execution '{execution_id}' launched in sandbox.",
                    event_sink=self.event_sink,
                    context=context,
                )

            # Record EXECUTION_STARTED audit event
            start_payload: dict[str, Any] = {
                "execution_id": execution_id,
                "experiment_id": exp_model.id,
                "research_run_id": run_model.id,
                "status": ExecutionStatus.RUNNING.value,
                "command": exec_model.command,
                "seed": exec_model.seed,
            }
            if context:
                start_payload.update(context)

            start_event = create_event(
                event_type=EventType.EXECUTION_STARTED,
                actor=actor_enum,
                research_run_id=run_model.id,
                experiment_id=exp_model.id,
                execution_id=execution_id,
                payload=start_payload,
            )
            EventRepository(session).record_event(start_event)
            session.flush()

            if self.event_sink is not None:
                self.event_sink.emit(start_event)

            # Extract request parameters for sandbox invocation
            cmd_list: list[str]
            if command is not None:
                cmd_list = command
            elif exec_model.command:
                cmd_list = shlex.split(exec_model.command)
            else:
                cmd_list = ["python", "src/main.py"]

            request = ExecutionRequest(
                execution_id=exec_model.id,
                experiment_id=exp_model.id,
                research_run_id=run_model.id,
                command=cmd_list,
                code_files=code_files,
                image=image,
                environment_variables=environment_variables or {},
                limits=effective_limits,
                network_disabled=network_disabled,
                non_root_user=non_root_user,
                seed=exec_model.seed,
            )

        # ---------------------------------------------------------------------
        # PHASE 2: Sandboxed Backend Execution (Strictly Outside DB Transaction)
        # ---------------------------------------------------------------------
        try:
            outcome = self.backend.execute(request)
        except Exception as exc:
            logger.exception(
                "Sandbox backend execution failed with unhandled error for execution_id=%s",
                execution_id,
            )
            # Fail closed: transition execution to FAILED in DB and re-raise
            with get_db_session(self.session_factory) as err_session:
                update_execution_status(
                    session=err_session,
                    execution_id=execution_id,
                    new_status=ExecutionStatus.FAILED,
                    actor=actor_enum,
                    reason=f"Sandbox execution backend error: {exc}",
                    event_sink=self.event_sink,
                    context=context,
                )
            raise

        # ---------------------------------------------------------------------
        # PHASE 3: Post-flight Telemetry, Artifacts, and State Sync (Transaction)
        # ---------------------------------------------------------------------
        with get_db_session(self.session_factory) as session:
            exec_model = session.get(ExecutionModel, execution_id)
            if exec_model is None:
                raise MissingExecutionError(execution_id)

            # 1. Persist stdout log artifact if present
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
                    event_sink=self.event_sink,
                    context=context,
                )
                exec_model.stdout_artifact_id = stdout_art.id

            # 2. Persist stderr log artifact if present
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
                    event_sink=self.event_sink,
                    context=context,
                )
                exec_model.stderr_artifact_id = stderr_art.id

            # 3. Persist produced output artifacts
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
                    event_sink=self.event_sink,
                    context=context,
                )

            # 4. Record empirical results if provided
            if results:
                record_results_batch(
                    session=session,
                    execution_id=execution_id,
                    results=results,
                    actor=actor_enum,
                    event_sink=self.event_sink,
                )

            # 5. Transition execution to terminal outcome status
            updated_execution = update_execution_status(
                session=session,
                execution_id=execution_id,
                new_status=outcome.status,
                exit_code=outcome.exit_code,
                resource_usage=outcome.resource_usage,
                reason=outcome.failure_reason,
                actor=actor_enum,
                event_sink=self.event_sink,
                context=context,
            )

        return updated_execution, outcome

    def cancel_execution(
        self,
        execution_id: str,
        actor: ActorType | str = ActorType.OWNER,
        reason: str = "Execution cancelled by request",
    ) -> bool:
        """Cancel an active or pending execution run.

        Returns True if cancelled, or False if already in a terminal status.
        """
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)

        with get_db_session(self.session_factory) as session:
            exec_model = session.get(ExecutionModel, execution_id)
            if exec_model is None:
                raise MissingExecutionError(execution_id)

            if exec_model.status in [s.value for s in TERMINAL_EXECUTION_STATUSES]:
                return False

            update_execution_status(
                session=session,
                execution_id=execution_id,
                new_status=ExecutionStatus.CANCELLED,
                actor=actor_enum,
                reason=reason,
                event_sink=self.event_sink,
            )

            # If the backend provides a cancellation hook, invoke it
            if hasattr(self.backend, "cancel"):
                try:
                    self.backend.cancel(execution_id)
                except Exception:
                    logger.warning(
                        "Backend cancel call failed for execution %s", execution_id, exc_info=True
                    )

            return True

    def reconcile_stale_executions(
        self,
        stale_threshold_seconds: int = 3600,
        actor: ActorType | str = ActorType.SYSTEM,
    ) -> list[str]:
        """Detect and recover executions stuck in RUNNING status (e.g. from process crashes).

        Transitions stuck executions to FAILED with structured audit logging.
        """
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
        reconciled_ids: list[str] = []

        with get_db_session(self.session_factory) as session:
            now = datetime.now(UTC)
            running_execs = (
                session.query(ExecutionModel)
                .filter(ExecutionModel.status == ExecutionStatus.RUNNING.value)
                .all()
            )

            for ex in running_execs:
                duration: float = float("inf")
                if ex.started_at is not None:
                    st = (
                        ex.started_at
                        if ex.started_at.tzinfo is not None
                        else ex.started_at.replace(tzinfo=UTC)
                    )
                    duration = (now - st).total_seconds()

                if duration >= stale_threshold_seconds:
                    logger.warning(
                        "Reconciling stale execution %s (running for %.1fs, threshold %ds)",
                        ex.id,
                        duration,
                        stale_threshold_seconds,
                    )
                    update_execution_status(
                        session=session,
                        execution_id=ex.id,
                        new_status=ExecutionStatus.FAILED,
                        actor=actor_enum,
                        reason=(
                            f"Execution marked stale and failed after exceeding threshold of "
                            f"{stale_threshold_seconds}s without heartbeat."
                        ),
                        event_sink=self.event_sink,
                    )
                    reconciled_ids.append(ex.id)

        return reconciled_ids
