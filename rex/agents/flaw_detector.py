"""Automated Experiment Design Flaw Detector (REX-015 / Track A Sec 8).

Detects critical methodological flaws in proposed experiment specifications before execution:
1. Cross-dataset comparisons (baseline evaluated on Dataset A, method on Dataset B, or split mismatches).
2. Confounded multi-variable shifts (simultaneous hyperparameter/architecture changes without ablations).
3. Training metric as a proxy for test generalization (e.g., using train loss/accuracy to claim generalization).
4. Missing baseline controls (omitted, empty, or uncalibrated baseline reference).
5. Single-seed fragility (single seed or 1 repetition when claiming comparative statistical superiority).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rex.domain.models import ExperimentSpecification


class DesignFlawType(StrEnum):
    """Categorization of experiment design methodological defects."""

    CROSS_DATASET_COMPARISON = "cross_dataset_comparison"
    CONFOUNDED_VARIABLES = "confounded_variables"
    TRAIN_METRIC_PROXY_FOR_GENERALIZATION = "train_metric_proxy_for_generalization"
    MISSING_BASELINE = "missing_baseline"
    SINGLE_SEED_FRAGILITY = "single_seed_fragility"


class FlawSeverity(StrEnum):
    """Severity classification for experiment design flaws."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DesignFlaw(BaseModel):
    """Representation of an individual detected design flaw."""

    model_config = ConfigDict(extra="forbid")

    flaw_type: DesignFlawType = Field(description="Category of methodological flaw")
    severity: FlawSeverity = Field(description="Severity impact on scientific validity")
    description: str = Field(description="Detailed explanation of the detected flaw")
    recommendation: str = Field(description="Actionable remediation steps to fix the design")
    context: dict[str, Any] = Field(
        default_factory=dict, description="Diagnostic contextual metadata"
    )


