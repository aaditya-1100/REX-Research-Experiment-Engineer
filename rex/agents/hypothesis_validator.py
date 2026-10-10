"""Hypothesis Quality, Falsifiability & Epistemic Validator (REX-014 / Track A Sec 6-7).

Validates proposed scientific hypotheses against rigorous epistemological criteria:
1. Specificity & measurable outcomes
2. Independent (IV) vs Dependent (DV) variable partitioning
3. Explicit baseline and control references
4. Falsifiability and quantitative refutation thresholds
5. Tautology, circular claim, and unfalsifiable subjective claim detection
6. Rival / competing hypothesis structure
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from rex.domain.models import ExpectedDirection, Hypothesis


class HypothesisValidationResult(BaseModel):
    """Structured evaluation report for hypothesis scientific quality."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(
        description="True if hypothesis satisfies all mandatory scientific invariants"
    )
    quality_score: float = Field(
        ge=0.0, le=1.0, description="Overall normalized quality score (0.0 to 1.0)"
    )
    falsifiability_score: float = Field(
        ge=0.0, le=1.0, description="Score evaluating concreteness of falsification condition"
    )
    specificity_score: float = Field(
        ge=0.0, le=1.0, description="Score evaluating specificity of variables and mechanisms"
    )
    tautology_detected: bool = Field(
        default=False, description="True if statement is definitional, trivial, or circular"
    )
    circularity_detected: bool = Field(
        default=False, description="True if statement rationale merely restates the claim"
    )
    has_baseline_reference: bool = Field(
        default=True, description="True if statement or proposal cites an explicit baseline"
    )
    has_iv_dv_partition: bool = Field(
        default=True,
        description="True if independent and dependent variables are disjoint and identified",
    )
    has_rival_hypothesis: bool = Field(
        default=False, description="True if a competing or rival hypothesis is articulated"
    )
    errors: list[str] = Field(default_factory=list, description="Hard failures blocking acceptance")
    warnings: list[str] = Field(
        default_factory=list, description="Quality warnings or improvement points"
    )


