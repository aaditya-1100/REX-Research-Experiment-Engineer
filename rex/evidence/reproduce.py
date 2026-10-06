"""REX Reproducibility Engine (REX-027).

Provides formal reproducibility assessment and re-execution for experiments.
Guarantees that reproduction attempts create distinct, linked execution records
without overwriting historical empirical results, and compares metric outputs
within defined numerical tolerances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    ResultModel,
)
from rex.persistence.repositories import EventRepository


class ReproducibilityStatus(StrEnum):
    """Classification of an experiment's readiness for reproduction."""

    REPRODUCIBLE = "reproducible"
    PARTIALLY_REPRODUCIBLE = "partially_reproducible"
    NOT_REPRODUCIBLE = "not_reproducible"


class ReproductionOutcome(StrEnum):
    """Comparison outcome of a reproduction re-execution."""

    EXACT_MATCH = "exact_match"
    WITHIN_TOLERANCE = "within_tolerance"
    DIVERGED = "diverged"
    FAILED = "failed"


@dataclass(frozen=True)
class ReproducibilityAssessment:
    """Readiness assessment of an experiment for mechanical reproduction."""

    experiment_id: str
    status: ReproducibilityStatus
    has_code: bool
    has_configuration: bool
    has_dataset: bool
    has_environment: bool
    has_seed: bool
    has_prior_execution: bool
    missing_elements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_executable(self) -> bool:
        return self.status != ReproducibilityStatus.NOT_REPRODUCIBLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "status": self.status.value,
            "has_code": self.has_code,
            "has_configuration": self.has_configuration,
            "has_dataset": self.has_dataset,
            "has_environment": self.has_environment,
            "has_seed": self.has_seed,
            "has_prior_execution": self.has_prior_execution,
            "missing_elements": self.missing_elements,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class MetricComparison:
    """Pairwise comparison of an original vs. reproduced metric value."""

    metric_name: str
    original_value: float
    reproduced_value: float
    absolute_difference: float
    relative_difference: float | None
    within_tolerance: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "original_value": self.original_value,
            "reproduced_value": self.reproduced_value,
            "absolute_difference": self.absolute_difference,
            "relative_difference": self.relative_difference,
            "within_tolerance": self.within_tolerance,
        }


@dataclass(frozen=True)
class ReproductionReport:
    """Comprehensive report detailing the execution and verification of a reproduction run."""

    experiment_id: str
    original_execution_id: str
    reproduction_execution_id: str
    outcome: ReproductionOutcome
    metric_comparisons: list[MetricComparison]
    tolerance: float
    is_reproduced: bool
    error_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "original_execution_id": self.original_execution_id,
            "reproduction_execution_id": self.reproduction_execution_id,
            "outcome": self.outcome.value,
            "metric_comparisons": [m.as_dict() for m in self.metric_comparisons],
            "tolerance": self.tolerance,
            "is_reproduced": self.is_reproduced,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