class DesignFlawReport(BaseModel):
    """Complete diagnostic report for an experiment specification."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(
        description="True if experiment has zero CRITICAL or HIGH severity flaws"
    )
    has_critical_flaws: bool = Field(description="True if one or more CRITICAL flaws exist")
    flaw_count: int = Field(description="Total number of flaws detected")
    flaws: list[DesignFlaw] = Field(default_factory=list, description="List of detected flaws")
    soundness_score: float = Field(
        ge=0.0, le=1.0, description="Normalized methodological soundness score (0.0 to 1.0)"
    )


class ExperimentDesignFlawDetector:
    """Deterministic analyzer identifying methodological vulnerabilities in experiment specifications."""

    def detect_flaws(
        self,
        specification: ExperimentSpecification | dict[str, Any],
    ) -> DesignFlawReport:
        """Inspect an experiment specification and produce an exhaustive flaw report."""
        spec_dict: dict[str, Any]
        if isinstance(specification, ExperimentSpecification):
            spec_dict = specification.to_dict()
        elif isinstance(specification, (dict, Mapping)):
            spec_dict = dict(specification)
        else:
            raise TypeError(f"Unsupported specification type: {type(specification)}")

        flaws: list[DesignFlaw] = []

        # 1. Missing baseline check
        baseline_flaw = self._check_missing_baseline(spec_dict)
        if baseline_flaw:
            flaws.append(baseline_flaw)

        # 2. Cross-dataset comparison check
        cross_ds_flaws = self._check_cross_dataset(spec_dict)
        flaws.extend(cross_ds_flaws)

        # 3. Confounded multi-variable shift check
        confounded_flaw = self._check_confounded_variables(spec_dict)
        if confounded_flaw:
            flaws.append(confounded_flaw)

        # 4. Training metric proxy for generalization
        metric_flaw = self._check_train_metric_proxy(spec_dict)
        if metric_flaw:
            flaws.append(metric_flaw)

        # 5. Single-seed fragility check
        seed_flaw = self._check_seed_fragility(spec_dict)
        if seed_flaw:
            flaws.append(seed_flaw)

        # Calculate soundness score
        critical_count = sum(1 for f in flaws if f.severity == FlawSeverity.CRITICAL)
        high_count = sum(1 for f in flaws if f.severity == FlawSeverity.HIGH)
        medium_count = sum(1 for f in flaws if f.severity == FlawSeverity.MEDIUM)
        low_count = sum(1 for f in flaws if f.severity == FlawSeverity.LOW)

        penalty = (
            (critical_count * 0.4)
            + (high_count * 0.25)
            + (medium_count * 0.15)
            + (low_count * 0.05)
        )
        soundness_score = round(max(0.0, min(1.0, 1.0 - penalty)), 3)

        has_critical = critical_count > 0
        is_valid = critical_count == 0 and high_count == 0

        return DesignFlawReport(
            is_valid=is_valid,
            has_critical_flaws=has_critical,
            flaw_count=len(flaws),
            flaws=flaws,
            soundness_score=soundness_score,
        )

    def _check_missing_baseline(self, spec: dict[str, Any]) -> DesignFlaw | None:
        """Verify baseline definition is present and non-trivial."""
        baseline = spec.get("baseline")
        if baseline is None:
            return DesignFlaw(
                flaw_type=DesignFlawType.MISSING_BASELINE,
                severity=FlawSeverity.CRITICAL,
                description="No baseline reference configuration was declared.",
                recommendation="Specify an explicit baseline configuration or benchmark reference model.",
                context={"baseline": baseline},
            )

        if not isinstance(baseline, Mapping):
            return DesignFlaw(
                flaw_type=DesignFlawType.MISSING_BASELINE,
                severity=FlawSeverity.CRITICAL,
                description=f"Baseline reference must be a structured configuration mapping, got {type(baseline).__name__}.",
                recommendation="Specify an explicit baseline configuration dictionary or benchmark reference model.",
                context={"baseline": baseline},
            )

        if not baseline:
            return DesignFlaw(
                flaw_type=DesignFlawType.MISSING_BASELINE,
                severity=FlawSeverity.CRITICAL,
                description="Baseline reference is an empty dictionary.",
                recommendation="Provide a concrete baseline method, model, or reference metric value.",
                context={"baseline": baseline},
            )

        # Check if name is placeholder or empty
        name = str(baseline.get("name", "")).strip().lower()
        if not name or name in ("none", "null", "undefined"):
            return DesignFlaw(
                flaw_type=DesignFlawType.MISSING_BASELINE,
                severity=FlawSeverity.CRITICAL,
                description=f"Baseline name '{name}' is uninformative or empty.",
                recommendation="Specify a concrete standard baseline identifier (e.g. 'vanilla_adamw', 'standard_resnet').",
                context={"baseline": baseline},
            )

        return None

    def _check_cross_dataset(self, spec: dict[str, Any]) -> list[DesignFlaw]:
        """Detect cross-dataset comparison or split discrepancies between baseline and treatment."""
        flaws: list[DesignFlaw] = []
        raw_datasets = spec.get("datasets")
        datasets = raw_datasets if isinstance(raw_datasets, (list, tuple)) else []
        baseline = spec.get("baseline", {})

        # Extract baseline dataset if specified
        baseline_dataset = None
        baseline_split = None
        if isinstance(baseline, Mapping):
            baseline_dataset = baseline.get("dataset") or baseline.get("dataset_name")
            baseline_split = baseline.get("split")

        # Treatment datasets
        treatment_names: set[str] = set()
        treatment_splits: set[str] = set()
        for d in datasets:
            if isinstance(d, Mapping):
                d_name = d.get("name", "")
                if d_name:
                    treatment_names.add(str(d_name).strip().lower())
                d_split = d.get("split", "")
                if d_split:
                    treatment_splits.add(str(d_split).strip().lower())

        if baseline_dataset:
            b_ds_clean = str(baseline_dataset).strip().lower()
            if treatment_names and b_ds_clean not in treatment_names:
                flaws.append(
                    DesignFlaw(
                        flaw_type=DesignFlawType.CROSS_DATASET_COMPARISON,
                        severity=FlawSeverity.CRITICAL,
                        description=(
                            f"Cross-dataset comparison detected: baseline is evaluated on dataset '{baseline_dataset}', "
                            f"whereas experimental arm evaluates on {sorted(treatment_names)}."
                        ),
                        recommendation="Evaluate both baseline and proposed method on identical datasets.",
                        context={
                            "baseline_dataset": baseline_dataset,
                            "treatment_datasets": list(treatment_names),
                        },
                    )
                )

        if baseline_split:
            b_split_clean = str(baseline_split).strip().lower()
            if treatment_splits and b_split_clean not in treatment_splits:
                flaws.append(
                    DesignFlaw(
                        flaw_type=DesignFlawType.CROSS_DATASET_COMPARISON,
                        severity=FlawSeverity.HIGH,
                        description=(
                            f"Split mismatch detected: baseline uses split '{baseline_split}', "
                            f"while experimental arm uses splits {sorted(treatment_splits)}."
                        ),
                        recommendation="Ensure evaluation splits are strictly matched between baseline and treatment.",
                        context={
                            "baseline_split": baseline_split,
                            "treatment_splits": list(treatment_splits),
                        },
                    )
                )

        return flaws

    def _check_confounded_variables(self, spec: dict[str, Any]) -> DesignFlaw | None:
        """Detect multiple simultaneous variable changes without isolated ablation controls."""
        variables = spec.get("variables", {})
        if not isinstance(variables, Mapping):
            return None

        # Count modified independent variables
        var_count = len(variables)
        controls = spec.get("controls", {})
        raw_methods = spec.get("analysis_methods")
        analysis_methods = (
            [str(m).lower() for m in raw_methods] if isinstance(raw_methods, (list, tuple)) else []
        )

        has_ablation_method = any("ablation" in m for m in analysis_methods)
        ablation_arms = spec.get("ablation_arms") or spec.get("ablations")
        has_ablation_arms = bool(ablation_arms) and isinstance(
            ablation_arms, (list, tuple, Mapping)
        )

        has_ablation = has_ablation_method or has_ablation_arms

        if var_count >= 2 and not has_ablation:
            return DesignFlaw(
                flaw_type=DesignFlawType.CONFOUNDED_VARIABLES,
                severity=FlawSeverity.HIGH,
                description=(
                    f"Confounded multi-variable shift: {var_count} independent variables "
                    f"({sorted(variables.keys())}) are varied simultaneously without ablation controls."
                ),
                recommendation="Isolate variable effects by varying one factor at a time or including ablation studies.",
                context={"variables": list(variables.keys()), "controls": controls},
            )

        return None

    def _check_train_metric_proxy(self, spec: dict[str, Any]) -> DesignFlaw | None:
        """Detect using training metrics as an invalid proxy for test generalization."""
        raw_metrics = spec.get("metrics")
        metrics = raw_metrics if isinstance(raw_metrics, (list, tuple)) else []
        raw_datasets = spec.get("datasets")
        datasets = raw_datasets if isinstance(raw_datasets, (list, tuple)) else []
        desc = (str(spec.get("description") or "") + " " + str(spec.get("name") or "")).lower()

        is_generalization_claim = any(
            term in desc
            for term in ("generalization", "generalize", "test performance", "unseen", "transfer")
        )

        metric_names = []
        for m in metrics:
            if isinstance(m, Mapping):
                metric_names.append(str(m.get("name", "")).strip().lower())
            elif isinstance(m, str):
                metric_names.append(m.strip().lower())

        splits = []
        for d in datasets:
            if isinstance(d, Mapping):
                splits.append(str(d.get("split", "")).strip().lower())

        # Check if only train metrics are monitored
        only_train_metrics = bool(metric_names) and all(
            m.startswith("train_") or m in ("train_loss", "train_acc", "train_accuracy")
            for m in metric_names
        )

        only_train_splits = bool(splits) and all(s in ("train", "training") for s in splits)

        if is_generalization_claim and (only_train_metrics or only_train_splits):
            return DesignFlaw(
                flaw_type=DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION,
                severity=FlawSeverity.CRITICAL,
                description=(
                    "Invalid generalization proxy: Experiment claims test generalization, "
                    f"but metrics are {metric_names} and splits are {splits}."
                ),
                recommendation="Measure validation/test metrics on held-out test splits to evaluate generalization.",
                context={"metrics": metric_names, "splits": splits},
            )

        if only_train_metrics and not any(m.startswith(("val_", "test_")) for m in metric_names):
            return DesignFlaw(
                flaw_type=DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION,
                severity=FlawSeverity.HIGH,
                description=f"Experiment evaluates solely on training metrics ({metric_names}) with no validation/test tracking.",
                recommendation="Add validation or test set evaluation metrics.",
                context={"metrics": metric_names},
            )

        return None

    def _check_seed_fragility(self, spec: dict[str, Any]) -> DesignFlaw | None:
        """Detect single-seed experimental setups vulnerable to stochastic noise."""
        seeds = spec.get("seeds", [])
        try:
            raw_reps = spec.get("repetitions")
            repetitions = int(raw_reps) if raw_reps is not None else 1
            repetitions = max(repetitions, 1)
        except (ValueError, TypeError):
            repetitions = 1

        seed_count = len(seeds) if isinstance(seeds, (list, tuple)) else (1 if seeds else 0)

        if seed_count <= 1 and repetitions <= 1:
            return DesignFlaw(
                flaw_type=DesignFlawType.SINGLE_SEED_FRAGILITY,
                severity=FlawSeverity.HIGH,
                description=(
                    f"Single-seed fragility: Experiment configures only {seed_count} seed(s) "
                    f"and {repetitions} repetition(s). Findings cannot establish statistical significance."
                ),
                recommendation="Specify multiple independent random seeds (e.g. >= 3 seeds) to quantify variance.",
                context={"seeds": seeds, "repetitions": repetitions},
            )

        return None
