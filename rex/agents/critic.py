"""REX Research Critic Agent (REX-033).

Evaluates research investigations, experimental methodology, evidence sufficiency,
sample sizes, control conditions, data leakage, confounders, and conclusion scope.
Does NOT execute experiments and does NOT mutate state, budgets, or claims.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from rex.agents.base import BaseAgent
from rex.domain.models import (
    CritiqueCategory,
    CritiqueFinding,
    CritiqueSeverity,
    EpistemicStatus,
    ResearchCritique,
)
from rex.evidence.verifier import VerificationReport
from rex.llm.base import LLMProvider
from rex.llm.models import LLMRequest
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
    freeze_value,
)
from rex.persistence.models import (
    AnalysisModel,
    ClaimModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    LiteratureSourceModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    CritiqueRepository,
    EventRepository,
)

logger = logging.getLogger(__name__)


class CriticObservationProposal(BaseModel):
    """Structured LLM proposal for an individual methodological or evidentiary finding."""

    model_config = ConfigDict(extra="forbid")

    category: CritiqueCategory = Field(
        default=CritiqueCategory.METHODOLOGY,
        description="Methodological dimension: baseline, controls, leakage, sample_size, seeds, metrics, confounders, conclusion_scope, methodology",
    )
    severity: CritiqueSeverity = Field(
        default=CritiqueSeverity.MEDIUM,
        description="Severity of finding: info, low, medium, high, critical",
    )
    description: str = Field(
        description="Concrete, factual critique statement explaining the concern"
    )
    epistemic_status: EpistemicStatus = Field(
        default=EpistemicStatus.OBSERVED,
        description="Standing of evidence: observed, inferred, proposed, verified, unsupported, contradicted",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Exact database entity IDs (e.g. exp_..., res_..., clm_..., lit_...) referenced",
    )
    recommendation: str = Field(
        default="",
        description="Specific actionable remediation or next step recommended by the critic",
    )


class ResearchCritiqueProposal(BaseModel):
    """Structured container for LLM critique evaluation of a research run."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="Comprehensive synthesis of current experimental standing")
    strengths: list[str] = Field(
        default_factory=list, description="Methodological and empirical strengths identified"
    )
    weaknesses: list[str] = Field(
        default_factory=list, description="Evidentiary gaps, design weaknesses, or failure modes"
    )
    contradictions: list[str] = Field(
        default_factory=list,
        description="Inconsistencies across results, claims, or literature",
    )
    unresolved_questions: list[str] = Field(
        default_factory=list,
        description="Open empirical questions remaining before conclusion",
    )
    methodological_concerns: list[str] = Field(
        default_factory=list,
        description="Issues regarding baselines, leakage, seeds, confounders, or metrics",
    )
    findings: list[CriticObservationProposal] = Field(
        default_factory=list, description="List of discrete, categorized critique findings"
    )
    recommended_action: str = Field(
        default="refine",
        description="Action recommendation: refine, replicate, pivot, stop, complete",
    )
    recommended_action_rationale: str = Field(
        default="", description="Detailed rationale grounding recommended action"
    )


