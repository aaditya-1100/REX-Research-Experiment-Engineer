"""REX Deterministic Statistical Analysis Engine (REX-021).

Implements deterministic summary statistics, two-sample baseline comparisons (Welch's t-test),
effect size estimations, zero-baseline safeguards, and persistence of analysis records
with full lineage tracking to source Result IDs.
"""

import logging
import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from scipy import stats
from sqlalchemy.orm import Session

from rex.analysis.exceptions import InsufficientDataError
from rex.domain.models import Result
from rex.observability.events import ActorType, EventSink, EventType, create_event
from rex.persistence.models import AnalysisModel, ResultModel
from rex.persistence.repositories import AnalysisRepository, EventRepository

logger = logging.getLogger(__name__)


class SummaryStatistics(BaseModel):
    """Deterministic summary statistics for an empirical metric series."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(description="Name of the analyzed metric")
    sample_size: int = Field(ge=1, description="Number of observed metric samples (N)")
    mean: float = Field(description="Sample arithmetic mean")
    variance: float | None = Field(
        default=None,
        description="Sample variance with Bessel's correction (N-1 degrees of freedom; None if N < 2)",
    )
    std_dev: float | None = Field(
        default=None,
        description="Sample standard deviation (None if N < 2)",
    )
    standard_error: float | None = Field(
        default=None,
        description="Standard error of the mean (SEM = s / sqrt(N); None if N < 2)",
    )
    median: float = Field(description="Sample median value")
    min_value: float = Field(description="Minimum observed value in sample")
    max_value: float = Field(description="Maximum observed value in sample")
    source_result_ids: list[str] = Field(
        default_factory=list,
        description="IDs of stored Result entities contributing to this analysis",
    )
    method: str = Field(
        default="sample_summary_statistics",
        description="Identifier of statistical method executed",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters passed to statistical computation",
    )

    def to_dict(self) -> dict[str, Any]:
        """Export summary statistics as JSON-compatible dictionary."""
        return self.model_dump()


class ComparisonStatistics(BaseModel):
    """Deterministic comparison between a baseline and candidate/treatment condition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(description="Name of the compared metric")
    baseline_summary: SummaryStatistics = Field(description="Summary statistics of baseline group")
    treatment_summary: SummaryStatistics = Field(
        description="Summary statistics of treatment group"
    )
    absolute_difference: float = Field(
        description="Difference of means: mean(treatment) - mean(baseline)"
    )
    relative_difference: float | None = Field(
        default=None,
        description="Relative ratio: (mean(treatment) - mean(baseline)) / abs(mean(baseline)); None if baseline is zero",
    )
    relative_difference_percent: float | None = Field(
        default=None,
        description="Percentage change: relative_difference * 100.0; None if baseline is zero",
    )
    zero_baseline_warning: bool = Field(
        default=False,
        description="Flag indicating relative difference was omitted because baseline mean is zero",
    )
    t_statistic: float | None = Field(
        default=None,
        description="Welch's t-test statistic (None if sample size insufficient)",
    )
    p_value: float | None = Field(
        default=None,
        description="Two-tailed p-value from Welch's t-test (None if sample size insufficient)",
    )
    statistically_significant: bool | None = Field(
        default=None,
        description="Whether p_value < alpha significance threshold",
    )
    alpha: float = Field(default=0.05, description="Significance threshold level")
    degrees_of_freedom: float | None = Field(
        default=None,
        description="Welch-Satterthwaite effective degrees of freedom",
    )
    source_result_ids: list[str] = Field(
        default_factory=list,
        description="Combined IDs of all baseline and treatment Result entities",
    )
    method: str = Field(
        default="welch_t_test_comparison",
        description="Identifier of comparison method executed",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters passed to comparison computation",
    )

    def to_dict(self) -> dict[str, Any]:
        """Export comparison statistics as JSON-compatible dictionary."""
        return self.model_dump()


