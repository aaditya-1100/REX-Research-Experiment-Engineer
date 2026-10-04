"""Research Run API endpoints (REX-037, REX-038).

Handles creation, retrieval, lifecycle transitions, event audit logs, verification,
and report generation for research runs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rex.api.schemas import (
    ClaimResponse,
    CreateResearchRunRequest,
    EventResponse,
    ExperimentResponse,
    HypothesisResponse,
    ResearchReportResponse,
    ResearchRunResponse,
    ResearchRunStats,
    RunActionRequest,
    VerificationCheckItem,
    VerificationReportResponse,
)
from rex.controller.state_machine import ResearchStateMachine, create_research_run
from rex.domain.models import ClaimStatus, ResearchState
from rex.evidence.verifier import ResearchVerifier
from rex.observability.events import ActorType, EventType, create_event
from rex.persistence.models import (
    ClaimModel,
    CritiqueModel,
    DecisionModel,
    EventModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import (
    ClaimRepository,
    EventRepository,
    ExperimentRepository,
    HypothesisRepository,
)
from rex.reporting.report_generator import ReportGenerator

router = APIRouter(prefix="/research", tags=["research"])


def get_db(request: Request):
    """Yield a database session from the application session factory."""
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_run_stats(session: Session, run_id: str) -> ResearchRunStats:
    """Calculate aggregate metrics for a research run."""
    hyp_count = (
        session.scalar(
            select(func.count(HypothesisModel.id)).where(
                HypothesisModel.research_run_id == run_id
            )
        )
        or 0
    )
    exp_count = (
        session.scalar(
            select(func.count(ExperimentModel.id)).where(
                ExperimentModel.research_run_id == run_id
            )
        )
        or 0
    )
    verified_results = (
        session.scalar(
            select(func.count(ResultModel.id))
            .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .where(
                ExperimentModel.research_run_id == run_id,
                ExecutionModel.status == "completed",
            )
        )
        or 0
    )
    pending_count = (
        session.scalar(
            select(func.count(ExecutionModel.id))
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .where(
                ExperimentModel.research_run_id == run_id,
                ExecutionModel.status.in_(["pending", "running"]),
            )
        )
        or 0
    )
    claims_count = (
        session.scalar(
            select(func.count(ClaimModel.id)).where(ClaimModel.research_run_id == run_id)
        )
        or 0
    )
    verified_claims = (
        session.scalar(
            select(func.count(ClaimModel.id)).where(
                ClaimModel.research_run_id == run_id,
                ClaimModel.status == ClaimStatus.VERIFIED.value,
            )
        )
        or 0
    )

    # Compute cost estimate from executions
    durations = session.scalars(
        select(ExecutionModel.resource_usage_json)
        .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
        .where(ExperimentModel.research_run_id == run_id)
    ).all()
    cost = 0.0
    for res in durations:
        if isinstance(res, dict):
            cost += float(res.get("cost_estimate", res.get("duration_seconds", 0) * 0.001))

    return ResearchRunStats(
        hypotheses_count=hyp_count,
        experiments_count=exp_count,
        verified_results_count=verified_results,
        pending_count=pending_count,
        claims_count=claims_count,
        verified_claims_count=verified_claims,
        compute_cost_estimate=round(cost, 2),
    )


def _build_run_response(session: Session, run: ResearchRunModel) -> ResearchRunResponse:
    """Transform persistence model to API response with autonomous state metadata."""
    stats = _build_run_stats(session, run.id)

    # Inspect latest decision or critique to populate autonomous status
    latest_decision = (
        session.query(DecisionModel)
        .filter(DecisionModel.research_run_id == run.id)
        .order_by(DecisionModel.created_at.desc())
        .first()
    )
    latest_critique = (
        session.query(CritiqueModel)
        .filter(CritiqueModel.research_run_id == run.id)
        .order_by(CritiqueModel.created_at.desc())
        .first()
    )

    current_action = None
    current_action_reason = None
    next_action = None
    current_action_progress = None

    if run.status in ["COMPLETE", "STOP"]:
        current_action = "Research complete"
        current_action_progress = 1.0
    elif run.status == "FAILED":
        current_action = "Research terminated with failure"
        current_action_progress = 1.0
    elif run.status == "PAUSED":
        current_action = "Run paused by user"
        current_action_reason = "Execution temporarily suspended."
        next_action = "Resume run to continue autonomous cycle"
        current_action_progress = 0.20
    elif latest_decision:
        current_action = f"Executing decision: {latest_decision.action}"
        current_action_reason = latest_decision.rationale
        next_action = (
            f"Target: {latest_decision.target_entity_type or 'experiment'}"
        )
        current_action_progress = 0.65
    elif latest_critique:
        current_action = f"Critiqued iteration {latest_critique.iteration}"
        current_action_reason = latest_critique.summary
        next_action = f"Recommended: {latest_critique.recommended_action}"
        current_action_progress = 0.50
    else:
        current_action = f"Phase: {run.status}"
        current_action_progress = 0.20

    return ResearchRunResponse(
        id=run.id,
        title=run.title,
        research_question=run.research_question,
        status=run.status,
        created_at=run.created_at,
        updated_at=run.updated_at,
        configuration=run.configuration_json or {},
        budget=run.budget_json or {},
        stats=stats,
        current_action=current_action,
        current_action_reason=current_action_reason,
        current_action_progress=current_action_progress,
        next_action=next_action,
    )


@router.post("", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: CreateResearchRunRequest,
    session: Session = Depends(get_db),
) -> ResearchRunResponse:
    """Create a new scientific research run investigation."""
    title = payload.title.strip() or payload.research_question[:60]
    domain_run = create_research_run(
        session=session,
        research_question=payload.research_question,
        title=title,
        configuration=payload.configuration,
        budget=payload.budget,
        actor=ActorType.OWNER,
    )
    run_model = session.get(ResearchRunModel, domain_run.id)
    if not run_model:
        raise HTTPException(status_code=500, detail="Failed to initialize research run.")
    return _build_run_response(session, run_model)


@router.get("", response_model=list[ResearchRunResponse])
def list_runs(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[ResearchRunResponse]:
    """List research runs with summary stats and optional status filter."""
    stmt = select(ResearchRunModel).order_by(ResearchRunModel.created_at.desc())
    if status_filter:
        if status_filter.lower() == "active":
            stmt = stmt.where(
                ResearchRunModel.status.notin_(["COMPLETE", "FAILED", "STOP"])
            )
        elif status_filter.lower() == "completed":
            stmt = stmt.where(ResearchRunModel.status == "COMPLETE")
        elif status_filter.lower() == "failed":
            stmt = stmt.where(ResearchRunModel.status == "FAILED")
        else:
            stmt = stmt.where(ResearchRunModel.status == status_filter.upper())

    stmt = stmt.offset(offset).limit(limit)
    models = session.scalars(stmt).all()
    return [_build_run_response(session, m) for m in models]


@router.get("/{run_id}", response_model=ResearchRunResponse)
def get_run(
    run_id: str,
    session: Session = Depends(get_db),
) -> ResearchRunResponse:
    """Retrieve full detail for a research run."""
    model = session.get(ResearchRunModel, run_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")
    return _build_run_response(session, model)


@router.post("/{run_id}/start", response_model=ResearchRunResponse)
def start_run(
    run_id: str,
    payload: RunActionRequest | None = None,
    session: Session = Depends(get_db),
) -> ResearchRunResponse:
    """Transition research run from INITIALIZE to UNDERSTAND."""
    model = session.get(ResearchRunModel, run_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")

    if model.status == ResearchState.INITIALIZE.value:
        ResearchStateMachine.transition(
            session=session,
            run_id=run_id,
            target_state=ResearchState.UNDERSTAND,
            actor=ActorType.OWNER,
            reason=payload.reason if payload else "User started research run",
        )
    return _build_run_response(session, model)


@router.post("/{run_id}/pause", response_model=ResearchRunResponse)
def pause_run(
    run_id: str,
    payload: RunActionRequest | None = None,
    session: Session = Depends(get_db),
) -> ResearchRunResponse:
    """Pause an active research run."""
    model = session.get(ResearchRunModel, run_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")

    # Record pause event
    model.status = "PAUSED"
    session.add(model)
    event = create_event(
        event_type=EventType.AUTONOMOUS_ACTION_COMPLETED,
        actor=ActorType.OWNER,
        research_run_id=run_id,
        payload={"action": "pause", "reason": payload.reason if payload else "User requested pause"},
    )
    EventRepository(session).record_event(event)
    return _build_run_response(session, model)


@router.post("/{run_id}/resume", response_model=ResearchRunResponse)
def resume_run(
    run_id: str,
    payload: RunActionRequest | None = None,
    session: Session = Depends(get_db),
) -> ResearchRunResponse:
    """Resume an active research run."""
    model = session.get(ResearchRunModel, run_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")

    model.status = "UNDERSTAND"
    session.add(model)
    event = create_event(
        event_type=EventType.AUTONOMOUS_ACTION_STARTED,
        actor=ActorType.OWNER,
        research_run_id=run_id,
        payload={"action": "resume", "reason": payload.reason if payload else "User requested resume"},
    )
    EventRepository(session).record_event(event)
    return _build_run_response(session, model)


@router.get("/{run_id}/hypotheses", response_model=list[HypothesisResponse])
def list_hypotheses(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[HypothesisResponse]:
    """List scientific hypotheses formulated in this research run."""
    repo = HypothesisRepository(session)
    models = repo.list_by_run(run_id)
    return [
        HypothesisResponse(
            id=m.id,
            research_run_id=m.research_run_id,
            statement=m.statement,
            rationale=m.rationale,
            expected_direction=m.expected_direction,
            falsification_condition=m.falsification_condition,
            status=m.status,
            created_at=m.created_at,
        )
        for m in models
    ]


@router.get("/{run_id}/experiments", response_model=list[ExperimentResponse])
def list_run_experiments(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[ExperimentResponse]:
    """List experiments designed under this research run."""
    repo = ExperimentRepository(session)
    models = repo.list_by_run(run_id)
    results = []
    for m in models:
        executions = session.scalars(
            select(ExecutionModel)
            .where(ExecutionModel.experiment_id == m.id)
            .order_by(ExecutionModel.started_at.desc())
        ).all()
        latest = executions[0] if executions else None

        # Fetch latest metrics
        latest_metrics = {}
        if latest:
            res_models = session.scalars(
                select(ResultModel).where(ResultModel.execution_id == latest.id)
            ).all()
            for r in res_models:
                latest_metrics[r.metric_name] = r.metric_value

        results.append(
            ExperimentResponse(
                id=m.id,
                research_run_id=m.research_run_id,
                hypothesis_id=m.hypothesis_id,
                objective=m.objective,
                specification=m.specification_json or {},
                status=m.status,
                created_at=m.created_at,
                parent_experiment_id=m.parent_experiment_id,
                execution_count=len(executions),
                latest_status=latest.status if latest else None,
                latest_execution_id=latest.id if latest else None,
                latest_metrics=latest_metrics,
            )
        )
    return results


@router.get("/{run_id}/claims", response_model=list[ClaimResponse])
def list_run_claims(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[ClaimResponse]:
    """List claims asserted in this research run."""
    repo = ClaimRepository(session)
    models = repo.list_by_run(run_id)
    results = []
    for m in models:
        link_count = (
            session.scalar(
                select(func.count(EvidenceLinkModel.id)).where(
                    EvidenceLinkModel.claim_id == m.id
                )
            )
            or 0
        )
        results.append(
            ClaimResponse(
                id=m.id,
                research_run_id=m.research_run_id,
                text=m.text,
                statement=m.statement,
                claim_type=m.claim_type,
                status=m.status,
                confidence=m.confidence,
                created_by=m.created_by,
                metadata=m.metadata_json or {},
                created_at=m.created_at,
                evidence_links_count=link_count,
            )
        )
    return results


@router.get("/{run_id}/events", response_model=list[EventResponse])
def list_events(
    run_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[EventResponse]:
    """Retrieve audit event timeline for a research run."""
    repo = EventRepository(session)
    models = repo.list_by_run(run_id, limit=limit, offset=offset)
    return [
        EventResponse(
            event_id=m.id,
            timestamp=m.timestamp,
            event_type=m.event_type,
            actor=m.actor_type,
            research_run_id=m.research_run_id,
            experiment_id=dict(m.payload_json or {}).get("_experiment_id"),
            execution_id=dict(m.payload_json or {}).get("_execution_id"),
            payload=m.payload_json or {},
        )
        for m in models
    ]


@router.get("/{run_id}/events/stream")
async def stream_events(
    run_id: str,
    request: Request,
) -> StreamingResponse:
    """Server-Sent Events (SSE) live event stream for research run updates."""
    session_factory = request.app.state.session_factory

    async def event_generator() -> AsyncGenerator[str, None]:
        last_id = None
        while True:
            if await request.is_disconnected():
                break

            with session_factory() as session:
                stmt = (
                    select(EventModel)
                    .where(EventModel.research_run_id == run_id)
                    .order_by(EventModel.timestamp.asc())
                )
                if last_id:
                    stmt = stmt.where(EventModel.id > last_id)
                events = session.scalars(stmt.limit(20)).all()

                for ev in events:
                    last_id = ev.id
                    data = {
                        "event_id": ev.id,
                        "timestamp": ev.timestamp.isoformat(),
                        "event_type": ev.event_type,
                        "actor": ev.actor_type,
                        "research_run_id": ev.research_run_id,
                        "payload": ev.payload_json or {},
                    }
                    yield f"event: research_event\ndata: {json.dumps(data)}\n\n"

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/verify", response_model=VerificationReportResponse)
def verify_run(
    run_id: str,
    tolerance: float = Query(1e-6, ge=0.0),
    session: Session = Depends(get_db),
) -> VerificationReportResponse:
    """Trigger the formal verification protocol (REX-026 / REX-041)."""
    run = session.get(ResearchRunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")

    verifier = ResearchVerifier(session=session, tolerance=tolerance)
    report = verifier.verify_run(run_id)

    # Convert to granular checklist items for REX-041 UI
    checks = []

    # 1. Lineage check
    all_lineage_intact = all(c.is_lineage_intact for c in report.claims_verified) if report.claims_verified else True
    checks.append(
        VerificationCheckItem(
            name="Mechanical Lineage Traceability",
            status="pass" if all_lineage_intact else "fail",
            message=f"{len([c for c in report.claims_verified if c.is_lineage_intact])}/{len(report.claims_verified)} claims have unbroken provenance." if report.claims_verified else "No claims asserted yet.",
            details={"claims_count": len(report.claims_verified)},
        )
    )

    # 2. Artifact hash check
    all_artifacts_valid = all(a.is_valid for a in report.artifacts_verified) if report.artifacts_verified else True
    checks.append(
        VerificationCheckItem(
            name="Cryptographic Byte Hashes (SHA-256)",
            status="pass" if all_artifacts_valid else "fail",
            message=f"{len([a for a in report.artifacts_verified if a.is_valid])}/{len(report.artifacts_verified)} artifacts match cryptographic records." if report.artifacts_verified else "No artifacts registered.",
            details={"artifacts_count": len(report.artifacts_verified)},
        )
    )

    # 3. Statistical recomputations
    all_recomputed_deterministic = all(an.is_deterministic for an in report.analyses_recomputed) if report.analyses_recomputed else True
    checks.append(
        VerificationCheckItem(
            name="Statistical Determinism & Recomputations",
            status="pass" if all_recomputed_deterministic else "fail",
            message=f"{len([an for an in report.analyses_recomputed if an.is_deterministic])}/{len(report.analyses_recomputed)} statistical analyses deterministically reproduced." if report.analyses_recomputed else "No statistical analyses recomputed.",
            details={"analyses_count": len(report.analyses_recomputed)},
        )
    )

    # 4. Cross-run isolation
    no_cross_run = len(report.cross_run_violations) == 0
    checks.append(
        VerificationCheckItem(
            name="Cross-Run Provenance Boundary Isolation",
            status="pass" if no_cross_run else "fail",
            message="No cross-run isolation violations detected." if no_cross_run else f"{len(report.cross_run_violations)} cross-run boundary violations detected!",
            details={"violations": report.cross_run_violations},
        )
    )

    return VerificationReportResponse(
        research_run_id=report.research_run_id,
        status=report.status.value,
        is_passed=report.is_passed,
        checks=checks,
        claims_verified=[c.as_dict() for c in report.claims_verified],
        artifacts_verified=[a.as_dict() for a in report.artifacts_verified],
        analyses_recomputed=[a.as_dict() for a in report.analyses_recomputed],
        cross_run_violations=report.cross_run_violations,
        errors=report.errors,
        warnings=report.warnings,
        started_at=report.started_at,
        completed_at=report.completed_at,
    )


@router.get("/{run_id}/report", response_model=ResearchReportResponse)
def get_or_generate_report(
    run_id: str,
    save: bool = Query(True),
    session: Session = Depends(get_db),
) -> ResearchReportResponse:
    """Generate or retrieve evidence-grounded research report (REX-036)."""
    run = session.get(ResearchRunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")

    generator = ReportGenerator()
    report = generator.generate_report(research_run_id=run_id, session=session, save_artifact=save)

    exec_summary = (
        f"Investigation into: {report.research_question}. "
        f"Completed {report.total_experiments} experiment(s) with {len(report.supported_claims)} supported claim(s)."
    )
    return ResearchReportResponse(
        research_run_id=report.research_run_id,
        title=report.title,
        generated_at=report.generated_at.isoformat(),
        is_fully_grounded=report.is_fully_grounded,
        executive_summary=exec_summary,
        hypotheses=[h.model_dump() for h in report.hypotheses],
        experiments=[e.model_dump() for e in report.experiments],
        metrics=[m.model_dump() for m in report.metrics],
        analyses=[a.model_dump() for a in report.analyses],
        critiques=[c.model_dump() for c in report.critiques],
        limitations=list(report.limitations),
        conclusions=[{"statement": c} for c in report.conclusions],
        unsupported_claims=[c.model_dump() for c in report.unsupported_claims],
        failed_experiments=[e.model_dump() for e in report.failed_experiments],
        markdown=report.to_markdown(),
    )
