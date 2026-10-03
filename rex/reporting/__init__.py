"""REX Reporting Subsystem (REX-036).

Provides evidence-grounded research report generation directly from persisted,
machine-auditable experimental records and verified evidence graphs.
"""

from rex.reporting.models import (
    ReportAnalysisSummary,
    ReportClaimRef,
    ReportExperimentSummary,
    ReportHypothesisSummary,
    ReportMethodologyCritique,
    ReportResultMetric,
    ResearchReport,
)
from rex.reporting.report_generator import ReportGenerator

__all__ = [
    "ReportAnalysisSummary",
    "ReportClaimRef",
    "ReportExperimentSummary",
    "ReportGenerator",
    "ReportHypothesisSummary",
    "ReportMethodologyCritique",
    "ReportResultMetric",
    "ResearchReport",
]
