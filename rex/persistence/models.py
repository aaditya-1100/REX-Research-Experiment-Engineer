"""SQLAlchemy 2.x Declarative Models for REX (REX-004).

Implements the 11 authoritative database schema tables defined in 02_Technical_Architecture.md §6:
1. research_runs
2. hypotheses
3. experiments
4. executions
5. results
6. analyses
7. artifacts
8. literature_sources
9. claims
10. evidence_links
11. events
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rex.persistence.database import Base


def _gen_id(prefix: str = "") -> str:
    """Generate a unique hex string identifier with optional prefix."""
    uid = uuid.uuid4().hex
    return f"{prefix}_{uid[:12]}" if prefix else uid


class ResearchRunModel(Base):
    """Top-level research investigation."""

    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("run"))
    title: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="INITIALIZE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    hypotheses: Mapped[list["HypothesisModel"]] = relationship(
        "HypothesisModel", back_populates="research_run", cascade="all, delete-orphan"
    )
    experiments: Mapped[list["ExperimentModel"]] = relationship(
        "ExperimentModel", back_populates="research_run", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["AnalysisModel"]] = relationship(
        "AnalysisModel", back_populates="research_run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["ArtifactModel"]] = relationship(
        "ArtifactModel", back_populates="research_run", cascade="all, delete-orphan"
    )
    literature_sources: Mapped[list["LiteratureSourceModel"]] = relationship(
        "LiteratureSourceModel", back_populates="research_run", cascade="all, delete-orphan"
    )
    claims: Mapped[list["ClaimModel"]] = relationship(
        "ClaimModel", back_populates="research_run", cascade="all, delete-orphan"
    )
    events: Mapped[list["EventModel"]] = relationship(
        "EventModel", back_populates="research_run", cascade="all, delete-orphan"
    )


class HypothesisModel(Base):
    """Structured testable research hypothesis."""

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("hyp"))
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(32), default="increase", nullable=False)
    falsification_condition: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="hypotheses"
    )
    experiments: Mapped[list["ExperimentModel"]] = relationship(
        "ExperimentModel", back_populates="hypothesis"
    )


class ExperimentModel(Base):
    """Immutable experiment specification and parent/child tracking."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("exp"))
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hypothesis_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    specification_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="designed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    parent_experiment_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="experiments"
    )
    hypothesis: Mapped["HypothesisModel | None"] = relationship(
        "HypothesisModel", back_populates="experiments"
    )
    parent_experiment: Mapped["ExperimentModel | None"] = relationship(
        "ExperimentModel", remote_side=[id], backref="child_experiments"
    )
    executions: Mapped[list["ExecutionModel"]] = relationship(
        "ExecutionModel", back_populates="experiment", cascade="all, delete-orphan"
    )

    def __init__(self, **kwargs: Any) -> None:
        if "title" in kwargs and "objective" not in kwargs:
            kwargs["objective"] = kwargs.pop("title")
        if "parameters_json" in kwargs and "specification_json" not in kwargs:
            kwargs["specification_json"] = kwargs.pop("parameters_json")
        super().__init__(**kwargs)

    @property
    def title(self) -> str:
        return self.objective

    @title.setter
    def title(self, value: str) -> None:
        self.objective = value

    @property
    def parameters_json(self) -> dict[str, Any]:
        return self.specification_json

    @parameters_json.setter
    def parameters_json(self, value: dict[str, Any]) -> None:
        self.specification_json = value


class ExecutionModel(Base):
    """Execution run instance with code version, seed, configuration, and telemetry."""

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("exec"))
    experiment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    command: Mapped[str] = mapped_column(Text, default="", nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    environment_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    resource_usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stderr_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    experiment: Mapped["ExperimentModel"] = relationship(
        "ExperimentModel", back_populates="executions"
    )
    results: Mapped[list["ResultModel"]] = relationship(
        "ResultModel", back_populates="execution", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["ArtifactModel"]] = relationship(
        "ArtifactModel", back_populates="execution"
    )


class ResultModel(Base):
    """Raw metric outputs produced by an execution run."""

    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("res"))
    execution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_unit: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    execution: Mapped["ExecutionModel"] = relationship("ExecutionModel", back_populates="results")


class AnalysisModel(Base):
    """Deterministic statistical analyses derived from stored results."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("an"))
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_type: Mapped[str] = mapped_column(String(64), nullable=False)
    input_result_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    method: Mapped[str] = mapped_column(String(128), nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="analyses"
    )


class ArtifactModel(Base):
    """Integrity metadata for disk-stored artifacts (plots, logs, checkpoints)."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("art"))
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("executions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="artifacts"
    )
    execution: Mapped["ExecutionModel | None"] = relationship(
        "ExecutionModel", back_populates="artifacts"
    )


class LiteratureSourceModel(Base):
    """Scholarly publication retrieved and verified from external scholarly providers."""

    __tablename__ = "literature_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("lit"))
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstract: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    raw_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="literature_sources"
    )


class ClaimModel(Base):
    """High-level empirical or methodological claim asserted by research agents."""

    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("clm"))
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), default="empirical", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="claims"
    )
    evidence_links: Mapped[list["EvidenceLinkModel"]] = relationship(
        "EvidenceLinkModel", back_populates="claim", cascade="all, delete-orphan"
    )

    def __init__(self, **kwargs: Any) -> None:
        if "statement" in kwargs and "text" not in kwargs:
            kwargs["text"] = kwargs.pop("statement")
        if "confidence_score" in kwargs and "confidence" not in kwargs:
            kwargs["confidence"] = kwargs.pop("confidence_score")
        super().__init__(**kwargs)

    @property
    def statement(self) -> str:
        return self.text

    @statement.setter
    def statement(self, value: str) -> None:
        self.text = value

    @property
    def confidence_score(self) -> float | None:
        return self.confidence

    @confidence_score.setter
    def confidence_score(self, value: float | None) -> None:
        self.confidence = value


class EvidenceLinkModel(Base):
    """Explicit provenance edge linking claims to results, analyses, or literature."""

    __tablename__ = "evidence_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _gen_id("lnk"))
    claim_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("claims.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), default="claim", nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(
        String(64), default="supported_by", nullable=False
    )
    research_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    claim: Mapped["ClaimModel | None"] = relationship("ClaimModel", back_populates="evidence_links")


class EventModel(Base):
    """Historical research lifecycle audit event record."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    research_run: Mapped["ResearchRunModel"] = relationship(
        "ResearchRunModel", back_populates="events"
    )
