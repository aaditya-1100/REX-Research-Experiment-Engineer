"""REX Domain Entities and Lifecycle Vocabulary (REX-005, REX-006, REX-007).

Defines strongly typed domain representations for research investigations, hypotheses,
experiment specifications, and authoritative lifecycle states.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.observability.events import freeze_value, unfreeze_value
from rex.persistence.models import ExperimentModel, HypothesisModel, ResearchRunModel


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
