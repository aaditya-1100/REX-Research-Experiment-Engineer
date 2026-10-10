"""Multi-Iteration Critique Feedback & Experiment Refinement Benchmark (Track A Sec 14-16).

Benchmarks feedback loop closure:
- Actionable critique findings passed to experiment designer
- Prior experiment specification forwarded for iterative refinement
- Autonomous research loop executing multi-iteration refinement under REFINE decisions
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from rex.agents.experiment_designer import ExperimentDesignerAgent
from rex.controller.autonomous_loop import AutonomousLoopConfig, AutonomousResearchLoop
from rex.controller.decision_engine import DecisionEngine
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    CritiqueCategory,
    CritiqueFinding,
    CritiqueSeverity,
    DecisionType,
    ExpectedDirection,
    ExperimentSpecification,
    Hypothesis,
    HypothesisStatus,
    ResearchContext,
    ResearchCritique,
    ResearchState,
)
from rex.observability.events import ActorType, InMemoryEventSink
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import (
    CritiqueModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
)


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for multi-iteration loop tests."""
    db_file = tmp_path / "test_iteration_refinement.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


class TestCritiqueFeedbackLoopClosure:
    """Benchmark tests validating feedback loop closure from critique to experiment designer."""

    def test_experiment_designer_prompt_includes_prior_critique_and_experiment(self):
        """Verifies that design_experiment includes critique findings and prior spec in prompt and context."""
        designer = ExperimentDesignerAgent()
        context = ResearchContext(
            research_run_id="run_refine_01",
            problem_definition="Optimize learning rate schedule.",
            task_domain="deep_learning",
        )
        hypothesis = Hypothesis(
            research_run_id="run_refine_01",
            statement="Cosine learning rate schedule improves accuracy over constant LR.",
            rationale="Better convergence dynamics.",
            expected_direction=ExpectedDirection.INCREASE,
            falsification_condition="Accuracy delta < 1.0%",
        )

        prior_critique = ResearchCritique(
            research_run_id="run_refine_01",
            iteration=1,
            summary="Experiment lacked baseline control and used only 1 seed.",
            findings=(
                CritiqueFinding(
                    finding_id="f_01",
                    category=CritiqueCategory.METHODOLOGY,
                    severity=CritiqueSeverity.HIGH,
                    description="Single seed fragility: Run only evaluated seed 42.",
                    recommendation="Evaluate at least 3 random seeds.",
                ),
            ),
            recommended_action="refine",
            recommended_action_rationale="Add multi-seed replication.",
        )

        prior_spec = ExperimentSpecification(
            name="initial_exp_v1",
            description="Initial single seed test.",
            method="cosine_lr",
            baseline={"name": "constant_lr", "value": 0.85},
            datasets=({"name": "cifar10", "split": "test"},),
            metrics=({"name": "accuracy", "direction": "maximize"},),
            seeds=(42,),
            repetitions=1,
            success_criteria="acc > 0.85",
            falsification_criteria="acc <= 0.85",
        )

        spec, _exp = designer.design_experiment(
            research_question="How to optimize learning rate?",
            research_context=context,
            hypothesis=hypothesis,
            critique=prior_critique,
            prior_experiment=prior_spec,
        )

        # Inspect recorded request in mock provider
        req = designer.provider.recorded_requests[-1]
        assert req.context["has_prior_critique"] is True
        assert req.context["has_prior_experiment"] is True
        assert "PRIOR CRITIQUE FEEDBACK" in req.user_prompt
        assert "Single seed fragility" in req.user_prompt
        assert "Evaluate at least 3 random seeds." in req.user_prompt
        assert "initial_exp_v1" in req.user_prompt
        assert spec.name

    def test_autonomous_loop_step_design_forwards_prior_critique(
        self, session_factory: sessionmaker
    ):
        """Verifies _step_design retrieves latest CritiqueModel and forwards it to designer_agent."""
        run_id = "run_loop_refine_02"
        sink = InMemoryEventSink()

        with get_db_session(session_factory) as session:
            run = create_research_run(session, "Refinement loop test")
            run_id = run.id
            run_model = session.get(ResearchRunModel, run_id)
            if run_model:
                run_model.status = ResearchState.DESIGN.value

            hyp = HypothesisModel(
                id="hyp_02",
                research_run_id=run_id,
                statement="Adaptive batch size improves convergence speed.",
                status=HypothesisStatus.ACTIVE.value,
                falsification_condition="Speed delta < 1.05x",
            )
            # Create prior critique in database
            critique_model = CritiqueModel(
                id="crt_01",
                research_run_id=run_id,
                iteration=1,
                summary="Initial experiment had missing baseline.",
                findings_json=[
                    {
                        "finding_type": "BASELINE",
                        "severity": "critical",
                        "title": "Missing baseline reference",
                        "description": "Missing baseline reference: Baseline was empty.",
                        "recommendation": "Declare constant batch size baseline.",
                    }
                ],
                recommended_action="refine",
                recommended_action_rationale="Remediate missing baseline.",
                created_by="critic",
            )
            # Prior experiment
            prior_exp = ExperimentModel(
                id="exp_prior_01",
                research_run_id=run_id,
                objective="Initial test",
                specification_json={
                    "name": "prior_exp_v1",
                    "description": "v1",
                    "method": "adaptive_batch",
                    "baseline": {},
                    "datasets": [{"name": "cifar10", "split": "test"}],
                    "metrics": [{"name": "accuracy", "direction": "maximize"}],
                    "seeds": [42],
                    "repetitions": 1,
                    "success_criteria": "acc > 0.8",
                    "falsification_criteria": "acc <= 0.8",
                },
            )

            session.add_all([hyp, critique_model, prior_exp])
            session.commit()

        loop = AutonomousResearchLoop(
            session_factory=session_factory,
            event_sink=sink,
            config=AutonomousLoopConfig(max_iterations=3),
        )

        # Call _step_design
        exp_id = loop._step_design(
            research_run_id=run_id,
            context=None,
            actor=ActorType.RESEARCH_AGENT,
        )

        assert exp_id is not None
        req = loop.designer_agent.provider.recorded_requests[-1]
        assert req.context["has_prior_critique"] is True
        assert req.context["has_prior_experiment"] is True
        assert "Missing baseline reference" in req.user_prompt


class TestDecisionEngineTransitionsAndRefinement:
    """Benchmark tests evaluating decision engine actions across iterative cycles."""

    def test_decision_engine_recommends_refine_on_high_severity_critique(
        self, session_factory: sessionmaker
    ):
        """When critique identifies critical methodology flaws, decision engine issues REFINE."""
        engine = DecisionEngine()
        with get_db_session(session_factory) as session:
            run = create_research_run(session, "Refinement test")
            run_id = run.id
            critique = ResearchCritique(
                research_run_id=run_id,
                iteration=1,
                summary="Flaws in control arms detected.",
                findings=(
                    CritiqueFinding(
                        finding_id="f_02",
                        category=CritiqueCategory.METHODOLOGY,
                        severity=CritiqueSeverity.HIGH,
                        description="Confounded variables: Changed lr and batch size together.",
                        recommendation="Perform isolated ablations.",
                    ),
                ),
                recommended_action="refine",
                recommended_action_rationale="Ablation studies needed.",
            )
            session.add(critique.to_persistence())
            session.flush()
            decision = engine.evaluate_decision(
                research_run_id=run_id,
                session=session,
                critique=critique,
                proposed_action=DecisionType.REFINE,
                iteration=1,
                actor=ActorType.DECISION_ENGINE,
            )

        assert decision.action == DecisionType.REFINE
        assert "refine" in decision.action.value.lower()
