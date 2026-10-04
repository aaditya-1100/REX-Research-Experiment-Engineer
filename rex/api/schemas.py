"""Pydantic Request and Response Schemas for REX HTTP API (REX-037).

Provides strongly typed schemas for research runs, hypotheses, experiments, executions,
metrics, analyses, evidence links, claims, verification reports, and system telemetry.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    """Base schema with standard serialization configuration."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


# --- System & Settings ---


class SystemStatusResponse(BaseSchema):
    """Aggregate health and operational metrics of the REX environment."""

    status: str = Field(default="running", description="Operational status of backend")
    active_runs_count: int = 0
    total_runs_count: int = 0
    experiments_count: int = 0
    claims_count: int = 0
    reports_count: int = 0
    app_name: str = "REX"
    version: str = "v0.8.0"
    environment: str = "development"
    docker_enabled: bool = True
    database_url_masked: str = ""


class SettingsResponse(BaseSchema):
    """Public system configuration with masked secrets."""

    app: dict[str, Any]
    persistence: dict[str, Any]
    docker: dict[str, Any]
    budgets: dict[str, Any]
    literature: dict[str, Any]
    llm: dict[str, Any]


# --- Research Run ---


class CreateResearchRunRequest(BaseSchema):
    """Payload to initiate a new scientific research investigation."""

    research_question: str = Field(..., min_length=3, description="Core scientific question")
    title: str = Field(default="", description="Optional human-readable title")
    configuration: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


class RunActionRequest(BaseSchema):
    """Payload for manual or autonomous lifecycle actions."""

    reason: str | None = Field(default=None, description="Action rationale")
    target_state: str | None = Field(default=None, description="Target lifecycle state")


class ResearchRunStats(BaseSchema):
    """Summary counts for a research run."""

    hypotheses_count: int = 0
    experiments_count: int = 0
    verified_results_count: int = 0
    pending_count: int = 0
    claims_count: int = 0
    verified_claims_count: int = 0
    compute_cost_estimate: float = 0.0


class ResearchRunResponse(BaseSchema):
    """Detailed view of a research run."""

    id: str
    title: str
    research_question: str
    status: str
    created_at: datetime
    updated_at: datetime
    configuration: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    stats: ResearchRunStats = Field(default_factory=ResearchRunStats)
    current_action: str | None = None
    current_action_reason: str | None = None
    current_action_progress: float | None = None
    next_action: str | None = None


# --- Hypotheses ---


class HypothesisResponse(BaseSchema):
    """Scientific hypothesis model."""

    id: str
    research_run_id: str
    statement: str
    rationale: str = ""
    expected_direction: str = "increase"
    falsification_condition: str
    status: str = "proposed"
    created_at: datetime


# --- Experiments ---


class ExperimentSpecificationResponse(BaseSchema):
    """Specification details for an experiment."""

    name: str = ""
    description: str = ""
    method: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)
    controls: dict[str, Any] = Field(default_factory=dict)
    baseline: dict[str, Any] = Field(default_factory=dict)
    datasets: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=lambda: [42])
    repetitions: int = 1
    analysis_methods: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    falsification_criteria: str = ""


class ExperimentResponse(BaseSchema):
    """Experiment specification and execution summary."""

    id: str
    research_run_id: str
    hypothesis_id: str | None = None
    objective: str
    specification: dict[str, Any] = Field(default_factory=dict)
    status: str = "designed"
    created_at: datetime
    parent_experiment_id: str | None = None
    execution_count: int = 0
    latest_status: str | None = None
    latest_execution_id: str | None = None
    latest_metrics: dict[str, Any] = Field(default_factory=dict)


# --- Executions & Results ---


class ExecutionResponse(BaseSchema):
    """Concrete execution attempt details."""

    id: str
    experiment_id: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    command: str = ""
    git_commit: str = ""
    code_hash: str = ""
    dataset_hash: str = ""
    configuration_hash: str = ""
    seed: int | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    exit_code: int | None = None
    stdout_artifact_id: str | None = None
    stderr_artifact_id: str | None = None
    duration_seconds: float | None = None


