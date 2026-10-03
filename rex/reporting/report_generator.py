"""REX Evidence-Grounded Research Report Generator (REX-036).

Synthesizes research investigations from persisted, machine-auditable evidence records.
Enforces:
1. Complete structured report (question, hypotheses, methods, experiments, results, limitations, conclusions).
2. Direct linking of numerical statements to claim IDs and evidence lineage.
3. Strict epistemic discipline: unsupported claims are quarantined or explicitly labelled.
4. Honest representation of failed experiments and execution errors.
5. Deterministic content hashing and persistence as auditable research artifacts.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from rex.evidence.graph import EvidenceGraphService
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
    CritiqueModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    ArtifactRepository,
    EventRepository,
)
from rex.reporting.models import (
    ReportAnalysisSummary,
    ReportClaimRef,
    ReportExperimentSummary,
    ReportHypothesisSummary,
    ReportMethodologyCritique,
    ReportResultMetric,
    ResearchReport,
)

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Evidence-grounded report generator synthesizing auditable research documentation (REX-036)."""

    def __init__(self, event_sink: EventSink | None = None) -> None:
        self.event_sink = event_sink

    def _emit(self, event: Any, session: Session) -> None:
        EventRepository(session).record_event(event)
        if self.event_sink is not None:
            self.event_sink.emit(event)

    def generate_report(
        self,
        research_run_id: str,
        session: Session,
        save_artifact: bool = True,
        artifact_root: Path | str | None = None,
        actor: ActorType = ActorType.REPORT_GENERATOR,
    ) -> ResearchReport:
        """Generate a complete, evidence-grounded research report for an active or completed research run."""
        run = session.get(ResearchRunModel, research_run_id)
        if run is None:
            raise ValueError(f"Research run '{research_run_id}' not found in persistence.")

        # 1. Query all persisted evidence from database
        hypotheses_models = (
            session.query(HypothesisModel)
            .filter(HypothesisModel.research_run_id == research_run_id)
            .order_by(HypothesisModel.created_at.asc())
            .all()
        )
        experiments_models = (
            session.query(ExperimentModel)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .order_by(ExperimentModel.created_at.asc())
            .all()
        )
        executions_models = (
            session.query(ExecutionModel)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .order_by(ExecutionModel.started_at.asc())
            .all()
        )
        results_models = (
            session.query(ResultModel)
            .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .order_by(ResultModel.created_at.asc())
            .all()
        )
        analyses_models = (
            session.query(AnalysisModel)
            .filter(AnalysisModel.research_run_id == research_run_id)
            .order_by(AnalysisModel.created_at.asc())
            .all()
        )
        claims_models = (
            session.query(ClaimModel)
            .filter(ClaimModel.research_run_id == research_run_id)
            .order_by(ClaimModel.created_at.asc())
            .all()
        )
        critiques_models = (
            session.query(CritiqueModel)
            .filter(CritiqueModel.research_run_id == research_run_id)
            .order_by(CritiqueModel.iteration.asc())
            .all()
        )

        # 2. Process Hypotheses
        report_hypotheses: list[ReportHypothesisSummary] = []
        for h in hypotheses_models:
            report_hypotheses.append(
                ReportHypothesisSummary(
                    hypothesis_id=h.id,
                    statement=h.statement,
                    rationale=h.rationale,
                    expected_direction=h.expected_direction,
                    falsification_condition=h.falsification_condition,
                    status=h.status,
                )
            )

        # 3. Process Experiments & Honestly Identify Failures (AC-4)
        report_experiments: list[ReportExperimentSummary] = []
        failed_experiments: list[ReportExperimentSummary] = []

        executions_by_exp: dict[str, list[ExecutionModel]] = {}
        for ex in executions_models:
            executions_by_exp.setdefault(ex.experiment_id, []).append(ex)

        for exp in experiments_models:
            exp_executions = executions_by_exp.get(exp.id, [])
            total_execs = len(exp_executions)
            failed_execs = [
                ex
                for ex in exp_executions
                if ex.status != "completed" or (ex.exit_code is not None and ex.exit_code != 0)
            ]
            failed_count = len(failed_execs)
            is_failed = failed_count > 0 or exp.status == "failed"

            failure_reasons: list[str] = []
            for fe in failed_execs:
                reason = f"Execution {fe.id} exited with status '{fe.status}'"
                if fe.exit_code is not None and fe.exit_code != 0:
                    reason += f" (exit code {fe.exit_code})"
                if fe.stderr_artifact_id:
                    reason += f" [stderr logged in artifact {fe.stderr_artifact_id}]"
                failure_reasons.append(reason)

            if exp.status == "failed" and not failure_reasons:
                failure_reasons.append("Experiment status marked as failed in controller.")

            spec = exp.specification_json or {}
            baseline_info = spec.get("baseline")
            baseline_name = None
            baseline_val = None
            if isinstance(baseline_info, dict):
                baseline_name = baseline_info.get("name")
                baseline_val = baseline_info.get("value")
            elif isinstance(baseline_info, str):
                baseline_name = baseline_info

            summary = ReportExperimentSummary(
                experiment_id=exp.id,
                objective=exp.objective,
                hypothesis_id=exp.hypothesis_id,
                status=exp.status,
                method=spec.get("method", "empirical_evaluation"),
                baseline_name=baseline_name,
                baseline_value=baseline_val,
                execution_count=total_execs,
                failed_executions_count=failed_count,
                is_failed=is_failed,
                failure_reasons=tuple(failure_reasons),
            )
            report_experiments.append(summary)
            if is_failed:
                failed_experiments.append(summary)

        # 4. Process Results & Metrics
        report_metrics: list[ReportResultMetric] = []
        for r in results_models:
            report_metrics.append(
                ReportResultMetric(
                    result_id=r.id,
                    execution_id=r.execution_id,
                    metric_name=r.metric_name,
                    metric_value=r.metric_value,
                    metric_unit=r.metric_unit,
                )
            )

        # 5. Process Statistical Analyses
        report_analyses: list[ReportAnalysisSummary] = []
        for a in analyses_models:
            out = a.output_json or {}
            report_analyses.append(
                ReportAnalysisSummary(
                    analysis_id=a.id,
                    analysis_type=a.analysis_type,
                    method=a.method,
                    metric_name=str(out.get("metric_name", "")),
                    mean=out.get("mean"),
                    std=out.get("std"),
                    ci_lower=out.get("ci_lower"),
                    ci_upper=out.get("ci_upper"),
                    sample_size=out.get("sample_size", 0),
                    p_value=out.get("p_value"),
                    effect_size=out.get("effect_size") or out.get("cohens_d"),
                )
            )

        # 6. Process Claims & Verify Evidence Lineage (AC-2, AC-3)
        evidence_graph = EvidenceGraphService(session)
        all_claims: list[ReportClaimRef] = []
        supported_claims: list[ReportClaimRef] = []
        unsupported_claims: list[ReportClaimRef] = []

        for c in claims_models:
            lineage = evidence_graph.trace_claim_lineage(c.id)
            supporting_results = tuple(r.id for r in lineage.results)
            supporting_analyses = tuple(a.id for a in lineage.analyses)
            is_supported = len(supporting_results) > 0 or len(supporting_analyses) > 0

            epistemic_status = c.status
            if not is_supported:
                epistemic_status = "unsupported"
            elif epistemic_status == "proposed":
                epistemic_status = "observed"

            claim_ref = ReportClaimRef(
                claim_id=c.id,
                statement=c.text,
                claim_type=c.claim_type,
                epistemic_status=epistemic_status,
                is_supported=is_supported,
                supporting_result_ids=supporting_results,
                supporting_analysis_ids=supporting_analyses,
            )
            all_claims.append(claim_ref)
            if is_supported:
                supported_claims.append(claim_ref)
            else:
                unsupported_claims.append(claim_ref)

        # 7. Process Methodological Critiques
        report_critiques: list[ReportMethodologyCritique] = []
        critique_weaknesses: list[str] = []
        for cr in critiques_models:
            findings_raw = cr.findings_json or []
            critical_fnds = [
                f.get("description", "")
                for f in findings_raw
                if isinstance(f, dict) and f.get("severity") == "critical"
            ]
            report_critiques.append(
                ReportMethodologyCritique(
                    critique_id=cr.id,
                    iteration=cr.iteration,
                    summary=cr.summary,
                    weaknesses=tuple(cr.weaknesses_json or []),
                    methodological_concerns=tuple(cr.methodological_concerns_json or []),
                    critical_findings=tuple(critical_fnds),
                    recommended_action=cr.recommended_action,
                )
            )
            critique_weaknesses.extend(cr.weaknesses_json or [])

        # 8. Synthesize Limitations & Conclusions (AC-1, AC-3)
        limitations: list[str] = []
        # Check sample sizes
        small_samples = [a for a in report_analyses if a.sample_size < 3 and a.sample_size > 0]
        if small_samples:
            limitations.append(
                f"Limited statistical sample size: {len(small_samples)} analysis group(s) have sample size < 3."
            )

        # Check failures
        if failed_experiments:
            limitations.append(
                f"Empirical attrition: {len(failed_experiments)} out of {len(experiments_models)} experiment(s) "
                "failed during execution and could not contribute verified results."
            )

        # Check unsupported claims
        if unsupported_claims:
            limitations.append(
                f"Ungrounded assertions: {len(unsupported_claims)} claim(s) lacked valid backing evidence links "
                "in the evidence graph and were excluded from verified conclusions."
            )

        # Incorporate critic weaknesses
        for w in set(critique_weaknesses):
            if w not in limitations:
                limitations.append(f"Critic observation: {w}")

        conclusions: list[str] = []
        if supported_claims:
            for sc in supported_claims:
                ev_count = len(sc.supporting_result_ids) + len(sc.supporting_analysis_ids)
                conclusions.append(
                    f"Verified claim [ID: `{sc.claim_id}`]: {sc.statement} (Supported by {ev_count} verified evidence record(s))."
                )
        else:
            if report_metrics:
                conclusions.append(
                    f"Investigation captured {len(report_metrics)} metric measurement(s), but formal scientific claims have not been linked."
                )
            else:
                conclusions.append(
                    "Investigation produced insufficient empirical measurements to support conclusive scientific claims."
                )

        if unsupported_claims:
            conclusions.append(
                f"NOTICE: {len(unsupported_claims)} ungrounded claim(s) were flagged during verification and omitted from findings."
            )

        report = ResearchReport(
            research_run_id=research_run_id,
            title=run.title,
            research_question=run.research_question or run.title,
            run_status=run.status,
            hypotheses=tuple(report_hypotheses),
            experiments=tuple(report_experiments),
            failed_experiments=tuple(failed_experiments),
            metrics=tuple(report_metrics),
            analyses=tuple(report_analyses),
            claims=tuple(all_claims),
            supported_claims=tuple(supported_claims),
            unsupported_claims=tuple(unsupported_claims),
            critiques=tuple(report_critiques),
            limitations=tuple(limitations),
            conclusions=tuple(conclusions),
            total_experiments=len(experiments_models),
            total_executions=len(executions_models),
            total_results=len(results_models),
        )

        # 9. Optionally persist report artifacts to disk and database
        if save_artifact:
            self._save_report_artifacts(
                report=report,
                research_run_id=research_run_id,
                session=session,
                artifact_root=artifact_root,
            )

        # 10. Emit audit event
        self._emit(
            create_event(
                event_type=EventType.REPORT_GENERATED,
                actor=actor,
                research_run_id=research_run_id,
                payload={
                    "report_id": report.report_id,
                    "research_run_id": research_run_id,
                    "title": report.title,
                    "content_hash": report.content_hash(),
                    "total_experiments": report.total_experiments,
                    "total_executions": report.total_executions,
                    "total_results": report.total_results,
                    "claims_count": len(report.claims),
                    "supported_claims_count": len(report.supported_claims),
                    "unsupported_claims_count": len(report.unsupported_claims),
                    "failed_experiments_count": len(report.failed_experiments),
                    "is_fully_grounded": report.is_fully_grounded,
                },
            ),
            session,
        )

        return report

    def _save_report_artifacts(
        self,
        report: ResearchReport,
        research_run_id: str,
        session: Session,
        artifact_root: Path | str | None,
    ) -> None:
        """Persist report JSON and Markdown files to disk and register them in the artifacts table."""
        base_dir = Path(artifact_root) if artifact_root else Path("data/runs")
        report_dir = base_dir / research_run_id / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        json_path = report_dir / f"{report.report_id}.json"
        md_path = report_dir / f"{report.report_id}.md"

        json_content = report.to_json(indent=2)
        md_content = report.to_markdown()

        json_path.write_text(json_content, encoding="utf-8")
        md_path.write_text(md_content, encoding="utf-8")

        json_hash = hashlib.sha256(json_content.encode("utf-8")).hexdigest()
        md_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()

        artifact_repo = ArtifactRepository(session)

        # Register JSON artifact
        json_artifact = ArtifactModel(
            research_run_id=research_run_id,
            artifact_type="research_report_json",
            path=str(json_path.as_posix()),
            content_hash=json_hash,
            size_bytes=len(json_content.encode("utf-8")),
            metadata_json={"report_id": report.report_id, "format": "json"},
        )
        artifact_repo.create(json_artifact)

        # Register Markdown artifact
        md_artifact = ArtifactModel(
            research_run_id=research_run_id,
            artifact_type="research_report_md",
            path=str(md_path.as_posix()),
            content_hash=md_hash,
            size_bytes=len(md_content.encode("utf-8")),
            metadata_json={"report_id": report.report_id, "format": "markdown"},
        )
        artifact_repo.create(md_artifact)


__all__ = ["ReportGenerator"]
