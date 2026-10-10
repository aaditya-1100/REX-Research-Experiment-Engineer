"""FastAPI Application Factory for REX (REX-037, REX-038).

Configures middleware, route registration, dependency injection of database engine and session
factory, correlation ID propagation, standardized error envelopes, crash recovery reconciliation,
and optional static serving of the compiled frontend distribution bundle.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from rex.api.routes import (
    artifacts,
    evaluation,
    evidence,
    executions,
    experiments,
    reports,
    research,
    settings,
    system,
)
from rex.config import RexSettings, get_settings
from rex.observability.logging import get_correlation_id, set_correlation_id
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)

logger = logging.getLogger(__name__)


def _status_to_error_code(status_code: int) -> str:
    """Map HTTP status codes to standardized machine-readable error codes."""
    mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "RESOURCE_NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_SERVER_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }
    return mapping.get(status_code, f"HTTP_{status_code}")


def _reconcile_startup_crashes(session_factory: sessionmaker[Session]) -> None:
    """Reconcile orphaned executions stuck in RUNNING status on application boot."""
    try:
        from rex.controller.execution_orchestrator import ExecutionOrchestrator

        orchestrator = ExecutionOrchestrator(session_factory=session_factory)
        reconciled = orchestrator.reconcile_stale_executions(stale_threshold_seconds=0)
        if reconciled:
            logger.info(
                "Startup crash recovery: reconciled %d stale executions: %s",
                len(reconciled),
                reconciled,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Startup crash recovery failed: %s", exc)


def create_app(
    engine: Engine | None = None,
    session_factory: sessionmaker[Session] | None = None,
    rex_settings: RexSettings | None = None,
    **kwargs: Any,
) -> FastAPI:
    """Create and configure a production FastAPI instance for REX."""
    cfg = rex_settings or kwargs.get("settings") or get_settings()

    # Initialize persistence if not provided
    resolved_engine = (
        engine or kwargs.get("db_engine") or create_db_engine(cfg.persistence.database_url)
    )
    init_db(resolved_engine)
    resolved_factory = (
        session_factory
        or kwargs.get("db_session_factory")
        or create_session_factory(resolved_engine)
    )

    # Execute startup crash recovery reconciliation immediately
    _reconcile_startup_crashes(resolved_factory)

    app = FastAPI(
        title="REX — Research Experiment Engineer",
        description="Autonomous computational research with machine-readable provenance and independent verification",
        version=cfg.app.version,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Attach shared resources to app state
    app.state.engine = resolved_engine
    app.state.session_factory = resolved_factory
    app.state.settings = cfg

    # Add Correlation ID Middleware
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next: Any) -> Any:
        corr_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        set_correlation_id(corr_id)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr_id
        return response

    # Exception Handlers for Deterministic Error Envelopes
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        corr_id = get_correlation_id()
        code = _status_to_error_code(exc.status_code)
        msg = exc.detail if isinstance(exc.detail, str) else "Request error"
        details = exc.detail if not isinstance(exc.detail, str) else {}
        headers = dict(getattr(exc, "headers", None) or {})
        headers["X-Correlation-ID"] = corr_id
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": msg,
                    "details": details,
                    "correlation_id": corr_id,
                },
                "detail": exc.detail,
            },
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        corr_id = get_correlation_id()
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": exc.errors(),
                    "correlation_id": corr_id,
                },
                "detail": exc.errors(),
            },
            headers={"X-Correlation-ID": corr_id},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        corr_id = get_correlation_id()
        logger.exception("Unhandled server exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal server error occurred.",
                    "details": {"error_type": type(exc).__name__},
                    "correlation_id": corr_id,
                },
                "detail": "Internal Server Error",
            },
            headers={"X-Correlation-ID": corr_id},
        )

    # Enable CORS for local-first frontend integration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    app.include_router(system.router, prefix="/api")
    app.include_router(research.router, prefix="/api")
    app.include_router(experiments.router, prefix="/api")
    app.include_router(executions.router, prefix="/api")
    app.include_router(executions.executions_router, prefix="/api")
    app.include_router(evidence.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(evaluation.router, prefix="/api")

    # Static file serving if frontend/dist exists
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists() and (frontend_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            clean_path = full_path.lstrip("/")
            # Do not intercept /api or docs
            if clean_path.startswith(("api", "docs", "redoc", "openapi.json")):
                raise HTTPException(status_code=404, detail=f"Route '/{clean_path}' not found.")
            target = frontend_dist / full_path
            if target.is_file():
                return FileResponse(target)
            return FileResponse(frontend_dist / "index.html")

    return app
