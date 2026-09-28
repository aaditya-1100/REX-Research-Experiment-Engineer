"""REX Research Run Domain Model and Lifecycle States (REX-005).

Defines strongly typed domain representations for research investigations and the
authoritative research lifecycle state enum.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.persistence.models import ResearchRunModel


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


def _gen_run_id() -> str:
    """Generate a stable, unique research run identifier."""
    return f"run_{uuid.uuid4().hex[:12]}"


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
