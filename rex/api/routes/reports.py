"""Research Report API endpoints (REX-037, REX-036).

Provides retrieval and listing of evidence-grounded research reports with full
lineage traceability, metric extraction, and markdown export.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from rex.api.routes.research import get_db
from rex.api.schemas import ResearchReportResponse
from rex.persistence.models import ResearchRunModel
from rex.reporting.report_generator import ReportGenerator

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=list[dict])
def list_reports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[dict]:
    """List generated research reports across all completed and active research runs."""
    stmt = (
        select(ResearchRunModel)
        .order_by(ResearchRunModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    runs = session.scalars(stmt).all()

    results = []
    generator = ReportGenerator()
    for r in runs:
        try:
            report = generator.generate_report(
                research_run_id=r.id, session=session, save_artifact=False
            )
            summary_text = f"Investigation: {report.research_question}. Experiments: {report.total_experiments}."
            results.append(
                {
                    "research_run_id": r.id,
                    "title": report.title or r.title or r.research_question[:60],
                    "generated_at": report.generated_at.isoformat(),
                    "is_fully_grounded": report.is_fully_grounded,
                    "executive_summary": summary_text,
                    "experiments_count": len(report.experiments),
                    "metrics_count": len(report.metrics),
                    "unsupported_claims_count": len(report.unsupported_claims),
                }
            )
        except (ValueError, KeyError, RuntimeError, AttributeError):
            results.append(
                {
                    "research_run_id": r.id,
                    "title": r.title or r.research_question[:60],
                    "generated_at": r.created_at.isoformat(),
                    "is_fully_grounded": False,
                    "executive_summary": "Report preview unavailable.",
                    "experiments_count": 0,
                    "metrics_count": 0,
                    "unsupported_claims_count": 0,
                }
            )
    return results


@router.get("/{run_id}", response_model=ResearchReportResponse)
def get_report(
    run_id: str,
    save: bool = Query(False),
    session: Session = Depends(get_db),
) -> ResearchReportResponse:
    """Retrieve or generate evidence-grounded research report for a specific run."""
    run = session.get(ResearchRunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Research run '{run_id}' not found.")

    generator = ReportGenerator()
    report = generator.generate_report(research_run_id=run_id, session=session, save_artifact=save)

    summary_text = (
        f"Investigation into: {report.research_question}. "
        f"Completed {report.total_experiments} experiment(s) with {len(report.supported_claims)} supported claim(s)."
    )

    return ResearchReportResponse(
        research_run_id=report.research_run_id,
        title=report.title,
        generated_at=report.generated_at.isoformat(),
        is_fully_grounded=report.is_fully_grounded,
        executive_summary=summary_text,
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
