"""REX Metric Extraction and Validation Engine (REX-020).

Parses structured experiment outputs into strictly validated, immutable Result domain models.
Enforces non-empty names, finite float constraints (rejecting NaN/Inf), schema compliance,
and atomic database persistence with event auditing.
"""

import csv
import io
import json
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from rex.analysis.exceptions import MalformedMetricError
from rex.domain.models import Result
from rex.execution.workspace import Workspace
from rex.observability.events import ActorType, EventSink

logger = logging.getLogger(__name__)


def _reject_non_finite_json_constant(c: str) -> None:
    raise MalformedMetricError(
        f"Prohibited non-finite floating-point constant '{c}' detected in metrics output. "
        "Metrics must be strictly finite numbers (NaN and Infinity are forbidden)."
    )


class RawMetric(BaseModel):
    """Intermediate validated container for extracted metric values before database persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(description="Name of the measured metric (e.g. accuracy, latency)")
    metric_value: float | None = Field(
        default=None,
        description="Finite scalar numeric metric value (must not be NaN or Inf)",
    )
    metric_unit: str = Field(default="", description="Unit of measurement (e.g. %, s, bytes)")
    result_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured dictionary for rich metrics (distributions, confusion matrices)",
    )

    @field_validator("metric_name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        cleaned = str(v).strip()
        if not cleaned:
            raise MalformedMetricError("Metric name cannot be empty.")
        return cleaned

    @field_validator("metric_value")
    @classmethod
    def _validate_finite_value(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            val_float = float(v)
        except (TypeError, ValueError) as err:
            raise MalformedMetricError(
                f"Metric value '{v}' cannot be converted to a float: {err}"
            ) from err

        if not math.isfinite(val_float):
            raise MalformedMetricError(
                f"Metric value must be a finite float, got non-finite value: {val_float}."
            )
        return val_float


class MetricExtractor:
    """Extracts, validates, and records empirical results from sandbox execution outputs."""

    def parse_metrics_json(
        self, content_or_path: str | bytes | Path | dict[str, Any] | list[Any]
    ) -> list[RawMetric]:
        """Parse JSON metrics content into validated RawMetric instances.

        Supports:
        - List of dicts: [{"metric_name": "loss", "metric_value": 0.12}, ...]
        - Dict mapping: {"loss": 0.12, "accuracy": 0.95}
        - Nested dict: {"loss": {"value": 0.12, "unit": ""}, "metrics": [...]}
        """
        raw_data: Any
        if isinstance(content_or_path, Path):
            if not content_or_path.exists():
                raise MalformedMetricError(f"Metrics file '{content_or_path}' does not exist.")
            text = content_or_path.read_text(encoding="utf-8")
            raw_data = json.loads(text, parse_constant=_reject_non_finite_json_constant)
        elif isinstance(content_or_path, (str, bytes)):
            text = (
                content_or_path.decode("utf-8")
                if isinstance(content_or_path, bytes)
                else content_or_path
            )
            raw_data = json.loads(text, parse_constant=_reject_non_finite_json_constant)
        else:
            raw_data = content_or_path

        return self._normalize_metrics_data(raw_data)

    def parse_metrics_csv(self, content_or_path: str | bytes | Path) -> list[RawMetric]:
        """Parse CSV metrics content into validated RawMetric instances.

        Expects header containing 'metric_name' (or 'name') and 'metric_value' (or 'value').
        """
        text: str
        if isinstance(content_or_path, Path):
            if not content_or_path.exists():
                raise MalformedMetricError(f"Metrics CSV file '{content_or_path}' does not exist.")
            text = content_or_path.read_text(encoding="utf-8")
        elif isinstance(content_or_path, bytes):
            text = content_or_path.decode("utf-8")
        else:
            text = content_or_path

        reader = csv.DictReader(io.StringIO(text.strip()))
        if not reader.fieldnames:
            raise MalformedMetricError("CSV metrics file has no header row.")

        # Find header mapping
        headers = {h.strip().lower(): h for h in reader.fieldnames if h}
        name_key = headers.get("metric_name") or headers.get("name") or headers.get("metric")
        value_key = headers.get("metric_value") or headers.get("value") or headers.get("val")
        unit_key = headers.get("metric_unit") or headers.get("unit")

        if not name_key or not value_key:
            raise MalformedMetricError(
                f"CSV must contain 'metric_name' and 'metric_value' columns. Found: {list(reader.fieldnames)}"
            )

        metrics: list[RawMetric] = []
        for row in reader:
            raw_name = row.get(name_key, "").strip()
            raw_val = row.get(value_key, "").strip()
            raw_unit = row.get(unit_key, "").strip() if unit_key else ""

            if not raw_name:
                continue

            try:
                metrics.append(
                    RawMetric(
                        metric_name=raw_name,
                        metric_value=float(raw_val) if raw_val else None,
                        metric_unit=raw_unit,
                    )
                )
            except Exception as exc:
                raise MalformedMetricError(f"Failed parsing CSV row {row}: {exc}") from exc

        return metrics

    def extract_from_workspace(self, workspace: Workspace) -> list[RawMetric]:
        """Scan workspace output directory and extract metrics from known files."""
        if not workspace.output_dir.exists():
            return []

        # Candidate files in priority order
        candidates = [
            workspace.output_dir / "metrics.json",
            workspace.output_dir / "results.json",
            workspace.output_dir / "eval.json",
            workspace.output_dir / "metrics.csv",
        ]

        # Also search for any *.metric.json or *metrics*.json
        for extra in sorted(workspace.output_dir.glob("*metric*.json")):
            if extra not in candidates:
                candidates.append(extra)

        extracted: list[RawMetric] = []
        for file_path in candidates:
            if not file_path.exists() or not file_path.is_file():
                continue
            try:
                if file_path.suffix.lower() == ".csv":
                    metrics = self.parse_metrics_csv(file_path)
                else:
                    metrics = self.parse_metrics_json(file_path)
                extracted.extend(metrics)
                logger.info(
                    "Extracted %d metrics from '%s' in workspace '%s'",
                    len(metrics),
                    file_path.name,
                    workspace.execution_id,
                )
            except MalformedMetricError:
                raise
            except Exception as exc:
                raise MalformedMetricError(
                    f"Error extracting metrics from '{file_path.name}': {exc}"
                ) from exc

        return extracted

    def persist_metrics(
        self,
        session: Session,
        execution_id: str,
        metrics: list[RawMetric],
        actor: ActorType | str = ActorType.EXECUTION_WORKER,
        event_sink: EventSink | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[Result]:
        """Atomically persist validated RawMetric list to database as domain Result models."""
        recorded_results: list[Result] = []
        actor_enum = actor if isinstance(actor, ActorType) else ActorType(actor)
        from rex.controller.executions import record_result

        for m in metrics:
            res = record_result(
                session=session,
                execution_id=execution_id,
                metric_name=m.metric_name,
                metric_value=m.metric_value,
                metric_unit=m.metric_unit,
                result_data=m.result_data,
                actor=actor_enum,
                event_sink=event_sink,
                context=context,
            )
            recorded_results.append(res)

        return recorded_results

    def extract_and_record(
        self,
        session: Session,
        execution_id: str,
        workspace: Workspace,
        actor: ActorType | str = ActorType.EXECUTION_WORKER,
        event_sink: EventSink | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[Result]:
        """Extract metrics from workspace output directory and persist them to database."""
        raw_metrics = self.extract_from_workspace(workspace)
        if not raw_metrics:
            return []
        return self.persist_metrics(
            session=session,
            execution_id=execution_id,
            metrics=raw_metrics,
            actor=actor,
            event_sink=event_sink,
            context=context,
        )

    def _normalize_metrics_data(self, raw_data: Any) -> list[RawMetric]:
        """Convert heterogeneous JSON structures into a uniform list of RawMetric instances."""
        if raw_data is None:
            return []

        # If data is a list of objects
        if isinstance(raw_data, list):
            metrics: list[RawMetric] = []
            for item in raw_data:
                if not isinstance(item, dict):
                    raise MalformedMetricError(
                        f"Expected metric item to be a dictionary, got {type(item).__name__}."
                    )
                name = item.get("metric_name") or item.get("name") or item.get("metric")
                val = item.get("metric_value") if "metric_value" in item else item.get("value")
                unit = item.get("metric_unit") or item.get("unit") or ""
                data = item.get("result_data") or item.get("data") or {}

                if not name:
                    raise MalformedMetricError(
                        f"Metric item missing 'name' or 'metric_name': {item}"
                    )
                metrics.append(
                    RawMetric(
                        metric_name=str(name),
                        metric_value=val,
                        metric_unit=str(unit),
                        result_data=dict(data) if isinstance(data, Mapping) else {},
                    )
                )
            return metrics

        # If data is a dictionary
        if isinstance(raw_data, dict):
            # Check if there is an explicit "metrics" key containing a list
            if "metrics" in raw_data and isinstance(raw_data["metrics"], list):
                return self._normalize_metrics_data(raw_data["metrics"])

            metrics = []
            for key, val in raw_data.items():
                if isinstance(val, dict):
                    nested_val = val.get("value") if "value" in val else val.get("metric_value")
                    nested_unit = val.get("unit") or val.get("metric_unit") or ""
                    nested_data = val.get("data") or val.get("result_data") or {}
                    metrics.append(
                        RawMetric(
                            metric_name=str(key),
                            metric_value=nested_val,
                            metric_unit=str(nested_unit),
                            result_data=dict(nested_data)
                            if isinstance(nested_data, Mapping)
                            else {},
                        )
                    )
                elif isinstance(val, (int, float, type(None))):
                    metrics.append(
                        RawMetric(
                            metric_name=str(key),
                            metric_value=val,
                        )
                    )
                elif isinstance(val, str):
                    try:
                        f_val = float(val)
                        metrics.append(RawMetric(metric_name=str(key), metric_value=f_val))
                    except ValueError as err:
                        raise MalformedMetricError(
                            f"Metric '{key}' has non-numeric string value '{val}'."
                        ) from err
                else:
                    raise MalformedMetricError(
                        f"Metric '{key}' has unsupported value type '{type(val).__name__}'."
                    )
            return metrics

        raise MalformedMetricError(
            f"Unsupported metric JSON root type '{type(raw_data).__name__}'. Expected dict or list."
        )
