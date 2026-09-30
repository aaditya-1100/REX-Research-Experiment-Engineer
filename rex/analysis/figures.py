"""REX Experiment Visualization and Deterministic Figure Generation (REX-022).

Generates deterministic scientific visualizations (comparison bar charts, metric progression curves,
and distribution boxplots) directly from empirical Result sets. Enforces headless rendering,
PNG/SVG export, cryptographic SHA-256 hash generation, and lineage tracking to source Result IDs.
"""

import hashlib
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Ensure headless Agg backend before importing pyplot
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from rex.analysis.exceptions import FigureGenerationError
from rex.analysis.statistics import StatisticalAnalyzer, SummaryStatistics
from rex.domain.models import Artifact, ArtifactType, Result
from rex.observability.events import ActorType, EventSink
from rex.persistence.models import ResultModel

logger = logging.getLogger(__name__)


class FigureArtifactInfo(BaseModel):
    """Metadata representing a generated scientific figure artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(description="Relative path of figure inside workspace or artifact store")
    file_path: Path = Field(description="Absolute path to generated figure file on disk")
    content_hash: str = Field(description="Cryptographic SHA-256 digest of figure bytes")
    size_bytes: int = Field(ge=0, description="Figure file size in bytes")
    artifact_type: ArtifactType = Field(
        default=ArtifactType.FIGURE,
        description="Artifact classification",
    )
    figure_type: str = Field(description="Visual chart type (comparison_bar, progression, boxplot)")
    format: str = Field(description="File format extension (png, svg)")
    source_result_ids: list[str] = Field(
        default_factory=list,
        description="IDs of stored Result entities plotted in this figure",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplemental visual parameters, labels, and statistical summaries",
    )


class FigureGenerator:
    """Deterministic scientific figure and chart generator for empirical research outcomes."""

    def __init__(self, analyzer: StatisticalAnalyzer | None = None) -> None:
        self.analyzer = analyzer or StatisticalAnalyzer()

    def generate_comparison_bar_chart(
        self,
        baseline_results: Sequence[Result | ResultModel | dict[str, Any] | float | int],
        treatment_results: Sequence[Result | ResultModel | dict[str, Any] | float | int],
        output_dir: Path,
        filename: str = "baseline_comparison.png",
        title: str = "Baseline vs Treatment Comparison",
        baseline_label: str = "Baseline",
        treatment_label: str = "Treatment",
        metric_name: str = "",
        metric_unit: str = "",
        format: str = "png",
    ) -> FigureArtifactInfo:
        """Generate a comparison bar chart with standard error (SEM) error bars from empirical results."""
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_path = (output_dir / filename).resolve()

        base_summary: SummaryStatistics = self.analyzer.compute_summary(
            baseline_results, metric_name=metric_name or "metric"
        )
        treat_summary: SummaryStatistics = self.analyzer.compute_summary(
            treatment_results, metric_name=metric_name or "metric"
        )

        labels = [baseline_label, treatment_label]
        means = [base_summary.mean, treat_summary.mean]
        # Use SEM if available, otherwise std_dev or 0
        sems = [
            base_summary.standard_error or 0.0,
            treat_summary.standard_error or 0.0,
        ]

        fig, ax = plt.subplots(figsize=(6, 5))
        try:
            colors = ["#4C72B0", "#55A868"]
            bars = ax.bar(
                labels,
                means,
                yerr=sems,
                capsize=5,
                color=colors,
                alpha=0.85,
                edgecolor="black",
                linewidth=1.0,
            )

            ylabel = metric_name or base_summary.metric_name
            if metric_unit:
                ylabel += f" ({metric_unit})"
            ax.set_ylabel(ylabel, fontsize=11)
            ax.set_title(title, fontsize=12, pad=12)
            ax.grid(axis="y", linestyle="--", alpha=0.5)

            # Annotate bar values
            for bar in bars:
                height = bar.get_height()
                ax.annotate(
                    f"{height:.3f}",
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )

            plt.tight_layout()
            fig.savefig(dest_path, format=format, dpi=150, bbox_inches="tight")
        except Exception as exc:
            raise FigureGenerationError(f"Failed rendering comparison bar chart: {exc}") from exc
        finally:
            plt.close(fig)

        content_bytes = dest_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        size_bytes = len(content_bytes)

        combined_ids = sorted(set(base_summary.source_result_ids + treat_summary.source_result_ids))

        return FigureArtifactInfo(
            path=dest_path.name,
            file_path=dest_path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            artifact_type=ArtifactType.FIGURE,
            figure_type="comparison_bar",
            format=format,
            source_result_ids=combined_ids,
            metadata={
                "title": title,
                "metric_name": metric_name or base_summary.metric_name,
                "baseline_mean": base_summary.mean,
                "treatment_mean": treat_summary.mean,
                "baseline_sem": base_summary.standard_error,
                "treatment_sem": treat_summary.standard_error,
            },
        )

    def generate_progression_plot(
        self,
        series_data: Mapping[str, Sequence[float]],
        output_dir: Path,
        filename: str = "metric_progression.png",
        title: str = "Metric Progression",
        xlabel: str = "Step",
        ylabel: str = "Value",
        format: str = "png",
        source_result_ids: list[str] | None = None,
    ) -> FigureArtifactInfo:
        """Generate a multi-series line progression plot (e.g. loss or accuracy curves over epochs)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_path = (output_dir / filename).resolve()

        fig, ax = plt.subplots(figsize=(7, 4.5))
        try:
            for label, series in series_data.items():
                x_vals = list(range(1, len(series) + 1))
                ax.plot(x_vals, series, label=label, marker="o", markersize=4, linewidth=1.5)

            ax.set_title(title, fontsize=12, pad=12)
            ax.set_xlabel(xlabel, fontsize=10)
            ax.set_ylabel(ylabel, fontsize=10)
            ax.grid(True, linestyle="--", alpha=0.5)
            ax.legend(loc="best", frameon=True)

            plt.tight_layout()
            fig.savefig(dest_path, format=format, dpi=150, bbox_inches="tight")
        except Exception as exc:
            raise FigureGenerationError(f"Failed rendering progression plot: {exc}") from exc
        finally:
            plt.close(fig)

        content_bytes = dest_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        size_bytes = len(content_bytes)

        return FigureArtifactInfo(
            path=dest_path.name,
            file_path=dest_path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            artifact_type=ArtifactType.FIGURE,
            figure_type="progression",
            format=format,
            source_result_ids=source_result_ids or [],
            metadata={
                "title": title,
                "series_names": list(series_data.keys()),
                "total_steps": max((len(s) for s in series_data.values()), default=0),
            },
        )

    def generate_distribution_boxplot(
        self,
        groups: Mapping[str, Sequence[Result | ResultModel | dict[str, Any] | float | int]],
        output_dir: Path,
        filename: str = "distribution_boxplot.png",
        title: str = "Metric Distribution across Conditions",
        ylabel: str = "Metric Value",
        format: str = "png",
    ) -> FigureArtifactInfo:
        """Generate a boxplot comparing metric sample distributions across experimental conditions."""
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_path = (output_dir / filename).resolve()

        labels = list(groups.keys())
        parsed_data = []
        all_ids: list[str] = []

        for name, res_seq in groups.items():
            vals, ids, _ = self.analyzer._extract_values_and_ids(res_seq)
            if not vals:
                raise FigureGenerationError(
                    f"Group '{name}' contains no valid metric values for boxplot."
                )
            parsed_data.append(vals)
            all_ids.extend(ids)

        fig, ax = plt.subplots(figsize=(6.5, 5))
        try:
            ax.boxplot(
                parsed_data,
                patch_artist=True,
                boxprops={"facecolor": "#C44E52", "alpha": 0.7},
            )
            ax.set_xticks(list(range(1, len(labels) + 1)))
            ax.set_xticklabels(labels)
            ax.set_title(title, fontsize=12, pad=12)
            ax.set_ylabel(ylabel, fontsize=10)
            ax.grid(axis="y", linestyle="--", alpha=0.5)

            plt.tight_layout()
            fig.savefig(dest_path, format=format, dpi=150, bbox_inches="tight")
        except Exception as exc:
            raise FigureGenerationError(f"Failed rendering distribution boxplot: {exc}") from exc
        finally:
            plt.close(fig)

        content_bytes = dest_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        size_bytes = len(content_bytes)

        return FigureArtifactInfo(
            path=dest_path.name,
            file_path=dest_path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            artifact_type=ArtifactType.FIGURE,
            figure_type="boxplot",
            format=format,
            source_result_ids=sorted(set(all_ids)),
            metadata={
                "title": title,
                "group_names": labels,
                "group_sizes": [len(d) for d in parsed_data],
            },
        )

    def record_figure_artifact(
        self,
        session: Session,
        execution_id: str,
        figure_info: FigureArtifactInfo,
        actor: ActorType | str = ActorType.EXECUTION_WORKER,
        event_sink: EventSink | None = None,
        context: dict[str, Any] | None = None,
    ) -> Artifact:
        """Register generated figure as a verified Artifact entity in the database."""
        meta = dict(figure_info.metadata)
        meta["figure_type"] = figure_info.figure_type
        meta["format"] = figure_info.format
        meta["source_result_ids"] = figure_info.source_result_ids
        if context:
            meta.update(context)

        from rex.controller.executions import record_artifact

        return record_artifact(
            session=session,
            artifact_type=figure_info.artifact_type,
            path=figure_info.path,
            content_hash=figure_info.content_hash,
            size_bytes=figure_info.size_bytes,
            execution_id=execution_id,
            metadata=meta,
            actor=actor,
            event_sink=event_sink,
            context=context,
        )
