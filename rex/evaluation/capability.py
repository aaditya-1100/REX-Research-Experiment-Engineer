"""REX Researcher Capability Scorecard (REX-045 / Batch 10 Track A Sec 19).

Defines the authoritative 6-dimensional scientific capability scorecard for autonomous research agents:
1. Hypothesis Quality & Falsifiability (Sec 6-7)
2. Experiment Design & Flaw Detection (Sec 8)
3. Code Generation & Method Alignment (Sec 9-10)
4. Scientific Self-Deception Resistance & Grounding (Sec 11-13)
5. Critique, Decision & Iteration Refinement (Sec 14-16)
6. Literature & Multi-Model Abstraction Integrity (Sec 17-18)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapabilityDimensionScore(BaseModel):
    """Evaluation score and metrics for an individual research capability dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension_id: str = Field(description="Unique dimension code (e.g., DIM_1_HYPOTHESIS_QUALITY)")
    title: str = Field(description="Human-readable title of the dimension")
    section_ref: str = Field(description="Reference section in the Batch 10 specification")
    score: float = Field(ge=0.0, le=1.0, description="Normalized score from 0.0 to 1.0")
    benchmark_count: int = Field(ge=0, description="Total number of benchmark test cases evaluated")
    passed_count: int = Field(ge=0, description="Number of passed benchmark test cases")
    passed: bool = Field(
        description="True if score meets or exceeds minimum threshold (typically >= 0.85)"
    )
    status: str = Field(default="GREEN", description="Status indicator: GREEN, YELLOW, or RED")
    key_findings: list[str] = Field(
        default_factory=list, description="Qualitative findings and strengths"
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="Fine-grained empirical sub-metrics"
    )


class ResearcherCapabilityScorecard(BaseModel):
    """Authoritative comprehensive scientific capability scorecard for REX autonomous research agents."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"cap_{uuid.uuid4().hex[:12]}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    overall_score: float = Field(
        ge=0.0, le=1.0, description="Aggregated overall capability score across all dimensions"
    )
    is_ready: bool = Field(
        description="True if platform meets researcher-grade threshold with zero RED blockers"
    )
    readiness_classification: str = Field(
        default="READY",
        description="Platform readiness state: READY (GREEN), CONDITIONAL (YELLOW), BLOCKED (RED)",
    )

    # 6 Authoritative Dimensions
    hypothesis_quality: CapabilityDimensionScore
    experiment_design: CapabilityDimensionScore
    code_method_alignment: CapabilityDimensionScore
    self_deception_resistance: CapabilityDimensionScore
    iteration_refinement: CapabilityDimensionScore
    literature_stage_integrity: CapabilityDimensionScore

    summary_markdown: str = Field(default="", description="Rendered markdown capability scorecard")

    @classmethod
    def create(
        cls,
        hypothesis_quality: CapabilityDimensionScore,
        experiment_design: CapabilityDimensionScore,
        code_method_alignment: CapabilityDimensionScore,
        self_deception_resistance: CapabilityDimensionScore,
        iteration_refinement: CapabilityDimensionScore,
        literature_stage_integrity: CapabilityDimensionScore,
    ) -> ResearcherCapabilityScorecard:
        """Construct scorecard, aggregate overall scores, and render markdown."""
        dimensions = [
            hypothesis_quality,
            experiment_design,
            code_method_alignment,
            self_deception_resistance,
            iteration_refinement,
            literature_stage_integrity,
        ]

        overall_score = round(sum(d.score for d in dimensions) / len(dimensions), 3)

        has_red = any(d.status == "RED" or not d.passed for d in dimensions)
        has_yellow = any(d.status == "YELLOW" for d in dimensions)

        if has_red:
            readiness = "BLOCKED"
            is_ready = False
        elif has_yellow:
            readiness = "CONDITIONAL"
            is_ready = overall_score >= 0.85
        else:
            readiness = "READY"
            is_ready = overall_score >= 0.90

        scorecard = cls(
            overall_score=overall_score,
            is_ready=is_ready,
            readiness_classification=readiness,
            hypothesis_quality=hypothesis_quality,
            experiment_design=experiment_design,
            code_method_alignment=code_method_alignment,
            self_deception_resistance=self_deception_resistance,
            iteration_refinement=iteration_refinement,
            literature_stage_integrity=literature_stage_integrity,
        )

        scorecard.summary_markdown = render_capability_scorecard_markdown(scorecard)
        return scorecard


def render_capability_scorecard_markdown(scorecard: ResearcherCapabilityScorecard) -> str:
    """Format the capability scorecard into authoritative markdown."""
    dims = [
        scorecard.hypothesis_quality,
        scorecard.experiment_design,
        scorecard.code_method_alignment,
        scorecard.self_deception_resistance,
        scorecard.iteration_refinement,
        scorecard.literature_stage_integrity,
    ]

    lines = [
        "# REX Researcher Capability Scorecard (Batch 10 Track A)",
        "",
        f"- **Scorecard ID:** `{scorecard.id}`",
        f"- **Timestamp:** `{scorecard.timestamp.isoformat()}`",
        f"- **Overall Capability Score:** **{scorecard.overall_score * 100:.1f}%**",
        f"- **Readiness Classification:** **{scorecard.readiness_classification}** (Ready: `{scorecard.is_ready}`)",
        "",
        "## Multi-Dimensional Capability Matrix",
        "",
        "| Dimension | Section | Tests Passed | Pass Rate | Score | Status |",
        "|---|---|---|---|---|---|",
    ]

    for d in dims:
        pct = (
            (d.passed_count / d.benchmark_count * 100) if d.benchmark_count > 0 else (d.score * 100)
        )
        lines.append(
            f"| **{d.title}** | {d.section_ref} | {d.passed_count}/{d.benchmark_count} | {pct:.1f}% | {d.score * 100:.1f}% | {d.status} |"
        )

    lines.extend(
        [
            "",
            "## Dimension Findings & Integrity Guarantees",
            "",
        ]
    )

    for d in dims:
        lines.append(f"### {d.title} ({d.section_ref})")
        lines.append(f"- **Score:** {d.score * 100:.1f}% [{d.status}]")
        if d.key_findings:
            for f in d.key_findings:
                lines.append(f"- {f}")
        lines.append("")

    return "\n".join(lines)
