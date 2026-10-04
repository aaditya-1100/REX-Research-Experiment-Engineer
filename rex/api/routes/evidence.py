"""Evidence & Lineage API endpoints (REX-037, REX-040).

Provides claim inspection, evidence relations, and deterministic provenance DAG tracing
connecting high-level claims down to analyses, empirical results, executions, experiments,
code commits, datasets, configurations, and cryptographic disk artifacts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rex.api.routes.research import get_db
from rex.api.schemas import (
    ClaimLineageResponse,
    ClaimResponse,
    LineageEdge,
    LineageNode,
)
from rex.evidence.graph import EvidenceGraphService
from rex.persistence.models import (
    ClaimModel,
    EvidenceLinkModel,
)

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("/claims", response_model=list[ClaimResponse])
def list_claims(
    run_id: str | None = Query(None, alias="run_id"),
    status_filter: str | None = Query(None, alias="status"),
    claim_type: str | None = Query(None, alias="type"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[ClaimResponse]:
    """List scientific claims with filtering options."""
    stmt = select(ClaimModel).order_by(ClaimModel.created_at.desc())
    if run_id:
        stmt = stmt.where(ClaimModel.research_run_id == run_id)
    if status_filter:
        stmt = stmt.where(ClaimModel.status == status_filter.lower())
    if claim_type:
        stmt = stmt.where(ClaimModel.claim_type == claim_type.lower())

    stmt = stmt.offset(offset).limit(limit)
    models = session.scalars(stmt).all()

    responses = []
    for m in models:
        link_count = (
            session.scalar(
                select(func.count(EvidenceLinkModel.id)).where(EvidenceLinkModel.claim_id == m.id)
            )
            or 0
        )
        responses.append(
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
    return responses


@router.get("/claims/{claim_id}", response_model=ClaimResponse)
def get_claim(
    claim_id: str,
    session: Session = Depends(get_db),
) -> ClaimResponse:
    """Retrieve full detail for a claim."""
    model = session.get(ClaimModel, claim_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    link_count = (
        session.scalar(
            select(func.count(EvidenceLinkModel.id)).where(EvidenceLinkModel.claim_id == model.id)
        )
        or 0
    )
    return ClaimResponse(
        id=model.id,
        research_run_id=model.research_run_id,
        text=model.text,
        statement=model.statement,
        claim_type=model.claim_type,
        status=model.status,
        confidence=model.confidence,
        created_by=model.created_by,
        metadata=model.metadata_json or {},
        created_at=model.created_at,
        evidence_links_count=link_count,
    )


@router.get("/lineage/{claim_id}", response_model=ClaimLineageResponse)
def get_claim_lineage(
    claim_id: str,
    session: Session = Depends(get_db),
) -> ClaimLineageResponse:
    """Trace deterministic mechanical provenance lineage for a claim (REX-023 / REX-040).

    Constructs the complete traceable path:
    Claim -> Analysis -> Result -> Execution -> Experiment -> Code -> Dataset -> Configuration -> Artifact.
    """
    claim = session.get(ClaimModel, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    graph_service = EvidenceGraphService(session=session)
    lineage = graph_service.trace_claim_lineage(claim_id)

    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    seen_nodes: set[str] = set()

    # 1. Claim Node
    claim_status = claim.status.lower()
    nodes.append(
        LineageNode(
            id=claim.id,
            type="claim",
            label=f"Claim: {claim.id}",
            sublabel=claim.statement[:70] + ("..." if len(claim.statement) > 70 else ""),
            status="verified"
            if claim_status == "verified"
            else ("unsupported" if claim_status in ["unsupported", "tampered"] else "unverified"),
            hash=None,
            details={
                "claim_type": claim.claim_type,
                "confidence": claim.confidence,
                "statement": claim.statement,
                "status": claim.status,
            },
        )
    )
    seen_nodes.add(claim.id)
    prev_node_id = claim.id

    # 2. Analyses
    if lineage.analyses:
        for an in lineage.analyses:
            an_id = an.id
            if an_id not in seen_nodes:
                nodes.append(
                    LineageNode(
                        id=an_id,
                        type="analysis",
                        label=f"Analysis: {an_id}",
                        sublabel=f"{an.method} ({an.analysis_type})",
                        status="verified" if lineage.is_complete else "unverified",
                        details={
                            "method": an.method,
                            "analysis_type": an.analysis_type,
                            "input_results": an.input_result_ids,
                            "output": an.output_json,
                        },
                    )
                )
                seen_nodes.add(an_id)
            edges.append(
                LineageEdge(source_id=prev_node_id, target_id=an_id, relation="derived_from")
            )
            prev_node_id = an_id

    # 3. Results
    if lineage.results:
        for res in lineage.results:
            res_id = res.id
            if res_id not in seen_nodes:
                unit = f" {res.metric_unit}" if res.metric_unit else ""
                val_str = f"{res.metric_value}{unit}" if res.metric_value is not None else "N/A"
                nodes.append(
                    LineageNode(
                        id=res_id,
                        type="result",
                        label=f"Result: {res_id}",
                        sublabel=f"{res.metric_name}: {val_str}",
                        status="verified" if lineage.is_complete else "unverified",
                        details={
                            "metric_name": res.metric_name,
                            "metric_value": res.metric_value,
                            "metric_unit": res.metric_unit,
                            "result_data": res.result_json,
                        },
                    )
                )
                seen_nodes.add(res_id)
            edges.append(
                LineageEdge(source_id=prev_node_id, target_id=res_id, relation="supported_by")
            )
            prev_node_id = res_id

    # 4. Executions
    if lineage.executions:
        for ex in lineage.executions:
            ex_id = ex.id
            if ex_id not in seen_nodes:
                nodes.append(
                    LineageNode(
                        id=ex_id,
                        type="execution",
                        label=f"Execution: {ex_id}",
                        sublabel=f"Status: {ex.status}, Exit code: {ex.exit_code}",
                        status="verified" if ex.status == "completed" else "unverified",
                        hash=ex.code_hash or None,
                        details={
                            "status": ex.status,
                            "command": ex.command,
                            "exit_code": ex.exit_code,
                            "seed": ex.seed,
                        },
                    )
                )
                seen_nodes.add(ex_id)
            edges.append(
                LineageEdge(source_id=prev_node_id, target_id=ex_id, relation="produced_by")
            )
            prev_node_id = ex_id

            # Add Code, Dataset, Configuration sub-nodes attached to execution
            if ex.code_hash:
                code_id = f"code_{ex.id}"
                if code_id not in seen_nodes:
                    nodes.append(
                        LineageNode(
                            id=code_id,
                            type="code",
                            label=f"Code: {ex.git_commit[:7] if ex.git_commit else 'SHA256'}",
                            sublabel=f"Hash: {ex.code_hash[:16]}...",
                            status="verified",
                            hash=ex.code_hash,
                            details={"git_commit": ex.git_commit, "code_hash": ex.code_hash},
                        )
                    )
                    seen_nodes.add(code_id)
                edges.append(LineageEdge(source_id=ex_id, target_id=code_id, relation="uses_code"))

            if ex.dataset_hash:
                data_id = f"dataset_{ex.id}"
                if data_id not in seen_nodes:
                    nodes.append(
                        LineageNode(
                            id=data_id,
                            type="dataset",
                            label="Dataset Manifest",
                            sublabel=f"Hash: {ex.dataset_hash[:16]}...",
                            status="verified",
                            hash=ex.dataset_hash,
                            details={"dataset_hash": ex.dataset_hash},
                        )
                    )
                    seen_nodes.add(data_id)
                edges.append(
                    LineageEdge(source_id=ex_id, target_id=data_id, relation="uses_dataset")
                )

            if ex.configuration_hash:
                cfg_id = f"config_{ex.id}"
                if cfg_id not in seen_nodes:
                    nodes.append(
                        LineageNode(
                            id=cfg_id,
                            type="configuration",
                            label="Configuration",
                            sublabel=f"Hash: {ex.configuration_hash[:16]}...",
                            status="verified",
                            hash=ex.configuration_hash,
                            details={"configuration_hash": ex.configuration_hash},
                        )
                    )
                    seen_nodes.add(cfg_id)
                edges.append(
                    LineageEdge(source_id=ex_id, target_id=cfg_id, relation="uses_configuration")
                )

    # 5. Experiments
    if lineage.experiments:
        for exp in lineage.experiments:
            exp_id = exp.id
            if exp_id not in seen_nodes:
                nodes.append(
                    LineageNode(
                        id=exp_id,
                        type="experiment",
                        label=f"Experiment: {exp_id}",
                        sublabel=exp.objective[:60],
                        status="verified",
                        details={"objective": exp.objective, "status": exp.status},
                    )
                )
                seen_nodes.add(exp_id)
            edges.append(
                LineageEdge(source_id=prev_node_id, target_id=exp_id, relation="instance_of")
            )

    # 6. Artifacts
    if lineage.artifacts:
        for art in lineage.artifacts:
            art_id = art.id
            if art_id not in seen_nodes:
                nodes.append(
                    LineageNode(
                        id=art_id,
                        type="artifact",
                        label=f"Artifact: {art.artifact_type}",
                        sublabel=f"{art.path} ({art.size_bytes} B)",
                        status="verified",
                        hash=art.content_hash,
                        details={
                            "artifact_type": art.artifact_type,
                            "path": art.path,
                            "size_bytes": art.size_bytes,
                            "content_hash": art.content_hash,
                        },
                    )
                )
                seen_nodes.add(art_id)
            edges.append(
                LineageEdge(source_id=prev_node_id, target_id=art_id, relation="uses_artifact")
            )

    return ClaimLineageResponse(
        claim_id=claim.id,
        statement=claim.statement,
        status=claim.status,
        is_complete=lineage.is_complete,
        gaps=lineage.gaps,
        nodes=nodes,
        edges=edges,
    )
