"""REX Research Report Domain Models (REX-036).

Provides strongly typed, deeply immutable models for evidence-grounded research reports.
Every empirical claim, metric, and finding in a report is linked to its exact persisted
database entity and verified against evidence graph lineage.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _canonical_json(data: Any) -> str:
    """Serialize object to canonical, deterministic JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_hash(data: Any) -> str:
    """Compute SHA-256 hex digest of canonical JSON representation."""
    canonical = _canonical_json(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReportClaimRef(BaseModel):
    """Structured representation of a research claim within a report, with evidence lineage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str = Field(description="Unique identifier of the persisted claim")
    statement: str = Field(description="Scientific claim assertion text")
    claim_type: str = Field(
        default="observation", description="Type of claim (e.g. observation, finding)"
    )
    epistemic_status: str = Field(default="observed", description="Epistemic status of the claim")
    is_supported: bool = Field(
        description="Whether the claim is grounded in verified empirical evidence"
    )
    supporting_result_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="IDs of empirical results supporting this claim"
    )
    supporting_analysis_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="IDs of statistical analyses supporting this claim"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "claim_type": self.claim_type,
            "epistemic_status": self.epistemic_status,
            "is_supported": self.is_supported,
            "supporting_result_ids": list(self.supporting_result_ids),
            "supporting_analysis_ids": list(self.supporting_analysis_ids),
        }


class ReportExperimentSummary(BaseModel):
    """Summary of an experiment and its execution outcomes for honest reporting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(description="Unique experiment ID")
    objective: str = Field(description="Declared objective of the experiment")
    hypothesis_id: str | None = Field(default=None, description="Linked hypothesis ID if any")
    status: str = Field(description="Final experiment status")
    method: str = Field(
        default="empirical", description="Method specified in experiment specification"
    )
    baseline_name: str | None = Field(
        default=None, description="Name of the baseline condition evaluated"
    )
    baseline_value: float | None = Field(
        default=None, description="Baseline numerical reference value"
    )
    execution_count: int = Field(default=0, description="Total execution runs attempted")
    failed_executions_count: int = Field(
        default=0, description="Number of executions that failed or errored"
    )
    is_failed: bool = Field(
        default=False, description="Whether experiment failed or had execution errors"
    )
    failure_reasons: tuple[str, ...] = Field(
        default_factory=tuple, description="Explicit failure diagnostics and error explanations"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "objective": self.objective,
            "hypothesis_id": self.hypothesis_id,
            "status": self.status,
            "method": self.method,
            "baseline_name": self.baseline_name,
            "baseline_value": self.baseline_value,
            "execution_count": self.execution_count,
            "failed_executions_count": self.failed_executions_count,
            "is_failed": self.is_failed,
            "failure_reasons": list(self.failure_reasons),
        }


class ReportResultMetric(BaseModel):
    """Individual empirical metric captured during execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(description="Unique result record ID")
    execution_id: str = Field(description="Associated execution ID")
    metric_name: str = Field(description="Name of the metric evaluated")
    metric_value: float | None = Field(default=None, description="Numerical metric value recorded")
    metric_unit: str = Field(default="", description="Unit of measurement (e.g. ratio, seconds)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "execution_id": self.execution_id,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "metric_unit": self.metric_unit,
        }


class ReportAnalysisSummary(BaseModel):
    """Summary of a statistical analysis derived from empirical results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(description="Unique analysis record ID")
    analysis_type: str = Field(description="Type of analysis (e.g. summary, compare_groups)")
    method: str = Field(
        description="Statistical method used (e.g. sample_summary_statistics, t_test)"
    )
    metric_name: str = Field(default="", description="Target metric analyzed")
    mean: float | None = Field(default=None, description="Sample mean")
    std: float | None = Field(default=None, description="Sample standard deviation")
    ci_lower: float | None = Field(
        default=None, description="Lower bound of 95% confidence interval"
    )
    ci_upper: float | None = Field(
        default=None, description="Upper bound of 95% confidence interval"
    )
    sample_size: int = Field(default=0, description="Sample size analyzed")
    p_value: float | None = Field(default=None, description="p-value if hypothesis test performed")
    effect_size: float | None = Field(
        default=None, description="Standardized effect size (Cohen's d)"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "analysis_type": self.analysis_type,
            "method": self.method,
            "metric_name": self.metric_name,
            "mean": self.mean,
            "std": self.std,
            "ci_lower": self.ci_lower,
            "ci_upper": self.ci_upper,
            "sample_size": self.sample_size,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
        }


class ReportHypothesisSummary(BaseModel):
    """Summary of a scientific hypothesis and its empirical validation status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: str = Field(description="Unique hypothesis record ID")
    statement: str = Field(description="Hypothesis statement text")
    rationale: str = Field(default="", description="Theoretical or experimental rationale")
    expected_direction: str = Field(default="increase", description="Expected direction of effect")
    falsification_condition: str = Field(
        description="Condition that disproves or rejects the hypothesis"
    )
    status: str = Field(
        description="Current validation status (e.g. proposed, validated, falsified)"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "rationale": self.rationale,
            "expected_direction": self.expected_direction,
            "falsification_condition": self.falsification_condition,
            "status": self.status,
        }


class ReportMethodologyCritique(BaseModel):
    """Summary of methodological criticisms identified by the research critic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    critique_id: str = Field(description="Unique critique ID")
    iteration: int = Field(description="Loop iteration of the critique")
    summary: str = Field(description="High-level overview of critique")
    weaknesses: tuple[str, ...] = Field(
        default_factory=tuple, description="Identified methodological weaknesses"
    )
    methodological_concerns: tuple[str, ...] = Field(
        default_factory=tuple, description="Specific methodological concerns"
    )
    critical_findings: tuple[str, ...] = Field(
        default_factory=tuple, description="Critical findings requiring remediation"
    )
    recommended_action: str = Field(description="Action recommended by critic")

    def to_dict(self) -> dict[str, Any]:
        return {
            "critique_id": self.critique_id,
            "iteration": self.iteration,
            "summary": self.summary,
            "weaknesses": list(self.weaknesses),
            "methodological_concerns": list(self.methodological_concerns),
            "critical_findings": list(self.critical_findings),
            "recommended_action": self.recommended_action,
        }


class ResearchReport(BaseModel):
    """Comprehensive, machine-readable, evidence-grounded research report (REX-036).

    Synthesizes the complete empirical research investigation from persisted database records:
    - Research question and formulated hypotheses
    - Experimental methods and honest representation of all experiments (including failures)
    - Empirical metrics, descriptive statistics, and inferential findings
    - Verifiable scientific claims strictly linked to evidence graph records
    - Explicit quarantine and labeling of unsupported claims
    - Methodological limitations, critique weaknesses, and grounded conclusions
    """

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        arbitrary_types_allowed=True,
    )

    report_id: str = Field(
        default_factory=lambda: f"rep_{uuid.uuid4().hex[:12]}",
        description="Unique identifier for this report",
    )
    research_run_id: str = Field(description="Associated research run ID")
    title: str = Field(description="Title of the research investigation")
    research_question: str = Field(description="Central scientific research question investigated")
    run_status: str = Field(description="Lifecycle status of the research run at report generation")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timezone-aware UTC timestamp of report compilation",
    )
    hypotheses: tuple[ReportHypothesisSummary, ...] = Field(default_factory=tuple)
    experiments: tuple[ReportExperimentSummary, ...] = Field(default_factory=tuple)
    failed_experiments: tuple[ReportExperimentSummary, ...] = Field(default_factory=tuple)
    metrics: tuple[ReportResultMetric, ...] = Field(default_factory=tuple)
    analyses: tuple[ReportAnalysisSummary, ...] = Field(default_factory=tuple)
    claims: tuple[ReportClaimRef, ...] = Field(default_factory=tuple)
    supported_claims: tuple[ReportClaimRef, ...] = Field(default_factory=tuple)
    unsupported_claims: tuple[ReportClaimRef, ...] = Field(default_factory=tuple)
    critiques: tuple[ReportMethodologyCritique, ...] = Field(default_factory=tuple)
    limitations: tuple[str, ...] = Field(default_factory=tuple)
    conclusions: tuple[str, ...] = Field(default_factory=tuple)
    total_experiments: int = Field(default=0)
    total_executions: int = Field(default=0)
    total_results: int = Field(default=0)

    @property
    def is_fully_grounded(self) -> bool:
        """Whether all formulated claims are fully supported by empirical evidence."""
        return len(self.unsupported_claims) == 0

    def content_hash(self) -> str:
        """Deterministic SHA-256 hash of report data for provenance and verification."""
        data = self.to_dict()
        # Remove report_id and generated_at for stable semantic hashing
        data_clean = {k: v for k, v in data.items() if k not in ("report_id", "generated_at")}
        return _compute_hash(data_clean)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to standard JSON-compatible dictionary."""
        return {
            "report_id": self.report_id,
            "research_run_id": self.research_run_id,
            "title": self.title,
            "research_question": self.research_question,
            "run_status": self.run_status,
            "generated_at": self.generated_at.isoformat(),
            "total_experiments": self.total_experiments,
            "total_executions": self.total_executions,
            "total_results": self.total_results,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "experiments": [e.to_dict() for e in self.experiments],
            "failed_experiments": [e.to_dict() for e in self.failed_experiments],
            "metrics": [m.to_dict() for m in self.metrics],
            "analyses": [a.to_dict() for a in self.analyses],
            "claims": [c.to_dict() for c in self.claims],
            "supported_claims": [c.to_dict() for c in self.supported_claims],
            "unsupported_claims": [c.to_dict() for c in self.unsupported_claims],
            "critiques": [cr.to_dict() for cr in self.critiques],
            "limitations": list(self.limitations),
            "conclusions": list(self.conclusions),
            "is_fully_grounded": self.is_fully_grounded,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchReport:
        """Parse ResearchReport instance from a dictionary."""
        cleaned = {k: v for k, v in data.items() if k != "is_fully_grounded"}
        return cls.model_validate(cleaned)

    @classmethod
    def from_json(cls, json_str: str) -> ResearchReport:
        """Parse ResearchReport instance from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def to_markdown(self) -> str:
        """Render complete, publication-grade, human-readable Markdown research report."""
        lines: list[str] = [
            f"# Research Report: {self.title}",
            "",
            f"**Report ID:** `{self.report_id}`  ",
            f"**Research Run ID:** `{self.research_run_id}`  ",
            f"**Run Status:** `{self.run_status.upper()}`  ",
            f"**Generated At (UTC):** `{self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}`  ",
            f"**Evidence Integrity:** `{'VERIFIED' if self.is_fully_grounded else 'UNSUPPORTED CLAIMS DETECTED'}`  ",
            f"**Content SHA-256:** `{self.content_hash()}`  ",
            "",
            "---",
            "",
            "## 1. Executive Summary & Research Question",
            "",
            f"> **Research Question:** {self.research_question}",
            "",
            (
                f"This investigation evaluated **{self.total_experiments}** experimental design(s) across "
                f"**{self.total_executions}** execution run(s), capturing **{self.total_results}** raw metric measurements."
            ),
        ]

        if self.failed_experiments:
            lines.append(
                f"\n> ⚠️ **Operational Notice:** {len(self.failed_experiments)} experiment(s) encountered execution "
                "failures or non-zero exits, which have been honestly documented in Section 3 below."
            )

        # Hypotheses Section
        lines.extend(["", "---", "", "## 2. Scientific Hypotheses"])
        if not self.hypotheses:
            lines.append("\n*No formal hypotheses were recorded for this investigation.*")
        else:
            lines.extend(
                [
                    "",
                    "| Hypothesis ID | Statement | Expected Direction | Status | Falsification Condition |",
                    "| :--- | :--- | :---: | :---: | :--- |",
                ]
            )
            for h in self.hypotheses:
                lines.append(
                    f"| `{h.hypothesis_id}` | {h.statement} | `{h.expected_direction}` | "
                    f"**{h.status.upper()}** | {h.falsification_condition} |"
                )

        # Experimental Methodology & Execution Section
        lines.extend(["", "---", "", "## 3. Experimental Methodology & Execution"])
        if not self.experiments:
            lines.append("\n*No experiment records found in persistence.*")
        else:
            lines.extend(
                [
                    "",
                    "| Exp ID | Objective | Method | Baseline | Executions | Status |",
                    "| :--- | :--- | :--- | :--- | :---: | :---: |",
                ]
            )
            for e in self.experiments:
                b_str = f"{e.baseline_name} ({e.baseline_value})" if e.baseline_name else "None"
                st_str = "❌ FAILED" if e.is_failed else f"✅ {e.status.upper()}"
                lines.append(
                    f"| `{e.experiment_id}` | {e.objective} | `{e.method}` | {b_str} | "
                    f"{e.execution_count} | {st_str} |"
                )

        # Failed Experiments Honest Representation (AC-4)
        if self.failed_experiments:
            lines.extend(
                [
                    "",
                    "### ⚠️ Failed Experiments & Operational Anomalies",
                    "",
                    (
                        "In adherence to strict empirical honesty (REX Evidence Standard), all execution failures "
                        "and operational crashes are explicitly documented below:"
                    ),
                    "",
                ]
            )
            for fe in self.failed_experiments:
                lines.append(f"- **Experiment `{fe.experiment_id}`** (`{fe.objective}`):")
                lines.append(
                    f"  - Total executions attempted: {fe.execution_count} ({fe.failed_executions_count} failed)"
                )
                if fe.failure_reasons:
                    lines.append("  - Failure diagnostics:")
                    for r in fe.failure_reasons:
                        lines.append(f"    - `{r}`")
                else:
                    lines.append(
                        "  - Failure diagnostics: Execution status recorded as failed with non-zero exit code."
                    )

        # Results & Metrics Section
        lines.extend(["", "---", "", "## 4. Empirical Results & Measurements"])
        if not self.metrics:
            lines.append("\n*No empirical metrics recorded.*")
        else:
            lines.extend(
                [
                    "",
                    "| Result ID | Execution ID | Metric | Value | Unit |",
                    "| :--- | :--- | :--- | :---: | :--- |",
                ]
            )
            for m in self.metrics:
                val_str = f"{m.metric_value:.4f}" if m.metric_value is not None else "N/A"
                lines.append(
                    f"| `{m.result_id}` | `{m.execution_id}` | `{m.metric_name}` | {val_str} | {m.metric_unit} |"
                )

        # Statistical Analyses Section
        lines.extend(["", "---", "", "## 5. Statistical Analyses"])
        if not self.analyses:
            lines.append("\n*No statistical analyses computed.*")
        else:
            lines.extend(
                [
                    "",
                    "| Analysis ID | Method | Metric | Mean | 95% CI | Sample Size | p-value | Effect Size |",
                    "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |",
                ]
            )
            for a in self.analyses:
                ci_str = (
                    f"[{a.ci_lower:.4f}, {a.ci_upper:.4f}]"
                    if a.ci_lower is not None and a.ci_upper is not None
                    else "N/A"
                )
                mean_str = f"{a.mean:.4f}" if a.mean is not None else "N/A"
                p_str = f"{a.p_value:.4e}" if a.p_value is not None else "N/A"
                eff_str = f"{a.effect_size:.3f}" if a.effect_size is not None else "N/A"
                lines.append(
                    f"| `{a.analysis_id}` | `{a.method}` | `{a.metric_name}` | {mean_str} | {ci_str} | "
                    f"{a.sample_size} | {p_str} | {eff_str} |"
                )

        # Scientific Claims & Evidence Lineage (AC-2, AC-3)
        lines.extend(["", "---", "", "## 6. Scientific Claims & Evidence Lineage"])
        if not self.claims:
            lines.append("\n*No formal scientific claims formulated for this investigation.*")
        else:
            lines.extend(
                [
                    "",
                    (
                        "Every scientific claim below is mapped to its underlying evidence graph records. "
                        "Numerical assertions must link to verified results or statistical analyses."
                    ),
                    "",
                    "| Claim ID | Statement | Epistemic Status | Evidence Lineage | Validation |",
                    "| :--- | :--- | :---: | :--- | :---: |",
                ]
            )
            for c in self.claims:
                ev_links: list[str] = []
                for rid in c.supporting_result_ids:
                    ev_links.append(f"Result:`{rid}`")
                for aid in c.supporting_analysis_ids:
                    ev_links.append(f"Analysis:`{aid}`")
                ev_str = ", ".join(ev_links) if ev_links else "*None*"

                if c.is_supported:
                    val_badge = "✅ SUPPORTED"
                else:
                    val_badge = "⚠️ **UNSUPPORTED**"

                lines.append(
                    f"| `{c.claim_id}` | {c.statement} | `{c.epistemic_status}` | {ev_str} | {val_badge} |"
                )

        # Explicit Warning on Unsupported Claims (AC-3)
        if self.unsupported_claims:
            lines.extend(
                [
                    "",
                    "### ⚠️ Unsupported Claims Notice & Epistemic Anomalies",
                    "",
                    (
                        "The following claims lack backing evidence links in the relational evidence graph and "
                        "have been **quarantined from positive conclusions**:"
                    ),
                    "",
                ]
            )
            for uc in self.unsupported_claims:
                lines.append(f"- **[UNSUPPORTED CLAIM: `{uc.claim_id}`]**: {uc.statement}")

        # Limitations & Methodological Critiques Section
        lines.extend(["", "---", "", "## 7. Methodological Limitations & Criticisms"])
        if not self.limitations and not self.critiques:
            lines.append("\n*No specific methodological limitations flagged.*")
        else:
            if self.limitations:
                lines.append("\n### Identified Limitations:")
                for lim in self.limitations:
                    lines.append(f"- {lim}")

            if self.critiques:
                lines.append("\n### Research Critic Assessments:")
                for cr in self.critiques:
                    lines.append(f"\n#### Iteration {cr.iteration} Critique (`{cr.critique_id}`)")
                    lines.append(f"**Assessment:** {cr.summary}")
                    if cr.weaknesses:
                        lines.append("**Weaknesses:**")
                        for w in cr.weaknesses:
                            lines.append(f"- {w}")
                    if cr.critical_findings:
                        lines.append("**Critical Findings:**")
                        for cf in cr.critical_findings:
                            lines.append(f"- 🛑 {cf}")
                    lines.append(f"**Recommended Next Action:** `{cr.recommended_action}`")

        # Conclusions Section
        lines.extend(["", "---", "", "## 8. Grounded Conclusions"])
        if not self.conclusions:
            lines.append("\n*Investigation incomplete or terminated without formal conclusions.*")
        else:
            lines.append("\nBased strictly on verified, machine-auditable empirical evidence:\n")
            for conc in self.conclusions:
                lines.append(f"- {conc}")

        lines.extend(
            [
                "",
                "---",
                f"*Report compiled by REX Reporting Subsystem (REX-036). Content Hash: `{self.content_hash()}`*",
            ]
        )
        return "\n".join(lines)


__all__ = [
    "ReportAnalysisSummary",
    "ReportClaimRef",
    "ReportExperimentSummary",
    "ReportHypothesisSummary",
    "ReportMethodologyCritique",
    "ReportResultMetric",
    "ResearchReport",
]
