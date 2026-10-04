"""FastAPI Application Factory for REX (REX-037).

Configures middleware, route registration, dependency injection of database engine and session
factory, and optional static serving of the compiled frontend distribution bundle.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from rex.api.routes import (
    artifacts,
    evidence,
    executions,
    experiments,
    reports,
    research,
    settings,
    system,
)
from rex.config import RexSettings, get_settings
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)


def create_app(
    engine: Engine | None = None,
    session_factory: sessionmaker[Session] | None = None,
    rex_settings: RexSettings | None = None,
) -> FastAPI:
    """Create and configure a production FastAPI instance for REX."""
    cfg = rex_settings or get_settings()

    # Initialize persistence if not provided
    db_engine = engine or create_db_engine(cfg.persistence.database_url)
    init_db(db_engine)
    db_session_factory = session_factory or create_session_factory(db_engine)

    app = FastAPI(
        title="REX — Research Experiment Engineer",
        description="Autonomous computational research with machine-readable provenance and independent verification",
        version="0.8.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Attach shared resources to app state
    app.state.engine = db_engine
    app.state.session_factory = db_session_factory
    app.state.settings = cfg

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
            "*",
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
    app.include_router(evidence.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")

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
