"""Scientific Self-Deception & Grounding Verifier (REX-043 / Track A Sec 11-13).

Protects the scientific discovery loop against self-deception, false validation, and spurious conclusions:
1. Statistical significance gating (enforces p < 0.05 for superiority claims).
2. Complete seed verification (detects cherry-picked runs or omitted execution seeds).
3. Confounded causal assertions and metric surrogacy (train loss proxy for test generalization).
4. Automatic fail-closed state transitions:
   - ClaimStatus.INCONCLUSIVE / ClaimStatus.REJECTED
   - HypothesisStatus.INCONCLUSIVE / HypothesisStatus.FALSIFIED
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from rex.domain.models import ClaimStatus, HypothesisStatus


class SelfDeceptionType(StrEnum):
    """Categorization of scientific self-deception and ungrounded claim patterns."""

    LACKS_STATISTICAL_SIGNIFICANCE = "lacks_statistical_significance"
    CHERRY_PICKED_SEEDS = "cherry_picked_seeds"
    CONFOUNDED_CAUSAL_CLAIM = "confounded_causal_claim"
    METRIC_SURROGATE_MISMATCH = "metric_surrogate_mismatch"
    SPURIOUS_CORRELATION = "spurious_correlation"


class SelfDeceptionFinding(BaseModel):
    """Specific finding from self-deception auditing."""

    model_config = ConfigDict(extra="forbid")

    finding_type: SelfDeceptionType = Field(description="Category of self-deception detected")
    severity: str = Field(default="critical", description="Severity impact (critical or warning)")
    description: str = Field(description="Explanatory details of the evidentiary flaw")
    recommended_claim_status: ClaimStatus = Field(
        description="Authoritative fail-closed status for the claim"
    )
    recommended_hypothesis_status: HypothesisStatus = Field(
        description="Authoritative fail-closed status for the associated hypothesis"
    )
    context: dict[str, Any] = Field(default_factory=dict, description="Diagnostic data and metrics")


class SelfDeceptionAuditReport(BaseModel):
    """Comprehensive evidentiary grounding audit report."""

    model_config = ConfigDict(extra="forbid")

    is_grounded: bool = Field(
        description="True if claim is free of self-deception and fully grounded"
    )
    findings: list[SelfDeceptionFinding] = Field(
        default_factory=list, description="Self-deception findings"
    )
    p_value: float | None = Field(
        default=None, description="Extracted p-value if statistical test performed"
    )
    is_statistically_significant: bool = Field(
        default=True, description="True if p-value is below significance threshold (default 0.05)"
    )
    executed_seeds: list[int] = Field(default_factory=list, description="All random seeds executed")
    aggregated_seeds: list[int] = Field(
        default_factory=list, description="Seeds actually included in empirical analysis"
    )
    is_seed_complete: bool = Field(
        default=True, description="True if zero executed seeds were omitted or cherry-picked"
    )
    grounding_score: float = Field(ge=0.0, le=1.0, description="Normalized grounding score")


class ScientificSelfDeceptionDetector:
    """Deterministic analyzer ensuring scientific claims are statistically sound and free of cherry-picking."""

    SUPERIORITY_WORDS: ClassVar[set[str]] = {
        "outperforms",
        "outperform",
        "outperformed",
        "superior",
        "better",
        "improves",
        "improved",
        "improvement",
        "higher",
        "gain",
        "gained",
        "increase",
        "increased",
        "statistically significant",
        "significant",
        "exceeds",
        "beats",
    }

    CAUSAL_WORDS: ClassVar[set[str]] = {
        "causes",
        "caused",
        "causing",
        "due to",
        "because of",
        "results from",
        "attributable to",
    }

    def __init__(self, significance_threshold: float = 0.05) -> None:
        self.significance_threshold = significance_threshold

    def audit_claim(
        self,
        claim_statement: str,
        claim_metadata: dict[str, Any] | None = None,
        analyses: list[Any] | None = None,
        results: list[Any] | None = None,
        executions: list[Any] | None = None,
        experiment_spec: dict[str, Any] | None = None,
    ) -> SelfDeceptionAuditReport:
        """Audit a claim and its empirical lineage for statistical validity and cherry-picking."""
        findings: list[SelfDeceptionFinding] = []
        stmt_lower = claim_statement.lower()
        stmt_words = set(re.findall(r"\b[a-zA-Z_]+\b", stmt_lower))
        meta = claim_metadata or {}

        # 1. Statistical Significance Gating
        is_superiority = bool(stmt_words & self.SUPERIORITY_WORDS) or bool(
            meta.get("claim_type") in ("comparison", "superiority")
        )

        extracted_p_val: float | None = None
        has_stat_test = False

        # Look for p-value in analyses
        for an in analyses or []:
            out_json = dict(getattr(an, "output_json", {}) or {})
            for p_key in ("p_value", "p", "pvalue", "p_val", "significance_p"):
                if p_key in out_json and isinstance(out_json[p_key], (int, float)):
                    extracted_p_val = float(out_json[p_key])
                    has_stat_test = True
                    break
            if has_stat_test:
                break

        delta = self._extract_empirical_delta(analyses or [])

        # Check p-value against threshold with NaN and negative protection
        is_stat_sig = True
        if extracted_p_val is not None and (
            math.isnan(extracted_p_val)
            or extracted_p_val < 0.0
            or extracted_p_val >= self.significance_threshold
        ):
            is_stat_sig = False

        # VULN-M2-01: Decouple delta < 0 check from p-value gating.
        # Any superiority claim asserting positive gains when empirical data degraded
        # must fail closed to REJECTED and FALSIFIED regardless of p-value.
        if is_superiority and delta is not None and delta < 0:
            is_stat_sig = False
            p_desc = (
                f" (p={extracted_p_val:.4f})"
                if extracted_p_val is not None and not math.isnan(extracted_p_val)
                else ""
            )
            findings.append(
                SelfDeceptionFinding(
                    finding_type=SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE,
                    severity="critical",
                    description=(
                        f"Claim asserts superiority ('{claim_statement}'), but empirical data shows "
                        f"performance degraded (delta={delta}){p_desc}."
                    ),
                    recommended_claim_status=ClaimStatus.REJECTED,
                    recommended_hypothesis_status=HypothesisStatus.FALSIFIED,
                    context={"p_value": extracted_p_val, "delta": delta},
                )
            )
        elif extracted_p_val is not None and not is_stat_sig:
            if is_superiority or "statistically significant" in stmt_lower:
                p_str = f"p={extracted_p_val:.4f}" if not math.isnan(extracted_p_val) else "p=NaN"
                findings.append(
                    SelfDeceptionFinding(
                        finding_type=SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE,
                        severity="critical",
                        description=(
                            f"Claim asserts superiority ('{claim_statement}'), but empirical evidence "
                            f"fails significance gating ({p_str} >= {self.significance_threshold}). Result is inconclusive."
                        ),
                        recommended_claim_status=ClaimStatus.INCONCLUSIVE,
                        recommended_hypothesis_status=HypothesisStatus.INCONCLUSIVE,
                        context={"p_value": extracted_p_val, "delta": delta},
                    )
                )
        elif extracted_p_val is None and "statistically significant" in stmt_lower:
            # Claim claims significance with no statistical test performed
            findings.append(
                SelfDeceptionFinding(
                    finding_type=SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE,
                    severity="critical",
                    description=(
                        f"Claim states 'statistically significant' ('{claim_statement}'), "
                        "but no statistical hypothesis test or p-value was computed in supporting analyses."
                    ),
                    recommended_claim_status=ClaimStatus.INCONCLUSIVE,
                    recommended_hypothesis_status=HypothesisStatus.INCONCLUSIVE,
                    context={},
                )
            )

        # 2. Complete Seed Verification (Cherry-Picking Detection)
        executed_seeds: set[int] = set()
        for ex in executions or []:
            if hasattr(ex, "seed") and ex.seed is not None:
                executed_seeds.add(int(ex.seed))
            cfg = dict(getattr(ex, "configuration_json", {}) or {})
            if "seed" in cfg and isinstance(cfg["seed"], int):
                executed_seeds.add(cfg["seed"])
            elif "seeds" in cfg and isinstance(cfg["seeds"], list):
                executed_seeds.update(s for s in cfg["seeds"] if isinstance(s, int))

        # Check results seeds
        for res in results or []:
            r_json = dict(getattr(res, "result_json", {}) or {})
            if "seed" in r_json and isinstance(r_json["seed"], int):
                executed_seeds.add(r_json["seed"])

        aggregated_seeds: set[int] = set()
        for an in analyses or []:
            out_json = dict(getattr(an, "output_json", {}) or {})
            if "seeds" in out_json and isinstance(out_json["seeds"], list):
                aggregated_seeds.update(s for s in out_json["seeds"] if isinstance(s, int))
            elif "seed_list" in out_json and isinstance(out_json["seed_list"], list):
                aggregated_seeds.update(s for s in out_json["seed_list"] if isinstance(s, int))
            elif (
                "sample_size" in out_json
                and isinstance(out_json["sample_size"], int)
                and executed_seeds
                and out_json["sample_size"] < len(executed_seeds)
            ):
                # Potential seed filtering
                pass

        # Fallback: if analysis output_json omitted seeds, infer from input results
        if not aggregated_seeds and results:
            result_map = {r.id: r for r in results if hasattr(r, "id")}
            for an in analyses or []:
                input_ids = getattr(an, "input_result_ids", []) or []
                for rid in input_ids:
                    r = result_map.get(rid)
                    if r:
                        r_json = dict(getattr(r, "result_json", {}) or {})
                        if "seed" in r_json and isinstance(r_json["seed"], int):
                            aggregated_seeds.add(r_json["seed"])

        is_seed_complete = True
        if executed_seeds and aggregated_seeds:
            omitted_seeds = executed_seeds - aggregated_seeds
            if omitted_seeds:
                is_seed_complete = False
                findings.append(
                    SelfDeceptionFinding(
                        finding_type=SelfDeceptionType.CHERRY_PICKED_SEEDS,
                        severity="critical",
                        description=(
                            f"Cherry-picking detected: Experiment executed seeds {sorted(executed_seeds)}, "
                            f"but analysis only aggregated seeds {sorted(aggregated_seeds)} (omitted: {sorted(omitted_seeds)})."
                        ),
                        recommended_claim_status=ClaimStatus.REJECTED,
                        recommended_hypothesis_status=HypothesisStatus.INCONCLUSIVE,
                        context={
                            "executed_seeds": sorted(executed_seeds),
                            "aggregated_seeds": sorted(aggregated_seeds),
                            "omitted_seeds": sorted(omitted_seeds),
                        },
                    )
                )

        # 3. Metric Surrogacy & Generalization Check
        if any(
            term in stmt_lower for term in ("generalization", "test accuracy", "test performance")
        ):
            # Check if all results are from train set
            is_surrogate = False
            for res in results or []:
                r_json = dict(getattr(res, "result_json", {}) or {})
                split = str(r_json.get("split", "")).lower()
                m_name = str(getattr(res, "metric_name", "")).lower()
                if split in ("train", "training") or m_name.startswith("train_"):
                    is_surrogate = True
                elif split in ("test", "val", "validation") or m_name.startswith(("test_", "val_")):
                    is_surrogate = False
                    break
            if is_surrogate:
                findings.append(
                    SelfDeceptionFinding(
                        finding_type=SelfDeceptionType.METRIC_SURROGATE_MISMATCH,
                        severity="critical",
                        description=(
                            f"Metric surrogate mismatch: Claim asserts test generalization ('{claim_statement}'), "
                            "but empirical evidence is exclusively evaluated on training loss/metrics."
                        ),
                        recommended_claim_status=ClaimStatus.REJECTED,
                        recommended_hypothesis_status=HypothesisStatus.INCONCLUSIVE,
                        context={},
                    )
                )

        # 4. Confounded Causal Claim Check
        single_causal = {w for w in self.CAUSAL_WORDS if " " not in w}
        multi_causal = [w for w in self.CAUSAL_WORDS if " " in w]
        has_causal_claim = bool(stmt_words & single_causal) or any(
            re.search(r"\b" + re.escape(phrase) + r"\b", stmt_lower) for phrase in multi_causal
        )
        if has_causal_claim and experiment_spec:
            variables = dict(experiment_spec.get("variables", {}) or {})
            if len(variables) >= 2:
                has_ablation = (
                    "ablation" in str(experiment_spec.get("analysis_methods", [])).lower()
                )
                if not has_ablation:
                    findings.append(
                        SelfDeceptionFinding(
                            finding_type=SelfDeceptionType.CONFOUNDED_CAUSAL_CLAIM,
                            severity="critical",
                            description=(
                                f"Confounded causal claim: Claim asserts causal attribution ('{claim_statement}'), "
                                f"but experiment simultaneously modified {len(variables)} variables ({sorted(variables.keys())}) without ablation isolation."
                            ),
                            recommended_claim_status=ClaimStatus.INCONCLUSIVE,
                            recommended_hypothesis_status=HypothesisStatus.INCONCLUSIVE,
                            context={"variables": list(variables.keys())},
                        )
                    )

        is_grounded = len(findings) == 0
        critical_count = sum(1 for f in findings if f.severity == "critical")
        score = max(0.0, 1.0 - (critical_count * 0.4) - (len(findings) * 0.2))

        return SelfDeceptionAuditReport(
            is_grounded=is_grounded,
            findings=findings,
            p_value=extracted_p_val,
            is_statistically_significant=is_stat_sig,
            executed_seeds=sorted(executed_seeds),
            aggregated_seeds=sorted(aggregated_seeds),
            is_seed_complete=is_seed_complete,
            grounding_score=round(score, 3),
        )

    def _extract_empirical_delta(self, analyses: list[Any]) -> float | None:
        """Extract empirical performance delta from analysis outputs."""
        for an in analyses:
            out_json = dict(getattr(an, "output_json", {}) or {})
            for key in ("delta", "diff", "difference", "improvement", "gain"):
                if key in out_json and isinstance(out_json[key], (int, float)):
                    return float(out_json[key])
        return None
