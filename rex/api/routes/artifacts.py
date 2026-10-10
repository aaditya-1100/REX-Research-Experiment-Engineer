"""Artifacts API endpoints (REX-037).

Provides listing, metadata inspection, cryptographic hash verification,
and file download/preview for research artifacts.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from rex.api.routes.research import get_db
from rex.api.schemas import ArtifactResponse
from rex.config import get_settings
from rex.evidence.hashing import verify_artifact_hash
from rex.persistence.models import ArtifactModel

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=list[ArtifactResponse])
def list_artifacts(
    run_id: str | None = Query(None, alias="run_id"),
    artifact_type: str | None = Query(None, alias="type"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[ArtifactResponse]:
    """List filesystem artifacts with optional filtering."""
    stmt = select(ArtifactModel).order_by(ArtifactModel.created_at.desc())
    if run_id:
        stmt = stmt.where(ArtifactModel.research_run_id == run_id)
    if artifact_type:
        stmt = stmt.where(ArtifactModel.artifact_type == artifact_type.lower())

    stmt = stmt.offset(offset).limit(limit)
    models = session.scalars(stmt).all()
    return [
        ArtifactResponse(
            id=m.id,
            research_run_id=m.research_run_id,
            execution_id=m.execution_id,
            artifact_type=m.artifact_type,
            path=m.path,
            content_hash=m.content_hash,
            size_bytes=m.size_bytes,
            metadata=m.metadata_json or {},
            created_at=m.created_at,
        )
        for m in models
    ]


@router.get("/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(
    artifact_id: str,
    run_id: str | None = Query(None, alias="run_id"),
    session: Session = Depends(get_db),
) -> ArtifactResponse:
    """Retrieve metadata for a specific artifact."""
    model = session.get(ArtifactModel, artifact_id)
    if not model or (run_id is not None and model.research_run_id != run_id):
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found.")

    return ArtifactResponse(
        id=model.id,
        research_run_id=model.research_run_id,
        execution_id=model.execution_id,
        artifact_type=model.artifact_type,
        path=model.path,
        content_hash=model.content_hash,
        size_bytes=model.size_bytes,
        metadata=model.metadata_json or {},
        created_at=model.created_at,
    )


@router.api_route("/{artifact_id}/verify", methods=["GET", "POST"])
def verify_artifact(
    artifact_id: str,
    request: Request,
    run_id: str | None = Query(None, alias="run_id"),
    session: Session = Depends(get_db),
) -> dict:
    """Check cryptographic SHA-256 hash of artifact against disk content."""
    model = session.get(ArtifactModel, artifact_id)
    if not model or (run_id is not None and model.research_run_id != run_id):
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found.")

    settings = getattr(request.app.state, "settings", None) or get_settings()
    artifact_root = Path(settings.persistence.artifact_root)
    p = Path(model.path)
    if not p.is_absolute():
        p = artifact_root / p

    result = verify_artifact_hash(model, root_dir=artifact_root)
    return {
        "artifact_id": model.id,
        "path": model.path,
        "recorded_hash": model.content_hash,
        "actual_hash": result.computed_hash,
        "exists_on_disk": result.file_exists,
        "is_valid": result.is_valid,
        "status": "verified"
        if result.is_valid
        else ("missing" if not result.file_exists else "tampered"),
    }


@router.get("/{artifact_id}/content")
def get_artifact_content(
    artifact_id: str,
    request: Request,
    run_id: str | None = Query(None, alias="run_id"),
    session: Session = Depends(get_db),
):
    """Retrieve raw file content or stream file for download/preview."""
    model = session.get(ArtifactModel, artifact_id)
    if not model or (run_id is not None and model.research_run_id != run_id):
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found.")

    settings = getattr(request.app.state, "settings", None) or get_settings()
    artifact_root = Path(settings.persistence.artifact_root).resolve()
    raw_p = Path(model.path)
    p = raw_p.resolve() if raw_p.is_absolute() else (artifact_root / raw_p).resolve()
    if not p.is_relative_to(artifact_root):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not p.exists():
        raise HTTPException(
            status_code=404, detail=f"Artifact file '{model.path}' not found on disk."
        )

    # If small text or json, return inline
    if (
        model.artifact_type in ["log", "stdout", "stderr", "code", "manifest"]
        and model.size_bytes < 5 * 1024 * 1024
    ):
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            return PlainTextResponse(content)
        except OSError:
            pass

    return FileResponse(
        path=str(p),
        filename=p.name,
        media_type="application/octet-stream",
    )
