"""REX Domain Entities and Lifecycle Vocabulary (REX-005, REX-006, REX-007).

Defines strongly typed domain representations for research investigations, hypotheses,
experiment specifications, and authoritative lifecycle states.
"""

import math
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rex.observability.events import freeze_value, unfreeze_value
from rex.persistence.models import (
    ArtifactModel,
    ClaimModel,
    CritiqueModel,
    DecisionModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    LiteratureSourceModel,
    ResearchRunModel,
    ResultModel,
)


class ResearchState(StrEnum):
    """Explicit, type-safe vocabulary for the authoritative REX research lifecycle."""

    # Primary sequential lifecycle stages
    INITIALIZE = "INITIALIZE"
    UNDERSTAND = "UNDERSTAND"
    LITERATURE = "LITERATURE"
    HYPOTHESES = "HYPOTHESES"
    DESIGN = "DESIGN"
    IMPLEMENT = "IMPLEMENT"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    ANALYZE = "ANALYZE"
    CRITIQUE = "CRITIQUE"
    DECIDE = "DECIDE"

    # Branch / decision states
    REFINE = "REFINE"
    REPLICATE = "REPLICATE"
    PIVOT = "PIVOT"
    STOP = "STOP"

    # Terminal states
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# Authoritative set of terminal research states
TERMINAL_STATES: frozenset[ResearchState] = frozenset(
    {
        ResearchState.COMPLETE,
        ResearchState.FAILED,
        ResearchState.STOP,
    }
)


class HypothesisStatus(StrEnum):
    """Explicit, type-safe vocabulary for individual research hypothesis statuses."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    TESTING = "testing"
    VALIDATED = "validated"
    SUPPORTED = "supported"
    FALSIFIED = "falsified"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class ExpectedDirection(StrEnum):
    """Measurable expected direction of the experimental effect."""

    INCREASE = "increase"
    DECREASE = "decrease"
    NO_CHANGE = "no_change"
    NON_ZERO = "non_zero"
    OTHER = "other"


def _gen_run_id() -> str:
    """Generate a stable, unique research run identifier."""
    return f"run_{uuid.uuid4().hex[:12]}"


def _gen_hypothesis_id() -> str:
    """Generate a stable, unique hypothesis identifier."""
    return f"hyp_{uuid.uuid4().hex[:12]}"


def _gen_experiment_id() -> str:
    """Generate a stable, unique experiment identifier."""
    return f"exp_{uuid.uuid4().hex[:12]}"


def _gen_execution_id() -> str:
    """Generate a stable, unique execution identifier."""
    return f"exec_{uuid.uuid4().hex[:12]}"


def _gen_result_id() -> str:
    """Generate a stable, unique result identifier."""
    return f"res_{uuid.uuid4().hex[:12]}"


def _gen_artifact_id() -> str:
    """Generate a stable, unique artifact identifier."""
    return f"art_{uuid.uuid4().hex[:12]}"


def _gen_claim_id() -> str:
    """Generate a stable, unique claim identifier."""
    return f"clm_{uuid.uuid4().hex[:12]}"


def _gen_link_id() -> str:
    """Generate a stable, unique evidence link identifier."""
    return f"lnk_{uuid.uuid4().hex[:12]}"


class ClaimType(StrEnum):
    """Semantic taxonomy for research claims."""

    OBSERVATION = "observation"
    COMPARISON = "comparison"
    CAUSAL = "causal"
    METHODOLOGICAL = "methodological"
    CONCLUSION = "conclusion"
    EMPIRICAL = "empirical"


class ClaimStatus(StrEnum):
    """Authoritative lifecycle states for research claims."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    VERIFIED = "verified"
    REJECTED = "rejected"
    TAMPERED = "tampered"
    DISPROVEN = "disproven"
    INCONCLUSIVE = "inconclusive"


class EvidenceNodeType(StrEnum):
    """Classifications for nodes in the research evidence plane."""

    CLAIM = "claim"
    ANALYSIS = "analysis"
    RESULT = "result"
    EXECUTION = "execution"
    EXPERIMENT = "experiment"
    ARTIFACT = "artifact"
    CODE = "code"
    CONFIGURATION = "configuration"
    DATASET = "dataset"
    LITERATURE_SOURCE = "literature_source"
    HYPOTHESIS = "hypothesis"


class EvidenceRelationType(StrEnum):
    """Semantic directional relationship types connecting evidence nodes."""

    SUPPORTED_BY = "supported_by"
    DERIVED_FROM = "derived_from"
    PRODUCED_BY = "produced_by"
    INSTANCE_OF = "instance_of"
    USES_ARTIFACT = "uses_artifact"
    USES_DATASET = "uses_dataset"
    USES_CODE = "uses_code"
    USES_CONFIGURATION = "uses_configuration"
    CITES = "cites"
    REFINES = "refines"


class ExperimentStatus(StrEnum):
    """Explicit, type-safe vocabulary for experiment lifecycle states."""

    DESIGNED = "designed"
    PENDING = "pending"
    RUNNING = "running"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_EXPERIMENT_STATUSES: frozenset[ExperimentStatus] = frozenset(
    {
        ExperimentStatus.COMPLETED,
        ExperimentStatus.FAILED,
        ExperimentStatus.CANCELLED,
    }
)


