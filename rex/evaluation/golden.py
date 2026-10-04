"""Golden Reference Baseline & Regression Evaluation Engine (REX Milestone 5).

Maintains canonical reference metrics and golden test fixtures to assert
numerical determinism and prevent statistical or pipeline regression across releases.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from rex.analysis.statistics import StatisticalAnalyzer
from rex.evaluation.models import (
    EvaluationCaseResult,
    EvaluationStatus,
)


class GoldenRegressionComparator:
    """Verifies that core mathematical and pipeline computations match golden baselines."""

    GOLDEN_BENCHMARKS: ClassVar[dict[str, dict[str, Any]]] = {
        "polynomial_regression": {
            "expected_coefficients": [2.5, -1.8, 0.75],
            "max_tolerated_mse": 0.05,
            "min_tolerated_r2": 0.95,
        },
        "welch_t_test": {
            "sample_a": [12.5, 13.1, 12.8, 14.0, 13.5],
            "sample_b": [10.2, 10.8, 11.1, 10.5, 10.9],
            "expected_t_stat": 8.0717,
            "expected_p_val": 0.00012,
            "tolerance": 1e-3,
        },
    }

    def verify_statistical_determinism(self) -> EvaluationCaseResult:
        """Assert that statistical routines match golden reference values within 1e-3."""
        start_time = time.perf_counter()
        analyzer = StatisticalAnalyzer()

        t_cfg = self.GOLDEN_BENCHMARKS["welch_t_test"]
        res = analyzer.compare_groups(
            baseline_results=t_cfg["sample_b"],
            treatment_results=t_cfg["sample_a"],
        )

        assert res.t_statistic is not None and res.p_value is not None
        t_diff = abs(res.t_statistic - t_cfg["expected_t_stat"])
        p_diff = abs(res.p_value - t_cfg["expected_p_val"])

        passed = t_diff <= t_cfg["tolerance"] and p_diff <= t_cfg["tolerance"]
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return EvaluationCaseResult(
            id="case_golden_statistical_determinism",
            case_name="Golden Baseline: SciPy Statistical Recomputation Determinism",
            suite="core_correctness",
            status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
            duration_ms=round(duration_ms, 2),
            assertions_passed=2 if passed else 0,
            assertions_failed=0 if passed else 1,
            failure_reason=None
            if passed
            else f"Statistical divergence: t_diff={t_diff}, p_diff={p_diff}",
            details={
                "t_statistic": res.t_statistic,
                "p_value": res.p_value,
            },
        )
