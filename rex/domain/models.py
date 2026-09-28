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
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
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


TERMINAL_EXECUTION_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
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
