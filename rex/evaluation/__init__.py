"""REX Evaluation & Quality Assurance Subsystem (REX Epic 11).

Provides deterministic benchmarking (REX-042), evidence corruption testing (REX-043),
reproducibility evaluation (REX-044), and controlled baseline comparison (REX-045).
"""

from rex.evaluation.benchmark import BenchmarkExecutionReport, ToyBenchmarkTask
from rex.evaluation.capability import (
    CapabilityDimensionScore,
    ResearcherCapabilityScorecard,
    render_capability_scorecard_markdown,
)
from rex.evaluation.comparative import BaselineVsRexEvaluator
from rex.evaluation.corruption import EvidenceCorruptionHarness
from rex.evaluation.golden import GoldenRegressionComparator
from rex.evaluation.models import (
    ComparativeDimensionResult,
    ComparativeEvaluationResult,
    EvaluationCaseResult,
    EvaluationRunSummary,
    EvaluationStatus,
    EvaluationSuiteType,
    FailureClassification,
    QualityScorecard,
)
from rex.evaluation.orchestrator import EvaluationOrchestrator
from rex.evaluation.reproducibility import (
    MultiRunReproducibilityReport,
    ReproducibilityEvaluator,
)

__all__ = [
    "BaselineVsRexEvaluator",
    "BenchmarkExecutionReport",
    "CapabilityDimensionScore",
    "ComparativeDimensionResult",
    "ComparativeEvaluationResult",
    "EvaluationCaseResult",
    "EvaluationOrchestrator",
    "EvaluationRunSummary",
    "EvaluationStatus",
    "EvaluationSuiteType",
    "EvidenceCorruptionHarness",
    "FailureClassification",
    "GoldenRegressionComparator",
    "MultiRunReproducibilityReport",
    "QualityScorecard",
    "ReproducibilityEvaluator",
    "ResearcherCapabilityScorecard",
    "ToyBenchmarkTask",
    "render_capability_scorecard_markdown",
]
