"""REX Analysis and Metric Extraction Exceptions (REX-020, REX-021, REX-022).

Defines strongly typed exceptions for metric extraction, schema validation,
deterministic statistical computation, and scientific figure generation.
"""


class AnalysisError(Exception):
    """Base exception for all analysis layer errors."""


class MetricExtractionError(AnalysisError):
    """Base exception for metric extraction and parsing errors."""


class MalformedMetricError(MetricExtractionError):
    """Raised when extracted metrics violate the defined schema, contain non-finite numbers (NaN/Inf),
    or have missing/invalid field formats.
    """


class InsufficientDataError(AnalysisError):
    """Raised when statistical analysis cannot be performed due to insufficient sample size."""


class FigureGenerationError(AnalysisError):
    """Raised when deterministic figure or plot generation fails."""
