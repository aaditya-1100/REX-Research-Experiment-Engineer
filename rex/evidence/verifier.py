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
        tolerance: float = 1e-4,
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

        # Step 1: Emit VERIFICATION_STARTED event
        start_event = create_event(
            event_type=EventType.VERIFICATION_STARTED,
            research_run_id=research_run_id,
            actor=actor_enum,
            payload={"verifier": "ResearchVerifier", "started_at": started_at.isoformat()},
        )
        self.event_sink.emit(start_event)

        # Step 2: Verify research run exists
        run = self.session.get(ResearchRunModel, research_run_id)
        if run is None:
            errors.append(f"Research run '{research_run_id}' not found.")
            completed_at = datetime.now(UTC)
            fail_report = VerificationReport(
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
            self._emit_completion_event(fail_report, actor_enum)
            return fail_report

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

            # If claim claims to be VERIFIED or SUPPORTED, lineage must be complete
            is_valid = True
            if claim.status in (ClaimStatus.VERIFIED.value, ClaimStatus.SUPPORTED.value):
                if not lineage.is_complete:
                    is_valid = False
                    msg = (
                        f"Claim '{claim.id}' is marked '{claim.status}' but lineage is broken: "
                        f"{'; '.join(lineage.gaps)}"
                    )
                    errors.append(msg)
            elif not lineage.is_complete:
                warnings.append(
                    f"Draft/unsupported claim '{claim.id}' has incomplete lineage: {'; '.join(lineage.gaps)}"
                )

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
                        if diff > self.tolerance:
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
                            if diff > self.tolerance:
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

    def _emit_completion_event(
        self,
        report: VerificationReport,
        actor: ActorType,
    ) -> None:
        """Emit either VERIFICATION_COMPLETED or VERIFICATION_FAILED event."""
        event_type = (
            EventType.VERIFICATION_COMPLETED
            if report.is_passed or report.status == VerificationStatus.WARNING
            else EventType.VERIFICATION_FAILED
        )

        event = create_event(
            event_type=event_type,
            research_run_id=report.research_run_id,
            actor=actor,
            payload={
                "status": report.status.value,
                "claims_count": len(report.claims_verified),
                "artifacts_count": len(report.artifacts_verified),
                "analyses_count": len(report.analyses_recomputed),
                "errors_count": len(report.errors),
                "warnings_count": len(report.warnings),
                "errors": report.errors[:10],
                "warnings": report.warnings[:10],
            },
        )
        self.event_sink.emit(event)
