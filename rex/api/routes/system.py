"""System health and operational telemetry API endpoints (REX-037, REX-038)."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rex.api.routes.research import get_db
from rex.api.schemas import SystemDiagnosticsResponse, SystemStatusResponse
from rex.config import RexSettings, get_settings
from rex.persistence.models import (
    ClaimModel,
    ExperimentModel,
    ResearchRunModel,
)

router = APIRouter(prefix="/system", tags=["system"])


def _get_app_settings(request: Request) -> RexSettings:
    """Resolve settings from FastAPI app state or fallback to defaults."""
    return getattr(request.app.state, "settings", None) or get_settings()


def _mask_database_url(url: str) -> str:
    """Mask credentials in database connection string."""
    clean_url = url.split("?")[0]
    if "@" in clean_url and "://" in clean_url:
        scheme, rest = clean_url.split("://", 1)
        creds, host = rest.split("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{host}"
    return clean_url


@router.get("/health")
def health(request: Request, session: Session = Depends(get_db)) -> dict[str, str]:
    """Dynamic health check probe with active database probe."""
    settings = _get_app_settings(request)
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database connectivity probe failed: {exc}",
        ) from exc

    return {"status": "ok", "app": "rex", "version": settings.app.version}


@router.get("/health/live")
def health_live(request: Request) -> dict[str, str]:
    """Liveness probe confirming the server process is responsive."""
    settings = _get_app_settings(request)
    return {"status": "ok", "app": "rex", "version": settings.app.version}


@router.get("/health/ready")
def health_ready(request: Request, session: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness probe validating database connectivity and operational state."""
    settings = _get_app_settings(request)
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database connectivity probe failed: {exc}",
        ) from exc

    return {
        "status": "ready",
        "database": "connected",
        "app": "rex",
        "version": settings.app.version,
    }


@router.get("/status", response_model=SystemStatusResponse)
def system_status(
    request: Request,
    session: Session = Depends(get_db),
) -> SystemStatusResponse:
    """Retrieve operational environment metrics and status."""
    settings = _get_app_settings(request)

    total_runs = session.scalar(select(func.count(ResearchRunModel.id))) or 0
    active_runs = (
        session.scalar(
            select(func.count(ResearchRunModel.id)).where(
                ResearchRunModel.status.notin_(["COMPLETE", "FAILED", "STOP", "CANCELLED"])
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
        version=settings.app.version,
        environment=settings.app.environment,
        docker_enabled=settings.docker.enabled,
        database_url_masked=_mask_database_url(settings.persistence.database_url),
    )


@router.get("/diagnostics", response_model=SystemDiagnosticsResponse)
def diagnostics(request: Request, session: Session = Depends(get_db)) -> SystemDiagnosticsResponse:
    """Structured environment diagnostics and operational readiness verification."""
    settings = _get_app_settings(request)
    details: dict[str, Any] = {}

    # 1. Python version check
    python_ver = platform.python_version()
    python_ok = sys.version_info >= (3, 11)
    details["python_version"] = python_ver
    details["python_requirement"] = ">=3.11"

    # 2. Database connection & SQLite WAL check
    db_ok = False
    wal_ok = False
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()
        wal_ok = str(journal_mode).lower() == "wal"
        details["journal_mode"] = str(journal_mode).lower()
    except Exception as exc:  # noqa: BLE001
        details["db_error"] = str(exc)

    # 3. Workspace storage writability
    ws_writable = False
    try:
        ws_path = Path(settings.persistence.workspace_root)
        ws_path.mkdir(parents=True, exist_ok=True)
        probe_file = ws_path / ".probe_health_write_test"
        probe_file.write_text("probe", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        ws_writable = True
        details["workspace_root"] = str(ws_path)
    except Exception as exc:  # noqa: BLE001
        details["workspace_error"] = str(exc)

    # 4. Artifact storage writability
    art_writable = False
    try:
        art_path = Path(settings.persistence.artifact_root)
        art_path.mkdir(parents=True, exist_ok=True)
        probe_file = art_path / ".probe_health_write_test"
        probe_file.write_text("probe", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        art_writable = True
        details["artifact_root"] = str(art_path)
    except Exception as exc:  # noqa: BLE001
        details["artifact_error"] = str(exc)

    # 5. Docker daemon check
    docker_available = False
    if settings.docker.enabled:
        try:
            import docker

            client = docker.from_env()
            docker_available = bool(client.ping())
            details["docker_status"] = "connected"
        except Exception as exc:  # noqa: BLE001
            details["docker_status"] = f"unavailable ({exc})"
    else:
        details["docker_status"] = "disabled"

    # Compute aggregate status
    if not (python_ok and db_ok and ws_writable and art_writable):
        overall_status = "unhealthy"
    elif settings.docker.enabled and not docker_available:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return SystemDiagnosticsResponse(
        status=overall_status,
        python_version=python_ver,
        python_version_ok=python_ok,
        sqlite_wal_enabled=wal_ok,
        workspace_writable=ws_writable,
        artifact_writable=art_writable,
        docker_available=docker_available,
        database_connected=db_ok,
        details=details,
    )