class ExperimentReproducer:
    """Assesses reproducibility readiness and orchestrates non-destructive re-executions."""

    def __init__(
        self,
        session: Session,
        event_sink: EventSink | None = None,
    ) -> None:
        self.session = session
        self.event_sink = event_sink if event_sink is not None else EventRepository(session)

    def assess_reproducibility(self, experiment_id: str) -> ReproducibilityAssessment:
        """Inspect experiment and execution metadata to determine reproducibility readiness."""
        exp = self.session.get(ExperimentModel, experiment_id)
        if exp is None:
            return ReproducibilityAssessment(
                experiment_id=experiment_id,
                status=ReproducibilityStatus.NOT_REPRODUCIBLE,
                has_code=False,
                has_configuration=False,
                has_dataset=False,
                has_environment=False,
                has_seed=False,
                has_prior_execution=False,
                missing_elements=["experiment_not_found"],
                notes=[f"Experiment '{experiment_id}' does not exist."],
            )

        # Look for prior successful executions
        completed_execs = [e for e in exp.executions if e.status in ("completed", "success")]
        target_exec = (
            completed_execs[-1]
            if completed_execs
            else (exp.executions[-1] if exp.executions else None)
        )

        has_prior_exec = target_exec is not None
        missing: list[str] = []
        notes: list[str] = []

        if not has_prior_exec:
            return ReproducibilityAssessment(
                experiment_id=experiment_id,
                status=ReproducibilityStatus.NOT_REPRODUCIBLE,
                has_code=False,
                has_configuration=False,
                has_dataset=False,
                has_environment=False,
                has_seed=False,
                has_prior_execution=False,
                missing_elements=["prior_execution"],
                notes=["Experiment has never been executed."],
            )

        assert target_exec is not None

        has_code = bool(target_exec.code_hash or target_exec.git_commit)
        has_config = bool(target_exec.configuration_hash or exp.parameters_json)
        has_dataset = bool(target_exec.dataset_hash)
        has_env = bool(target_exec.environment_json)
        has_seed = target_exec.seed is not None

        if not has_code:
            missing.append("code_hash_or_git_commit")
        if not has_config:
            missing.append("configuration_hash")
        if not has_dataset:
            missing.append("dataset_hash")
        if not has_env:
            missing.append("environment_specification")
        if not has_seed:
            missing.append("random_seed")

        if not has_code or not has_config:
            status = ReproducibilityStatus.NOT_REPRODUCIBLE
            notes.append("Critical code or configuration provenance is missing.")
        elif not has_dataset or not has_env or not has_seed:
            status = ReproducibilityStatus.PARTIALLY_REPRODUCIBLE
            notes.append("Reproducible with caveats; some environment or seed constraints missing.")
        else:
            status = ReproducibilityStatus.REPRODUCIBLE
            notes.append("All provenance artifacts and seeds recorded for deterministic re-run.")

        return ReproducibilityAssessment(
            experiment_id=experiment_id,
            status=status,
            has_code=has_code,
            has_configuration=has_config,
            has_dataset=has_dataset,
            has_environment=has_env,
            has_seed=has_seed,
            has_prior_execution=has_prior_exec,
            missing_elements=missing,
            notes=notes,
        )

    def reproduce_experiment(
        self,
        experiment_id: str,
        original_execution_id: str | None = None,
        runner_fn: Any | None = None,
        simulated_results: list[dict[str, Any]] | None = None,
        tolerance: float = 1e-3,
        actor: ActorType | str = ActorType.VERIFIER,
        allow_mock_fallback: bool = False,
    ) -> ReproductionReport:
        """Perform a formal experiment reproduction attempt.

        Guarantees:
        - NEVER overwrites original ExecutionModel or ResultModel rows.
        - Creates a NEW ExecutionModel with lineage metadata linking to original.
        - Emits REPRODUCTION_STARTED and REPRODUCTION_COMPLETED / REPRODUCTION_FAILED events.
        - Compares metric values against original results within tolerance.
        - Rejects silent metric mirroring unless explicitly opted in via allow_mock_fallback.
        """
        started_at = datetime.now(UTC)
        actor_enum = (
            actor if isinstance(actor, ActorType) else ActorType(str(actor).strip().lower())
        )

        exp = self.session.get(ExperimentModel, experiment_id)
        if exp is None:
            raise ValueError(f"Experiment '{experiment_id}' does not exist.")

        # Resolve target original execution
        orig_exec: ExecutionModel | None = None
        if original_execution_id:
            orig_exec = self.session.get(ExecutionModel, original_execution_id)
            if orig_exec is None or orig_exec.experiment_id != experiment_id:
                raise ValueError(
                    f"Execution '{original_execution_id}' does not belong to experiment '{experiment_id}'."
                )
        else:
            # Pick latest completed execution
            completed = [e for e in exp.executions if e.status in ("completed", "success")]
            orig_exec = (
                completed[-1] if completed else (exp.executions[-1] if exp.executions else None)
            )

        if orig_exec is None:
            raise ValueError(f"No prior execution found for experiment '{experiment_id}'.")

        # Emit REPRODUCTION_STARTED event
        self.event_sink.emit(
            create_event(
                event_type=EventType.REPRODUCTION_STARTED,
                research_run_id=exp.research_run_id,
                actor=actor_enum,
                payload={
                    "experiment_id": experiment_id,
                    "original_execution_id": orig_exec.id,
                    "tolerance": tolerance,
                },
            )
        )

        # Create NEW execution record (never mutate original!)
        repro_exec = ExecutionModel(
            experiment_id=experiment_id,
            status="pending",
            command=orig_exec.command,
            git_commit=orig_exec.git_commit,
            code_hash=orig_exec.code_hash,
            dataset_hash=orig_exec.dataset_hash,
            configuration_hash=orig_exec.configuration_hash,
            seed=orig_exec.seed,
            environment_json=dict(orig_exec.environment_json or {}),
            resource_usage_json={
                "is_reproduction": True,
                "reproduction_of_execution_id": orig_exec.id,
            },
            started_at=datetime.now(UTC),
        )
        self.session.add(repro_exec)
        self.session.flush()

        comparisons: list[MetricComparison] = []
        outcome = ReproductionOutcome.EXACT_MATCH
        error_msg: str | None = None

        try:
            # Execute reproduction via injected runner_fn or simulated_results
            if runner_fn is not None:
                # runner_fn takes (repro_exec, orig_exec) and records results
                runner_fn(repro_exec, orig_exec)
                self.session.flush()
            elif simulated_results is not None:
                # Record supplied simulated results on repro_exec
                for r in simulated_results:
                    res_model = ResultModel(
                        execution_id=repro_exec.id,
                        metric_name=r["metric_name"],
                        metric_value=float(r["metric_value"]),
                        metric_unit=r.get("metric_unit", ""),
                        result_json=r.get("result_json", {}),
                    )
                    self.session.add(res_model)
                repro_exec.status = "completed"
                repro_exec.finished_at = datetime.now(UTC)
                self.session.flush()
            elif allow_mock_fallback:
                # Explicitly opted-in mock fallback (used ONLY when testing DB record preservation)
                for orig_res in orig_exec.results:
                    if orig_res.metric_value is not None:
                        res_model = ResultModel(
                            execution_id=repro_exec.id,
                            metric_name=orig_res.metric_name,
                            metric_value=orig_res.metric_value,
                            metric_unit=orig_res.metric_unit,
                            result_json=dict(orig_res.result_json or {}),
                        )
                        self.session.add(res_model)
                repro_exec.status = "completed"
                repro_exec.finished_at = datetime.now(UTC)
                self.session.flush()
            else:
                # Disallow silent metric mirroring: no runner was provided for computational re-execution
                repro_exec.status = "failed"
                repro_exec.finished_at = datetime.now(UTC)
                self.session.flush()
                outcome = ReproductionOutcome.FAILED
                error_msg = (
                    "No execution runner provided for computational reproduction. "
                    "Automatic metric mirroring is disabled for epistemic integrity."
                )

            # Compare reproduced results with original results
            orig_metrics = {
                r.metric_name: r.metric_value
                for r in orig_exec.results
                if r.metric_value is not None
            }
            repro_metrics = {
                r.metric_name: r.metric_value
                for r in repro_exec.results
                if r.metric_value is not None
            }

            all_within_tol = True
            any_diff = False

            for m_name, orig_val in orig_metrics.items():
                if m_name in repro_metrics:
                    rep_val = repro_metrics[m_name]
                    assert rep_val is not None
                    abs_diff = abs(orig_val - rep_val)
                    rel_diff = abs_diff / abs(orig_val) if orig_val != 0.0 else None
                    within_tol = abs_diff <= tolerance or (
                        rel_diff is not None and rel_diff <= tolerance
                    )

                    if not within_tol:
                        all_within_tol = False
                    if abs_diff > 1e-7:
                        any_diff = True

                    comparisons.append(
                        MetricComparison(
                            metric_name=m_name,
                            original_value=orig_val,
                            reproduced_value=rep_val,
                            absolute_difference=abs_diff,
                            relative_difference=rel_diff,
                            within_tolerance=within_tol,
                        )
                    )
                else:
                    all_within_tol = False
                    comparisons.append(
                        MetricComparison(
                            metric_name=m_name,
                            original_value=orig_val,
                            reproduced_value=float("nan"),
                            absolute_difference=float("inf"),
                            relative_difference=None,
                            within_tolerance=False,
                        )
                    )

            if outcome == ReproductionOutcome.FAILED:
                pass
            elif not comparisons or not all_within_tol:
                outcome = ReproductionOutcome.DIVERGED
            elif any_diff:
                outcome = ReproductionOutcome.WITHIN_TOLERANCE
            else:
                outcome = ReproductionOutcome.EXACT_MATCH

        except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
            outcome = ReproductionOutcome.FAILED
            error_msg = str(exc)
            repro_exec.status = "failed"
            repro_exec.finished_at = datetime.now(UTC)
            self.session.flush()

        completed_at = datetime.now(UTC)
        is_reproduced = outcome in (
            ReproductionOutcome.EXACT_MATCH,
            ReproductionOutcome.WITHIN_TOLERANCE,
        )

        report = ReproductionReport(
            experiment_id=experiment_id,
            original_execution_id=orig_exec.id,
            reproduction_execution_id=repro_exec.id,
            outcome=outcome,
            metric_comparisons=comparisons,
            tolerance=tolerance,
            is_reproduced=is_reproduced,
            error_message=error_msg,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Emit completion/failure event
        event_type = (
            EventType.REPRODUCTION_COMPLETED if is_reproduced else EventType.REPRODUCTION_FAILED
        )
        self.event_sink.emit(
            create_event(
                event_type=event_type,
                research_run_id=exp.research_run_id,
                actor=actor_enum,
                payload={
                    "experiment_id": experiment_id,
                    "original_execution_id": orig_exec.id,
                    "reproduction_execution_id": repro_exec.id,
                    "outcome": outcome.value,
                    "is_reproduced": is_reproduced,
                    "comparisons_count": len(comparisons),
                    "error_message": error_msg or "",
                },
            )
        )

        return report
