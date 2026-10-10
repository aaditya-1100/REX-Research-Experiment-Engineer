"""REX Experiment Designer Agent (REX-015).

Translates a scientific hypothesis and research context into an authoritative,
strongly typed ExperimentSpecification with explicit controls, baselines, datasets, and metrics.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from rex.agents.base import BaseAgent
from rex.controller.experiments import create_experiment
from rex.domain.models import (
    DatasetSpec,
    Experiment,
    ExperimentSpecification,
    Hypothesis,
    MetricSpec,
    ResearchContext,
    ResearchCritique,
)
from rex.llm.models import LLMRequest
from rex.observability.events import ActorType


class ExperimentDesignProposal(BaseModel):
    """Schema representing an LLM-proposed experiment design."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Short descriptive title or tag for this experimental design")
    description: str = Field(
        description="Detailed narrative explaining experimental intent and setup"
    )
    method: str = Field(description="Algorithmic approach, model architecture, or technique tested")
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Independent experimental variables varied between configurations",
    )
    controls: dict[str, Any] = Field(
        default_factory=dict,
        description="Controlled parameters kept strictly invariant across runs",
    )
    baseline: dict[str, Any] = Field(
        default_factory=dict,
        description="Baseline reference configuration or control benchmark",
    )
    datasets: list[dict[str, Any]] = Field(
        min_length=1, description="List of dataset specifications"
    )
    metrics: list[dict[str, Any]] = Field(
        min_length=1, description="Evaluation metrics with explicit names and directions"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Hyperparameters and model configurations"
    )
    seeds: list[int] = Field(default=[42], description="List of random replication seeds")
    repetitions: int = Field(default=1, ge=1, description="Number of repetitions per fold/seed")
    analysis_methods: list[str] = Field(
        default_factory=list, description="Statistical tests or evaluation procedures"
    )
    success_criteria: str = Field(
        description="Quantitative condition determining experimental validation"
    )
    falsification_criteria: str = Field(
        description="Quantitative condition demonstrating experimental failure or refutation"
    )