class ResearchCriticAgent(BaseAgent):
    """Dedicated research critic evaluating experimental validity and conclusion strength (REX-033)."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        event_sink: EventSink | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        super().__init__(provider=provider or llm_provider, event_sink=event_sink)

    def _emit(self, event: Any, session: Session) -> None:
        EventRepository(session).record_event(event)
        if self.event_sink is not None:
            self.event_sink.emit(event)

    def critique_run(
        self,
        research_run_id: str,
        session: Session,
        iteration: int = 1,
        actor: ActorType = ActorType.CRITIC,
        verifier_report: VerificationReport | None = None,
    ) -> ResearchCritique:
        """Evaluate the current research state against actual persisted evidence.

        Audits baselines, control conditions, data leakage, seeds, metrics, confounders,
        and conclusion scope. Hallucinated entity references are sanitized and quarantined.
        """
        # Emit critique started event
        self._emit(
            create_event(
                event_type=EventType.CRITIQUE_STARTED,
                actor=actor,
                research_run_id=research_run_id,
                payload={"iteration": iteration},
            ),
            session,
        )

        run = session.get(ResearchRunModel, research_run_id)
        if run is None:
            raise ValueError(f"Research run '{research_run_id}' not found in persistence.")

        # 1. Gather all actual persisted evidence
        hypotheses = (
            session.query(HypothesisModel)
            .filter(HypothesisModel.research_run_id == research_run_id)
            .all()
        )
        experiments = (
            session.query(ExperimentModel)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .all()
        )
        executions = (
            session.query(ExecutionModel)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .all()
        )
        results = (
            session.query(ResultModel)
            .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .all()
        )
        analyses = (
            session.query(AnalysisModel)
            .filter(AnalysisModel.research_run_id == research_run_id)
            .all()
        )
        claims = (
            session.query(ClaimModel).filter(ClaimModel.research_run_id == research_run_id).all()
        )
        literature = (
            session.query(LiteratureSourceModel)
            .filter(LiteratureSourceModel.research_run_id == research_run_id)
            .all()
        )

        # Build set of valid IDs for strict hallucination quarantine
        valid_entity_ids: set[str] = {
            run.id,
            *(h.id for h in hypotheses),
            *(e.id for e in experiments),
            *(ex.id for ex in executions),
            *(r.id for r in results),
            *(a.id for a in analyses),
            *(c.id for c in claims),
            *(lit.id for lit in literature),
        }

        # 2. Format grounded context prompt for critic
        evidence_prompt = self._format_evidence_prompt(
            run=run,
            hypotheses=hypotheses,
            experiments=experiments,
            executions=executions,
            results=results,
            analyses=analyses,
            claims=claims,
            literature=literature,
            verifier_report=verifier_report,
        )

        system_prompt = (
            "You are an expert empirical research scientist and methodological auditor acting as the REX Research Critic.\n"
            "Your objective is to provide an adversarial, rigorous critique of the current research investigation.\n\n"
            "CRITICAL AUDITING RESPONSIBILITIES:\n"
            "1. Baseline & Controls: Did experiments include genuine baselines and proper control conditions?\n"
            "2. Leakage & Confounders: Is there risk of train/test leakage, lookahead bias, or confounding variables?\n"
            "3. Sample Size & Seeds: Are sample sizes adequate? Were multiple seeds evaluated for variance?\n"
            "4. Metrics & Evaluation: Are metrics appropriate, non-degenerate, and aligned with stated hypotheses?\n"
            "5. Conclusion Scope: Do claims overreach empirical evidence? Does evidence warrant conclusions?\n"
            "6. Literature Distinction: Literature reports are external third-party data, NOT verified REX results.\n"
            "7. Strict Entity Grounding: You may ONLY cite entity IDs that exist in the provided evidence. DO NOT hallucinate IDs.\n"
            "8. Recommend Next Action: Choose one of [refine, replicate, pivot, stop, complete] with grounded justification."
        )

        user_prompt = (
            f"### RESEARCH RUN AUDIT CONTEXT [Run ID: {research_run_id}, Iteration: {iteration}]\n"
            f"{evidence_prompt}\n\n"
            "Produce a structured methodological critique adhering strictly to the JSON schema."
        )

        request = LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            research_run_id=research_run_id,
            action_name="research_critique",
            context={"iteration": iteration},
        )

        proposal, _response = self._generate_structured(
            request=request,
            response_model=ResearchCritiqueProposal,
            session=session,
            actor=actor,
        )

        # 3. Post-processing & Deterministic Hallucination Quarantine
        clean_findings: list[CritiqueFinding] = []
        quarantined_hallucinations: list[str] = []

        for p_finding in proposal.findings:
            clean_refs: list[str] = []
            for ref in p_finding.evidence_refs:
                clean_ref = ref.strip()
                if clean_ref in valid_entity_ids:
                    clean_refs.append(clean_ref)
                else:
                    clean_refs.append(f"[UNRESOLVED_ENTITY: {clean_ref}]")
                    quarantined_hallucinations.append(clean_ref)

            finding = CritiqueFinding(
                finding_id=f"fnd_{uuid.uuid4().hex[:8]}",
                category=p_finding.category,
                severity=p_finding.severity,
                description=p_finding.description,
                epistemic_status=p_finding.epistemic_status,
                evidence_refs=tuple(clean_refs),
                recommendation=p_finding.recommendation,
            )
            clean_findings.append(finding)

        weaknesses = list(proposal.weaknesses)
        if quarantined_hallucinations:
            weaknesses.append(
                f"Critic referenced non-existent entity IDs: {', '.join(set(quarantined_hallucinations))} (quarantined)."
            )

        # If verification report was supplied and failed, inject a critical finding
        if verifier_report is not None and not verifier_report.is_passed:
            clean_findings.append(
                CritiqueFinding(
                    finding_id=f"fnd_verif_{uuid.uuid4().hex[:6]}",
                    category=CritiqueCategory.METHODOLOGY,
                    severity=CritiqueSeverity.CRITICAL,
                    description=f"Formal research verification failed with {len(verifier_report.errors)} errors: {'; '.join(verifier_report.errors[:3])}",
                    epistemic_status=EpistemicStatus.CONTRADICTED,
                    evidence_refs=tuple(verifier_report.cross_run_violations[:5]),
                    recommendation="Remediate broken evidence lineage or execution checksums before drawing conclusions.",
                )
            )

        referenced_evidence = {
            "hypotheses": [h.id for h in hypotheses],
            "experiments": [e.id for e in experiments],
            "executions": [ex.id for ex in executions],
            "results": [r.id for r in results],
            "analyses": [a.id for a in analyses],
            "claims": [c.id for c in claims],
            "literature": [lit.id for lit in literature],
        }

        critique = ResearchCritique(
            research_run_id=research_run_id,
            iteration=iteration,
            summary=proposal.summary,
            strengths=tuple(proposal.strengths),
            weaknesses=tuple(weaknesses),
            contradictions=tuple(proposal.contradictions),
            unresolved_questions=tuple(proposal.unresolved_questions),
            methodological_concerns=tuple(proposal.methodological_concerns),
            findings=tuple(clean_findings),
            recommended_action=proposal.recommended_action.lower().strip(),
            recommended_action_rationale=proposal.recommended_action_rationale,
            referenced_evidence=freeze_value(referenced_evidence),
            created_by=actor.value,
        )

        # Persist critique
        repo = CritiqueRepository(session)
        repo.create(critique.to_persistence())

        # Emit critique completed event
        self._emit(
            create_event(
                event_type=EventType.CRITIQUE_COMPLETED,
                actor=actor,
                research_run_id=research_run_id,
                payload={
                    "critique_id": critique.id,
                    "iteration": iteration,
                    "findings_count": len(clean_findings),
                    "has_critical": critique.has_critical_findings,
                    "recommended_action": critique.recommended_action,
                },
            ),
            session,
        )

        return critique

    critique_research = critique_run

    def _format_evidence_prompt(
        self,
        run: ResearchRunModel,
        hypotheses: list[HypothesisModel],
        experiments: list[ExperimentModel],
        executions: list[ExecutionModel],
        results: list[ResultModel],
        analyses: list[AnalysisModel],
        claims: list[ClaimModel],
        literature: list[LiteratureSourceModel],
        verifier_report: VerificationReport | None,
    ) -> str:
        """Format persisted evidence summary for LLM critique evaluation."""
        lines: list[str] = [
            f"Research Question: {run.research_question or run.title}",
            f"Current Lifecycle State: {run.status}",
            "",
            f"=== 1. HYPOTHESES ({len(hypotheses)}) ===",
        ]
        for h in hypotheses:
            lines.append(
                f"- [ID: {h.id}] Statement: {h.statement} (Direction: {h.expected_direction})"
            )

        lines.extend(["", f"=== 2. EXPERIMENTS ({len(experiments)}) ==="])
        for e in experiments:
            spec = e.specification_json or {}
            lines.append(
                f"- [ID: {e.id}] Objective: {e.objective}, Status: {e.status}, Method: {spec.get('method', 'N/A')}, Baseline: {spec.get('baseline', 'None')}"
            )

        lines.extend(["", f"=== 3. EXECUTIONS ({len(executions)}) ==="])
        for ex in executions:
            lines.append(
                f"- [ID: {ex.id}] Exp ID: {ex.experiment_id}, Status: {ex.status}, Exit Code: {ex.exit_code}"
            )

        lines.extend(["", f"=== 4. RESULTS & METRICS ({len(results)}) ==="])
        for r in results:
            lines.append(
                f"- [ID: {r.id}] Exec ID: {r.execution_id}, Metric: {r.metric_name} = {r.metric_value} ({r.metric_unit})"
            )

        lines.extend(["", f"=== 5. STATISTICAL ANALYSES ({len(analyses)}) ==="])
        for a in analyses:
            lines.append(
                f"- [ID: {a.id}] Method: {a.method}, Type: {a.analysis_type}, Output: {a.output_json}"
            )

        lines.extend(["", f"=== 6. SCIENTIFIC CLAIMS ({len(claims)}) ==="])
        for c in claims:
            lines.append(
                f"- [ID: {c.id}] Status: {c.status}, Claim Type: {c.claim_type}, Text: {c.text}"
            )

        lines.extend(["", f"=== 7. SCHOLARLY LITERATURE EVIDENCE ({len(literature)}) ==="])
        for lit in literature:
            lines.append(
                f"- [ID: {lit.id}] Provider: {lit.provider}, Title: {lit.title}, Year: {lit.year}, Ext ID: {lit.external_id}"
            )

        if verifier_report is not None:
            lines.extend(
                [
                    "",
                    "=== 8. FORMAL VERIFICATION REPORT ===",
                    f"Overall Status: {verifier_report.status.value}",
                    f"Errors: {verifier_report.errors}",
                    f"Warnings: {verifier_report.warnings}",
                ]
            )

        return "\n".join(lines)


__all__ = [
    "CriticObservationProposal",
    "ResearchCriticAgent",
    "ResearchCritiqueProposal",
]
