"""REX Research Verifier Engine (REX-026).

Implements the formal 12-step verification protocol for research runs, enforcing
mechanical traceability from scientific claims down to raw empirical data, cryptographic
artifact byte hashes, and deterministic statistical recomputations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rex.analysis.statistics import StatisticalAnalyzer
from rex.domain.models import ClaimStatus
from rex.evidence.graph import EvidenceGraphError, EvidenceGraphService
from rex.evidence.hashing import HashVerificationResult, verify_artifact_hash
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import EventRepository


class VerificationStatus(StrEnum):
    """Aggregate outcome status of the verification protocol."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"

    # Backwards-compatible aliases
    VERIFIED = PASS
    FAILED = FAIL


@dataclass(frozen=True)
class ClaimVerificationResult:
    """Verification outcome for an individual scientific claim."""

    claim_id: str
    statement: str
    status: str
    is_lineage_intact: bool
    empirical_nodes: list[str]
    gaps: list[str]
    is_valid: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "status": self.status,
            "is_lineage_intact": self.is_lineage_intact,
            "empirical_nodes": self.empirical_nodes,
            "gaps": self.gaps,
            "is_valid": self.is_valid,
        }


@dataclass(frozen=True)
class AnalysisRecomputationResult:
    """Outcome of recomputing a statistical analysis from source Results."""

    analysis_id: str
    method: str
    is_deterministic: bool
    max_difference: float
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "method": self.method,
            "is_deterministic": self.is_deterministic,
            "max_difference": self.max_difference,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class VerificationReport:
    """Immutable comprehensive verification report for a research run."""

    research_run_id: str
    status: VerificationStatus
    claims_verified: list[ClaimVerificationResult]
    artifacts_verified: list[HashVerificationResult]
    analyses_recomputed: list[AnalysisRecomputationResult]
    cross_run_violations: list[str]
    errors: list[str]
    warnings: list[str]
    started_at: datetime
    completed_at: datetime

    @property
    def is_passed(self) -> bool:
        return self.status == VerificationStatus.PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_run_id": self.research_run_id,
            "status": self.status.value,
            "claims_verified": [c.as_dict() for c in self.claims_verified],
            "artifacts_verified": [a.as_dict() for a in self.artifacts_verified],
            "analyses_recomputed": [a.as_dict() for a in self.analyses_recomputed],
            "cross_run_violations": self.cross_run_violations,
            "errors": self.errors,
            "warnings": self.warnings,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