class ExperimentDesignerAgent(BaseAgent):
    """Reasoning agent responsible for translating hypotheses into executable experiment designs."""

    def design_experiment(
        self,
        research_question: str,
        research_context: ResearchContext,
        hypothesis: Hypothesis,
        session: Session | None = None,
        actor: ActorType = ActorType.RESEARCH_AGENT,
        critique: ResearchCritique | None = None,
        prior_experiment: ExperimentSpecification | None = None,
    ) -> tuple[ExperimentSpecification, Experiment | None]:
        """Convert a hypothesis and research context into an ExperimentSpecification.

        If a database session is provided, persists the Experiment via create_experiment()
        and records lifecycle events. Returns (specification, experiment_entity_or_None).
        Incorporates prior critique findings and prior experiment specs when provided to close
        the iterative refinement loop.
        """
        # 1. Enforce cross-run hypothesis integrity
        if hypothesis.research_run_id != research_context.research_run_id:
            raise ValueError(
                f"Hypothesis belongs to run '{hypothesis.research_run_id}', which does not match "
                f"research context run '{research_context.research_run_id}'."
            )

        # 2. Build design prompt
        system_prompt = (
            "You are an expert AI/ML research scientist acting as the REX Experiment Designer.\n"
            "Your task is to design a rigorous, reproducible computational experiment to test the given hypothesis.\n\n"
            "MANDATORY DESIGN INVARIANTS:\n"
            "1. Baseline: Must declare an explicit baseline or control benchmark.\n"
            "2. Metrics: Every metric must have an explicit name and optimization direction (maximize/minimize).\n"
            "3. Datasets: Must specify concrete dataset names and splits.\n"
            "4. Repetition & Seeds: Must specify a list of integer random seeds (e.g. [42, 123]) and repetitions >= 1.\n"
            "5. Success Criteria: Must define quantitative, unambiguous criteria for confirming the hypothesis.\n"
            "6. Falsification Criteria: Must define quantitative criteria for rejecting the hypothesis.\n"
            "7. Critique Remediation: If prior critique findings are provided, the new experiment MUST explicitly remediate them.\n"
            "8. No arbitrary executable script code: Output must only specify experimental configuration."
        )

        user_prompt = (
            f"### [RESEARCH QUESTION]\n{research_question.strip()}\n\n"
            f"### [RESEARCH CONTEXT]\n"
            f"Domain: {research_context.task_domain}\n"
            f"Likely Baselines: {', '.join(research_context.likely_baselines)}\n"
            f"Measurable Outcomes: {', '.join(research_context.measurable_outcomes)}\n"
            f"Design Considerations: {', '.join(research_context.experiment_considerations)}\n\n"
            f"### [TARGET HYPOTHESIS]\n"
            f"Statement: {hypothesis.statement}\n"
            f"Rationale: {hypothesis.rationale}\n"
            f"Expected Direction: {hypothesis.expected_direction.value}\n"
            f"Falsification Condition: {hypothesis.falsification_condition}\n"
        )

        if critique is not None:
            user_prompt += (
                f"\n### [PRIOR CRITIQUE FEEDBACK & REFINEMENT GOALS]\n"
                f"Recommended Action: {critique.recommended_action}\n"
                f"Rationale: {critique.recommended_action_rationale}\n"
            )
            if critique.methodological_concerns:
                user_prompt += (
                    f"Methodological Concerns: {', '.join(critique.methodological_concerns)}\n"
                )
            if critique.weaknesses:
                user_prompt += f"Weaknesses: {', '.join(critique.weaknesses)}\n"
            if critique.findings:
                user_prompt += "Specific Findings to Remediate:\n"
                for f in critique.findings:
                    sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
                    label = getattr(f, "title", None) or getattr(f, "category", "")
                    label_str = label.value if hasattr(label, "value") else str(label)
                    user_prompt += (
                        f"- [{sev}] {label_str}: {f.description} (Fix: {f.recommendation})\n"
                    )

        if prior_experiment is not None:
            metric_names = [
                m.name
                if hasattr(m, "name")
                else (m.get("name", str(m)) if isinstance(m, dict) else str(m))
                for m in prior_experiment.metrics
            ]
            user_prompt += (
                f"\n### [PRIOR EXPERIMENT SPECIFICATION TO REFINE]\n"
                f"Name: {prior_experiment.name}\n"
                f"Method: {prior_experiment.method}\n"
                f"Baseline: {prior_experiment.baseline}\n"
                f"Variables: {prior_experiment.variables}\n"
                f"Controls: {prior_experiment.controls}\n"
                f"Seeds: {list(prior_experiment.seeds)}\n"
                f"Metrics: {metric_names}\n"
            )

        user_prompt += "\nProduce an exhaustive, structured experiment design conforming strictly to the JSON schema."

        request = LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            research_run_id=research_context.research_run_id,
            action_name="experiment_design",
            context={
                "hypothesis_id": hypothesis.id,
                "research_question": research_question,
                "has_prior_critique": critique is not None,
                "has_prior_experiment": prior_experiment is not None,
            },
        )

        proposal, _response = self._generate_structured(
            request=request,
            response_model=ExperimentDesignProposal,
            session=session,
            actor=actor,
        )

        # 3. Construct domain Datasets & Metrics
        datasets: list[DatasetSpec] = []
        for d in proposal.datasets:
            datasets.append(DatasetSpec.from_dict(d))

        metrics: list[MetricSpec] = []
        for m in proposal.metrics:
            metrics.append(MetricSpec.from_dict(m))

        seeds = tuple(int(s) for s in proposal.seeds) if proposal.seeds else (42,)

        # 4. Build domain ExperimentSpecification
        spec = ExperimentSpecification(
            name=proposal.name.strip(),
            description=proposal.description.strip(),
            method=proposal.method.strip(),
            variables=proposal.variables,
            controls=proposal.controls,
            baseline=proposal.baseline,
            datasets=tuple(datasets),
            metrics=tuple(metrics),
            parameters=proposal.parameters,
            seeds=seeds,
            repetitions=max(1, proposal.repetitions),
            analysis_methods=tuple(str(a).strip() for a in proposal.analysis_methods),
            success_criteria=proposal.success_criteria.strip(),
            falsification_criteria=proposal.falsification_criteria.strip(),
        )

        # 5. Persist via controller if database session is provided
        domain_experiment: Experiment | None = None
        if session is not None:
            domain_experiment = create_experiment(
                session=session,
                research_run_id=research_context.research_run_id,
                objective=f"Test hypothesis: {hypothesis.statement}",
                specification=spec,
                hypothesis_id=hypothesis.id,
                actor=actor,
                event_sink=self.event_sink,
            )

        return spec, domain_experiment
