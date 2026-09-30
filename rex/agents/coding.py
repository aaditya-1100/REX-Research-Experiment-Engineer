"""REX Coding Agent (REX-016).

Translates an immutable ExperimentSpecification and ResearchContext into an executable
code package (GeneratedExperiment) with strict AST syntax validation, safe relative workspace
paths, dependency declarations, and deterministic canonical content hashing.

CRITICAL ARCHITECTURAL INVARIANT:
The Coding Agent NEVER executes code or invokes Docker sandboxes directly.
Code generation is strictly decoupled from code execution.
"""

import ast
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from rex.agents.base import BaseAgent
from rex.domain.models import (
    ExperimentSpecification,
    GeneratedExperiment,
    ResearchContext,
)
from rex.execution.models import PROHIBITED_COMMAND_PATTERNS, ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.llm.models import LLMRequest
from rex.observability.events import (
    ActorType,
    EventType,
    create_event,
)
from rex.persistence.repositories import EventRepository

logger = logging.getLogger(__name__)


class GeneratedCodeProposal(BaseModel):
    """Schema representing an LLM-generated experiment code proposal."""

    model_config = ConfigDict(extra="forbid")

    entrypoint: str = Field(
        default="main.py",
        description="Relative entrypoint filename within workspace src/ (e.g. main.py)",
    )
    source_files: dict[str, str] = Field(
        min_length=1,
        description="Mapping of relative file path to full source code contents",
    )
    command: list[str] = Field(
        default=["python", "src/main.py"],
        description="Command argv to execute inside container workspace",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Python packages required for execution (e.g. ['numpy', 'scipy'])",
    )
    configuration: dict[str, Any] = Field(
        default_factory=dict,
        description="Runtime hyperparameter and execution configuration mapping",
    )
    expected_metrics: list[str] = Field(
        default_factory=list,
        description="Metric names expected to be measured and produced by the code",
    )


