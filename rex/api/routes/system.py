"""System health and operational telemetry API endpoints (REX-037)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rex.api.routes.research import get_db
from rex.api.schemas import SystemStatusResponse
from rex.config import get_settings
from rex.persistence.models import (
    ClaimModel,
    ExperimentModel,
    ResearchRunModel,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    """Basic health check probe."""
    return {"status": "ok", "app": "rex", "version": "v0.8.0"}


@router.get("/status", response_model=SystemStatusResponse)
def system_status(
    session: Session = Depends(get_db),
) -> SystemStatusResponse:
    """Retrieve operational environment metrics and status."""
    settings = get_settings()

    total_runs = session.scalar(select(func.count(ResearchRunModel.id))) or 0
    active_runs = (
        session.scalar(
            select(func.count(ResearchRunModel.id)).where(
                ResearchRunModel.status.notin_(["COMPLETE", "FAILED", "STOP"])
            )
        )
        or 0
    )
    exp_count = session.scalar(select(func.count(ExperimentModel.id))) or 0
    claim_count = session.scalar(select(func.count(ClaimModel.id))) or 0

    return SystemStatusResponse(
        status="running",
        active_runs_count=active_runs,
        total_runs_count=total_runs,
        experiments_count=exp_count,
        claims_count=claim_count,
        reports_count=total_runs,  # Every run has a potential/actual report
        app_name=settings.app.name.upper(),
        version="v0.8.0",
        environment=settings.app.environment,
        docker_enabled=settings.docker.enabled,
        database_url_masked=settings.persistence.database_url.split("?")[0],
    )