class ResultResponse(BaseSchema):
    """Raw metric observation produced by execution."""

    id: str
    execution_id: str
    metric_name: str
    metric_value: float | None = None
    metric_unit: str = ""
    result_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MetricSeriesPoint(BaseSchema):
    """A data point for metric over time/steps."""

    step: int | float
    value: float
    series_name: str
    timestamp: datetime | None = None


class ExperimentComparisonMetric(BaseSchema):
    """Metric comparison row."""

    metric_name: str
    values_by_experiment: dict[str, float | None] = Field(default_factory=dict)
    unit: str = ""
    direction: str = "maximize"


class CompareExperimentsRequest(BaseSchema):
    """Request payload to compare multiple experiments."""

    experiment_ids: list[str] = Field(..., min_length=1, description="List of experiment IDs to compare")


class ExperimentComparisonResponse(BaseSchema):
    """Multi-experiment side-by-side comparison payload."""

    experiment_ids: list[str]
    experiments: list[ExperimentResponse]
    metrics_summary: list[ExperimentComparisonMetric] = Field(default_factory=list)
    series_data: list[MetricSeriesPoint] = Field(default_factory=list)


# --- Evidence & Lineage ---


class ClaimResponse(BaseSchema):
    """Empirical or scientific claim asserted during research."""

    id: str
    research_run_id: str
    text: str
    statement: str
    claim_type: str = "observation"
    status: str = "proposed"
    confidence: float | None = None
    created_by: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    evidence_links_count: int = 0


class EvidenceLinkResponse(BaseSchema):
    """Direct edge connecting claim to supporting empirical nodes."""

    id: str
    claim_id: str | None = None
    source_type: str
    source_id: str
    target_type: str = "claim"
    target_id: str = ""
    relationship_type: str = "supported_by"
    research_run_id: str | None = None
    created_by: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class LineageNode(BaseSchema):
    """A node in the evidence lineage chain (Claim -> Analysis -> Result -> Execution -> etc.)."""

    id: str
    type: str  # claim, analysis, result, execution, experiment, code, dataset, configuration, artifact
    label: str
    sublabel: str = ""
    status: str = "verified"  # verified, unverified, unsupported, missing, tampered
    hash: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseSchema):
    """Directed connection in evidence lineage."""

    source_id: str
    target_id: str
    relation: str = "supported_by"
    is_valid: bool = True


class ClaimLineageResponse(BaseSchema):
    """Full deterministic lineage trace for a claim."""

    claim_id: str
    statement: str
    status: str
    is_complete: bool
    gaps: list[str] = Field(default_factory=list)
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)


# --- Verification ---


class VerificationCheckItem(BaseSchema):
    """Individual verification checklist item."""

    name: str
    status: str  # pass, fail, warning
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationReportResponse(BaseSchema):
    """Detailed formal verification result."""

    research_run_id: str
    status: str  # pass, warning, fail
    is_passed: bool
    checks: list[VerificationCheckItem] = Field(default_factory=list)
    claims_verified: list[dict[str, Any]] = Field(default_factory=list)
    artifacts_verified: list[dict[str, Any]] = Field(default_factory=list)
    analyses_recomputed: list[dict[str, Any]] = Field(default_factory=list)
    cross_run_violations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime


# --- Reports ---


class ResearchReportResponse(BaseSchema):
    """Evidence-grounded research report."""

    research_run_id: str
    title: str
    generated_at: str
    is_fully_grounded: bool
    executive_summary: str
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    experiments: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    analyses: list[dict[str, Any]] = Field(default_factory=list)
    critiques: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conclusions: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_claims: list[dict[str, Any]] = Field(default_factory=list)
    failed_experiments: list[dict[str, Any]] = Field(default_factory=list)
    markdown: str = ""


# --- Artifacts ---


class ArtifactResponse(BaseSchema):
    """Filesystem artifact metadata."""

    id: str
    research_run_id: str
    execution_id: str | None = None
    artifact_type: str = "other"
    path: str
    content_hash: str
    size_bytes: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- Events ---


class EventResponse(BaseSchema):
    """Structured audit event."""

    event_id: str
    timestamp: datetime
    event_type: str
    actor: str
    research_run_id: str
    experiment_id: str | None = None
    execution_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