class ExecutionStatus(StrEnum):
    """Explicit, type-safe vocabulary for concrete execution attempt states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


TERMINAL_EXECUTION_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMEOUT,
    }
)


class ArtifactType(StrEnum):
    """Classification of persisted execution artifacts."""

    LOG = "log"
    STDOUT = "stdout"
    STDERR = "stderr"
    METRIC = "metric"
    PLOT = "plot"
    FIGURE = "figure"
    CHECKPOINT = "checkpoint"
    MODEL = "model"
    DATASET = "dataset"
    CODE = "code"
    MANIFEST = "manifest"
    OUTPUT = "output"
    OTHER = "other"


# Artifact types that represent execution evidence and must have an execution_id
EXECUTION_ARTIFACT_TYPES: frozenset[ArtifactType] = frozenset(
    {
        ArtifactType.LOG,
        ArtifactType.STDOUT,
        ArtifactType.STDERR,
        ArtifactType.METRIC,
        ArtifactType.PLOT,
        ArtifactType.FIGURE,
        ArtifactType.CHECKPOINT,
        ArtifactType.OUTPUT,
    }
)


class MetricDirection(StrEnum):
    """Target optimization direction for experiment metrics."""

    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"
    TARGET = "target"
    OTHER = "other"


class ResearchRun(BaseModel):
    """Immutable domain representation of a research run investigation.

    Maintains clear separation from the SQLAlchemy persistence model while
    enforcing invariant validation on core research fields.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=_gen_run_id, description="Stable unique research run ID")
    title: str = Field(default="", description="Human-readable investigation title")
    research_question: str = Field(description="Core scientific ML/AI research question")
    state: ResearchState = Field(
        default=ResearchState.INITIALIZE, description="Current lifecycle state"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of run creation",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of last lifecycle update",
    )
    version: int = Field(
        default=1, description="Monotonic version for optimistic concurrency checks"
    )
    configuration: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Research configuration settings",
    )
    budget: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Research resource budget constraints",
    )

    @field_validator("research_question")
    @classmethod
    def _validate_research_question(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Research question must be a non-empty string.")
        return cleaned

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @property
    def is_terminal(self) -> bool:
        """Indicate whether the research run is in a terminal lifecycle state."""
        return self.state in TERMINAL_STATES

    @classmethod
    def from_persistence(cls, model: ResearchRunModel) -> "ResearchRun":
        """Reconstruct a domain ResearchRun from a SQLAlchemy persistence model."""
        config_data = dict(model.configuration_json or {})
        version = config_data.pop("_version", 1)

        created_ts = model.created_at
        if created_ts.tzinfo is None:
            created_ts = created_ts.replace(tzinfo=UTC)

        updated_ts = model.updated_at
        if updated_ts.tzinfo is None:
            updated_ts = updated_ts.replace(tzinfo=UTC)

        return cls(
            id=model.id,
            title=model.title,
            research_question=model.research_question,
            state=ResearchState(model.status),
            created_at=created_ts,
            updated_at=updated_ts,
            version=version,
            configuration=MappingProxyType(config_data),
            budget=MappingProxyType(dict(model.budget_json or {})),
        )

    def to_persistence(self) -> ResearchRunModel:
        """Convert domain ResearchRun to a SQLAlchemy persistence model."""
        config_dict = dict(self.configuration)
        config_dict["_version"] = self.version

        return ResearchRunModel(
            id=self.id,
            title=self.title,
            research_question=self.research_question,
            status=self.state.value,
            created_at=self.created_at,
            updated_at=self.updated_at,
            configuration_json=config_dict,
            budget_json=dict(self.budget),
        )


class Hypothesis(BaseModel):
    """Immutable domain representation of a scientific research hypothesis.

    Captures the testable scientific proposition, expected direction of effect,
    falsification conditions, and current validation status.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=_gen_hypothesis_id, description="Stable unique hypothesis ID")
    research_run_id: str = Field(description="ID of associated research run")
    statement: str = Field(description="Explicit scientific hypothesis statement")
    rationale: str = Field(default="", description="Theoretical or empirical motivation")
    expected_direction: ExpectedDirection = Field(
        default=ExpectedDirection.INCREASE,
        description="Measurable expected direction of effect",
    )
    falsification_condition: str = Field(
        description="Deterministic condition that disproves or rejects the hypothesis"
    )
    status: HypothesisStatus = Field(
        default=HypothesisStatus.PROPOSED,
        description="Current lifecycle status of the hypothesis",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of hypothesis creation",
    )

    @field_validator("id", "research_run_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier must be a non-empty string.")
        return cleaned

    @field_validator("statement")
    @classmethod
    def _validate_statement(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Hypothesis statement must be a non-empty string.")
        return cleaned

    @field_validator("falsification_condition")
    @classmethod
    def _validate_falsification_condition(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Falsification condition must be a non-empty string.")
        return cleaned

    @field_validator("created_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    def with_status(self, new_status: HypothesisStatus | str) -> "Hypothesis":
        """Return a new immutable Hypothesis with updated status, preserving all scientific content."""
        status_enum = (
            new_status if isinstance(new_status, HypothesisStatus) else HypothesisStatus(new_status)
        )
        return Hypothesis(
            id=self.id,
            research_run_id=self.research_run_id,
            statement=self.statement,
            rationale=self.rationale,
            expected_direction=self.expected_direction,
            falsification_condition=self.falsification_condition,
            status=status_enum,
            created_at=self.created_at,
        )

    @classmethod
    def from_persistence(cls, model: HypothesisModel) -> "Hypothesis":
        """Reconstruct a domain Hypothesis from a SQLAlchemy persistence model."""
        created_ts = model.created_at
        if created_ts.tzinfo is None:
            created_ts = created_ts.replace(tzinfo=UTC)

        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            statement=model.statement,
            rationale=model.rationale,
            expected_direction=ExpectedDirection(model.expected_direction),
            falsification_condition=model.falsification_condition,
            status=HypothesisStatus(model.status),
            created_at=created_ts,
        )

    def to_persistence(self) -> HypothesisModel:
        """Convert domain Hypothesis to a SQLAlchemy persistence model."""
        return HypothesisModel(
            id=self.id,
            research_run_id=self.research_run_id,
            statement=self.statement,
            rationale=self.rationale,
            expected_direction=self.expected_direction.value,
            falsification_condition=self.falsification_condition,
            status=self.status.value,
            created_at=self.created_at,
        )


class DatasetSpec(BaseModel):
    """Structured specification for an experimental input dataset."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    name: str = Field(description="Dataset identifier or canonical name")
    version: str = Field(default="", description="Dataset version or commit hash")
    split: str = Field(default="train", description="Dataset split (e.g. train, test, val)")
    parameters: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Preprocessing or subset selection parameters",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Dataset name must be a non-empty string.")
        return cleaned

    @field_validator("parameters", mode="after")
    @classmethod
    def _freeze_parameters(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "split": self.split,
            "parameters": unfreeze_value(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | str) -> "DatasetSpec":
        if isinstance(data, str):
            return cls(name=data)
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            split=str(data.get("split", "train")),
            parameters=data.get("parameters", {}),
        )