class HypothesisValidator:
    """Deterministic scientific validator evaluating hypothesis falsifiability and rigor."""

    # Patterns indicating definitional, trivial, or circular tautologies
    TAUTOLOGY_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(
            r"\b(trained|trained models?)\b.*\b(better than|outperform|exceed|better(?:\s+[a-z_]+)?\s+than|higher(?:\s+[a-z_]+)?\s+than)\b.*\b(untrained|random init|randomly initialized)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(changing|tuning|modifying)\b.*\b(hyperparameters?|parameters?)\b.*\b(changes|affects|alters|impacts)\b.*\b(performance|results?|loss|accuracy)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(different|various)\b.*\b(learning rates?|architectures?)\b.*\b(produce|produces|lead to|leads to|result in|results in|yield|yields)\b.*\b(different|distinct)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(better|superior|improved) models?\b.*\b(have|achieve|produce)\b.*\b(better|higher|superior)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(more parameters?|increasing\s+(?:[a-z_]+\s+)?(?:parameters?|capacity))\b.*\b(changes?|affects?)\b.*\b(capacity|parameter count)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bif\b.*\b(?:[a-z_]+\s+)?loss decreases\b.*\bthen\b.*(?:\bthe model has lower loss\b|\bloss is lower\b|\bmodel is better\b)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(more data|larger datasets?)\b.*\b(contains?|has?)\b.*\b(more samples?|more data)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bmodels?\s+with\s+(?:lower|higher)\s+(?:[a-z_]+\s+)?loss\b.*\bhave\s+(?:lower|higher)\s+loss\b",
            re.IGNORECASE,
        ),
    ]

    # Subjective, vague, or unfalsifiable phrasing
    SUBJECTIVE_UNFALSIFIABLE_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(
            r"\b(feels?|intuitively|natural|aesthetically|elegant|harmonious)\b", re.IGNORECASE
        ),
        re.compile(
            r"\b(true understanding|genuine intelligence|holistic synergy)\b", re.IGNORECASE
        ),
        re.compile(
            r"\b(cannot be falsified|impossible to refute|always holds|unquestionable)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(might or might not|could potentially possibly)\b", re.IGNORECASE),
    ]

    # Common quantitative tokens / metric keywords
    QUANTITATIVE_INDICATORS: ClassVar[list[str]] = [
        "%",
        ">",
        "<",
        ">=",
        "<=",
        "=",
        "pp",
        "percentage points",
        "margin",
        "threshold",
        "p <",
        "p-value",
        "accuracy",
        "loss",
        "f1",
        "latency",
        "perplexity",
        "bleu",
        "rouge",
        "error rate",
        "runtime",
        "flops",
        "mse",
        "mae",
    ]

    # Baseline indicators
    BASELINE_INDICATORS: ClassVar[list[str]] = [
        "baseline",
        "standard",
        "control",
        "vanilla",
        "default",
        "resnet",
        "transformer",
        "sota",
        "existing method",
        "prior work",
        "unaugmented",
        "unregularized",
    ]

    def validate(
        self,
        statement: str,
        falsification_condition: str,
        rationale: str = "",
        expected_direction: ExpectedDirection | str = ExpectedDirection.INCREASE,
        independent_variables: list[str] | None = None,
        dependent_variables: list[str] | None = None,
        baseline_reference: str = "",
        competing_hypothesis: str | None = None,
    ) -> HypothesisValidationResult:
        """Evaluate a scientific hypothesis proposal against all epistemic criteria."""
        errors: list[str] = []
        warnings: list[str] = []

        stmt = statement.strip()
        fals_cond = falsification_condition.strip()
        rat = rationale.strip()
        ivs = [v.strip().lower() for v in (independent_variables or []) if v.strip()]
        dvs = [v.strip().lower() for v in (dependent_variables or []) if v.strip()]
        baseline = baseline_reference.strip()

        # 1. Statement presence & minimum length
        if not stmt:
            errors.append("Hypothesis statement cannot be empty.")
            return HypothesisValidationResult(
                is_valid=False,
                quality_score=0.0,
                falsifiability_score=0.0,
                specificity_score=0.0,
                errors=errors,
            )

        if len(stmt) < 15:
            errors.append(
                f"Hypothesis statement '{stmt}' is too brief and lacks scientific specificity."
            )

        # 2. Tautology detection
        tautology_detected = self.detect_tautology(stmt)
        if tautology_detected:
            errors.append(
                f"TAUTOLOGY_DETECTED: Statement '{stmt}' is purely definitional, trivial, or circular."
            )

        # 3. Subjective / Unfalsifiable language in statement
        for pat in self.SUBJECTIVE_UNFALSIFIABLE_PATTERNS:
            if pat.search(stmt):
                errors.append(
                    f"UNFALSIFIABLE_CLAIM: Statement contains subjective or unfalsifiable phrasing matching '{pat.pattern}'."
                )
                break

        # 4. Circularity detection between statement and rationale
        circularity_detected = self.detect_circularity(stmt, rat)
        if circularity_detected:
            warnings.append(
                "CIRCULAR_RATIONALE: Rationale merely restates the hypothesis without providing a causal mechanism."
            )

        # 5. Falsification condition evaluation
        falsifiability_score, fals_errors = self.evaluate_falsifiability(fals_cond)
        errors.extend(fals_errors)

        # 6. IV / DV Partitioning
        has_iv_dv = True
        if independent_variables is not None or dependent_variables is not None:
            if not ivs:
                errors.append(
                    "MISSING_INDEPENDENT_VARIABLE: No independent experimental variable was declared."
                )
                has_iv_dv = False
            if not dvs:
                errors.append(
                    "MISSING_DEPENDENT_VARIABLE: No dependent measurable outcome variable was declared."
                )
                has_iv_dv = False

            # Check overlap
            overlap = set(ivs) & set(dvs)
            if overlap:
                errors.append(
                    f"CONFOUNDED_VARIABLES: Variables {sorted(overlap)} cannot be both independent and dependent variables."
                )
                has_iv_dv = False
        else:
            # Heuristic check on statement
            extracted_ivs, extracted_dvs = self._extract_variables(stmt)
            if not extracted_ivs or not extracted_dvs:
                warnings.append(
                    "Explicit independent/dependent variable decomposition was not provided."
                )

        # 7. Baseline reference check
        has_baseline = bool(baseline) or any(b in stmt.lower() for b in self.BASELINE_INDICATORS)
        if not has_baseline:
            warnings.append(
                "MISSING_BASELINE_REFERENCE: Hypothesis does not clearly reference a baseline or control condition."
            )

        # 8. Rival / Competing hypothesis check
        has_rival = bool(competing_hypothesis and competing_hypothesis.strip())
        if not has_rival:
            warnings.append("No competing/rival alternative hypothesis (HA vs HB) was specified.")

        # 9. Specificity scoring
        specificity_score = self.calculate_specificity(stmt, ivs, dvs, baseline)

        # 10. Overall quality calculation
        # Deduct for tautology, circularity, lack of baseline, low falsifiability
        quality = 0.3 * falsifiability_score + 0.3 * specificity_score
        if has_baseline:
            quality += 0.15
        if has_iv_dv:
            quality += 0.15
        if has_rival:
            quality += 0.10

        if tautology_detected:
            quality = min(quality, 0.2)
        if errors:
            quality = min(quality, 0.4)

        is_valid = len(errors) == 0 and not tautology_detected and falsifiability_score >= 0.5

        return HypothesisValidationResult(
            is_valid=is_valid,
            quality_score=round(min(1.0, max(0.0, quality)), 3),
            falsifiability_score=round(falsifiability_score, 3),
            specificity_score=round(specificity_score, 3),
            tautology_detected=tautology_detected,
            circularity_detected=circularity_detected,
            has_baseline_reference=has_baseline,
            has_iv_dv_partition=has_iv_dv,
            has_rival_hypothesis=has_rival,
            errors=errors,
            warnings=warnings,
        )

    def detect_tautology(self, statement: str) -> bool:
        """Check if statement matches known tautological forms or trivial circularities."""
        clean = statement.strip()
        for pat in self.TAUTOLOGY_PATTERNS:
            if pat.search(clean):
                return True

        # Check trivial identity: "A improves A" or identical subject/predicate
        lower = clean.lower()
        if re.search(r"\b([a-z_]+)\s+improves\s+\1\b", lower):
            return True
        return bool(re.search(r"\b([a-z_]+)\s+leads to\s+\1\b", lower))

    def detect_circularity(self, statement: str, rationale: str) -> bool:
        """Check if rationale is simply a rephrasing of the statement rather than a causal explanation."""
        if not rationale or not statement:
            return False

        stmt_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", statement.lower()))
        rat_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", rationale.lower()))

        if not stmt_words or not rat_words:
            return False

        # If rationale is very short and words heavily overlap without causal markers
        overlap = stmt_words & rat_words
        jaccard = len(overlap) / len(stmt_words | rat_words)

        new_explanation_words = rat_words - stmt_words
        causal_markers = {
            "mechanism",
            "gradient",
            "regularization",
            "variance",
            "entropy",
            "capacity",
            "convergence",
            "attention",
            "representation",
            "bias",
            "curvature",
            "eigenvalue",
            "stochasticity",
        }
        has_causal_mechanism = bool(new_explanation_words & causal_markers)

        return bool(jaccard > 0.60 and not has_causal_mechanism)

    def evaluate_falsifiability(self, falsification_condition: str) -> tuple[float, list[str]]:
        """Evaluate whether falsification condition is quantitative, testable, and operationalized."""
        errors: list[str] = []
        cond = falsification_condition.strip()

        if not cond:
            errors.append("FALSIFICATION_CONDITION_MISSING: Falsification condition is empty.")
            return 0.0, errors

        for pat in self.SUBJECTIVE_UNFALSIFIABLE_PATTERNS:
            if pat.search(cond):
                errors.append(
                    f"UNFALSIFIABLE_CONDITION: Condition '{cond}' contains subjective or unfalsifiable phrasing."
                )
                return 0.1, errors

        score = 0.3
        # Check quantitative thresholds / numbers
        has_numbers = bool(re.search(r"\b\d+(?:\.\d+)?\b", cond))
        if has_numbers:
            score += 0.35

        # Check quantitative indicators
        cond_lower = cond.lower()
        has_quant_tokens = any(ind in cond_lower for ind in self.QUANTITATIVE_INDICATORS)
        if has_quant_tokens:
            score += 0.25

        # Check directional clarity
        if any(
            d in cond_lower
            for d in (
                "less than",
                "greater than",
                "fails to exceed",
                "does not improve",
                "exceeds",
                "<",
                ">",
                "<=",
            )
        ):
            score += 0.1

        if not has_numbers and not has_quant_tokens:
            errors.append(
                f"NON_QUANTITATIVE_FALSIFICATION: Condition '{cond}' lacks quantitative metrics, numbers, or explicit thresholds."
            )

        return min(1.0, score), errors

    def calculate_specificity(
        self, statement: str, ivs: list[str], dvs: list[str], baseline: str
    ) -> float:
        """Calculate specificity score based on variable granularity and concrete targets."""
        score = 0.2
        if len(statement) > 40:
            score += 0.2
        if ivs:
            score += 0.2
        if dvs:
            score += 0.2
        if baseline:
            score += 0.1
        if any(tok in statement.lower() for tok in self.QUANTITATIVE_INDICATORS):
            score += 0.1
        return min(1.0, score)

    def _extract_variables(self, statement: str) -> tuple[list[str], list[str]]:
        """Heuristic variable extractor from statement text."""
        lower = statement.lower()
        ivs: list[str] = []
        dvs: list[str] = []

        candidate_dvs = [
            "accuracy",
            "loss",
            "precision",
            "recall",
            "f1",
            "latency",
            "throughput",
            "perplexity",
            "convergence",
            "robustness",
            "generalization",
        ]
        for cdv in candidate_dvs:
            if cdv in lower:
                dvs.append(cdv)

        candidate_ivs = [
            "learning rate",
            "batch size",
            "weight decay",
            "dropout",
            "attention",
            "layers",
            "data augmentation",
            "normalization",
            "optimizer",
            "pruning",
            "quantization",
        ]
        for civ in candidate_ivs:
            if civ in lower:
                ivs.append(civ)

        return ivs, dvs

    def validate_domain_hypothesis(self, hypothesis: Hypothesis) -> HypothesisValidationResult:
        """Convenience validation for an existing domain Hypothesis model."""
        return self.validate(
            statement=hypothesis.statement,
            falsification_condition=hypothesis.falsification_condition,
            rationale=hypothesis.rationale,
            expected_direction=hypothesis.expected_direction,
        )
