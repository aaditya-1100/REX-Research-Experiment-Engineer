"""REX Hypothesis Generation Agent (REX-014).

Generates rigorous, falsifiable scientific hypotheses from a validated ResearchContext,
enforcing explicit expected effect directions, measurable outcomes, baselines, and falsification conditions.
"""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from rex.agents.base import BaseAgent
from rex.controller.hypotheses import create_hypothesis
from rex.domain.models import (
    ExpectedDirection,
    Hypothesis,
    ResearchContext,
)
from rex.llm.models import LLMRequest
from rex.observability.events import ActorType


class SingleHypothesisProposal(BaseModel):
    """Schema for an individual proposed scientific hypothesis."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(
        description="Clear, testable scientific claim stating the expected relationship"
    )
    rationale: str = Field(description="Underlying theoretical mechanism or empirical rationale")
    expected_direction: ExpectedDirection = Field(
        default=ExpectedDirection.INCREASE,
        description="Measurable direction of effect: increase, decrease, no_change, non_zero, other",
    )
    falsification_condition: str = Field(
        description="Concrete, quantitative condition that conclusively falsifies or refutes this hypothesis"
    )


class HypothesesListProposal(BaseModel):
    """Container schema for generating multiple hypotheses."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[SingleHypothesisProposal] = Field(
        min_length=1, description="List of proposed scientific hypotheses"
    )


class HypothesisAgent(BaseAgent):
    """Reasoning agent responsible for proposing testable, falsifiable scientific hypotheses."""

    def generate_hypotheses(
        self,
        research_context: ResearchContext,
        count: int = 1,
        session: Session | None = None,
        actor: ActorType = ActorType.RESEARCH_AGENT,
    ) -> list[Hypothesis]:
        """Generate one or more falsifiable hypotheses based on a structured ResearchContext.

        If a database session is provided, persists hypotheses via the controller and records events.
        """
        if count < 1:
            raise ValueError("Hypothesis count must be at least 1.")

        system_prompt = (
            "You are an expert AI/ML research scientist acting as the REX Hypothesis Generator.\n"
            "Your task is to formulate scientifically rigorous, testable hypotheses based on the provided ResearchContext.\n\n"
            "MANDATORY SCIENTIFIC REQUIREMENTS:\n"
            "1. Falsifiability: Every hypothesis MUST have an explicit, quantitative falsification condition.\n"
            "2. Measurable Outcomes: Must reference concrete observable metrics.\n"
            "3. Explicit Comparison: Must compare against an identifiable baseline or control condition.\n"
            "4. Plausible Mechanism: Must explain the causal mechanism in the rationale.\n"
            "5. Avoid Tautologies: Purely definitional, trivial, or untestable subjective claims are forbidden.\n"
            f"Generate exactly {count} distinct, high-quality candidate hypothesis/hypotheses."
        )

        user_prompt = (
            f"### [RESEARCH PROBLEM DEFINITION]\n"
            f"{research_context.problem_definition}\n\n"
            f"### [DOMAIN & METHODOLOGICAL APPROACHES]\n"
            f"Domain: {research_context.task_domain}\n"
            f"Methods: {', '.join(research_context.methodological_approaches)}\n"
            f"Likely Baselines: {', '.join(research_context.likely_baselines)}\n"
            f"Measurable Outcomes: {', '.join(research_context.measurable_outcomes)}\n"
            f"Important Assumptions: {', '.join(research_context.important_assumptions)}\n"
            f"Unresolved Questions: {', '.join(research_context.unresolved_questions)}\n\n"
            f"Formulate {count} testable scientific hypothesis/hypotheses matching the JSON schema."
        )

        request = LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            research_run_id=research_context.research_run_id,
            action_name="hypothesis_generation",
            context={"count": count},
        )

        proposal_list, _response = self._generate_structured(
            request=request,
            response_model=HypothesesListProposal,
            session=session,
            actor=actor,
        )

        generated_hypotheses: list[Hypothesis] = []

        for prop in proposal_list.hypotheses[:count]:
            if session is not None:
                # Persist via authoritative controller
                domain_hyp = create_hypothesis(
                    session=session,
                    research_run_id=research_context.research_run_id,
                    statement=prop.statement,
                    rationale=prop.rationale,
                    expected_direction=prop.expected_direction,
                    falsification_condition=prop.falsification_condition,
                    actor=actor,
                    event_sink=self.event_sink,
                )
                generated_hypotheses.append(domain_hyp)
            else:
                # Unpersisted domain entity
                domain_hyp = Hypothesis(
                    research_run_id=research_context.research_run_id,
                    statement=prop.statement,
                    rationale=prop.rationale,
                    expected_direction=prop.expected_direction,
                    falsification_condition=prop.falsification_condition,
                )
                generated_hypotheses.append(domain_hyp)

        return generated_hypotheses