class MetricSpec(BaseModel):
    """Structured specification for a target evaluation metric."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    name: str = Field(description="Metric name (e.g. accuracy, perplexity, f1)")
    direction: MetricDirection = Field(
        default=MetricDirection.MAXIMIZE,
        description="Direction of optimization",
    )
    target_value: float | None = Field(
        default=None,
        description="Optional target threshold or stopping criterion value",
    )
    description: str = Field(default="", description="Description of metric definition")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Metric name must be a non-empty string.")
        return cleaned

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction.value,
            "target_value": self.target_value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | str) -> "MetricSpec":
        if isinstance(data, str):
            return cls(name=data)
        direction_val = data.get("direction", MetricDirection.MAXIMIZE)
        direction_enum = (
            direction_val
            if isinstance(direction_val, MetricDirection)
            else MetricDirection(direction_val)
        )
        return cls(
            name=str(data.get("name", "")),
            direction=direction_enum,
            target_value=data.get("target_value"),
            description=str(data.get("description", "")),
        )


class ExperimentSpecification(BaseModel):
    """Immutable, deeply frozen scientific definition of what an experiment executes."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    name: str = Field(default="", description="Short human-readable title or tag for this design")
    description: str = Field(default="", description="Detailed narrative of experimental intent")
    method: str = Field(default="", description="Algorithmic approach or methodology")
    variables: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Independent experimental variables being varied or compared",
    )
    controls: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Controlled variables kept invariant across conditions",
    )
    baseline: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Baseline configuration, benchmark reference, or control condition",
    )
    datasets: tuple[DatasetSpec, ...] = Field(
        default_factory=tuple,
        description="Datasets used in the experiment",
    )
    metrics: tuple[MetricSpec, ...] = Field(
        default_factory=tuple,
        description="Evaluation metrics to be measured",
    )
    parameters: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Hyperparameters, model architectures, and runtime configurations",
    )
    seeds: tuple[int, ...] = Field(
        default=(42,),
        description="Random seeds for replication and statistical variance control",
    )
    repetitions: int = Field(
        default=1,
        ge=1,
        description="Number of repetitions or folds per configuration",
    )
    analysis_methods: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Statistical analysis methods to apply to measured results",
    )
    success_criteria: str = Field(
        default="",
        description="Quantitative condition determining experimental success",
    )
    falsification_criteria: str = Field(
        default="",
        description="Condition indicating hypothesis falsification or failure",
    )

    @field_validator("variables", "controls", "baseline", "parameters", mode="after")
    @classmethod
    def _freeze_mappings(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    @field_validator("datasets", mode="before")
    @classmethod
    def _coerce_datasets(cls, v: Any) -> tuple[DatasetSpec, ...]:
        if v is None:
            return ()
        if isinstance(v, (list, tuple)):
            items: list[DatasetSpec] = []
            for item in v:
                if isinstance(item, DatasetSpec):
                    items.append(item)
                elif isinstance(item, (dict, Mapping)):
                    items.append(DatasetSpec.from_dict(item))
                elif isinstance(item, str):
                    items.append(DatasetSpec(name=item))
                else:
                    raise TypeError(f"Invalid dataset specification element: {type(item).__name__}")
            return tuple(items)
        raise ValueError(f"Datasets must be a sequence, got {type(v).__name__}.")

    @field_validator("metrics", mode="before")
    @classmethod
    def _coerce_metrics(cls, v: Any) -> tuple[MetricSpec, ...]:
        if v is None:
            return ()
        if isinstance(v, (list, tuple)):
            items: list[MetricSpec] = []
            for item in v:
                if isinstance(item, MetricSpec):
                    items.append(item)
                elif isinstance(item, (dict, Mapping)):
                    items.append(MetricSpec.from_dict(item))
                elif isinstance(item, str):
                    items.append(MetricSpec(name=item))
                else:
                    raise TypeError(f"Invalid metric specification element: {type(item).__name__}")
            return tuple(items)
        raise ValueError(f"Metrics must be a sequence, got {type(v).__name__}.")

    @field_validator("seeds", mode="before")
    @classmethod
    def _coerce_seeds(cls, v: Any) -> tuple[int, ...]:
        if v is None:
            return (42,)
        if isinstance(v, int):
            return (v,)
        if isinstance(v, (list, tuple)):
            return tuple(int(x) for x in v)
        raise ValueError(f"Seeds must be an int or sequence of ints, got {type(v).__name__}.")

    @field_validator("analysis_methods", mode="before")
    @classmethod
    def _coerce_analysis_methods(cls, v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, str):
            return (v.strip(),) if v.strip() else ()
        if isinstance(v, (list, tuple)):
            return tuple(str(x).strip() for x in v if str(x).strip())
        raise ValueError(f"Analysis methods must be a sequence of strings, got {type(v).__name__}.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize specification to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "method": self.method,
            "variables": unfreeze_value(self.variables),
            "controls": unfreeze_value(self.controls),
            "baseline": unfreeze_value(self.baseline),
            "datasets": [d.to_dict() for d in self.datasets],
            "metrics": [m.to_dict() for m in self.metrics],
            "parameters": unfreeze_value(self.parameters),
            "seeds": list(self.seeds),
            "repetitions": self.repetitions,
            "analysis_methods": list(self.analysis_methods),
            "success_criteria": self.success_criteria,
            "falsification_criteria": self.falsification_criteria,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ExperimentSpecification":
        """Reconstruct an ExperimentSpecification from a dictionary."""
        if not data:
            return cls()
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            method=str(data.get("method", "")),
            variables=data.get("variables", {}),
            controls=data.get("controls", {}),
            baseline=data.get("baseline", {}),
            datasets=data.get("datasets", ()),
            metrics=data.get("metrics", ()),
            parameters=data.get("parameters", {}),
            seeds=data.get("seeds", (42,)),
            repetitions=int(data.get("repetitions", 1)),
            analysis_methods=data.get("analysis_methods", ()),
            success_criteria=str(data.get("success_criteria", "")),
            falsification_criteria=str(data.get("falsification_criteria", "")),
        )


class Experiment(BaseModel):
    """Immutable domain representation of an experiment specification.

    Establishes the scientific contract describing what REX intends to execute,
    maintaining strict separation between hypothesis, design, execution, and outcomes.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=_gen_experiment_id, description="Stable unique experiment ID")
    research_run_id: str = Field(description="ID of associated research run")
    hypothesis_id: str | None = Field(
        default=None, description="Optional ID of hypothesis being tested"
    )
    objective: str = Field(description="Primary objective or experimental query")
    specification: ExperimentSpecification = Field(
        default_factory=ExperimentSpecification,
        description="Structured, immutable scientific specification",
    )
    status: ExperimentStatus = Field(
        default=ExperimentStatus.DESIGNED,
        description="Current lifecycle status of the experiment",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of experiment design",
    )
    parent_experiment_id: str | None = Field(
        default=None,
        description="Parent experiment ID for iterative refinements or variations",
    )

    @field_validator("id", "research_run_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier must be a non-empty string.")
        return cleaned

    @field_validator("hypothesis_id", "parent_experiment_id")
    @classmethod
    def _validate_optional_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Experiment objective must be a non-empty string.")
        return cleaned

    @field_validator("specification", mode="before")
    @classmethod
    def _coerce_specification(cls, v: Any) -> ExperimentSpecification:
        if v is None:
            return ExperimentSpecification()
        if isinstance(v, ExperimentSpecification):
            return v
        if isinstance(v, (dict, Mapping)):
            return ExperimentSpecification.from_dict(v)
        raise ValueError(
            f"Specification must be an ExperimentSpecification or mapping, got {type(v).__name__}."
        )

    @field_validator("created_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @property
    def is_terminal(self) -> bool:
        """Indicate whether the experiment has reached a terminal lifecycle state."""
        return self.status in TERMINAL_EXPERIMENT_STATUSES

    def with_status(self, new_status: ExperimentStatus | str) -> "Experiment":
        """Return a new immutable Experiment with updated status, preserving all scientific content."""
        status_enum = (
            new_status if isinstance(new_status, ExperimentStatus) else ExperimentStatus(new_status)
        )
        return Experiment(
            id=self.id,
            research_run_id=self.research_run_id,
            hypothesis_id=self.hypothesis_id,
            objective=self.objective,
            specification=self.specification,
            status=status_enum,
            created_at=self.created_at,
            parent_experiment_id=self.parent_experiment_id,
        )

    def create_refinement(
        self,
        objective: str | None = None,
        specification: ExperimentSpecification | Mapping[str, Any] | None = None,
        new_id: str | None = None,
    ) -> "Experiment":
        """Create a new child Experiment refining this experiment's specification."""
        if specification is None:
            spec_obj = self.specification
        elif isinstance(specification, ExperimentSpecification):
            spec_obj = specification
        elif isinstance(specification, (dict, Mapping)):
            spec_obj = ExperimentSpecification.from_dict(specification)
        else:
            raise ValueError(f"Invalid specification type: {type(specification).__name__}")

        init_kwargs: dict[str, Any] = {
            "research_run_id": self.research_run_id,
            "hypothesis_id": self.hypothesis_id,
            "objective": objective if objective is not None else self.objective,
            "specification": spec_obj,
            "status": ExperimentStatus.DESIGNED,
            "parent_experiment_id": self.id,
        }
        if new_id is not None:
            init_kwargs["id"] = new_id

        return Experiment(**init_kwargs)

    @classmethod
    def from_persistence(cls, model: ExperimentModel) -> "Experiment":
        """Reconstruct a domain Experiment from a SQLAlchemy persistence model."""
        created_ts = model.created_at
        if created_ts.tzinfo is None:
            created_ts = created_ts.replace(tzinfo=UTC)

        spec = ExperimentSpecification.from_dict(model.specification_json or {})
        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            hypothesis_id=model.hypothesis_id,
            objective=model.objective,
            specification=spec,
            status=ExperimentStatus(model.status),
            created_at=created_ts,
            parent_experiment_id=model.parent_experiment_id,
        )

    def to_persistence(self) -> ExperimentModel:
        """Convert domain Experiment to a SQLAlchemy persistence model."""
        return ExperimentModel(
            id=self.id,
            research_run_id=self.research_run_id,
            hypothesis_id=self.hypothesis_id,
            objective=self.objective,
            specification_json=self.specification.to_dict(),
            status=self.status.value,
            created_at=self.created_at,
            parent_experiment_id=self.parent_experiment_id,
        )