def compute_canonical_code_hash(
    source_files: Mapping[str, str],
    configuration: Mapping[str, Any],
    command: list[str] | tuple[str, ...],
    dependencies: list[str] | tuple[str, ...],
) -> str:
    """Compute deterministic SHA256 hash over canonical source code and configuration."""
    hasher = hashlib.sha256()

    # 1. Canonical sorted source files
    for fname in sorted(source_files.keys()):
        hasher.update(fname.strip().encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(source_files[fname].encode("utf-8"))
        hasher.update(b"\x00")

    # 2. Canonical JSON configuration
    config_json = json.dumps(dict(configuration), sort_keys=True, separators=(",", ":"))
    hasher.update(config_json.encode("utf-8"))
    hasher.update(b"\x00")

    # 3. Canonical command
    cmd_json = json.dumps(list(command), separators=(",", ":"))
    hasher.update(cmd_json.encode("utf-8"))
    hasher.update(b"\x00")

    # 4. Canonical dependencies
    deps_json = json.dumps(sorted(str(d) for d in dependencies), separators=(",", ":"))
    hasher.update(deps_json.encode("utf-8"))

    return hasher.hexdigest()


def validate_code_proposal(proposal: GeneratedCodeProposal) -> list[str]:
    """Perform deterministic invariant validation on a code proposal.

    Checks:
    1. Entrypoint presence in source_files
    2. Path safety (no traversal, no absolute paths, no drive colons)
    3. Python AST syntax validity on .py files
    4. Command safety (no shell injection characters)
    """
    errors: list[str] = []

    # 1. Entrypoint presence
    clean_entrypoint = proposal.entrypoint.strip()
    clean_entrypoint = clean_entrypoint.removeprefix("src/")

    if clean_entrypoint not in proposal.source_files:
        errors.append(
            f"Entrypoint '{proposal.entrypoint}' is not present in source_files: "
            f"{list(proposal.source_files.keys())}"
        )

    # 2. Path safety
    for rel_path in proposal.source_files:
        path_str = rel_path.strip()
        if not path_str:
            errors.append("File path in source_files cannot be empty.")
            continue
        if path_str.startswith(("/", "\\")):
            errors.append(f"Forbidden absolute path '{path_str}'. Paths must be relative.")
        if ":" in path_str:
            errors.append(f"Forbidden drive-relative or colon path '{path_str}'.")
        parts = [p for p in path_str.replace("\\", "/").split("/") if p]
        if ".." in parts:
            errors.append(f"Path traversal ('..') detected in '{path_str}'.")

    # 3. Python AST syntax validation
    for rel_path, code_content in proposal.source_files.items():
        if rel_path.endswith(".py"):
            try:
                ast.parse(code_content, filename=rel_path)
            except SyntaxError as e:
                errors.append(
                    f"Syntax error in '{rel_path}' at line {e.lineno}, col {e.offset}: {e.msg}"
                )

    # 4. Command safety
    if not proposal.command:
        errors.append("Command argv list cannot be empty.")
    else:
        for arg in proposal.command:
            for pattern in PROHIBITED_COMMAND_PATTERNS:
                if pattern in arg:
                    errors.append(
                        f"Forbidden shell operator '{pattern}' detected in command argument '{arg}'."
                    )

    return errors


class CodingAgent(BaseAgent):
    """Reasoning agent responsible for generating executable experiment code from an immutable specification."""

    def generate_code(
        self,
        specification: ExperimentSpecification,
        research_context: ResearchContext,
        experiment_id: str,
        research_run_id: str,
        seed: int = 42,
        session: Session | None = None,
        actor: ActorType = ActorType.RESEARCH_AGENT,
        additional_instructions: str | None = None,
    ) -> GeneratedExperiment:
        """Generate a validated GeneratedExperiment from an ExperimentSpecification.

        Enforces:
        - Strict AST syntax validation on all Python code files
        - Safe relative workspace paths
        - Non-empty entrypoint present in source_files
        - Shell operator prohibition in command argv
        - Deterministic canonical content hashing
        - Decoupled from execution: NO direct code execution or container launch
        """
        # 1. Validate inputs
        exp_id_clean = experiment_id.strip()
        if not exp_id_clean:
            raise ValueError("experiment_id must be a non-empty string.")
        run_id_clean = research_run_id.strip()
        if not run_id_clean:
            raise ValueError("research_run_id must be a non-empty string.")

        if research_context.research_run_id != run_id_clean:
            raise ValueError(
                f"research_context run ID '{research_context.research_run_id}' does not match "
                f"requested run ID '{run_id_clean}'."
            )

        # 2. Build multi-section prompt strictly maintaining trust boundaries
        system_prompt = (
            "You are an expert AI/ML research engineer acting as the REX Coding Agent.\n"
            "Your task is to write clean, complete, self-contained, and reproducible Python code\n"
            "implementing the given ExperimentSpecification.\n\n"
            "MANDATORY EXECUTION CONSTRAINTS:\n"
            "1. Isolated Sandbox: Code runs inside a secure Linux container with NO internet access.\n"
            "2. Non-interactive: Code must run end-to-end without GUI or stdin input.\n"
            "3. Relative Paths: Output source file paths MUST be relative (e.g. 'main.py', 'model.py').\n"
            "   Absolute paths (e.g. '/root/...') or path traversal ('..') are strictly FORBIDDEN.\n"
            "4. Entrypoint: You must provide an entrypoint (default 'main.py') that MUST exist in source_files.\n"
            "5. Deterministic Randomness: Seed all PRNGs (random, numpy, torch) using the provided seed.\n"
            "6. Standard Execution: Default command is ['python', 'src/main.py'].\n"
            "7. Output Structure: Print or save evaluation metrics matching expected metrics.\n"
            "8. No Host Execution: You are generating code as structured text. Do not execute code.\n"
            "Respond ONLY with valid JSON conforming to the schema."
        )

        spec_metrics = [m.name for m in specification.metrics]
        spec_datasets = [d.name for d in specification.datasets]

        prompt_sections: list[str] = [
            f"### [RESEARCH RUN ID]\n{run_id_clean}",
            f"### [EXPERIMENT ID]\n{exp_id_clean}",
            f"### [EXECUTION SEED]\n{seed}",
            "### [EXPERIMENT SPECIFICATION]",
            f"Name: {specification.name}",
            f"Description: {specification.description}",
            f"Method: {specification.method}",
            f"Variables: {dict(specification.variables)}",
            f"Controls: {dict(specification.controls)}",
            f"Baseline: {dict(specification.baseline)}",
            f"Datasets: {spec_datasets}",
            f"Metrics: {spec_metrics}",
            f"Parameters: {dict(specification.parameters)}",
            f"Seeds: {list(specification.seeds)}",
            f"Repetitions: {specification.repetitions}",
            f"Success Criteria: {specification.success_criteria}",
            f"Falsification Criteria: {specification.falsification_criteria}",
            "",
            "### [RESEARCH CONTEXT]",
            f"Domain: {research_context.task_domain}",
            f"Likely Baselines: {', '.join(research_context.likely_baselines)}",
            f"Measurable Outcomes: {', '.join(research_context.measurable_outcomes)}",
            f"Design Considerations: {', '.join(research_context.experiment_considerations)}",
        ]

        if additional_instructions:
            prompt_sections.extend(
                [
                    "",
                    "### [ADDITIONAL INSTRUCTIONS]",
                    additional_instructions.strip(),
                ]
            )

        user_prompt = "\n".join(prompt_sections)

        request = LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            research_run_id=run_id_clean,
            action_name="code_generation",
            context={
                "experiment_id": exp_id_clean,
                "seed": seed,
            },
        )

        # 3. Generate proposal
        proposal, _response = self._generate_structured(
            request=request,
            response_model=GeneratedCodeProposal,
            session=session,
            actor=actor,
        )

        # Normalize entrypoint if prefixed with 'src/'
        proposal.entrypoint = proposal.entrypoint.removeprefix("src/")

        # 4. Deterministic validation & bounded repair loop
        validation_errors = validate_code_proposal(proposal)

        if validation_errors:
            logger.warning(
                "Code proposal failed validation: %s. Attempting bounded repair.",
                validation_errors,
            )
            repair_prompt = (
                f"{user_prompt}\n\n"
                f"### [VALIDATION FAILURE - REPAIR REQUIRED]\n"
                f"The generated code had the following validation errors:\n"
                + "\n".join(f"- {err}" for err in validation_errors)
                + "\n\nPlease fix all errors, ensure syntax is valid Python, ensure entrypoint exists in source_files, "
                "and regenerate the complete JSON proposal."
            )
            repair_request = request.model_copy(
                update={
                    "user_prompt": repair_prompt,
                    "action_name": "code_generation_repair",
                }
            )
            proposal, _ = self._generate_structured(
                request=repair_request,
                response_model=GeneratedCodeProposal,
                session=session,
                actor=actor,
            )
            proposal.entrypoint = proposal.entrypoint.removeprefix("src/")

            recheck_errors = validate_code_proposal(proposal)
            if recheck_errors:
                raise ValueError(
                    f"Generated code failed validation after repair: {'; '.join(recheck_errors)}"
                )

        # 5. Fallback or merge expected metrics from specification
        expected_metrics = list(proposal.expected_metrics)
        for m_name in spec_metrics:
            if m_name not in expected_metrics:
                expected_metrics.append(m_name)

        # 6. Compute canonical content hash
        content_hash = compute_canonical_code_hash(
            source_files=proposal.source_files,
            configuration=proposal.configuration,
            command=proposal.command,
            dependencies=proposal.dependencies,
        )

        # 7. Construct immutable GeneratedExperiment domain model
        generated_experiment = GeneratedExperiment(
            experiment_id=exp_id_clean,
            research_run_id=run_id_clean,
            entrypoint=proposal.entrypoint,
            source_files=proposal.source_files,
            command=tuple(proposal.command),
            dependencies=tuple(proposal.dependencies),
            configuration=proposal.configuration,
            expected_metrics=tuple(expected_metrics),
            content_hash=content_hash,
            metadata={
                "generator": self.agent_name,
                "seed": seed,
                "spec_name": specification.name,
            },
        )

        # 8. Record audit event
        if session is not None:
            code_event = create_event(
                event_type=EventType.AGENT_ACTION,
                actor=actor,
                research_run_id=run_id_clean,
                payload={
                    "agent": self.agent_name,
                    "action": "code_generated",
                    "experiment_id": exp_id_clean,
                    "content_hash": content_hash,
                    "entrypoint": proposal.entrypoint,
                    "source_files": list(proposal.source_files.keys()),
                    "dependencies": list(proposal.dependencies),
                    "expected_metrics": expected_metrics,
                },
            )
            EventRepository(session).record_event(code_event)
            session.flush()

            if self.event_sink is not None:
                self.event_sink.emit(code_event)

        return generated_experiment


def create_execution_request_from_generated(
    generated: GeneratedExperiment,
    execution_id: str,
    image: str | None = None,
    environment_variables: Mapping[str, str] | None = None,
    limits: ResourceLimits | None = None,
    network_disabled: bool = True,
    non_root_user: bool = True,
) -> ExecutionRequest:
    """Bridge function to convert a GeneratedExperiment artifact into an ExecutionRequest.

    Ready for direct ingestion by ExecutionOrchestrator and Docker execution sandbox.
    """
    clean_exec_id = execution_id.strip()
    if not clean_exec_id:
        raise ValueError("execution_id must be a non-empty string.")

    return ExecutionRequest(
        execution_id=clean_exec_id,
        experiment_id=generated.experiment_id,
        research_run_id=generated.research_run_id,
        command=list(generated.command),
        code_files=dict(generated.source_files),
        image=image,
        environment_variables=dict(environment_variables or {}),
        limits=limits or ResourceLimits(),
        network_disabled=network_disabled,
        non_root_user=non_root_user,
    )
