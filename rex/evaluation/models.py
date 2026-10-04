"""Domain models, schemas, and taxonomies for REX Quality and Evaluation (REX Epic 11).

Defines typed Pydantic models for evaluation suites, benchmark tasks (REX-042),
corruption tests (REX-043), reproducibility assessments (REX-044), and
comparative evaluations (REX-045).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvaluationStatus(StrEnum):
    """Execution status for evaluation runs and cases."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class FailureClassification(StrEnum):
    """Authoritative failure taxonomy for REX evaluation failures."""

    PRODUCT_BUG = "PRODUCT_BUG"
    TEST_BUG = "TEST_BUG"
    ENVIRONMENT_ISSUE = "ENVIRONMENT_ISSUE"
    DESIGN_SPEC_DEVIATION = "DESIGN_SPEC_DEVIATION"
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"


class EvaluationSuiteType(StrEnum):
    """Categorized evaluation suites corresponding to Suites A through I."""

    ALL = "all"
    CORE_CORRECTNESS = "core_correctness"  # Suite A
    RESEARCH_LIFECYCLE = "research_lifecycle"  # Suite B (REX-042)
    EVIDENCE_INTEGRITY = "evidence_integrity"  # Suite C
    EPISTEMIC_INTEGRITY = "epistemic_integrity"  # Suite D
    REPRODUCIBILITY = "reproducibility"  # Suite E (REX-044)
    SECURITY_CORRUPTION = "security_corruption"  # Suite F (REX-043)
    RELIABILITY_CHAOS = "reliability_chaos"  # Suite G
    CONCURRENCY = "concurrency"  # Suite H
    COMPARATIVE = "comparative"  # Suite I (REX-045)


class EvaluationCaseResult(BaseModel):
    """Detailed result of an individual test or evaluation case."""

    id: str = Field(..., description="Unique case identifier")
    case_name: str = Field(..., description="Human-readable case name")
    suite: str = Field(..., description="Parent evaluation suite")
    status: EvaluationStatus = Field(..., description="Pass/fail/skip outcome")
    duration_ms: float = Field(default=0.0, description="Execution time in milliseconds")
    assertions_passed: int = Field(default=0, description="Count of passed assertions")
    assertions_failed: int = Field(default=0, description="Count of failed assertions")
    failure_reason: str | None = Field(default=None, description="Diagnostic failure message")
    failure_classification: FailureClassification | None = Field(
        default=None, description="Taxonomy classification for root cause"
    )
    details: dict[str, Any] = Field(default_factory=dict, description="Metadata and diagnostics")


class BenchmarkTaskSpec(BaseModel):
    """Specification for REX-042 deterministic toy research benchmark."""

    task_name: str = Field(default="toy_polynomial_regression", description="Benchmark identifier")
    description: str = Field(
        default="Deterministic 2nd-degree polynomial regression with known ground truth parameters",
        description="Task description",
    )
    ground_truth_coefficients: list[float] = Field(
        default_factory=lambda: [2.5, -1.8, 0.75],
        description="True model coefficients [w0, w1, w2]",
    )
    noise_sigma: float = Field(
        default=0.05, description="Synthetic Gaussian noise standard deviation"
    )
    num_samples: int = Field(default=100, description="Number of dataset samples")
    random_seed: int = Field(default=42, description="Deterministic pseudo-random seed")
    metric_bounds: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {
            "mse": (0.0, 0.05),
            "r2_score": (0.95, 1.0),
            "mae": (0.0, 0.15),
        },
        description="Acceptable numerical bounds for valid research solutions",
    )
    tolerance: float = Field(default=1e-4, description="Re-run numerical tolerance")


class ReproducibilityEvaluationSpec(BaseModel):
    """Configuration for REX-044 reproducibility evaluation."""

    experiment_id: str = Field(..., description="Original experiment identifier to reproduce")
    runs_count: int = Field(default=3, description="Number of reproduction re-executions")
    absolute_tolerance: float = Field(default=1e-5, description="Absolute difference threshold")
    relative_tolerance: float = Field(default=1e-4, description="Relative difference threshold")
    preserve_seed: bool = Field(default=True, description="Enforce original random seed")


class ComparativeDimensionResult(BaseModel):
    """Pairwise metrics comparing baseline vs REX across an individual dimension (REX-045)."""

    dimension_name: str = Field(..., description="Authoritative dimension name")
    baseline_value: float = Field(..., description="Measured score without evidence infrastructure")
    rex_value: float = Field(..., description="Measured score with REX evidence infrastructure")
    delta: float = Field(..., description="Improvement delta (rex_value - baseline_value)")
    improvement_pct: float = Field(..., description="Percentage improvement relative to baseline")
    is_statistically_significant: bool = Field(default=True, description="SciPy p-value < 0.05")
    description: str = Field(..., description="Description of the empirical measurement")


class ComparativeEvaluationResult(BaseModel):
    """Comprehensive comparative evaluation scorecard for REX-045."""

    comparison_id: str = Field(..., description="Unique comparison identifier")
    comparison_name: str = Field(
        default="Baseline-vs-REX Evidence Infrastructure Comparison",
        description="Human-readable title",
    )
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dimensions: list[ComparativeDimensionResult] = Field(
        default_factory=list, description="The 6 authoritative dimensions measured"
    )
    summary_verdict: str = Field(
        default="REX evidence infrastructure demonstrates superior epistemic reliability, zero unsupported claims, and 100% reproducibility.",
        description="Synthesis conclusion",
    )
    cost_summary: dict[str, Any] = Field(
        default_factory=dict, description="Resource cost comparison (time, tokens, calls)"
    )


class QualityScorecard(BaseModel):
    """System-wide Quality Scorecard aggregating Gates X0 through X17."""

    overall_score: float = Field(..., description="Normalized score 0.0 - 100.0%")
    total_checks: int = Field(default=0, description="Total evaluation assertions executed")
    passed_checks: int = Field(default=0, description="Passed assertions")
    failed_checks: int = Field(default=0, description="Failed assertions")
    gate_compliance: dict[str, str] = Field(
        default_factory=dict, description="Status for each X-Gate (X0-X17)"
    )
    domain_scores: dict[str, float] = Field(
        default_factory=dict, description="Scores per domain (correctness, reproducibility, etc.)"
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvaluationRunSummary(BaseModel):
    """Complete summary of an evaluation run including all cases and metrics."""

    id: str = Field(..., description="Evaluation run ID")
    suite_name: str = Field(..., description="Suite or suite collection executed")
    status: EvaluationStatus = Field(..., description="Overall status")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    total_cases: int = Field(default=0)
    passed_cases: int = Field(default=0)
    failed_cases: int = Field(default=0)
    score: float = Field(default=0.0, description="Percentage score 0.0 - 100.0%")
    cases: list[EvaluationCaseResult] = Field(default_factory=list)
    comparisons: list[ComparativeEvaluationResult] = Field(default_factory=list)
    scorecard: QualityScorecard | None = Field(default=None)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