class Execution(BaseModel):
    """Immutable domain representation of a concrete experiment execution attempt.

    Captures execution identity, environment metadata, runtime telemetry,
    exit codes, and reproducibility fingerprints.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=_gen_execution_id, description="Stable unique execution ID")
    experiment_id: str = Field(description="ID of associated experiment specification")
    status: ExecutionStatus = Field(
        default=ExecutionStatus.PENDING,
        description="Current lifecycle status of the execution",
    )
    started_at: datetime | None = Field(
        default=None, description="UTC timestamp when execution started"
    )
    finished_at: datetime | None = Field(
        default=None, description="UTC timestamp when execution finished"
    )
    command: str = Field(default="", description="Command or entrypoint executed")
    git_commit: str = Field(default="", description="Git commit hash of code repository")
    code_hash: str = Field(default="", description="Cryptographic hash of executed code files")
    dataset_hash: str = Field(default="", description="Cryptographic hash of input datasets")
    configuration_hash: str = Field(
        default="", description="Cryptographic hash of execution parameters"
    )
    seed: int | None = Field(default=None, description="Random seed used for replication")
    environment: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Execution environment specification (OS, packages, python version)",
    )
    resource_usage: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Resource telemetry (CPU, GPU, memory, duration)",
    )
    exit_code: int | None = Field(
        default=None, description="Process exit code (0 for success, non-zero for failure)"
    )
    stdout_artifact_id: str | None = Field(
        default=None, description="ID of captured stdout artifact"
    )
    stderr_artifact_id: str | None = Field(
        default=None, description="ID of captured stderr artifact"
    )

    @field_validator("id", "experiment_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier must be a non-empty string.")
        return cleaned

    @field_validator("stdout_artifact_id", "stderr_artifact_id")
    @classmethod
    def _validate_optional_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None

    @field_validator("environment", "resource_usage", mode="after")
    @classmethod
    def _freeze_nested(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @property
    def is_terminal(self) -> bool:
        """Indicate whether the execution has reached a terminal state."""
        return self.status in TERMINAL_EXECUTION_STATUSES

    def with_status(
        self,
        new_status: ExecutionStatus | str,
        exit_code: int | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        resource_usage: Mapping[str, Any] | None = None,
    ) -> "Execution":
        """Return a new immutable Execution with updated lifecycle attributes."""
        status_enum = (
            new_status if isinstance(new_status, ExecutionStatus) else ExecutionStatus(new_status)
        )
        return Execution(
            id=self.id,
            experiment_id=self.experiment_id,
            status=status_enum,
            started_at=started_at if started_at is not None else self.started_at,
            finished_at=finished_at if finished_at is not None else self.finished_at,
            command=self.command,
            git_commit=self.git_commit,
            code_hash=self.code_hash,
            dataset_hash=self.dataset_hash,
            configuration_hash=self.configuration_hash,
            seed=self.seed,
            environment=self.environment,
            resource_usage=resource_usage if resource_usage is not None else self.resource_usage,
            exit_code=exit_code if exit_code is not None else self.exit_code,
            stdout_artifact_id=self.stdout_artifact_id,
            stderr_artifact_id=self.stderr_artifact_id,
        )

    @classmethod
    def from_persistence(cls, model: ExecutionModel) -> "Execution":
        """Reconstruct a domain Execution from a SQLAlchemy persistence model."""
        started_ts = model.started_at
        if started_ts is not None and started_ts.tzinfo is None:
            started_ts = started_ts.replace(tzinfo=UTC)

        finished_ts = model.finished_at
        if finished_ts is not None and finished_ts.tzinfo is None:
            finished_ts = finished_ts.replace(tzinfo=UTC)

        return cls(
            id=model.id,
            experiment_id=model.experiment_id,
            status=ExecutionStatus(model.status),
            started_at=started_ts,
            finished_at=finished_ts,
            command=model.command,
            git_commit=model.git_commit,
            code_hash=model.code_hash,
            dataset_hash=model.dataset_hash,
            configuration_hash=model.configuration_hash,
            seed=model.seed,
            environment=model.environment_json or {},
            resource_usage=model.resource_usage_json or {},
            exit_code=model.exit_code,
            stdout_artifact_id=model.stdout_artifact_id,
            stderr_artifact_id=model.stderr_artifact_id,
        )

    def to_persistence(self) -> ExecutionModel:
        """Convert domain Execution to a SQLAlchemy persistence model."""
        return ExecutionModel(
            id=self.id,
            experiment_id=self.experiment_id,
            status=self.status.value,
            started_at=self.started_at,
            finished_at=self.finished_at,
            command=self.command,
            git_commit=self.git_commit,
            code_hash=self.code_hash,
            dataset_hash=self.dataset_hash,
            configuration_hash=self.configuration_hash,
            seed=self.seed,
            environment_json=unfreeze_value(self.environment),
            resource_usage_json=unfreeze_value(self.resource_usage),
            exit_code=self.exit_code,
            stdout_artifact_id=self.stdout_artifact_id,
            stderr_artifact_id=self.stderr_artifact_id,
        )


class Result(BaseModel):
    """Immutable domain representation of an empirical observation produced by an execution.

    Anchors machine-measured metrics to an execution run, preventing fabrication of scientific results.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=_gen_result_id, description="Stable unique result ID")
    execution_id: str = Field(description="ID of associated execution attempt")
    metric_name: str = Field(
        description="Name of the recorded metric (e.g. accuracy, loss, latency)"
    )
    metric_value: float | None = Field(
        default=None, description="Optional scalar numeric metric value"
    )
    metric_unit: str = Field(default="", description="Unit of measurement (e.g. %, s, GB)")
    result_data: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Structured result payload (per-class metrics, confusion matrices, distributions)",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of result capture",
    )

    @field_validator("id", "execution_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier must be a non-empty string.")
        return cleaned

    @field_validator("metric_name")
    @classmethod
    def _validate_metric_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Metric name must be a non-empty string.")
        return cleaned

    @field_validator("metric_value")
    @classmethod
    def _validate_finite_metric_value(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not math.isfinite(v):
            raise ValueError(f"Metric value must be a finite number, got {v}.")
        return float(v)

    @field_validator("result_data", mode="after")
    @classmethod
    def _freeze_result_data(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    @field_validator("created_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @classmethod
    def from_persistence(cls, model: ResultModel) -> "Result":
        """Reconstruct a domain Result from a SQLAlchemy persistence model."""
        created_ts = model.created_at
        if created_ts.tzinfo is None:
            created_ts = created_ts.replace(tzinfo=UTC)

        return cls(
            id=model.id,
            execution_id=model.execution_id,
            metric_name=model.metric_name,
            metric_value=model.metric_value,
            metric_unit=model.metric_unit,
            result_data=model.result_json or {},
            created_at=created_ts,
        )

    def to_persistence(self) -> ResultModel:
        """Convert domain Result to a SQLAlchemy persistence model."""
        return ResultModel(
            id=self.id,
            execution_id=self.execution_id,
            metric_name=self.metric_name,
            metric_value=self.metric_value,
            metric_unit=self.metric_unit,
            result_json=unfreeze_value(self.result_data),
            created_at=self.created_at,
        )


class Artifact(BaseModel):
    """Immutable domain representation of a filesystem artifact produced by or for a research run.

    Captures cryptographic SHA-256 identity, filesystem path, file size, and provenance metadata.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=_gen_artifact_id, description="Stable unique artifact ID")
    research_run_id: str = Field(description="ID of associated research run")
    execution_id: str | None = Field(
        default=None, description="Optional ID of producing execution run"
    )
    artifact_type: ArtifactType = Field(
        default=ArtifactType.OTHER, description="Categorization of the artifact"
    )
    path: str = Field(description="Storage path or location")
    content_hash: str = Field(
        description="Cryptographic SHA-256 hash or digest for integrity verification"
    )
    size_bytes: int = Field(default=0, ge=0, description="Artifact size in bytes")
    metadata: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Structured artifact metadata (MIME type, format, shape, etc.)",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of artifact registration",
    )

    @field_validator("id", "research_run_id", "path", "content_hash")
    @classmethod
    def _validate_non_empty_string(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field must be a non-empty string.")
        return cleaned

    @field_validator("execution_id")
    @classmethod
    def _validate_optional_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    @field_validator("artifact_type", mode="before")
    @classmethod
    def _coerce_artifact_type(cls, v: Any) -> ArtifactType:
        if isinstance(v, ArtifactType):
            return v
        if isinstance(v, str):
            val_lower = v.strip().lower()
            try:
                return ArtifactType(val_lower)
            except ValueError:
                return ArtifactType.OTHER
        raise TypeError(f"Invalid artifact type: {type(v).__name__}")

    @field_validator("created_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @property
    def is_execution_artifact(self) -> bool:
        """Whether this artifact represents execution evidence."""
        return self.execution_id is not None

    @model_validator(mode="after")
    def _validate_execution_provenance(self) -> "Artifact":
        if self.artifact_type in EXECUTION_ARTIFACT_TYPES and not self.execution_id:
            raise ValueError(
                f"Artifacts of type '{self.artifact_type.value}' represent execution outputs "
                f"and must have an 'execution_id'."
            )
        return self

    @classmethod
    def from_persistence(cls, model: ArtifactModel) -> "Artifact":
        """Reconstruct a domain Artifact from a SQLAlchemy persistence model."""
        created_ts = model.created_at
        if created_ts.tzinfo is None:
            created_ts = created_ts.replace(tzinfo=UTC)

        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            execution_id=model.execution_id,
            artifact_type=model.artifact_type,
            path=model.path,
            content_hash=model.content_hash,
            size_bytes=model.size_bytes,
            metadata=model.metadata_json or {},
            created_at=created_ts,
        )

    def to_persistence(self) -> ArtifactModel:
        """Convert domain Artifact to a SQLAlchemy persistence model."""
        return ArtifactModel(
            id=self.id,
            research_run_id=self.research_run_id,
            execution_id=self.execution_id,
            artifact_type=self.artifact_type.value,
            path=self.path,
            content_hash=self.content_hash,
            size_bytes=self.size_bytes,
            metadata_json=unfreeze_value(self.metadata),
            created_at=self.created_at,
        )


class ResearchContext(BaseModel):
    """Immutable domain representation of structured problem context produced by the Investigator."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    research_run_id: str = Field(description="ID of associated research run")
    problem_definition: str = Field(
        description="Concise, rigorous formulation of the scientific problem"
    )
    task_domain: str = Field(
        default="machine_learning", description="Scientific or computational domain"
    )
    relevant_terminology: tuple[str, ...] = Field(
        default_factory=tuple, description="Key domain concepts and terms"
    )
    methodological_approaches: tuple[str, ...] = Field(
        default_factory=tuple, description="Established candidate methods"
    )
    likely_baselines: tuple[str, ...] = Field(
        default_factory=tuple, description="Standard comparative baselines"
    )
    measurable_outcomes: tuple[str, ...] = Field(
        default_factory=tuple, description="Quantitatively observable metrics/signals"
    )
    important_assumptions: tuple[str, ...] = Field(
        default_factory=tuple, description="Explicit foundational assumptions"
    )
    unresolved_questions: tuple[str, ...] = Field(
        default_factory=tuple, description="Open empirical or theoretical questions"
    )
    experiment_considerations: tuple[str, ...] = Field(
        default_factory=tuple, description="Design constraints, compute, or safety notes"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC creation timestamp"
    )

    @field_validator("research_run_id", "problem_definition")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field must be a non-empty string.")
        return cleaned

    @field_validator(
        "relevant_terminology",
        "methodological_approaches",
        "likely_baselines",
        "measurable_outcomes",
        "important_assumptions",
        "unresolved_questions",
        "experiment_considerations",
        mode="before",
    )
    @classmethod
    def _coerce_tuple(cls, v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, str):
            return (v.strip(),) if v.strip() else ()
        if isinstance(v, (list, tuple, set)):
            return tuple(str(x).strip() for x in v if str(x).strip())
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_run_id": self.research_run_id,
            "problem_definition": self.problem_definition,
            "task_domain": self.task_domain,
            "relevant_terminology": list(self.relevant_terminology),
            "methodological_approaches": list(self.methodological_approaches),
            "likely_baselines": list(self.likely_baselines),
            "measurable_outcomes": list(self.measurable_outcomes),
            "important_assumptions": list(self.important_assumptions),
            "unresolved_questions": list(self.unresolved_questions),
            "experiment_considerations": list(self.experiment_considerations),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchContext":
        return cls(
            research_run_id=str(data.get("research_run_id", "")),
            problem_definition=str(data.get("problem_definition", "")),
            task_domain=str(data.get("task_domain", "machine_learning")),
            relevant_terminology=data.get("relevant_terminology", ()),
            methodological_approaches=data.get("methodological_approaches", ()),
            likely_baselines=data.get("likely_baselines", ()),
            measurable_outcomes=data.get("measurable_outcomes", ()),
            important_assumptions=data.get("important_assumptions", ()),
            unresolved_questions=data.get("unresolved_questions", ()),
            experiment_considerations=data.get("experiment_considerations", ()),
        )


class GeneratedExperiment(BaseModel):
    """Structured code-generation artifact produced by the Coding Agent for the Execution Plane."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
    )

    experiment_id: str = Field(description="Parent experiment specification ID")
    research_run_id: str = Field(description="Parent research run ID")
    entrypoint: str = Field(
        default="main.py", description="Relative entrypoint file inside workspace src/"
    )
    source_files: Mapping[str, str] = Field(description="Relative safe file paths to code contents")
    command: tuple[str, ...] = Field(
        default=("python", "src/main.py"), description="Execution command argv"
    )
    dependencies: tuple[str, ...] = Field(
        default_factory=tuple, description="Python package dependencies"
    )
    configuration: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Runtime configuration dictionary",
    )
    expected_metrics: tuple[str, ...] = Field(
        default_factory=tuple, description="Metric names expected in execution output"
    )
    content_hash: str = Field(
        description="Deterministic SHA256 hash of canonical source code and configuration"
    )
    metadata: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Provenance and generator metadata",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC creation timestamp"
    )

    @field_validator("experiment_id", "research_run_id", "entrypoint")
    @classmethod
    def _validate_non_empty_ids(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field must be a non-empty string.")
        return cleaned

    @field_validator("source_files", mode="after")
    @classmethod
    def _freeze_source_files(cls, v: Any) -> Mapping[str, str]:
        if not v or not isinstance(v, (dict, Mapping)):
            raise ValueError(
                "source_files must be a non-empty mapping of filenames to code strings."
            )
        return freeze_value(v)

    @field_validator("configuration", "metadata", mode="after")
    @classmethod
    def _freeze_dict(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    @field_validator("command", "dependencies", "expected_metrics", mode="before")
    @classmethod
    def _coerce_str_tuple(cls, v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, (list, tuple, set)):
            return tuple(str(x) for x in v)
        return (str(v),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "research_run_id": self.research_run_id,
            "entrypoint": self.entrypoint,
            "source_files": dict(self.source_files),
            "command": list(self.command),
            "dependencies": list(self.dependencies),
            "configuration": unfreeze_value(self.configuration),
            "expected_metrics": list(self.expected_metrics),
            "content_hash": self.content_hash,
            "metadata": unfreeze_value(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


class Claim(BaseModel):
    """Immutable domain representation of a scientific claim (REX-024)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=_gen_claim_id, description="Stable unique claim identifier")
    research_run_id: str = Field(description="Parent research run identifier")
    statement: str = Field(description="Natural language scientific claim or observation")
    claim_type: ClaimType = Field(
        default=ClaimType.OBSERVATION, description="Semantic classification of claim"
    )
    status: ClaimStatus = Field(
        default=ClaimStatus.DRAFT, description="Authoritative verification state"
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Confidence score where applicable"
    )
    created_by: str = Field(default="system", description="Author identity or proposing actor")
    metadata: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Structured supporting attributes and asserted values",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC creation timestamp"
    )

    @property
    def text(self) -> str:
        """Alias for statement matching persistence column."""
        return self.statement

    @field_validator("id", "research_run_id")
    @classmethod
    def _validate_ids(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("ID cannot be empty.")
        return cleaned

    @field_validator("statement")
    @classmethod
    def _validate_statement(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Claim statement cannot be empty.")
        return cleaned

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    def to_persistence(self) -> ClaimModel:
        return ClaimModel(
            id=self.id,
            research_run_id=self.research_run_id,
            text=self.statement,
            claim_type=self.claim_type.value,
            confidence=self.confidence,
            status=self.status.value,
            created_by=self.created_by,
            metadata_json=unfreeze_value(self.metadata),
            created_at=self.created_at,
        )

    @classmethod
    def from_persistence(cls, model: ClaimModel) -> "Claim":
        try:
            c_type = ClaimType(model.claim_type)
        except ValueError:
            c_type = ClaimType.OBSERVATION

        try:
            c_status = ClaimStatus(model.status)
        except ValueError:
            c_status = ClaimStatus.DRAFT

        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            statement=model.text,
            claim_type=c_type,
            status=c_status,
            confidence=model.confidence,
            created_by=getattr(model, "created_by", "system"),
            metadata=getattr(model, "metadata_json", {}) or {},
            created_at=model.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "research_run_id": self.research_run_id,
            "statement": self.statement,
            "claim_type": self.claim_type.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "created_by": self.created_by,
            "metadata": unfreeze_value(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


class EvidenceLink(BaseModel):
    """Immutable domain representation of an explicit evidence relation edge (REX-023)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        default_factory=_gen_link_id, description="Stable unique evidence link identifier"
    )
    research_run_id: str = Field(description="Scoped research run identifier")
    source_type: EvidenceNodeType = Field(description="Evidence classification of source node")
    source_id: str = Field(description="Identifier of source node")
    target_type: EvidenceNodeType = Field(
        default=EvidenceNodeType.CLAIM, description="Evidence classification of target node"
    )
    target_id: str = Field(description="Identifier of target node")
    relationship_type: EvidenceRelationType = Field(
        default=EvidenceRelationType.SUPPORTED_BY, description="Directional semantic relation"
    )
    created_by: str = Field(default="system", description="Actor who created link")
    metadata: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Supplemental edge attributes",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC creation timestamp"
    )

    @field_validator("id", "research_run_id", "source_id", "target_id")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier cannot be empty.")
        return cleaned

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    def to_persistence(self) -> EvidenceLinkModel:
        claim_id = (
            self.target_id
            if self.target_type == EvidenceNodeType.CLAIM
            else (self.source_id if self.source_type == EvidenceNodeType.CLAIM else None)
        )
        return EvidenceLinkModel(
            id=self.id,
            claim_id=claim_id,
            source_type=self.source_type.value,
            source_id=self.source_id,
            target_type=self.target_type.value,
            target_id=self.target_id,
            relationship_type=self.relationship_type.value,
            research_run_id=self.research_run_id,
            created_by=self.created_by,
            metadata_json=unfreeze_value(self.metadata),
            created_at=self.created_at,
        )

    @classmethod
    def from_persistence(cls, model: EvidenceLinkModel) -> "EvidenceLink":
        target_t = (
            EvidenceNodeType(model.target_type)
            if hasattr(model, "target_type") and model.target_type
            else EvidenceNodeType.CLAIM
        )
        target_i = getattr(model, "target_id", None) or model.claim_id or ""
        run_id = getattr(model, "research_run_id", None) or ""
        return cls(
            id=model.id,
            research_run_id=run_id,
            source_type=EvidenceNodeType(model.source_type),
            source_id=model.source_id,
            target_type=target_t,
            target_id=target_i,
            relationship_type=EvidenceRelationType(model.relationship_type),
            created_by=getattr(model, "created_by", "system"),
            metadata=getattr(model, "metadata_json", {}) or {},
            created_at=model.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "research_run_id": self.research_run_id,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type.value,
            "created_by": self.created_by,
            "metadata": unfreeze_value(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


def _gen_literature_id() -> str:
    return f"lit_{uuid.uuid4().hex[:12]}"


class LiteratureSource(BaseModel):
    """Immutable domain representation of an external scholarly literature source (REX-028)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        default_factory=_gen_literature_id,
        description="Stable unique literature source identifier",
    )
    research_run_id: str = Field(description="Associated research investigation identifier")
    provider: str = Field(
        description="Scholarly provider name (e.g. openalex, semantic_scholar, arxiv)"
    )
    external_id: str = Field(description="Provider-native paper or work identifier")
    title: str = Field(description="Standardized title of the publication")
    authors: tuple[str, ...] = Field(
        default_factory=tuple, description="Immutable tuple of contributing author names"
    )
    year: int | None = Field(default=None, description="Publication year")
    abstract: str = Field(default="", description="Abstract or summary text")
    url: str = Field(default="", description="Canonical landing page or preprint URL")
    citation_count: int | None = Field(
        default=None, ge=0, description="Optional scholarly citation count"
    )
    doi: str | None = Field(default=None, description="Optional digital object identifier")
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC timestamp of retrieval"
    )
    raw_metadata: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Preserved raw provider metadata payload",
    )

    @field_validator("id", "research_run_id", "provider", "external_id")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty.")
        return cleaned

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Title cannot be empty.")
        return cleaned

    @field_validator("authors", mode="before")
    @classmethod
    def _validate_authors(cls, v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, (list, tuple, set)):
            return tuple(str(a).strip() for a in v if str(a).strip())
        return (str(v).strip(),)

    @field_validator("raw_metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, v: Any) -> Mapping[str, Any]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)

    def content_hash(self) -> str:
        """Deterministic SHA256 fingerprint of scholarly source identity and textual content."""
        from rex.evidence.hashing import canonical_json_hash

        payload = {
            "provider": self.provider,
            "external_id": self.external_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "abstract": self.abstract,
        }
        return canonical_json_hash(payload)

    def to_persistence(self) -> LiteratureSourceModel:
        raw_meta = dict(unfreeze_value(self.raw_metadata))
        if self.citation_count is not None and "citation_count" not in raw_meta:
            raw_meta["citation_count"] = self.citation_count
        if self.doi is not None and "doi" not in raw_meta:
            raw_meta["doi"] = self.doi

        return LiteratureSourceModel(
            id=self.id,
            research_run_id=self.research_run_id,
            provider=self.provider,
            external_id=self.external_id,
            title=self.title,
            authors_json=list(self.authors),
            year=self.year,
            abstract=self.abstract,
            url=self.url,
            retrieved_at=self.retrieved_at,
            raw_metadata_json=raw_meta,
        )

    @classmethod
    def from_persistence(cls, model: LiteratureSourceModel) -> "LiteratureSource":
        authors = tuple(model.authors_json or [])
        meta = dict(getattr(model, "raw_metadata_json", {}) or {})
        citation_count = meta.get("citation_count")
        doi = meta.get("doi")
        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            provider=model.provider,
            external_id=model.external_id,
            title=model.title,
            authors=authors,
            year=model.year,
            abstract=model.abstract or "",
            url=model.url or "",
            citation_count=citation_count if isinstance(citation_count, int) else None,
            doi=str(doi) if doi is not None else None,
            retrieved_at=model.retrieved_at,
            raw_metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "research_run_id": self.research_run_id,
            "provider": self.provider,
            "external_id": self.external_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "abstract": self.abstract,
            "url": self.url,
            "citation_count": self.citation_count,
            "doi": self.doi,
            "retrieved_at": self.retrieved_at.isoformat(),
            "raw_metadata": unfreeze_value(self.raw_metadata),
            "content_hash": self.content_hash(),
        }


class CritiqueSeverity(StrEnum):
    """Explicit severity classification for research critique findings (REX-033)."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CritiqueCategory(StrEnum):
    """Categorical dimensions audited by the research critic (REX-033)."""

    BASELINE = "baseline"
    CONTROLS = "controls"
    LEAKAGE = "leakage"
    SAMPLE_SIZE = "sample_size"
    SEEDS = "seeds"
    METRICS = "metrics"
    CONFOUNDERS = "confounders"
    CONCLUSION_SCOPE = "conclusion_scope"
    METHODOLOGY = "methodology"


class EpistemicStatus(StrEnum):
    """Epistemic evidence classification required by authoritative evidence discipline."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    PROPOSED = "proposed"
    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class CritiqueFinding(BaseModel):
    """Individual methodological or evidentiary finding identified by the research critic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(description="Unique finding identifier within critique")
    category: CritiqueCategory = Field(description="Methodological category evaluated")
    severity: CritiqueSeverity = Field(description="Severity classification of the concern")
    description: str = Field(description="Concrete critique statement explaining the concern")
    epistemic_status: EpistemicStatus = Field(
        default=EpistemicStatus.OBSERVED,
        description="Epistemic standing of the evidence supporting this finding",
    )
    evidence_refs: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Persisted entity IDs grounding finding",
    )
    recommendation: str = Field(
        default="", description="Concrete actionable remediation recommended by critic"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "description": self.description,
            "epistemic_status": self.epistemic_status.value,
            "evidence_refs": list(self.evidence_refs),
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CritiqueFinding":
        return cls(
            finding_id=str(data.get("finding_id", uuid.uuid4().hex[:8])),
            category=CritiqueCategory(data.get("category", "methodology")),
            severity=CritiqueSeverity(data.get("severity", "medium")),
            description=str(data.get("description", "")),
            epistemic_status=EpistemicStatus(data.get("epistemic_status", "observed")),
            evidence_refs=tuple(data.get("evidence_refs", [])),
            recommendation=str(data.get("recommendation", "")),
        )


class ResearchCritique(BaseModel):
    """Structured methodological critique of an active research investigation (REX-033)."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=lambda: f"crt_{uuid.uuid4().hex[:12]}")
    research_run_id: str = Field(description="Research run identifier")
    iteration: int = Field(default=1, ge=1, description="Loop iteration index")
    summary: str = Field(description="High-level overview of critique assessment")
    strengths: tuple[str, ...] = Field(default_factory=tuple)
    weaknesses: tuple[str, ...] = Field(default_factory=tuple)
    contradictions: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_questions: tuple[str, ...] = Field(default_factory=tuple)
    methodological_concerns: tuple[str, ...] = Field(default_factory=tuple)
    findings: tuple[CritiqueFinding, ...] = Field(default_factory=tuple)
    recommended_action: str = Field(
        default="refine",
        description="Recommended next action (refine, replicate, pivot, stop, complete)",
    )
    recommended_action_rationale: str = Field(
        default="", description="Detailed rationale supporting recommendation"
    )
    referenced_evidence: MappingProxyType = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Resolved references to actual persisted database entities",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = Field(default="critic")

    @property
    def has_critical_findings(self) -> bool:
        return any(f.severity == CritiqueSeverity.CRITICAL for f in self.findings)

    def to_persistence(self) -> CritiqueModel:
        return CritiqueModel(
            id=self.id,
            research_run_id=self.research_run_id,
            iteration=self.iteration,
            summary=self.summary,
            strengths_json=list(self.strengths),
            weaknesses_json=list(self.weaknesses),
            contradictions_json=list(self.contradictions),
            unresolved_questions_json=list(self.unresolved_questions),
            methodological_concerns_json=list(self.methodological_concerns),
            findings_json=[f.to_dict() for f in self.findings],
            recommended_action=self.recommended_action,
            recommended_action_rationale=self.recommended_action_rationale,
            referenced_evidence_json=unfreeze_value(self.referenced_evidence),
            created_at=self.created_at,
            created_by=self.created_by,
        )

    @classmethod
    def from_persistence(cls, model: CritiqueModel) -> "ResearchCritique":
        raw_findings = model.findings_json or []
        findings = tuple(CritiqueFinding.from_dict(f) for f in raw_findings if isinstance(f, dict))
        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            iteration=model.iteration,
            summary=model.summary,
            strengths=tuple(model.strengths_json or []),
            weaknesses=tuple(model.weaknesses_json or []),
            contradictions=tuple(model.contradictions_json or []),
            unresolved_questions=tuple(model.unresolved_questions_json or []),
            methodological_concerns=tuple(model.methodological_concerns_json or []),
            findings=findings,
            recommended_action=model.recommended_action,
            recommended_action_rationale=model.recommended_action_rationale or "",
            referenced_evidence=freeze_value(model.referenced_evidence_json or {}),
            created_at=model.created_at,
            created_by=model.created_by,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "research_run_id": self.research_run_id,
            "iteration": self.iteration,
            "summary": self.summary,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "contradictions": list(self.contradictions),
            "unresolved_questions": list(self.unresolved_questions),
            "methodological_concerns": list(self.methodological_concerns),
            "findings": [f.to_dict() for f in self.findings],
            "recommended_action": self.recommended_action,
            "recommended_action_rationale": self.recommended_action_rationale,
            "referenced_evidence": unfreeze_value(self.referenced_evidence),
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }


