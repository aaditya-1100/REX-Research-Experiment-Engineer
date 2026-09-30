"""REX Deterministic Analysis and Empirical Evaluation Package (Epic 5).

Provides strictly deterministic extraction of empirical metric outputs,
statistical validation and group hypothesis tests, and reproducible figure generation
anchored to cryptographic execution artifacts.
"""

from rex.analysis.exceptions import (
    AnalysisError,
    FigureGenerationError,
    InsufficientDataError,
    MalformedMetricError,
    MetricExtractionError,
)
from rex.analysis.figures import (
    FigureArtifactInfo,
    FigureGenerator,
)
from rex.analysis.metrics import (
    MetricExtractor,
    RawMetric,
)
from rex.analysis.statistics import (
    ComparisonStatistics,
    StatisticalAnalyzer,
    SummaryStatistics,
)

__all__ = [
    "AnalysisError",
    "ComparisonStatistics",
    "FigureArtifactInfo",
    "FigureGenerationError",
    "FigureGenerator",
    "InsufficientDataError",
    "MalformedMetricError",
    "MetricExtractionError",
    "MetricExtractor",
    "RawMetric",
    "StatisticalAnalyzer",
    "SummaryStatistics",
]
