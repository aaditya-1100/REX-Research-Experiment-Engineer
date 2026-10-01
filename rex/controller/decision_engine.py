"""REX Deterministic Decision Engine (REX-034).

Decides whether to refine, replicate, pivot, stop, complete, or fail.
Validates LLM proposals against deterministic state machine rules, budget availability,
evidence sufficiency criteria, and replication/refinement prerequisites.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from rex.controller.budgets import calculate_budget_usage, load_run_budget
from rex.domain.models import (
    CritiqueSeverity,
    DecisionType,
    ResearchCritique,
    ResearchDecision,
)
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
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    DecisionRepository,
    EventRepository,
)

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Deterministic policy and validation engine for research next-action decisions (REX-034)."""

    def __init__(self, event_sink: EventSink | None = None) -> None:
        self.event_sink = event_sink

    def _emit(self, event: Any, session: Session) -> None:
        EventRepository(session).record_event(event)
        if self.event_sink is not None:
            self.event_sink.emit(event)

    def evaluate_decision(
        self,
        research_run_id: str,
        session: Session,
        critique: ResearchCritique | None = None,
        proposed_action: DecisionType | str | None = None,
        proposed_target_type: str | None = None,
        proposed_target_id: str | None = None,
        proposed_rationale: str | None = None,
        iteration: int = 1,
        actor: ActorType = ActorType.DECISION_ENGINE,
    ) -> ResearchDecision:
        """Deterministically validate and produce an authoritative research decision."""
        run = session.get(ResearchRunModel, research_run_id)
        if run is None:
            raise ValueError(f"Research run '{research_run_id}' not found in persistence.")

        # Determine candidate action from proposal or critique
        if proposed_action is not None:
            raw_action_str = (
                proposed_action.value
                if isinstance(proposed_action, DecisionType)
                else str(proposed_action).lower().strip()
            )
        elif critique is not None:
            raw_action_str = critique.recommended_action.lower().strip()
        else:
            raw_action_str = DecisionType.REFINE.value

        try:
            candidate_action = DecisionType(raw_action_str)
        except ValueError:
            candidate_action = DecisionType.STOP

        rationale = proposed_rationale or (
            critique.recommended_action_rationale if critique else "Autonomous research decision"
        )
        critique_id = critique.id if critique else None

        # Emit decision proposed event
        self._emit(
            create_event(
                event_type=EventType.DECISION_PROPOSED,
                actor=actor,
                research_run_id=research_run_id,
                payload={
                    "iteration": iteration,
                    "proposed_action": candidate_action.value,
                    "critique_id": critique_id,
                    "rationale": rationale,
                },
            ),
            session,
        )

        # 1. Deterministic Budget Check
        budget = load_run_budget(run)
        usage = calculate_budget_usage(session, research_run_id)

        remaining_experiments = max(0, budget.max_experiments - usage.experiments_count)
        remaining_executions = max(0, budget.max_executions - usage.executions_count)
        remaining_llm_calls = max(0, budget.max_llm_calls - usage.llm_calls_count)
        remaining_runtime = max(
            0.0, float(budget.max_runtime_seconds - usage.total_runtime_seconds)
        )

        budget_remaining: dict[str, Any] = {
            "remaining_experiments": remaining_experiments,
            "remaining_executions": remaining_executions,
            "remaining_llm_calls": remaining_llm_calls,
            "remaining_runtime_seconds": remaining_runtime,
        }

        validation_errors: list[str] = []
        final_action = candidate_action
        target_type = proposed_target_type
        target_id = proposed_target_id

        # 2. Check budget bounds for actions requiring new experiments/executions
        if candidate_action in (DecisionType.REFINE, DecisionType.REPLICATE, DecisionType.PIVOT):
            if remaining_experiments <= 0:
                err = f"Experiment budget exhausted ({usage.experiments_count}/{budget.max_experiments})."
                validation_errors.append(err)
                final_action = DecisionType.STOP
                rationale = f"{err} Decision overridden from {candidate_action.value} to stop."
            elif remaining_executions <= 0:
                err = f"Execution budget exhausted ({usage.executions_count}/{budget.max_executions})."
                validation_errors.append(err)
                final_action = DecisionType.STOP
                rationale = f"{err} Decision overridden from {candidate_action.value} to stop."
            elif remaining_runtime <= 0:
                err = f"Runtime budget exhausted ({usage.total_runtime_seconds:.1f}s/{budget.max_runtime_seconds}s)."
                validation_errors.append(err)
                final_action = DecisionType.STOP
                rationale = f"{err} Decision overridden from {candidate_action.value} to stop."

        # 3. Action-Specific Validation
        experiments = (
            session.query(ExperimentModel)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .order_by(ExperimentModel.created_at.desc())
            .all()
        )
        results = (
            session.query(ResultModel)
            .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(ExperimentModel.research_run_id == research_run_id)
            .all()
        )
        hypotheses = (
            session.query(HypothesisModel)
            .filter(HypothesisModel.research_run_id == research_run_id)
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

        valid_exp_ids = {e.id for e in experiments}

        if final_action == DecisionType.REPLICATE:
            # Must target a valid experiment that has completed with results
            if not target_id:
                if experiments:
                    target_id = experiments[0].id
                    target_type = "experiment"
                else:
                    validation_errors.append("Cannot REPLICATE: no experiments exist in this run.")
                    final_action = DecisionType.STOP

            if target_id and target_id not in valid_exp_ids:
                validation_errors.append(
                    f"Cannot REPLICATE: target experiment '{target_id}' does not exist in run."
                )
                final_action = DecisionType.STOP
            elif target_id:
                # Verify experiment has completed results
                exp_results = [
                    r for r in results if r.execution and r.execution.experiment_id == target_id
                ]
                if not exp_results:
                    validation_errors.append(
                        f"Cannot REPLICATE: target experiment '{target_id}' has no recorded results."
                    )
                    final_action = DecisionType.REFINE

        elif final_action == DecisionType.REFINE:
            if not target_id and experiments:
                target_id = experiments[0].id
                target_type = "experiment"
            elif target_id and target_id not in valid_exp_ids:
                validation_errors.append(
                    f"Target experiment '{target_id}' for REFINE does not exist in run."
                )
                if experiments:
                    target_id = experiments[0].id
                else:
                    final_action = DecisionType.STOP

        elif final_action == DecisionType.COMPLETE:
            # Deterministic evidence sufficiency check for COMPLETE
            completion_gaps = self._validate_completion_sufficiency(
                hypotheses=hypotheses,
                experiments=experiments,
                results=results,
                analyses=analyses,
                claims=claims,
                critique=critique,
            )
            if completion_gaps:
                validation_errors.extend(completion_gaps)
                # If evidence is not sufficient for complete, reject COMPLETE and pivot/refine or stop
                if remaining_experiments > 0:
                    final_action = DecisionType.REFINE
                    rationale = f"COMPLETE rejected due to evidence gaps: {'; '.join(completion_gaps)}. Proceeding with refinement."
                else:
                    final_action = DecisionType.STOP
                    rationale = f"COMPLETE rejected due to evidence gaps: {'; '.join(completion_gaps)}. Budget exhausted, stopping."

        is_validated = len(validation_errors) == 0 or (
            final_action != candidate_action
            and final_action in (DecisionType.STOP, DecisionType.REFINE)
        )

        decision = ResearchDecision(
            research_run_id=research_run_id,
            iteration=iteration,
            action=final_action,
            target_entity_type=target_type,
            target_entity_id=target_id,
            rationale=rationale,
            critique_id=critique_id,
            is_validated=is_validated,
            validation_errors=tuple(validation_errors),
            budget_checked=True,
            budget_remaining=freeze_value(budget_remaining),
            created_by=actor.value,
        )

        # Persist decision
        repo = DecisionRepository(session)
        repo.create(decision.to_persistence())

        # Emit decision accepted/rejected event
        if is_validated and final_action == candidate_action:
            self._emit(
                create_event(
                    event_type=EventType.DECISION_ACCEPTED,
                    actor=actor,
                    research_run_id=research_run_id,
                    payload={
                        "decision_id": decision.id,
                        "action": decision.action.value,
                        "iteration": iteration,
                    },
                ),
                session,
            )
        else:
            self._emit(
                create_event(
                    event_type=EventType.DECISION_REJECTED,
                    actor=actor,
                    research_run_id=research_run_id,
                    payload={
                        "decision_id": decision.id,
                        "requested_action": candidate_action.value,
                        "adjusted_action": decision.action.value,
                        "validation_errors": validation_errors,
                        "iteration": iteration,
                    },
                ),
                session,
            )

        return decision

    def _validate_completion_sufficiency(
        self,
        hypotheses: list[HypothesisModel],
        experiments: list[ExperimentModel],
        results: list[ResultModel],
        analyses: list[AnalysisModel],
        claims: list[ClaimModel],
        critique: ResearchCritique | None,
    ) -> list[str]:
        """Verify that deterministic conditions for research completion are satisfied."""
        gaps: list[str] = []

        if not hypotheses:
            gaps.append("Incomplete evidence: No hypotheses formulated.")

        if not experiments:
            gaps.append("Incomplete evidence: No experiments executed.")

        if not results:
            gaps.append("Incomplete evidence: No empirical results captured.")

        if not analyses:
            gaps.append("Incomplete evidence: No statistical analyses computed.")

        if critique is not None:
            critical_findings = [
                f.description for f in critique.findings if f.severity == CritiqueSeverity.CRITICAL
            ]
            if critical_findings:
                gaps.append(
                    f"Unresolved critical methodological findings: {'; '.join(critical_findings[:2])}"
                )

        return gaps


__all__ = ["DecisionEngine"]