class DecisionType(StrEnum):
    """Authoritative action vocabulary for research decision engine (REX-034)."""

    REFINE = "refine"
    REPLICATE = "replicate"
    PIVOT = "pivot"
    STOP = "stop"
    COMPLETE = "complete"
    FAILED = "failed"


class ResearchDecision(BaseModel):
    """Structured, deterministically validated research next-action decision (REX-034)."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    id: str = Field(default_factory=lambda: f"dec_{uuid.uuid4().hex[:12]}")
    research_run_id: str = Field(description="Research run identifier")
    iteration: int = Field(default=1, ge=1, description="Loop iteration index")
    action: DecisionType = Field(description="Validated next-action decision")
    target_entity_type: str | None = Field(
        default=None, description="Type of entity targeted (e.g. experiment, hypothesis)"
    )
    target_entity_id: str | None = Field(default=None, description="ID of specific entity targeted")
    rationale: str = Field(description="Explanatory rationale for decision")
    critique_id: str | None = Field(
        default=None, description="ID of motivating critique if applicable"
    )
    is_validated: bool = Field(default=False, description="Whether deterministic validation passed")
    validation_errors: tuple[str, ...] = Field(default_factory=tuple)
    budget_checked: bool = Field(default=False)
    budget_remaining: MappingProxyType = Field(default_factory=lambda: MappingProxyType({}))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = Field(default="decision_engine")

    def to_persistence(self) -> DecisionModel:
        return DecisionModel(
            id=self.id,
            research_run_id=self.research_run_id,
            iteration=self.iteration,
            action=self.action.value,
            target_entity_type=self.target_entity_type,
            target_entity_id=self.target_entity_id,
            rationale=self.rationale,
            critique_id=self.critique_id,
            is_validated=self.is_validated,
            validation_errors_json=list(self.validation_errors),
            budget_checked=self.budget_checked,
            budget_remaining_json=unfreeze_value(self.budget_remaining),
            created_at=self.created_at,
            created_by=self.created_by,
        )

    @classmethod
    def from_persistence(cls, model: DecisionModel) -> "ResearchDecision":
        return cls(
            id=model.id,
            research_run_id=model.research_run_id,
            iteration=model.iteration,
            action=DecisionType(model.action.lower()),
            target_entity_type=model.target_entity_type,
            target_entity_id=model.target_entity_id,
            rationale=model.rationale,
            critique_id=model.critique_id,
            is_validated=model.is_validated,
            validation_errors=tuple(model.validation_errors_json or []),
            budget_checked=model.budget_checked,
            budget_remaining=freeze_value(model.budget_remaining_json or {}),
            created_at=model.created_at,
            created_by=model.created_by,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "research_run_id": self.research_run_id,
            "iteration": self.iteration,
            "action": self.action.value,
            "target_entity_type": self.target_entity_type,
            "target_entity_id": self.target_entity_id,
            "rationale": self.rationale,
            "critique_id": self.critique_id,
            "is_validated": self.is_validated,
            "validation_errors": list(self.validation_errors),
            "budget_checked": self.budget_checked,
            "budget_remaining": unfreeze_value(self.budget_remaining),
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }
