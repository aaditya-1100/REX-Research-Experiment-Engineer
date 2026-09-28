"""REX Domain Entities and Lifecycle Vocabulary (REX-005, REX-006).

Defines strongly typed domain representations for research investigations, hypotheses,
and authoritative lifecycle states.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.persistence.models import HypothesisModel, ResearchRunModel


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
