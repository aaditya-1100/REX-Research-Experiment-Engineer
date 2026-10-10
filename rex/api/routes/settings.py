"""Settings API endpoints (REX-037).

Exposes non-sensitive system configuration, resource budgets, and environment parameters.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from rex.api.schemas import SettingsResponse
from rex.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
def get_system_settings(request: Request) -> SettingsResponse:
    """Retrieve sanitized system settings with secrets masked."""
    settings = getattr(request.app.state, "settings", None) or get_settings()

    return SettingsResponse(
        app={
            "name": settings.app.name,
            "version": settings.app.version,
            "environment": settings.app.environment,
            "debug": settings.app.debug,
            "log_level": settings.app.log_level,
        },
        persistence={
            "database_url": settings.persistence.database_url.split("?")[0],
            "artifact_root": str(settings.persistence.artifact_root),
            "workspace_root": str(settings.persistence.workspace_root),
        },
        docker={
            "enabled": settings.docker.enabled,
            "required": settings.docker.required,
            "image": settings.docker.image,
            "network_disabled": settings.docker.network_disabled,
            "non_root_user": settings.docker.non_root_user,
            "cpu_limit": settings.docker.cpu_limit,
            "memory_limit_mb": settings.docker.memory_limit_mb,
            "timeout_seconds": settings.docker.timeout_seconds,
        },
        budgets={
            "max_experiments": settings.budgets.max_experiments,
            "max_executions": settings.budgets.max_executions,
            "max_runtime_seconds": settings.budgets.max_runtime_seconds,
            "max_llm_calls": settings.budgets.max_llm_calls,
            "max_token_cost": settings.budgets.max_token_cost,
            "max_artifact_volume_bytes": settings.budgets.max_artifact_volume_bytes,
            "max_concurrent_executions": settings.budgets.max_concurrent_executions,
        },
        literature={
            "enabled": settings.literature.enabled,
            "max_results_limit": settings.literature.max_results_limit,
            "openalex_configured": settings.literature.openalex_api_key is not None,
            "semantic_scholar_configured": settings.literature.semantic_scholar_api_key is not None,
        },
        llm={
            "provider": settings.llm.provider,
            "model": settings.llm.model,
            "api_key": "**********" if settings.llm.api_key is not None else None,
            "api_key_configured": settings.llm.api_key is not None,
            "request_timeout_seconds": settings.llm.request_timeout_seconds,
            "max_token_cost": settings.llm.max_token_cost,
        },
    )
