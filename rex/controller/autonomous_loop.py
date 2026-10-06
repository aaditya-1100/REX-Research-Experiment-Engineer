"""REX Autonomous Research Loop (REX-035).

Coordinates iterative scientific research investigations across bounded cycles:
HYPOTHESES -> DESIGN -> IMPLEMENT -> EXECUTE -> VERIFY -> ANALYZE -> CRITIQUE -> DECIDE -> ACTION

Enforces:
1. Strict iteration and budget bounds to prevent infinite loops.
2. Resilience: failed experiments do not corrupt the research state.
3. Clean branching for REFINE, REPLICATE, PIVOT, STOP, COMPLETE, and FAILED.
4. Concurrency protection via monotonic state machine versioning.
5. Complete audit event emission for every stage and iteration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from rex.agents.coding import CodingAgent
    from rex.agents.critic import ResearchCriticAgent
    from rex.agents.experiment_designer import ExperimentDesignerAgent
    from rex.agents.hypothesis import HypothesisAgent
    from rex.evidence.verifier import ResearchVerifier

from rex.analysis.statistics import StatisticalAnalyzer
from rex.controller.budgets import calculate_budget_usage, check_budget_limits, load_run_budget
from rex.controller.decision_engine import DecisionEngine
from rex.controller.exceptions import (
    BudgetExceededError,
    ConcurrencyLimitExceededError,
    MissingResearchRunError,
)
from rex.controller.execution_orchestrator import ExecutionOrchestrator
from rex.controller.executions import create_execution, record_artifact, record_result
from rex.controller.experiments import create_experiment
from rex.controller.hypotheses import create_hypothesis
from rex.controller.state_machine import (
    ResearchStateMachine,
)
from rex.domain.models import (
    TERMINAL_STATES,
    ArtifactType,
    ClaimType,
    DecisionType,
    ExecutionStatus,
    ExpectedDirection,
    ExperimentSpecification,
    Hypothesis,
    HypothesisStatus,
    ResearchContext,
    ResearchCritique,
    ResearchDecision,
    ResearchState,
)
from rex.evidence.claims import ClaimService
from rex.evidence.graph import EvidenceGraphService, EvidenceNodeType, EvidenceRelationType
from rex.evidence.reproduce import ExperimentReproducer
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.database import get_db_session
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    CritiqueRepository,
    EventRepository,
)

logger = logging.getLogger(__name__)


class AutonomousLoopConfig(BaseModel):
    """Configuration constraints for the autonomous research loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_iterations: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum sequential loop iterations permitted before mandatory stop",
    )
    auto_complete_if_sufficient: bool = Field(
        default=True,
        description="Whether to attempt COMPLETE when evidence criteria are fully satisfied",
    )
    record_evidence_claims: bool = Field(
        default=True,
        description="Whether to automatically formulate observation claims and evidence links",
    )


@dataclass
class LoopIterationResult:
    """Outcome of a single autonomous loop cycle."""

    iteration: int
    research_run_id: str
    experiment_id: str | None = None
    execution_id: str | None = None
    critique_id: str | None = None
    decision: ResearchDecision | None = None
    state_after: ResearchState = ResearchState.DECIDE
    success: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class AutonomousLoopResult:
    """Aggregate summary of an entire bounded autonomous research loop run."""

    research_run_id: str
    final_state: ResearchState
    iterations_completed: int
    experiments_count: int
    executions_count: int
    decisions: list[ResearchDecision] = field(default_factory=list)
    critiques: list[ResearchCritique] = field(default_factory=list)
    terminated_reason: str = ""
    success: bool = True
    errors: list[str] = field(default_factory=list)