class ResearchVerifier:
    """Verifier engine that executes the formal verification protocol.

    Read-only with respect to historical evidence. Never repairs hashes or
    mutates results.
    """

    def __init__(
        self,
        session: Session,
        event_sink: EventSink | None = None,
        artifact_root: Path | str | None = None,
        tolerance: float = 1e-6,
    ) -> None:
        self.session = session
        self.event_sink = event_sink if event_sink is not None else EventRepository(session)
        self.graph = EvidenceGraphService(session)
        self.artifact_root = Path(artifact_root) if artifact_root else None
        self.tolerance = tolerance

    def verify_run(
        self,
        research_run_id: str,
        actor: ActorType | str = ActorType.VERIFIER,
    ) -> VerificationReport:
        """Execute the 12-step verification protocol for a research run."""
        started_at = datetime.now(UTC)
        actor_enum = (
            actor if isinstance(actor, ActorType) else ActorType(str(actor).strip().lower())
        )

        errors: list[str] = []
        warnings: list[str] = []
        cross_run_violations: list[str] = []
        claims_checked: list[ClaimVerificationResult] = []
        artifacts_checked: list[HashVerificationResult] = []
        analyses_checked: list[AnalysisRecomputationResult] = []

        # Step 1: Verify research run exists before emitting run-scoped events
        run = self.session.get(ResearchRunModel, research_run_id)
        if run is None:
            errors.append(f"Research run '{research_run_id}' not found.")
            completed_at = datetime.now(UTC)
            return VerificationReport(
                research_run_id=research_run_id,
                status=VerificationStatus.FAIL,
                claims_verified=[],
                artifacts_verified=[],
                analyses_recomputed=[],
                cross_run_violations=[],
                errors=errors,
                warnings=[],
                started_at=started_at,
                completed_at=completed_at,
            )

        # Step 2: Emit VERIFICATION_STARTED / RUN_VERIFICATION_STARTED event
        start_payload = {"verifier": "ResearchVerifier", "started_at": started_at.isoformat()}
        self.event_sink.emit(
            create_event(
                event_type=EventType.RUN_VERIFICATION_STARTED,
                research_run_id=research_run_id,
                actor=actor_enum,
                payload=start_payload,
            )
        )
        self.event_sink.emit(
            create_event(
                event_type=EventType.VERIFICATION_STARTED,
                research_run_id=research_run_id,
                actor=actor_enum,
                payload=start_payload,
            )
        )

        # Step 3: Check cross-run evidence links
        all_links = (
            self.session.execute(
                select(EvidenceLinkModel).where(
                    (EvidenceLinkModel.research_run_id == research_run_id)
                    | (
                        EvidenceLinkModel.claim_id.in_(
                            select(ClaimModel.id).where(
                                ClaimModel.research_run_id == research_run_id
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        for link in all_links:
            # Check source and target scoping
            try:
                _, src_run = self.graph.resolve_node(link.source_type, link.source_id)
                _, tgt_run = self.graph.resolve_node(link.target_type, link.target_id)
                if src_run and src_run != research_run_id:
                    v = f"Cross-run evidence: link '{link.id}' source '{link.source_id}' is from run '{src_run}'."
                    cross_run_violations.append(v)
                    errors.append(v)
                if tgt_run and tgt_run != research_run_id:
                    v = f"Cross-run evidence: link '{link.id}' target '{link.target_id}' is from run '{tgt_run}'."
                    cross_run_violations.append(v)
                    errors.append(v)
            except (EvidenceGraphError, ValueError, KeyError) as e:
                errors.append(f"Broken link '{link.id}': {e}")

        # Step 4: Verify claims and empirical lineage
        claims = (
            self.session.execute(
                select(ClaimModel).where(ClaimModel.research_run_id == research_run_id)
            )
            .scalars()
            .all()
        )

        if not claims:
            warnings.append(f"No scientific claims found in research run '{research_run_id}'.")

        for claim in claims:
            lineage = self.graph.trace_claim_lineage(claim.id)
            emp_nodes = [r.id for r in lineage.results] + [a.id for a in lineage.analyses]

            is_valid = True
            # In a formal verification run, every claim must have intact empirical lineage
            if not lineage.is_complete:
                is_valid = False
                msg = (
                    f"UNSUPPORTED_CLAIM: Claim '{claim.id}' is marked '{claim.status}' but "
                    f"lineage is broken or lacks supporting evidence: {'; '.join(lineage.gaps)}"
                )
                errors.append(msg)
            else:
                # Check numerical consistency between claim assertion and supporting evidence
                num_err = self._check_claim_numerical_consistency(claim, lineage)
                if num_err:
                    is_valid = False
                    errors.append(num_err)

            if is_valid and lineage.is_complete:
                claim.status = ClaimStatus.VERIFIED.value
                self.session.flush()

                # Emit CLAIM_VERIFIED event
                claim_event = create_event(
                    event_type=EventType.CLAIM_VERIFIED,
                    research_run_id=research_run_id,
                    actor=actor_enum,
                    payload={
                        "claim_id": claim.id,
                        "statement": claim.statement,
                        "empirical_nodes": emp_nodes,
                    },
                )
                self.event_sink.emit(claim_event)
            elif not is_valid:
                if claim.status == ClaimStatus.VERIFIED.value:
                    claim.status = ClaimStatus.TAMPERED.value
                    self.session.flush()

            claims_checked.append(
                ClaimVerificationResult(
                    claim_id=claim.id,
                    statement=claim.statement,
                    status=claim.status,
                    is_lineage_intact=lineage.is_complete,
                    empirical_nodes=emp_nodes,
                    gaps=lineage.gaps,
                    is_valid=is_valid,
                )
            )

        # Step 5 & 6: Verify artifact file existence and raw byte SHA-256 hashes
        artifacts = (
            self.session.execute(
                select(ArtifactModel).where(ArtifactModel.research_run_id == research_run_id)
            )
            .scalars()
            .all()
        )

        for art in artifacts:
            res = verify_artifact_hash(art, root_dir=self.artifact_root)
            artifacts_checked.append(res)
            if not res.file_exists:
                errors.append(f"Artifact file missing on disk: {art.path} (ID: {art.id})")
            elif not res.is_valid:
                errors.append(
                    f"Cryptographic hash mismatch for artifact {art.id} ({art.path}): "
                    f"expected {res.expected_hash}, computed {res.computed_hash}"
                )

        # Step 7 & 8: Recompute statistical analyses deterministically
        analyses = (
            self.session.execute(
                select(AnalysisModel).where(AnalysisModel.research_run_id == research_run_id)
            )
            .scalars()
            .all()
        )

        for an in analyses:
            recomp_res = self._verify_analysis_deterministic(an)
            analyses_checked.append(recomp_res)
            if not recomp_res.is_deterministic:
                errors.append(
                    f"Analysis '{an.id}' ({an.method}) non-deterministic or tampered: "
                    f"{recomp_res.error_message}"
                )

        # Step 9: Check execution validity for all executions in run
        experiments = (
            self.session.execute(
                select(ExperimentModel).where(ExperimentModel.research_run_id == research_run_id)
            )
            .scalars()
            .all()
        )

        for exp in experiments:
            for exec_model in exp.executions:
                if exec_model.status not in ("completed", "success"):
                    warnings.append(
                        f"Execution '{exec_model.id}' under experiment '{exp.id}' "
                        f"has non-terminal/failed status '{exec_model.status}'."
                    )

        # Step 10 & 11: Determine overall verification outcome
        if errors:
            status = VerificationStatus.FAIL
        elif warnings:
            status = VerificationStatus.WARNING
        else:
            status = VerificationStatus.PASS

        completed_at = datetime.now(UTC)
        report = VerificationReport(
            research_run_id=research_run_id,
            status=status,
            claims_verified=claims_checked,
            artifacts_verified=artifacts_checked,
            analyses_recomputed=analyses_checked,
            cross_run_violations=cross_run_violations,
            errors=errors,
            warnings=warnings,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Step 12: Emit completion event
        self._emit_completion_event(report, actor_enum)

        return report

    # Backwards-compatible alias for verification
    verify = verify_run

    def _verify_analysis_deterministic(
        self,
        analysis: AnalysisModel,
    ) -> AnalysisRecomputationResult:
        """Deterministically recompute a statistical analysis and compare to stored outputs."""
        method = analysis.method or analysis.analysis_type
        input_ids = list(analysis.input_result_ids or [])
        output_json = dict(analysis.output_json or {})

        if not input_ids:
            return AnalysisRecomputationResult(
                analysis_id=analysis.id,
                method=method,
                is_deterministic=False,
                max_difference=float("inf"),
                error_message="Analysis has no input_result_ids recorded.",
            )

        # Fetch inputs
        stmt = select(ResultModel).where(ResultModel.id.in_(input_ids))
        results = self.session.execute(stmt).scalars().all()
        if len(results) != len(input_ids):
            missing = set(input_ids) - {r.id for r in results}
            return AnalysisRecomputationResult(
                analysis_id=analysis.id,
                method=method,
                is_deterministic=False,
                max_difference=float("inf"),
                error_message=f"Missing input Results: {missing}",
            )

        # Deterministic recomputation based on method
        max_diff = 0.0
        analyzer = StatisticalAnalyzer()

        try:
            if method in ("sample_summary_statistics", "summary_statistics", "descriptive"):
                recomputed = analyzer.compute_summary(results)
                # Compare critical numeric properties
                for key in ("mean", "median", "variance", "std_dev"):
                    exp_val = output_json.get(key)
                    rec_val = getattr(recomputed, key)
                    if exp_val is not None and rec_val is not None:
                        diff = abs(float(exp_val) - float(rec_val))
                        max_diff = max(max_diff, diff)
                        tol_bound = max(
                            self.tolerance * max(abs(float(exp_val)), abs(float(rec_val))),
                            self.tolerance,
                        )
                        if diff > tol_bound:
                            return AnalysisRecomputationResult(
                                analysis_id=analysis.id,
                                method=method,
                                is_deterministic=False,
                                max_difference=diff,
                                error_message=(
                                    f"Property '{key}' mismatch: expected {exp_val}, "
                                    f"recomputed {rec_val} (diff: {diff:.6e} > tol {self.tolerance})"
                                ),
                            )

            elif method in ("welch_t_test_comparison", "two_sample_comparison", "comparison"):
                # Separate into baseline and treatment
                params = output_json.get("parameters") or {}
                baseline_ids = set(params.get("baseline_result_ids", []))
                treatment_ids = set(params.get("treatment_result_ids", []))

                baseline_results = [r for r in results if r.id in baseline_ids]
                treatment_results = [r for r in results if r.id in treatment_ids]

                if baseline_results and treatment_results:
                    recomputed_cmp = analyzer.compare_groups(
                        baseline_results=baseline_results,
                        treatment_results=treatment_results,
                    )
                    for key in ("absolute_difference", "t_statistic", "p_value"):
                        exp_val = output_json.get(key)
                        rec_val = getattr(recomputed_cmp, key)
                        if exp_val is not None and rec_val is not None:
                            diff = abs(float(exp_val) - float(rec_val))
                            max_diff = max(max_diff, diff)
                            tol_bound = max(
                                self.tolerance * max(abs(float(exp_val)), abs(float(rec_val))),
                                self.tolerance,
                            )
                            if diff > tol_bound:
                                return AnalysisRecomputationResult(
                                    analysis_id=analysis.id,
                                    method=method,
                                    is_deterministic=False,
                                    max_difference=diff,
                                    error_message=(
                                        f"Comparison '{key}' mismatch: expected {exp_val}, "
                                        f"recomputed {rec_val} (diff: {diff:.6e} > tol {self.tolerance})"
                                    ),
                                )

            elif method in ("model_evaluation", "evaluation"):
                for r in results:
                    if (
                        r.metric_name
                        and r.metric_value is not None
                        and r.metric_name in output_json
                        and isinstance(output_json[r.metric_name], (int, float))
                    ):
                        exp_val = output_json[r.metric_name]
                        rec_val = r.metric_value
                        diff = abs(float(exp_val) - float(rec_val))
                        max_diff = max(max_diff, diff)
                        tol_bound = max(
                            self.tolerance * max(abs(float(exp_val)), abs(float(rec_val))),
                            self.tolerance,
                        )
                        if diff > tol_bound:
                            return AnalysisRecomputationResult(
                                analysis_id=analysis.id,
                                method=method,
                                is_deterministic=False,
                                max_difference=diff,
                                error_message=(
                                    f"Result '{r.id}' metric '{r.metric_name}' value {rec_val} "
                                    f"contradicts analysis output {exp_val} (diff: {diff:.6e} > tol {self.tolerance})"
                                ),
                            )
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as exc:
            return AnalysisRecomputationResult(
                analysis_id=analysis.id,
                method=method,
                is_deterministic=False,
                max_difference=float("inf"),
                error_message=f"Recomputation failed with error: {exc}",
            )

        return AnalysisRecomputationResult(
            analysis_id=analysis.id,
            method=method,
            is_deterministic=True,
            max_difference=max_diff,
        )

    def _check_claim_numerical_consistency(
        self,
        claim: ClaimModel,
        lineage: Any,
    ) -> str | None:
        """Check whether numerical assertions in a claim match evidence outputs within tolerance."""
        import re

        meta = dict(claim.metadata_json or {})
        asserted_vals: list[float] = []

        # 1. Structured metadata values
        metric_name = meta.get("metric_name")
        asserted_metric_val: float | None = None
        for key in ("asserted_value", "metric_value", "expected_value", "value"):
            if key in meta and meta[key] is not None:
                try:
                    val = float(meta[key])
                    asserted_vals.append(val)
                    if asserted_metric_val is None:
                        asserted_metric_val = val
                except (ValueError, TypeError):
                    pass

        # 2. Extract numerical values and percentages from natural language statement
        raw_matches = re.findall(r"([+-]?\b\d+(?:\.\d+)?)\s*(%?)", claim.statement)
        for num_str, is_pct in raw_matches:
            try:
                val = float(num_str)
                asserted_vals.append(val)
                if is_pct:
                    asserted_vals.append(val / 100.0)
            except (ValueError, TypeError):
                pass

        stmt_words = set(re.findall(r"\b[a-zA-Z_]+\b", claim.statement.lower()))
        comparative_words = {
            "improved",
            "improve",
            "improvement",
            "increased",
            "increase",
            "increasing",
            "higher",
            "highest",
            "greater",
            "decreased",
            "decrease",
            "decreasing",
            "lower",
            "lowest",
            "reduced",
            "reduce",
            "reduction",
            "outperformed",
            "outperform",
            "better",
            "worse",
            "drop",
            "dropped",
            "less",
            "superior",
        }
        if stmt_words & comparative_words and not asserted_vals:
            return (
                f"CLAIM_NUMBER_MISMATCH: Claim '{claim.id}' asserts comparative/quantitative statements "
                f"('{claim.statement}') without specifying empirical metrics or numbers."
            )

        if not asserted_vals:
            return None

        # Validate that claimed metrics match empirical metric names
        metric_keywords = {
            "accuracy",
            "error",
            "loss",
            "precision",
            "recall",
            "f1",
            "auc",
            "roc_auc",
            "mse",
            "mae",
            "rmse",
            "latency",
            "runtime",
            "perplexity",
            "r2",
            "cost",
            "score",
        }
        claimed_metrics = stmt_words & metric_keywords
        if metric_name:
            claimed_metrics.add(str(metric_name).strip().lower())

        empirical_metric_names = {
            res.metric_name.strip().lower() for res in lineage.results if res.metric_name
        }
        for an in lineage.analyses:
            out_json = dict(an.output_json or {})
            if out_json.get("metric_name"):
                empirical_metric_names.add(str(out_json["metric_name"]).strip().lower())
            for k in out_json:
                if k.lower() in metric_keywords:
                    empirical_metric_names.add(k.lower())

        if (
            claimed_metrics
            and empirical_metric_names
            and not (claimed_metrics & empirical_metric_names)
        ):
            return (
                f"CLAIM_METRIC_MISMATCH: Claim '{claim.id}' asserts metrics {sorted(claimed_metrics)}, "
                f"but supporting evidence only provides metrics: {sorted(empirical_metric_names)}."
            )

        # Directional semantic checking
        decrease_words = {
            "decreased",
            "decrease",
            "decreasing",
            "lower",
            "lowest",
            "reduced",
            "reduce",
            "reduction",
            "drop",
            "dropped",
            "less",
        }
        increase_words = {
            "improved",
            "increase",
            "increased",
            "increasing",
            "higher",
            "highest",
            "greater",
            "gain",
            "gained",
            "outperformed",
            "outperform",
        }

        # Check empirical deltas from analyses
        for an in lineage.analyses:
            out_json = dict(an.output_json or {})
            for delta_key in (
                "delta",
                "diff",
                "difference",
                "relative_difference",
                "improvement",
                "gain",
            ):
                if delta_key in out_json and isinstance(out_json[delta_key], (int, float)):
                    delta = float(out_json[delta_key])
                    if stmt_words & decrease_words and delta > self.tolerance:
                        return (
                            f"CLAIM_DIRECTION_MISMATCH: Claim '{claim.id}' asserts decrease/reduction ('{claim.statement}'), "
                            f"but empirical evidence shows an increase (delta={delta} > {self.tolerance})."
                        )
                    if stmt_words & increase_words and delta < -self.tolerance:
                        return (
                            f"CLAIM_DIRECTION_MISMATCH: Claim '{claim.id}' asserts increase/improvement ('{claim.statement}'), "
                            f"but empirical evidence shows a decrease (delta={delta} < -{self.tolerance})."
                        )

        # 3. Check structured metric assertions against linked results
        if metric_name and asserted_metric_val is not None:
            norm_name = str(metric_name).strip().lower()
            for res in lineage.results:
                if (
                    res.metric_name
                    and res.metric_name.strip().lower() == norm_name
                    and res.metric_value is not None
                ):
                    val = float(res.metric_value)
                    tol_bound = max(
                        self.tolerance * max(abs(asserted_metric_val), abs(val)), self.tolerance
                    )
                    if abs(asserted_metric_val - val) > tol_bound:
                        return (
                            f"CLAIM_NUMBER_MISMATCH: Claim '{claim.id}' asserts metric '{metric_name}' of {asserted_metric_val}, "
                            f"but linked result '{res.id}' has metric_value {val} (diff: {abs(asserted_metric_val - val):.6e} > tol {self.tolerance})."
                        )

        # 4. Check internal consistency between results and analyses supporting this claim
        for res in lineage.results:
            if res.metric_name and res.metric_value is not None:
                res_val = float(res.metric_value)
                for an in lineage.analyses:
                    out_json = dict(an.output_json or {})
                    if res.metric_name in out_json and isinstance(
                        out_json[res.metric_name], (int, float)
                    ):
                        an_val = float(out_json[res.metric_name])
                        diff = abs(res_val - an_val)
                        tol = max(self.tolerance * max(abs(res_val), abs(an_val)), self.tolerance)
                        if diff > tol:
                            return (
                                f"CLAIM_NUMBER_MISMATCH: Inconsistent evidence for metric '{res.metric_name}': "
                                f"linked result '{res.id}' has {res_val} while analysis '{an.id}' reports {an_val} "
                                f"(diff: {diff:.6e} > tol {self.tolerance})."
                            )

        # 5. Check if statement explicitly mentions a linked result's metric name
        stmt_lower = claim.statement.lower()
        metric_names = {
            res.metric_name.strip().lower() for res in lineage.results if res.metric_name
        }
        for m_name in metric_names:
            pat = rf"\b{re.escape(m_name)}\b(?:\s+is|\s+of|[:=])?\s*([+-]?\d+(?:\.\d+)?)"
            m = re.search(pat, stmt_lower)
            if m:
                try:
                    stated_val = float(m.group(1))
                    matches_result = any(
                        res.metric_name
                        and res.metric_name.strip().lower() == m_name
                        and res.metric_value is not None
                        and abs(float(res.metric_value) - stated_val)
                        <= max(
                            self.tolerance * max(abs(float(res.metric_value)), abs(stated_val)),
                            self.tolerance,
                        )
                        for res in lineage.results
                    )
                    matches_analysis = any(
                        isinstance(v, (int, float))
                        and not isinstance(v, bool)
                        and abs(float(v) - stated_val)
                        <= max(
                            self.tolerance * max(abs(float(v)), abs(stated_val)),
                            self.tolerance,
                        )
                        for an in lineage.analyses
                        for v in dict(an.output_json or {}).values()
                    )
                    if not matches_result and not matches_analysis:
                        return (
                            f"CLAIM_NUMBER_MISMATCH: Claim '{claim.id}' states {m_name}={stated_val}, "
                            f"but no linked result or analysis provides this value within tolerance."
                        )
                except (ValueError, TypeError):
                    pass

        # 6. Collect empirical evidence numbers from analyses and results
        evidence_numbers: list[float] = []
        for an in lineage.analyses:
            out_json = dict(an.output_json or {})
            for v in out_json.values():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    evidence_numbers.append(float(v))

        for res in lineage.results:
            if res.metric_value is not None:
                evidence_numbers.append(float(res.metric_value))
            res_json = dict(res.result_json or {})
            for v in res_json.values():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    evidence_numbers.append(float(v))

        if not evidence_numbers:
            return (
                f"CLAIM_NUMBER_MISMATCH: Claim '{claim.id}' asserts numerical values {asserted_vals}, "
                "but supporting evidence contains no numeric outputs."
            )

        # Check if at least one candidate matches supporting evidence within tolerance
        has_match = False
        for a_val in asserted_vals:
            for e_val in evidence_numbers:
                tol_bound = max(self.tolerance * max(abs(a_val), abs(e_val)), self.tolerance)
                if abs(a_val - e_val) <= tol_bound:
                    has_match = True
                    break
            if has_match:
                break

        if not has_match:
            return (
                f"CLAIM_NUMBER_MISMATCH: Claim '{claim.id}' asserts numerical value {asserted_vals}, "
                f"which does not match any supporting evidence value {evidence_numbers[:5]} "
                f"(tolerance: {self.tolerance})."
            )

        return None

    def _emit_completion_event(
        self,
        report: VerificationReport,
        actor: ActorType,
    ) -> None:
        """Emit either VERIFICATION_COMPLETED / RUN_VERIFIED or VERIFICATION_FAILED events."""
        payload = {
            "status": report.status.value,
            "claims_count": len(report.claims_verified),
            "artifacts_count": len(report.artifacts_verified),
            "analyses_count": len(report.analyses_recomputed),
            "errors_count": len(report.errors),
            "warnings_count": len(report.warnings),
            "errors": report.errors[:10],
            "warnings": report.warnings[:10],
        }

        if report.is_passed:
            self.event_sink.emit(
                create_event(
                    event_type=EventType.RUN_VERIFIED,
                    research_run_id=report.research_run_id,
                    actor=actor,
                    payload={
                        "status": report.status.value,
                        "claims_count": len(report.claims_verified),
                    },
                )
            )
            self.event_sink.emit(
                create_event(
                    event_type=EventType.VERIFICATION_COMPLETED,
                    research_run_id=report.research_run_id,
                    actor=actor,
                    payload=payload,
                )
            )
        elif report.status == VerificationStatus.WARNING:
            self.event_sink.emit(
                create_event(
                    event_type=EventType.VERIFICATION_COMPLETED,
                    research_run_id=report.research_run_id,
                    actor=actor,
                    payload=payload,
                )
            )
        else:
            self.event_sink.emit(
                create_event(
                    event_type=EventType.RUN_VERIFICATION_FAILED,
                    research_run_id=report.research_run_id,
                    actor=actor,
                    payload={"status": report.status.value, "errors": report.errors[:10]},
                )
            )
            self.event_sink.emit(
                create_event(
                    event_type=EventType.VERIFICATION_FAILED,
                    research_run_id=report.research_run_id,
                    actor=actor,
                    payload=payload,
                )
            )


# Alias for REX architectural consistency
DeterministicVerifier = ResearchVerifier
