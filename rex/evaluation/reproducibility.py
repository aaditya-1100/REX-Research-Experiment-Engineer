"""Multi-Run Reproducibility Evaluation Suite (REX-044).

Systematically evaluates computational experiment reproducibility by re-executing
experiments under recorded configurations, ensuring new run IDs are assigned,
comparing metrics within configurable tolerances, and recording structured reproducibility status.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from rex.evaluation.benchmark import ToyBenchmarkTask
from rex.evaluation.models import (
    EvaluationCaseResult,
    EvaluationStatus,
)
from rex.evidence.hashing import compute_file_hash
from rex.evidence.reproduce import (
    ExperimentReproducer,
    ReproductionOutcome,
    ReproductionReport,
)
from rex.persistence.models import (
    ArtifactModel,
    ExecutionModel,
    ExperimentModel,
    ResultModel,
)


@dataclass
class MultiRunReproducibilityReport:
    """Consolidated report across multiple reproduction runs."""

    original_experiment_id: str
    original_execution_id: str
    reproduction_runs_count: int
    outcomes: list[ReproductionOutcome]
    overall_reproducible: bool
    reproducibility_rate: float
    metric_comparisons: dict[str, list[dict[str, Any]]]
    tolerance_used: dict[str, float]


class ReproducibilityEvaluator:
    """Evaluates reproducibility across computational experiments (REX-044)."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.reproducer = ExperimentReproducer(session)

    def evaluate_experiment_reproducibility(
        self,
        experiment_id: str,
        runner_fn: Any | None = None,
        runs_count: int = 3,
        absolute_tolerance: float = 1e-4,
        relative_tolerance: float = 1e-3,
    ) -> MultiRunReproducibilityReport:
        """Run multiple reproduction cycles for a single experiment and aggregate metrics.

        Executes independent computational re-runs via runner_fn if provided,
        enforcing strict tolerance boundaries and recording structured reproduction outcomes.
        """
        # Validate original experiment
        original_exp = self.session.get(ExperimentModel, experiment_id)
        if not original_exp:
            raise ValueError(f"Experiment {experiment_id} not found")

        original_exec = (
            self.session.query(ExecutionModel)
            .filter(ExecutionModel.experiment_id == experiment_id)
            .first()
        )
        if not original_exec:
            raise ValueError(f"No execution found for experiment {experiment_id}")

        outcomes: list[ReproductionOutcome] = []
        reproduction_results: list[ReproductionReport] = []
        metric_comparisons: dict[str, list[dict[str, Any]]] = {}

        for _ in range(runs_count):
            repro_result = self.reproducer.reproduce_experiment(
                experiment_id=experiment_id,
                runner_fn=runner_fn,
                tolerance=absolute_tolerance,
                actor="verifier",
            )
            reproduction_results.append(repro_result)
            outcomes.append(repro_result.outcome)

            for comp in repro_result.metric_comparisons:
                if comp.metric_name not in metric_comparisons:
                    metric_comparisons[comp.metric_name] = []
                metric_comparisons[comp.metric_name].append(comp.as_dict())

        # Determine overall reproducibility
        successful_runs = sum(
            1
            for o in outcomes
            if o in (ReproductionOutcome.EXACT_MATCH, ReproductionOutcome.WITHIN_TOLERANCE)
        )
        reproducibility_rate = (successful_runs / runs_count) * 100.0
        overall_reproducible = reproducibility_rate >= 95.0

        return MultiRunReproducibilityReport(
            original_experiment_id=experiment_id,
            original_execution_id=original_exec.id,
            reproduction_runs_count=runs_count,
            outcomes=outcomes,
            overall_reproducible=overall_reproducible,
            reproducibility_rate=round(reproducibility_rate, 2),
            metric_comparisons=metric_comparisons,
            tolerance_used={
                "absolute_tolerance": absolute_tolerance,
                "relative_tolerance": relative_tolerance,
            },
        )

    def run_reproducibility_suite(self) -> EvaluationCaseResult:
        """Execute the complete REX-044 reproducibility evaluation case with real computational re-runs."""
        start_time = time.perf_counter()

        # Step 1: Create deterministic benchmark run
        benchmark = ToyBenchmarkTask(
            task_name="reproducibility_test_task", num_samples=80, random_seed=42
        )
        bench_report = benchmark.run_benchmark(self.session)

        # Step 2: Define actual computational runner for re-execution
        def benchmark_runner(repro_exec: ExecutionModel, orig_exec: ExecutionModel) -> None:
            """Actively re-executes the deterministic toy benchmark in an independent workspace."""
            temp_dir = Path(tempfile.mkdtemp(prefix="rex_repro_rerun_"))
            ws = temp_dir / "workspace"
            ws.mkdir(parents=True, exist_ok=True)

            seed = orig_exec.seed if orig_exec.seed is not None else 42
            params = {}
            if orig_exec.experiment and orig_exec.experiment.parameters_json:
                params = orig_exec.experiment.parameters_json
            num_samples = params.get("num_samples", 80)
            noise_sigma = params.get("noise_sigma", 0.05)

            re_task = ToyBenchmarkTask(
                task_name="reproduction_re_execution",
                num_samples=num_samples,
                random_seed=seed,
                noise_sigma=noise_sigma,
            )
            output = re_task.execute_in_workspace(ws)
            metrics = output["metrics"]
            for m_name in ["mse", "mae", "r2_score"]:
                if m_name in metrics:
                    res_m = ResultModel(
                        execution_id=repro_exec.id,
                        metric_name=m_name,
                        metric_value=metrics[m_name],
                        metric_unit="",
                        result_json={m_name: metrics[m_name]},
                    )
                    self.session.add(res_m)

            art_file = output["artifact_file"]
            art_m = ArtifactModel(
                research_run_id=orig_exec.experiment.research_run_id,
                execution_id=repro_exec.id,
                artifact_type="text_plot",
                path=str(art_file),
                content_hash=compute_file_hash(art_file),
                size_bytes=art_file.stat().st_size,
            )
            self.session.add(art_m)
            repro_exec.status = "completed"
            repro_exec.finished_at = datetime.now(UTC)

        # Step 3: Run reproducibility evaluation with 3 active computational re-runs
        report = self.evaluate_experiment_reproducibility(
            experiment_id=bench_report.experiment_id,
            runner_fn=benchmark_runner,
            runs_count=3,
            absolute_tolerance=1e-4,
            relative_tolerance=1e-3,
        )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Assertions
        passed = (
            report.overall_reproducible
            and report.reproducibility_rate == 100.0
            and len(report.outcomes) == 3
        )

        return EvaluationCaseResult(
            id="case_reproducibility_suite",
            case_name="REX-044: Multi-Run Experiment Computational Reproducibility",
            suite="reproducibility",
            status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
            duration_ms=round(duration_ms, 2),
            assertions_passed=3 if passed else 0,
            assertions_failed=0 if passed else 1,
            failure_reason=None
            if passed
            else f"Reproducibility rate below target: {report.reproducibility_rate}%",
            details={
                "reproducibility_rate": report.reproducibility_rate,
                "outcomes": [o.value for o in report.outcomes],
                "tolerance": report.tolerance_used,
            },
        )