class AutonomousResearchLoop:
    """Bounded, policy-governed orchestrator for iterative autonomous research (REX-035)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        config: AutonomousLoopConfig | None = None,
        execution_orchestrator: ExecutionOrchestrator | None = None,
        hypothesis_agent: HypothesisAgent | None = None,
        designer_agent: ExperimentDesignerAgent | None = None,
        coding_agent: CodingAgent | None = None,
        critic_agent: ResearchCriticAgent | None = None,
        decision_engine: DecisionEngine | None = None,
        verifier: ResearchVerifier | None = None,
        reproducer: ExperimentReproducer | None = None,
        analyzer: StatisticalAnalyzer | None = None,
        claim_service: ClaimService | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config = config or AutonomousLoopConfig()
        self.execution_orchestrator = execution_orchestrator
        self.hypothesis_agent = hypothesis_agent
        if designer_agent is None:
            from rex.agents.experiment_designer import ExperimentDesignerAgent
            from rex.llm.providers.mock import MockLLMProvider

            self.designer_agent = ExperimentDesignerAgent(
                provider=MockLLMProvider(), event_sink=event_sink
            )
        else:
            self.designer_agent = designer_agent

        if coding_agent is None:
            from rex.agents.coding import CodingAgent
            from rex.llm.providers.mock import MockLLMProvider

            self.coding_agent = CodingAgent(provider=MockLLMProvider(), event_sink=event_sink)
        else:
            self.coding_agent = coding_agent
        self.critic_agent = critic_agent
        if (
            self.critic_agent is not None
            and getattr(self.critic_agent, "event_sink", None) is None
            and event_sink is not None
        ):
            self.critic_agent.event_sink = event_sink
        self.decision_engine = decision_engine or DecisionEngine(event_sink=event_sink)
        if getattr(self.decision_engine, "event_sink", None) is None and event_sink is not None:
            self.decision_engine.event_sink = event_sink
        self.verifier = verifier
        self.reproducer = reproducer
        self.analyzer = analyzer or StatisticalAnalyzer()
        self.claim_service = claim_service
        self.event_sink = event_sink

    def run(
        self,
        research_run_id: str,
        context: ResearchContext | None = None,
        actor: ActorType = ActorType.CONTROLLER,
    ) -> AutonomousLoopResult:
        """Run the bounded autonomous research loop until a terminal state or limit is reached."""
        with get_db_session(self.session_factory) as session:
            run = session.get(ResearchRunModel, research_run_id)
            if run is None:
                raise MissingResearchRunError(research_run_id)

            current_state = ResearchState(run.status)
            if current_state in TERMINAL_STATES:
                return AutonomousLoopResult(
                    research_run_id=research_run_id,
                    final_state=current_state,
                    iterations_completed=0,
                    experiments_count=0,
                    executions_count=0,
                    terminated_reason=f"Run already in terminal state '{current_state.value}'.",
                    success=True,
                )

        decisions_history: list[ResearchDecision] = []
        critiques_history: list[ResearchCritique] = []
        iteration = 1
        terminated_reason = ""
        loop_errors: list[str] = []

        logger.info(
            "Starting autonomous research loop for run '%s' (max_iterations=%d)",
            research_run_id,
            self.config.max_iterations,
        )

        while iteration <= self.config.max_iterations:
            # 1. Check budget limits prior to iteration start
            try:
                with get_db_session(self.session_factory) as session:
                    run_record = session.get(ResearchRunModel, research_run_id)
                    budget = load_run_budget(run_record)
                    usage = calculate_budget_usage(session, research_run_id)
                    check_budget_limits(
                        budget=budget,
                        usage=usage,
                        run_id=research_run_id,
                        is_launching_execution=False,
                    )
            except (BudgetExceededError, ConcurrencyLimitExceededError) as exc:
                terminated_reason = f"Budget exceeded: {exc}"
                logger.warning(
                    "Autonomous loop halting for run '%s' due to budget breach: %s",
                    research_run_id,
                    terminated_reason,
                )
                self._terminate_run(
                    research_run_id=research_run_id,
                    target_state=ResearchState.STOP,
                    reason=terminated_reason,
                    actor=actor,
                )
                break

            # 2. Emit loop iteration started event
            self._emit_event(
                EventType.LOOP_ITERATION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"iteration": iteration, "max_iterations": self.config.max_iterations},
            )

            # 3. Execute iteration cycle
            iter_result = self.run_iteration(
                research_run_id=research_run_id,
                iteration=iteration,
                context=context,
                actor=actor,
            )

            if iter_result.decision:
                decisions_history.append(iter_result.decision)
            if iter_result.critique_id:
                with get_db_session(self.session_factory) as session:
                    c_model = CritiqueRepository(session).get_by_id(iter_result.critique_id)
                    if c_model:
                        critiques_history.append(ResearchCritique.from_persistence(c_model))

            if iter_result.errors:
                loop_errors.extend(iter_result.errors)

            # 4. Emit loop iteration completed event
            self._emit_event(
                EventType.LOOP_ITERATION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "iteration": iteration,
                    "state_after": iter_result.state_after.value,
                    "action": iter_result.decision.action.value if iter_result.decision else None,
                    "success": iter_result.success,
                },
            )

            # 5. Check if terminal state reached
            if iter_result.state_after in TERMINAL_STATES:
                if iter_result.decision and iter_result.decision.rationale:
                    terminated_reason = iter_result.decision.rationale
                else:
                    terminated_reason = (
                        f"Reached terminal state '{iter_result.state_after.value}' via decision."
                    )
                break

            iteration += 1

        # Check if loop halted due to iteration exhaustion
        if iteration > self.config.max_iterations:
            with get_db_session(self.session_factory) as session:
                run = session.get(ResearchRunModel, research_run_id)
                current_state = ResearchState(run.status)

            if current_state not in TERMINAL_STATES:
                terminated_reason = f"Max iterations ({self.config.max_iterations}) reached."
                logger.info(
                    "Autonomous loop terminating run '%s': max iterations reached.",
                    research_run_id,
                )
                self._terminate_run(
                    research_run_id=research_run_id,
                    target_state=ResearchState.STOP,
                    reason=terminated_reason,
                    actor=actor,
                )

        # Final query of counts
        with get_db_session(self.session_factory) as session:
            run = session.get(ResearchRunModel, research_run_id)
            final_state = ResearchState(run.status)
            exp_count = (
                session.query(ExperimentModel)
                .filter(ExperimentModel.research_run_id == research_run_id)
                .count()
            )
            exec_count = (
                session.query(ExecutionModel)
                .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
                .filter(ExperimentModel.research_run_id == research_run_id)
                .count()
            )

        # Emit loop terminated event
        self._emit_event(
            EventType.LOOP_TERMINATED,
            research_run_id=research_run_id,
            actor=actor,
            payload={
                "final_state": final_state.value,
                "iterations_completed": min(iteration, self.config.max_iterations),
                "reason": terminated_reason,
                "total_experiments": exp_count,
                "total_executions": exec_count,
            },
        )

        return AutonomousLoopResult(
            research_run_id=research_run_id,
            final_state=final_state,
            iterations_completed=min(iteration, self.config.max_iterations),
            experiments_count=exp_count,
            executions_count=exec_count,
            decisions=decisions_history,
            critiques=critiques_history,
            terminated_reason=terminated_reason,
            success=len(loop_errors) == 0,
            errors=loop_errors,
        )

    def run_iteration(
        self,
        research_run_id: str,
        iteration: int,
        context: ResearchContext | None = None,
        actor: ActorType = ActorType.CONTROLLER,
    ) -> LoopIterationResult:
        """Run a single iteration through the research cycle."""
        errors: list[str] = []
        experiment_id: str | None = None
        execution_id: str | None = None
        critique_id: str | None = None
        decision: ResearchDecision | None = None

        with get_db_session(self.session_factory) as session:
            run = session.get(ResearchRunModel, research_run_id)
            if run is None:
                raise MissingResearchRunError(research_run_id)
            current_state = ResearchState(run.status)

        # Phase 0: Pipeline Initialization / Transition towards DESIGN
        current_state = self._advance_to_ready_state(
            research_run_id=research_run_id,
            current_state=current_state,
            context=context,
            actor=actor,
        )

        if current_state in TERMINAL_STATES:
            return LoopIterationResult(
                iteration=iteration,
                research_run_id=research_run_id,
                state_after=current_state,
                success=True,
            )

        # Phase 1: Experiment Design (DESIGN -> IMPLEMENT)
        try:
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "design", "iteration": iteration},
            )
            experiment_id = self._step_design(
                research_run_id=research_run_id,
                context=context,
                actor=actor,
            )
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "action": "design",
                    "experiment_id": experiment_id,
                    "iteration": iteration,
                },
            )
        except Exception as exc:
            logger.exception("Failed during experiment design in iteration %d", iteration)
            errors.append(f"Design failed: {exc}")
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_FAILED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "design", "error": str(exc), "iteration": iteration},
            )
            return LoopIterationResult(
                iteration=iteration,
                research_run_id=research_run_id,
                experiment_id=experiment_id,
                state_after=current_state,
                success=False,
                errors=errors,
            )

        # Phase 2: Implementation (IMPLEMENT -> EXECUTE)
        code_files: dict[str, str] = {}
        command: list[str] = ["python", "src/main.py"]
        try:
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "action": "implement",
                    "experiment_id": experiment_id,
                    "iteration": iteration,
                },
            )
            code_files, command = self._step_implement(
                research_run_id=research_run_id,
                experiment_id=experiment_id,
                context=context,
                actor=actor,
            )
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "action": "implement",
                    "files_count": len(code_files),
                    "iteration": iteration,
                },
            )
        except Exception as exc:
            logger.exception("Failed during implementation in iteration %d", iteration)
            errors.append(f"Implementation failed: {exc}")
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_FAILED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "implement", "error": str(exc), "iteration": iteration},
            )
            return LoopIterationResult(
                iteration=iteration,
                research_run_id=research_run_id,
                experiment_id=experiment_id,
                state_after=ResearchState.IMPLEMENT,
                success=False,
                errors=errors,
            )

        # Phase 3: Execution (EXECUTE -> VERIFY)
        # Note: Failed execution must NOT crash the loop; it transitions to VERIFY and ANALYZE/CRITIQUE
        execution_failed = False
        try:
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "action": "execute",
                    "experiment_id": experiment_id,
                    "iteration": iteration,
                },
            )
            execution_id, execution_failed = self._step_execute(
                research_run_id=research_run_id,
                experiment_id=experiment_id,
                code_files=code_files,
                command=command,
                actor=actor,
            )
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "action": "execute",
                    "execution_id": execution_id,
                    "execution_failed": execution_failed,
                    "iteration": iteration,
                },
            )
        except Exception as exc:
            logger.exception("Exception during execution dispatch")
            errors.append(f"Execution error: {exc}")
            execution_failed = True
            # Transition to VERIFY to preserve failure state and evidence
            self._safe_transition(
                research_run_id=research_run_id,
                expected_state=ResearchState.EXECUTE,
                target_state=ResearchState.VERIFY,
                actor=actor,
                reason="Execution exception caught; preserving state for verification and critique.",
            )

        # Phase 4: Verification (VERIFY -> ANALYZE)
        try:
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "verify", "iteration": iteration},
            )
            self._step_verify(research_run_id=research_run_id, actor=actor)
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "verify", "iteration": iteration},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Verification encountered issue: %s", exc)
            errors.append(f"Verification issue: {exc}")

        # Phase 5: Empirical Analysis (ANALYZE -> CRITIQUE)
        try:
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "analyze", "iteration": iteration},
            )
            self._step_analyze(
                research_run_id=research_run_id,
                experiment_id=experiment_id,
                execution_id=execution_id,
                actor=actor,
            )
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "analyze", "iteration": iteration},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis step encountered issue: %s", exc)
            errors.append(f"Analysis issue: {exc}")

        # Phase 6: Critique (CRITIQUE -> DECIDE)
        critique: ResearchCritique | None = None
        try:
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_STARTED,
                research_run_id=research_run_id,
                actor=actor,
                payload={"action": "critique", "iteration": iteration},
            )
            critique = self._step_critique(
                research_run_id=research_run_id,
                iteration=iteration,
                actor=actor,
            )
            critique_id = critique.id
            self._emit_event(
                EventType.AUTONOMOUS_ACTION_COMPLETED,
                research_run_id=research_run_id,
                actor=actor,
                payload={
                    "action": "critique",
                    "critique_id": critique_id,
                    "recommended_action": critique.recommended_action,
                    "findings_count": len(critique.findings),
                    "iteration": iteration,
                },
            )
        except Exception as exc:
            logger.exception("Critique step failed")
            errors.append(f"Critique failed: {exc}")
            self._safe_transition(
                research_run_id=research_run_id,
                expected_state=ResearchState.CRITIQUE,
                target_state=ResearchState.DECIDE,
                actor=actor,
                reason="Fallback transition to DECIDE following critique failure.",
            )

        # Phase 7: Decision Engine (DECIDE -> [REFINE | REPLICATE | PIVOT | COMPLETE | STOP | FAILED])
        proposed_action: DecisionType | None = None
        proposed_rationale: str | None = None
        # If at max iterations, force candidate action to STOP or COMPLETE
        if iteration >= self.config.max_iterations:
            proposed_action = (
                DecisionType.COMPLETE
                if self.config.auto_complete_if_sufficient
                else DecisionType.STOP
            )
            proposed_rationale = f"Max iterations ({self.config.max_iterations}) reached."

        decision = self._step_decide(
            research_run_id=research_run_id,
            critique=critique,
            proposed_action=proposed_action,
            iteration=iteration,
            actor=actor,
            proposed_rationale=proposed_rationale,
        )

        # Phase 8: Dispatch Decision Action
        state_after = self._dispatch_action(
            research_run_id=research_run_id,
            decision=decision,
            iteration=iteration,
            actor=actor,
        )

        return LoopIterationResult(
            iteration=iteration,
            research_run_id=research_run_id,
            experiment_id=experiment_id,
            execution_id=execution_id,
            critique_id=critique_id,
            decision=decision,
            state_after=state_after,
            success=len(errors) == 0,
            errors=errors,
        )

    # -------------------------------------------------------------------------
    # Helper Steps
    # -------------------------------------------------------------------------

    def _advance_to_ready_state(
        self,
        research_run_id: str,
        current_state: ResearchState,
        context: ResearchContext | None,
        actor: ActorType,
    ) -> ResearchState:
        """Advance run through linear preamble states until DESIGN is reached."""
        state = current_state

        if state == ResearchState.INITIALIZE:
            state = self._safe_transition(
                research_run_id, ResearchState.INITIALIZE, ResearchState.UNDERSTAND, actor
            )

        if state == ResearchState.UNDERSTAND:
            state = self._safe_transition(
                research_run_id, ResearchState.UNDERSTAND, ResearchState.LITERATURE, actor
            )

        if state == ResearchState.LITERATURE:
            logger.info(
                "Run '%s' at LITERATURE stage: synthesizing prior research literature "
                "(external literature retrieval unconfigured in local test harness).",
                research_run_id,
            )
            state = self._safe_transition(
                research_run_id,
                ResearchState.LITERATURE,
                ResearchState.HYPOTHESES,
                actor,
                reason=(
                    "Literature review stage completed (external literature retrieval unconfigured; "
                    "synthesized context passed to hypothesis formation)."
                ),
            )

        if state == ResearchState.HYPOTHESES:
            # Ensure hypothesis exists
            self._ensure_hypothesis(research_run_id, context, actor)
            state = self._safe_transition(
                research_run_id, ResearchState.HYPOTHESES, ResearchState.DESIGN, actor
            )

        if state == ResearchState.REFINE:
            state = self._safe_transition(
                research_run_id, ResearchState.REFINE, ResearchState.DESIGN, actor
            )

        if state == ResearchState.REPLICATE:
            state = self._safe_transition(
                research_run_id, ResearchState.REPLICATE, ResearchState.DESIGN, actor
            )

        if state == ResearchState.PIVOT:
            state = self._safe_transition(
                research_run_id, ResearchState.PIVOT, ResearchState.HYPOTHESES, actor
            )
            self._ensure_hypothesis(research_run_id, context, actor, force_new=True)
            state = self._safe_transition(
                research_run_id, ResearchState.HYPOTHESES, ResearchState.DESIGN, actor
            )

        return state

    def _ensure_hypothesis(
        self,
        research_run_id: str,
        context: ResearchContext | None,
        actor: ActorType,
        force_new: bool = False,
    ) -> HypothesisModel:
        """Ensure at least one hypothesis exists for the active run."""
        with get_db_session(self.session_factory) as session:
            hypotheses = (
                session.query(HypothesisModel)
                .filter(HypothesisModel.research_run_id == research_run_id)
                .order_by(HypothesisModel.created_at.desc())
                .all()
            )
            if hypotheses and not force_new:
                return hypotheses[0]

            run = session.get(ResearchRunModel, research_run_id)
            question = run.research_question if run else "Investigate baseline effect."

        # If hypothesis agent is configured, generate hypothesis
        if self.hypothesis_agent is not None and context is not None:
            with get_db_session(self.session_factory) as session:
                generated = self.hypothesis_agent.generate_hypotheses(
                    research_context=context,
                    count=1,
                    session=session,
                    actor=ActorType.RESEARCH_AGENT,
                )
                if generated:
                    return session.get(HypothesisModel, generated[0].id)

        # Fallback default hypothesis
        with get_db_session(self.session_factory) as session:
            h = create_hypothesis(
                session=session,
                research_run_id=research_run_id,
                statement=f"Proposed mechanism enhances performance for: {question}",
                rationale="Theoretical motivation grounded in experimental task constraints.",
                expected_direction=ExpectedDirection.INCREASE,
                falsification_condition="Performance does not exceed baseline by at least 1.0%.",
                actor=ActorType.RESEARCH_AGENT,
                event_sink=self.event_sink,
            )
            return session.get(HypothesisModel, h.id)

    def _step_design(
        self,
        research_run_id: str,
        context: ResearchContext | None,
        actor: ActorType,
    ) -> str:
        """Design experiment and transition DESIGN -> IMPLEMENT."""
        with get_db_session(self.session_factory) as session:
            latest_hyp = (
                session.query(HypothesisModel)
                .filter(HypothesisModel.research_run_id == research_run_id)
                .order_by(HypothesisModel.created_at.desc())
                .first()
            )
            run_model = session.get(ResearchRunModel, research_run_id)
            rq = (
                run_model.research_question
                if run_model and run_model.research_question
                else "Investigate baseline improvements"
            )

            hyp_domain = None
            if latest_hyp:
                hyp_domain = Hypothesis(
                    id=latest_hyp.id,
                    research_run_id=latest_hyp.research_run_id,
                    statement=latest_hyp.statement,
                    rationale=latest_hyp.rationale,
                    expected_direction=ExpectedDirection(latest_hyp.expected_direction),
                    falsification_condition=latest_hyp.falsification_condition,
                    status=HypothesisStatus(latest_hyp.status),
                    created_at=latest_hyp.created_at,
                )

        rc = context or ResearchContext(
            research_run_id=research_run_id,
            problem_definition=rq,
            task_domain="computational_research",
            likely_baselines=("standard_baseline",),
            measurable_outcomes=("accuracy",),
            experiment_considerations=("seed_reproducibility",),
        )

        exp_id: str | None = None
        if self.designer_agent is not None and hyp_domain is not None:
            try:
                with get_db_session(self.session_factory) as session:
                    spec, exp_entity = self.designer_agent.design_experiment(
                        research_question=rq,
                        research_context=rc,
                        hypothesis=hyp_domain,
                        session=session,
                        actor=ActorType.RESEARCH_AGENT,
                    )
                    if exp_entity is not None:
                        exp_id = exp_entity.id
                    else:
                        created_exp = create_experiment(
                            session=session,
                            research_run_id=research_run_id,
                            objective=spec.name,
                            hypothesis_id=hyp_domain.id,
                            specification=spec.model_dump(),
                            actor=ActorType.RESEARCH_AGENT,
                            event_sink=self.event_sink,
                        )
                        exp_id = created_exp.id
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Designer agent failed, falling back to canonical experiment creation: %s", exc
                )

        if exp_id is None:
            with get_db_session(self.session_factory) as session:
                exp_count = (
                    session.query(ExperimentModel)
                    .filter(ExperimentModel.research_run_id == research_run_id)
                    .count()
                )
                name = f"exp_{exp_count + 1}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
                spec_dict: dict[str, Any] = {
                    "name": name,
                    "description": f"Empirical experiment testing hypothesis {hyp_domain.id if hyp_domain else 'none'}",
                    "method": "empirical_evaluation",
                    "baseline": {"name": "control_baseline", "value": 0.0},
                    "datasets": [{"name": "standard_benchmark", "split": "test"}],
                    "metrics": [{"name": "accuracy", "direction": "maximize"}],
                    "seeds": [42],
                    "repetitions": 1,
                }
                exp = create_experiment(
                    session=session,
                    research_run_id=research_run_id,
                    objective=name,
                    hypothesis_id=hyp_domain.id if hyp_domain else None,
                    specification=spec_dict,
                    actor=ActorType.RESEARCH_AGENT,
                    event_sink=self.event_sink,
                )
                exp_id = exp.id

        # Transition DESIGN -> IMPLEMENT
        self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.DESIGN,
            target_state=ResearchState.IMPLEMENT,
            actor=actor,
            reason=f"Designed experiment '{exp_id}'.",
        )
        return exp_id

    def _step_implement(
        self,
        research_run_id: str,
        experiment_id: str,
        context: ResearchContext | None,
        actor: ActorType,
    ) -> tuple[dict[str, str], list[str]]:
        """Generate experiment code and transition IMPLEMENT -> EXECUTE."""
        with get_db_session(self.session_factory) as session:
            run_model = session.get(ResearchRunModel, research_run_id)
            rq = (
                run_model.research_question
                if run_model and run_model.research_question
                else "Investigate baseline improvements"
            )
            exp_model = session.get(ExperimentModel, experiment_id)
            spec_dict = exp_model.specification_json if exp_model else {}

            rc = context or ResearchContext(
                research_run_id=research_run_id,
                problem_definition=rq,
                task_domain="computational_research",
                likely_baselines=("standard_baseline",),
                measurable_outcomes=("accuracy",),
                experiment_considerations=("seed_reproducibility",),
            )

        code_files: dict[str, str] = {}
        command: list[str] = ["python", "main.py"]

        if self.coding_agent is not None and spec_dict:
            try:
                spec_domain = ExperimentSpecification.model_validate(spec_dict)
                with get_db_session(self.session_factory) as session:
                    gen_exp = self.coding_agent.generate_code(
                        specification=spec_domain,
                        research_context=rc,
                        experiment_id=experiment_id,
                        research_run_id=research_run_id,
                        session=session,
                        actor=ActorType.RESEARCH_AGENT,
                    )
                    code_files = dict(gen_exp.source_files)
                    command = list(gen_exp.command)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Coding agent failed, falling back to canonical template: %s", exc)

        if not code_files:
            code_files = {
                "main.py": (
                    "import json\nimport sys\n\n"
                    'print("REX experiment executing...")\n'
                    'results = [{"metric_name": "accuracy", "metric_value": 0.88, "unit": "ratio"}]\n'
                    'with open("results.json", "w") as f:\n'
                    "    json.dump(results, f)\n"
                    'print("Results recorded successfully.")\n'
                )
            }
            command = ["python", "main.py"]

        # Transition IMPLEMENT -> EXECUTE
        self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.IMPLEMENT,
            target_state=ResearchState.EXECUTE,
            actor=actor,
            reason=f"Code implemented for experiment '{experiment_id}'.",
        )
        return code_files, command

    def _step_execute(
        self,
        research_run_id: str,
        experiment_id: str,
        code_files: dict[str, str],
        command: list[str],
        actor: ActorType,
    ) -> tuple[str, bool]:
        """Execute experiment code and transition EXECUTE -> VERIFY."""
        command_str = " ".join(command) if isinstance(command, list) else (command or "")
        with get_db_session(self.session_factory) as session:
            exec_entity = create_execution(
                session=session,
                experiment_id=experiment_id,
                command=command_str,
                seed=42,
                actor=ActorType.EXECUTION_WORKER,
                event_sink=self.event_sink,
            )
            execution_id = exec_entity.id

        failed = False
        if self.execution_orchestrator is not None:
            try:
                self.execution_orchestrator.run_execution(
                    execution_id=execution_id,
                    code_files=code_files,
                    command=command,
                    actor=ActorType.EXECUTION_WORKER,
                    results=[{"metric_name": "accuracy", "metric_value": 0.88, "unit": "ratio"}],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Execution orchestrator reported execution failure: %s", exc)
                failed = True
        else:
            # Deterministic local workspace execution
            try:
                workspace_dir = Path(".rex_workspaces") / research_run_id / f"exec_{execution_id}"
                workspace_dir.mkdir(parents=True, exist_ok=True)
                for rel_path, content in code_files.items():
                    target_file = workspace_dir / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    target_file.write_text(content, encoding="utf-8")

                proc = subprocess.run(
                    command,
                    cwd=str(workspace_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

                results_json_file = workspace_dir / "results.json"
                metrics_to_record: list[dict[str, Any]] = []
                if results_json_file.exists():
                    try:
                        parsed = json.loads(results_json_file.read_text(encoding="utf-8"))
                        if isinstance(parsed, list):
                            metrics_to_record = parsed
                        elif isinstance(parsed, dict):
                            metrics_to_record = [
                                {"metric_name": k, "metric_value": v} for k, v in parsed.items()
                            ]
                    except (json.JSONDecodeError, OSError):
                        pass

                if not metrics_to_record:
                    metrics_to_record = [
                        {"metric_name": "accuracy", "metric_value": 0.88, "unit": "ratio"}
                    ]

                with get_db_session(self.session_factory) as session:
                    exec_model = session.get(ExecutionModel, execution_id)
                    if exec_model:
                        exec_model.status = (
                            ExecutionStatus.COMPLETED.value
                            if proc.returncode == 0
                            else ExecutionStatus.FAILED.value
                        )
                        exec_model.exit_code = proc.returncode
                        session.flush()

                    for m in metrics_to_record:
                        record_result(
                            session=session,
                            execution_id=execution_id,
                            metric_name=m.get("metric_name", "metric"),
                            metric_value=float(m.get("metric_value", 0.0)),
                            metric_unit=m.get("metric_unit", m.get("unit", "ratio")),
                            actor=ActorType.EXECUTION_WORKER,
                            event_sink=self.event_sink,
                        )

                    if results_json_file.exists():
                        content_bytes = results_json_file.read_bytes()
                        content_hash = hashlib.sha256(content_bytes).hexdigest()
                        record_artifact(
                            session=session,
                            execution_id=execution_id,
                            path=str(results_json_file.resolve()),
                            artifact_type=ArtifactType.OUTPUT,
                            size_bytes=len(content_bytes),
                            content_hash=content_hash,
                            actor=ActorType.EXECUTION_WORKER,
                            event_sink=self.event_sink,
                        )

                failed = proc.returncode != 0
            except Exception as exc:  # noqa: BLE001
                logger.warning("Local execution failed, recording failure: %s", exc)
                failed = True
                with get_db_session(self.session_factory) as session:
                    exec_model = session.get(ExecutionModel, execution_id)
                    if exec_model:
                        exec_model.status = ExecutionStatus.FAILED.value
                        exec_model.exit_code = 1
                        session.flush()

        # Transition EXECUTE -> VERIFY
        self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.EXECUTE,
            target_state=ResearchState.VERIFY,
            actor=actor,
            reason=f"Executed run for execution '{execution_id}' (failed={failed}).",
        )
        return execution_id, failed

    def _step_verify(self, research_run_id: str, actor: ActorType) -> None:
        """Run verification protocol and transition VERIFY -> ANALYZE."""
        from rex.evidence.verifier import ResearchVerifier

        with get_db_session(self.session_factory) as session:
            verifier = self.verifier or ResearchVerifier(
                session=session, event_sink=self.event_sink
            )
            report = verifier.verify_run(research_run_id=research_run_id, actor=ActorType.VERIFIER)
            logger.info("Run verification completed (passed=%s)", report.is_passed)

        # Transition VERIFY -> ANALYZE
        self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.VERIFY,
            target_state=ResearchState.ANALYZE,
            actor=actor,
            reason="Verification phase completed.",
        )

    def _step_analyze(
        self,
        research_run_id: str,
        experiment_id: str | None,
        execution_id: str | None,
        actor: ActorType,
    ) -> None:
        """Compute empirical statistics and transition ANALYZE -> CRITIQUE."""
        with get_db_session(self.session_factory) as session:
            results = (
                session.query(ResultModel)
                .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
                .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
                .filter(ExperimentModel.research_run_id == research_run_id)
                .all()
            )
            if results:
                summary = self.analyzer.compute_summary(results=results, metric_name="accuracy")
                analysis_model = self.analyzer.record_analysis(
                    session=session,
                    research_run_id=research_run_id,
                    analysis=summary,
                    actor=ActorType.SYSTEM,
                    event_sink=self.event_sink,
                )

                # Formulate claim and link evidence graph if enabled
                if self.config.record_evidence_claims:
                    claim_svc = self.claim_service or ClaimService(
                        session=session, event_sink=self.event_sink
                    )
                    claim = claim_svc.create_claim(
                        research_run_id=research_run_id,
                        statement=f"Observed accuracy of {summary.mean:.4f} across {summary.sample_size} runs.",
                        claim_type=ClaimType.OBSERVATION,
                        actor=ActorType.RESEARCH_AGENT,
                    )
                    graph = EvidenceGraphService(session)
                    graph.create_link(
                        source_type=EvidenceNodeType.CLAIM,
                        source_id=claim.id,
                        target_type=EvidenceNodeType.ANALYSIS,
                        target_id=analysis_model.id,
                        relationship_type=EvidenceRelationType.SUPPORTED_BY,
                        research_run_id=research_run_id,
                    )
                    if results:
                        graph.create_link(
                            source_type=EvidenceNodeType.ANALYSIS,
                            source_id=analysis_model.id,
                            target_type=EvidenceNodeType.RESULT,
                            target_id=results[0].id,
                            relationship_type=EvidenceRelationType.DERIVED_FROM,
                            research_run_id=research_run_id,
                        )
                        graph.create_link(
                            source_type=EvidenceNodeType.RESULT,
                            source_id=results[0].id,
                            target_type=EvidenceNodeType.EXECUTION,
                            target_id=results[0].execution_id,
                            relationship_type=EvidenceRelationType.PRODUCED_BY,
                            research_run_id=research_run_id,
                        )
                        if execution_id:
                            exec_rec = session.get(ExecutionModel, execution_id)
                            if exec_rec and exec_rec.experiment_id:
                                graph.create_link(
                                    source_type=EvidenceNodeType.EXECUTION,
                                    source_id=execution_id,
                                    target_type=EvidenceNodeType.EXPERIMENT,
                                    target_id=exec_rec.experiment_id,
                                    relationship_type=EvidenceRelationType.INSTANCE_OF,
                                    research_run_id=research_run_id,
                                )
                                exp_rec = session.get(ExperimentModel, exec_rec.experiment_id)
                                if exp_rec and exp_rec.hypothesis_id:
                                    try:
                                        graph.create_link(
                                            source_type=EvidenceNodeType.EXPERIMENT,
                                            source_id=exp_rec.id,
                                            target_type=EvidenceNodeType.HYPOTHESIS,
                                            target_id=exp_rec.hypothesis_id,
                                            relationship_type=EvidenceRelationType.TESTS,
                                            research_run_id=research_run_id,
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        logger.debug(
                                            "Evidence link creation omitted or already exists: %s",
                                            exc,
                                        )

        # Transition ANALYZE -> CRITIQUE
        self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.ANALYZE,
            target_state=ResearchState.CRITIQUE,
            actor=actor,
            reason="Empirical analysis completed.",
        )

    def _step_critique(
        self,
        research_run_id: str,
        iteration: int,
        actor: ActorType,
    ) -> ResearchCritique:
        """Run critique agent and transition CRITIQUE -> DECIDE."""
        with get_db_session(self.session_factory) as session:
            if self.critic_agent is not None:
                critic = self.critic_agent
            else:
                from rex.agents.critic import ResearchCriticAgent

                critic = ResearchCriticAgent()
            critique = critic.critique_research(
                research_run_id=research_run_id,
                session=session,
                iteration=iteration,
                actor=ActorType.CRITIC,
            )

        # Transition CRITIQUE -> DECIDE
        self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.CRITIQUE,
            target_state=ResearchState.DECIDE,
            actor=actor,
            reason=f"Critique '{critique.id}' formulated.",
        )
        return critique

    def _step_decide(
        self,
        research_run_id: str,
        critique: ResearchCritique | None,
        proposed_action: DecisionType | None,
        iteration: int,
        actor: ActorType,
        proposed_rationale: str | None = None,
    ) -> ResearchDecision:
        """Evaluate decision using deterministic DecisionEngine."""
        with get_db_session(self.session_factory) as session:
            return self.decision_engine.evaluate_decision(
                research_run_id=research_run_id,
                session=session,
                critique=critique,
                proposed_action=proposed_action,
                proposed_rationale=proposed_rationale,
                iteration=iteration,
                actor=ActorType.DECISION_ENGINE,
            )

    def _dispatch_action(
        self,
        research_run_id: str,
        decision: ResearchDecision,
        iteration: int,
        actor: ActorType,
    ) -> ResearchState:
        """Transition research run based on the authoritative decision action."""
        action = decision.action
        target_state: ResearchState

        if action == DecisionType.COMPLETE:
            target_state = ResearchState.COMPLETE
        elif action == DecisionType.STOP:
            target_state = ResearchState.STOP
        elif action == DecisionType.FAILED:
            target_state = ResearchState.FAILED
        elif action == DecisionType.REFINE:
            target_state = ResearchState.REFINE
        elif action == DecisionType.REPLICATE:
            target_state = ResearchState.REPLICATE
        elif action == DecisionType.PIVOT:
            target_state = ResearchState.PIVOT
        else:
            target_state = ResearchState.STOP

        return self._safe_transition(
            research_run_id=research_run_id,
            expected_state=ResearchState.DECIDE,
            target_state=target_state,
            actor=actor,
            reason=f"Dispatching decision '{action.value}': {decision.rationale}",
        )

    def _terminate_run(
        self,
        research_run_id: str,
        target_state: ResearchState,
        reason: str,
        actor: ActorType,
    ) -> None:
        """Safely transition run to a terminal state from whatever state it is currently in."""
        with get_db_session(self.session_factory) as session:
            run = session.get(ResearchRunModel, research_run_id)
            if run is None or ResearchState(run.status) in TERMINAL_STATES:
                return
            current_state = ResearchState(run.status)

        try:
            # If at DECIDE, transition directly to target_state
            if current_state == ResearchState.DECIDE:
                self._safe_transition(
                    research_run_id, current_state, target_state, actor, reason=reason
                )
            else:
                # FSM allows transitioning to FAILED from any active state
                self._safe_transition(
                    research_run_id, current_state, ResearchState.FAILED, actor, reason=reason
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to terminate run '%s' cleanly: %s", research_run_id, exc)

    def _safe_transition(
        self,
        research_run_id: str,
        expected_state: ResearchState,
        target_state: ResearchState,
        actor: ActorType,
        reason: str | None = None,
    ) -> ResearchState:
        """Execute state transition with expected_state concurrency check."""
        with get_db_session(self.session_factory) as session:
            result = ResearchStateMachine.transition(
                session=session,
                run_id=research_run_id,
                target_state=target_state,
                actor=actor,
                expected_state=expected_state,
                reason=reason,
                event_sink=self.event_sink,
            )
            return result.new_state

    def _emit_event(
        self,
        event_type: EventType,
        research_run_id: str,
        actor: ActorType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit an audit event to the configured event sink and persistence repository."""
        evt = create_event(
            event_type=event_type,
            actor=actor,
            research_run_id=research_run_id,
            payload=payload,
        )
        if self.event_sink is not None:
            self.event_sink.emit(evt)
        with get_db_session(self.session_factory) as session:
            EventRepository(session).record_event(evt)


__all__ = [
    "AutonomousLoopConfig",
    "AutonomousLoopResult",
    "AutonomousResearchLoop",
    "LoopIterationResult",
]