class StatisticalAnalyzer:
    """Deterministic engine for computing empirical statistics from stored Result sets."""

    @staticmethod
    def _extract_values_and_ids(
        results: Sequence[Result | ResultModel | dict[str, Any] | float | int],
    ) -> tuple[list[float], list[str], str]:
        """Extract finite float values, result IDs, and metric name from input objects."""
        values: list[float] = []
        ids: list[str] = []
        inferred_name = ""

        for item in results:
            if isinstance(item, (int, float)):
                if not math.isfinite(item):
                    raise ValueError(f"Non-finite value '{item}' in numerical results.")
                values.append(float(item))
            elif isinstance(item, (Result, ResultModel)):
                if item.metric_value is not None:
                    values.append(float(item.metric_value))
                    ids.append(item.id)
                    if not inferred_name:
                        inferred_name = item.metric_name
            elif isinstance(item, dict):
                val = item.get("metric_value") if "metric_value" in item else item.get("value")
                if val is not None:
                    f_val = float(val)
                    if not math.isfinite(f_val):
                        raise ValueError(f"Non-finite value '{f_val}' in result dict.")
                    values.append(f_val)
                item_id = item.get("id") or item.get("result_id")
                if item_id:
                    ids.append(str(item_id))
                name = item.get("metric_name") or item.get("name")
                if name and not inferred_name:
                    inferred_name = str(name)
            else:
                raise TypeError(
                    f"Unsupported result type for statistical analysis: {type(item).__name__}"
                )

        return values, ids, inferred_name

    def compute_summary(
        self,
        results: Sequence[Result | ResultModel | dict[str, Any] | float | int],
        metric_name: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> SummaryStatistics:
        """Compute sample statistics (mean, variance, std dev, SEM, median, min, max).

        Raises InsufficientDataError if results sequence is empty.
        Requires N >= 2 for variance, std_dev, and SEM; returns None for those fields if N == 1.
        """
        values, result_ids, inferred_name = self._extract_values_and_ids(results)
        name = metric_name or inferred_name or "metric"

        n = len(values)
        if n == 0:
            raise InsufficientDataError(
                "Cannot compute summary statistics for an empty sample (N=0)."
            )

        mean_val = float(sum(values) / n)
        median_val = float(statistics.median(values))
        min_val = float(min(values))
        max_val = float(max(values))

        var_val: float | None = None
        std_val: float | None = None
        sem_val: float | None = None

        if n >= 2:
            var_val = float(statistics.variance(values))
            std_val = float(statistics.stdev(values))
            sem_val = float(std_val / math.sqrt(n))

        return SummaryStatistics(
            metric_name=name,
            sample_size=n,
            mean=mean_val,
            variance=var_val,
            std_dev=std_val,
            standard_error=sem_val,
            median=median_val,
            min_value=min_val,
            max_value=max_val,
            source_result_ids=result_ids,
            method="sample_summary_statistics",
            parameters=parameters or {},
        )

    def compare_groups(
        self,
        baseline_results: Sequence[Result | ResultModel | dict[str, Any] | float | int],
        treatment_results: Sequence[Result | ResultModel | dict[str, Any] | float | int],
        metric_name: str = "",
        alpha: float = 0.05,
        parameters: dict[str, Any] | None = None,
    ) -> ComparisonStatistics:
        """Perform deterministic two-sample comparison between baseline and treatment groups.

        Calculates:
        - Absolute difference of means: mean(treatment) - mean(baseline)
        - Relative difference / percentage change (gracefully handles zero baseline)
        - Welch's t-test (unequal variances assumed) when both samples have N >= 2
        """
        base_vals, base_ids, base_name = self._extract_values_and_ids(baseline_results)
        treat_vals, treat_ids, treat_name = self._extract_values_and_ids(treatment_results)

        name = metric_name or treat_name or base_name or "metric"

        base_summary = self.compute_summary(base_vals, metric_name=name)
        treat_summary = self.compute_summary(treat_vals, metric_name=name)

        abs_diff = treat_summary.mean - base_summary.mean

        # Safe relative difference calculation with zero-baseline protection
        rel_diff: float | None = None
        rel_diff_pct: float | None = None
        zero_baseline = False

        if abs(base_summary.mean) < 1e-12:
            zero_baseline = True
            logger.warning(
                "Baseline mean for metric '%s' is zero (%e). Relative difference cannot be computed.",
                name,
                base_summary.mean,
            )
        else:
            rel_diff = abs_diff / abs(base_summary.mean)
            rel_diff_pct = rel_diff * 100.0

        # Statistical hypothesis test: Welch's t-test (unequal variances)
        t_stat: float | None = None
        p_val: float | None = None
        significant: bool | None = None
        dof: float | None = None

        if base_summary.sample_size >= 2 and treat_summary.sample_size >= 2:
            # Check for identical zero-variance case
            if (
                base_summary.variance is not None
                and treat_summary.variance is not None
                and base_summary.variance == 0.0
                and treat_summary.variance == 0.0
            ):
                if base_summary.mean == treat_summary.mean:
                    t_stat = 0.0
                    p_val = 1.0
                    significant = False
                else:
                    # Non-identical zero-variance distributions
                    t_stat = (
                        float("inf") if treat_summary.mean > base_summary.mean else float("-inf")
                    )
                    p_val = 0.0
                    significant = True
            else:
                ttest_res = stats.ttest_ind(treat_vals, base_vals, equal_var=False)
                t_stat = float(ttest_res.statistic) if math.isfinite(ttest_res.statistic) else None
                p_val = float(ttest_res.pvalue) if math.isfinite(ttest_res.pvalue) else None
                dof = float(getattr(ttest_res, "df", 0.0)) or None
                if p_val is not None:
                    significant = p_val < alpha

        combined_ids = base_ids + treat_ids
        params = {"alpha": alpha}
        if parameters:
            params.update(parameters)

        return ComparisonStatistics(
            metric_name=name,
            baseline_summary=base_summary,
            treatment_summary=treat_summary,
            absolute_difference=abs_diff,
            relative_difference=rel_diff,
            relative_difference_percent=rel_diff_pct,
            zero_baseline_warning=zero_baseline,
            t_statistic=t_stat,
            p_value=p_val,
            statistically_significant=significant,
            alpha=alpha,
            degrees_of_freedom=dof,
            source_result_ids=combined_ids,
            method="welch_t_test_comparison",
            parameters=params,
        )

    def record_analysis(
        self,
        session: Session,
        research_run_id: str,
        analysis: SummaryStatistics | ComparisonStatistics,
        analysis_type: str = "",
        actor: ActorType | str = ActorType.SYSTEM,
        event_sink: EventSink | None = None,
        context: dict[str, Any] | None = None,
    ) -> AnalysisModel:
        """Persist structured analysis to the database and emit an ANALYSIS_COMPLETED event."""
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
        inferred_type = analysis_type or (
            "comparison" if isinstance(analysis, ComparisonStatistics) else "summary"
        )

        analysis_entity = AnalysisModel(
            research_run_id=research_run_id,
            analysis_type=inferred_type,
            input_result_ids=analysis.source_result_ids,
            method=analysis.method,
            output_json=analysis.to_dict(),
            created_at=datetime.now(UTC),
        )
        persisted = AnalysisRepository(session).create(analysis_entity)
        session.flush()

        event_payload: dict[str, Any] = {
            "analysis_id": persisted.id,
            "research_run_id": research_run_id,
            "analysis_type": inferred_type,
            "method": analysis.method,
            "metric_name": analysis.metric_name,
            "input_result_ids": analysis.source_result_ids,
        }
        if context:
            event_payload.update(context)

        evt = create_event(
            event_type=EventType.ANALYSIS_COMPLETED,
            actor=actor_enum,
            research_run_id=research_run_id,
            payload=event_payload,
        )
        EventRepository(session).record_event(evt)
        session.flush()

        if event_sink is not None:
            event_sink.emit(evt)

        logger.info(
            "Persisted analysis id='%s' type='%s' method='%s' for run '%s'",
            persisted.id,
            inferred_type,
            analysis.method,
            research_run_id,
        )
        return persisted
