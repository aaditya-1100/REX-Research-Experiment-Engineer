"""Experiment API endpoints (REX-037, REX-039).

Handles experiment inspection, parameter specifications, metric extractions,
execution tracking, side-by-side comparison, and reproducibility assessments.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from rex.api.routes.research import get_db
from rex.api.schemas import (
    CompareExperimentsRequest,
    ExecutionResponse,
    ExperimentComparisonMetric,
    ExperimentComparisonResponse,
    ExperimentResponse,
    MetricSeriesPoint,
)
from rex.evidence.reproduce import ExperimentReproducer
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    ResultModel,
)
from rex.persistence.repositories import (
    ExecutionRepository,
)

router = APIRouter(prefix="/experiments", tags=["experiments"])


def _build_experiment_response(session: Session, exp: ExperimentModel) -> ExperimentResponse:
    executions = session.scalars(
        select(ExecutionModel)
        .where(ExecutionModel.experiment_id == exp.id)
        .order_by(ExecutionModel.started_at.desc())
    ).all()
    latest = executions[0] if executions else None

    latest_metrics = {}
    if latest:
        results = session.scalars(
            select(ResultModel).where(ResultModel.execution_id == latest.id)
        ).all()
        for r in results:
            latest_metrics[r.metric_name] = r.metric_value

    return ExperimentResponse(
        id=exp.id,
        research_run_id=exp.research_run_id,
        hypothesis_id=exp.hypothesis_id,
        objective=exp.objective,
        specification=exp.specification_json or {},
        status=exp.status,
        created_at=exp.created_at,
        parent_experiment_id=exp.parent_experiment_id,
        execution_count=len(executions),
        latest_status=latest.status if latest else None,
        latest_execution_id=latest.id if latest else None,
        latest_metrics=latest_metrics,
    )


@router.get("", response_model=list[ExperimentResponse])
def list_experiments(
    run_id: str | None = Query(None, alias="run_id"),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[ExperimentResponse]:
    """List experiments across research runs or filtered by run and status."""
    stmt = select(ExperimentModel).order_by(ExperimentModel.created_at.desc())
    if run_id:
        stmt = stmt.where(ExperimentModel.research_run_id == run_id)
    if status_filter:
        stmt = stmt.where(ExperimentModel.status == status_filter.lower())

    stmt = stmt.offset(offset).limit(limit)
    models = session.scalars(stmt).all()
    return [_build_experiment_response(session, m) for m in models]


def _do_compare_experiments(exp_ids: list[str], session: Session) -> ExperimentComparisonResponse:
    if not exp_ids:
        raise HTTPException(status_code=400, detail="Must provide at least one experiment ID.")

    exp_models = session.scalars(
        select(ExperimentModel).where(ExperimentModel.id.in_(exp_ids))
    ).all()
    if not exp_models:
        raise HTTPException(status_code=404, detail="No matching experiments found.")

    run_ids = {m.research_run_id for m in exp_models if m.research_run_id}
    if len(run_ids) > 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot compare experiments across different research runs",
        )

    exp_responses = [_build_experiment_response(session, m) for m in exp_models]

    # Gather metrics across all executions of these experiments
    all_metrics: dict[str, dict[str, float | None]] = {}
    series_points: list[MetricSeriesPoint] = []

    for exp in exp_models:
        executions = session.scalars(
            select(ExecutionModel)
            .where(ExecutionModel.experiment_id == exp.id)
            .order_by(ExecutionModel.started_at.asc())
        ).all()

        for exec_idx, ex in enumerate(executions):
            results = session.scalars(
                select(ResultModel).where(ResultModel.execution_id == ex.id)
            ).all()

            for res in results:
                m_name = res.metric_name
                if m_name not in all_metrics:
                    all_metrics[m_name] = {}
                # Record latest value for experiment
                all_metrics[m_name][exp.id] = res.metric_value

                # Add series data if numeric
                if res.metric_value is not None:
                    step = exec_idx + 1
                    # Check if result_json has step/epoch
                    if isinstance(res.result_json, dict) and "step" in res.result_json:
                        step = res.result_json["step"]
                    elif isinstance(res.result_json, dict) and "epoch" in res.result_json:
                        step = res.result_json["epoch"]

                    series_points.append(
                        MetricSeriesPoint(
                            step=step,
                            value=res.metric_value,
                            series_name=f"{exp.objective} ({exp.id})",
                            timestamp=res.created_at,
                        )
                    )

    metrics_summary = [
        ExperimentComparisonMetric(
            metric_name=name,
            values_by_experiment=vals,
        )
        for name, vals in all_metrics.items()
    ]

    return ExperimentComparisonResponse(
        experiment_ids=exp_ids,
        experiments=exp_responses,
        metrics_summary=metrics_summary,
        series_data=series_points,
    )


@router.get("/compare", response_model=ExperimentComparisonResponse)
def compare_experiments(
    ids: str = Query(..., description="Comma-separated experiment IDs to compare"),
    session: Session = Depends(get_db),
) -> ExperimentComparisonResponse:
    """Compare multiple experiments side-by-side with metrics, hyperparameters, and series (GET)."""
    exp_ids = [i.strip() for i in ids.split(",") if i.strip()]
    return _do_compare_experiments(exp_ids, session)


@router.post("/compare", response_model=ExperimentComparisonResponse)
def compare_experiments_post(
    payload: CompareExperimentsRequest,
    session: Session = Depends(get_db),
) -> ExperimentComparisonResponse:
    """Compare multiple experiments side-by-side with metrics, hyperparameters, and series (POST)."""
    return _do_compare_experiments(payload.experiment_ids, session)


@router.get("/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(
    experiment_id: str,
    session: Session = Depends(get_db),
) -> ExperimentResponse:
    """Retrieve full experiment detail."""
    model = session.get(ExperimentModel, experiment_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found.")
    return _build_experiment_response(session, model)


@router.get("/{experiment_id}/runs", response_model=list[ExecutionResponse])
def list_experiment_executions(
    experiment_id: str,
    session: Session = Depends(get_db),
) -> list[ExecutionResponse]:
    """List execution attempts for an experiment."""
    repo = ExecutionRepository(session)
    models = repo.list_by_experiment(experiment_id)
    responses = []
    for m in models:
        duration = None
        if m.started_at and m.finished_at:
            duration = (m.finished_at - m.started_at).total_seconds()
        elif isinstance(m.resource_usage_json, dict):
            duration = m.resource_usage_json.get("duration_seconds")

        responses.append(
            ExecutionResponse(
                id=m.id,
                experiment_id=m.experiment_id,
                status=m.status,
                started_at=m.started_at,
                finished_at=m.finished_at,
                command=m.command,
                git_commit=m.git_commit,
                code_hash=m.code_hash,
                dataset_hash=m.dataset_hash,
                configuration_hash=m.configuration_hash,
                seed=m.seed,
                environment=m.environment_json or {},
                resource_usage=m.resource_usage_json or {},
                exit_code=m.exit_code,
                stdout_artifact_id=m.stdout_artifact_id,
                stderr_artifact_id=m.stderr_artifact_id,
                duration_seconds=duration,
            )
        )
    return responses


@router.get("/{experiment_id}/reproducibility")
def assess_reproducibility(
    experiment_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Assess readiness of experiment for mechanical reproduction (REX-027)."""
    exp = session.get(ExperimentModel, experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found.")

    reproducer = ExperimentReproducer(session=session)
    assessment = reproducer.assess_reproducibility(experiment_id)

    res = assessment.as_dict()
    res["is_reproducible"] = assessment.is_executable
    return res
