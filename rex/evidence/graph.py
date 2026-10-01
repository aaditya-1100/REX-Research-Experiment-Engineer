"""REX Evidence Graph Service (REX-023).

Provides relational evidence linking, cycle prevention, cross-run scoping enforcement,
and mechanical lineage traversal connecting claims to underlying analyses, results,
executions, experiments, and raw disk artifacts.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rex.domain.models import (
    Claim,
    EvidenceLink,
    EvidenceNodeType,
    EvidenceRelationType,
)
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    LiteratureSourceModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import EvidenceLinkRepository


class EvidenceGraphError(Exception):
    """Base exception for evidence graph operations."""


class NodeNotFoundError(EvidenceGraphError):
    """Raised when an evidence node cannot be found in persistence."""


class CrossRunEvidenceError(EvidenceGraphError):
    """Raised when evidence linkages cross research run isolation boundaries."""


class EvidenceCycleError(EvidenceGraphError):
    """Raised when an evidence link would introduce a directed cycle."""


class InvalidRelationError(EvidenceGraphError):
    """Raised when an evidence relationship or node type is invalid."""


@dataclass(frozen=True)
class ClaimLineage:
    """Complete provenance trace from a Claim down to raw empirical foundations."""

    claim: Claim
    analyses: list[AnalysisModel] = field(default_factory=list)
    results: list[ResultModel] = field(default_factory=list)
    executions: list[ExecutionModel] = field(default_factory=list)
    experiments: list[ExperimentModel] = field(default_factory=list)
    artifacts: list[ArtifactModel] = field(default_factory=list)
    links: list[EvidenceLink] = field(default_factory=list)
    is_complete: bool = False
    gaps: list[str] = field(default_factory=list)


class EvidenceGraphService:
    """Service managing the evidence graph and enforcing lineage invariants."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EvidenceLinkRepository(session)

    def _normalize_node_type(self, node_type: EvidenceNodeType | str) -> EvidenceNodeType:
        if isinstance(node_type, EvidenceNodeType):
            return node_type
        try:
            return EvidenceNodeType(str(node_type).strip().lower())
        except ValueError:
            raise InvalidRelationError(f"Unsupported evidence node type: {node_type}") from None

    def _normalize_relation_type(
        self, relation_type: EvidenceRelationType | str
    ) -> EvidenceRelationType:
        if isinstance(relation_type, EvidenceRelationType):
            return relation_type
        try:
            return EvidenceRelationType(str(relation_type).strip().lower())
        except ValueError:
            raise InvalidRelationError(
                f"Unsupported evidence relationship type: {relation_type}"
            ) from None

    def resolve_node(
        self, node_type: EvidenceNodeType | str, node_id: str
    ) -> tuple[Any, str | None]:
        """Resolve an entity by node type and ID, returning (entity, research_run_id).

        Raises:
            NodeNotFoundError: If the referenced entity does not exist.
        """
        n_type = self._normalize_node_type(node_type)

        entity: Any | None = None
        run_id: str | None = None

        if n_type == EvidenceNodeType.CLAIM:
            entity = self.session.get(ClaimModel, node_id)
            if entity:
                run_id = entity.research_run_id
        elif n_type == EvidenceNodeType.ANALYSIS:
            entity = self.session.get(AnalysisModel, node_id)
            if entity:
                run_id = entity.research_run_id
        elif n_type == EvidenceNodeType.RESULT:
            entity = self.session.get(ResultModel, node_id)
            if entity and entity.execution and entity.execution.experiment:
                run_id = entity.execution.experiment.research_run_id
        elif n_type == EvidenceNodeType.EXECUTION:
            entity = self.session.get(ExecutionModel, node_id)
            if entity and entity.experiment:
                run_id = entity.experiment.research_run_id
        elif n_type == EvidenceNodeType.EXPERIMENT:
            entity = self.session.get(ExperimentModel, node_id)
            if entity:
                run_id = entity.research_run_id
        elif n_type == EvidenceNodeType.ARTIFACT:
            entity = self.session.get(ArtifactModel, node_id)
            if entity:
                run_id = entity.research_run_id
        elif n_type == EvidenceNodeType.HYPOTHESIS:
            entity = self.session.get(HypothesisModel, node_id)
            if entity:
                run_id = entity.research_run_id
        elif n_type == EvidenceNodeType.LITERATURE_SOURCE:
            entity = self.session.get(LiteratureSourceModel, node_id)
            if entity:
                run_id = entity.research_run_id
        elif n_type == EvidenceNodeType.RESEARCH_RUN:
            entity = self.session.get(ResearchRunModel, node_id)
            if entity:
                run_id = entity.id
        elif n_type in (
            EvidenceNodeType.CODE,
            EvidenceNodeType.CONFIGURATION,
            EvidenceNodeType.DATASET,
        ):
            # These may be represented by ArtifactModel or ExecutionModel attributes
            entity = self.session.get(ArtifactModel, node_id)
            if entity:
                run_id = entity.research_run_id
            else:
                entity = self.session.get(ExecutionModel, node_id)
                if entity and entity.experiment:
                    run_id = entity.experiment.research_run_id

        if entity is None:
            raise NodeNotFoundError(
                f"Evidence node of type '{n_type.value}' with ID '{node_id}' does not exist."
            )

        return entity, run_id

    def has_path(self, start_id: str, target_id: str) -> bool:
        """Check whether a directed path exists from start_id to target_id."""
        if start_id == target_id:
            return True

        visited: set[str] = set()
        queue: deque[str] = deque([start_id])

        while queue:
            current = queue.popleft()
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)

            # Query all outgoing edges from current
            stmt = select(EvidenceLinkModel.target_id).where(EvidenceLinkModel.source_id == current)
            neighbors = self.session.execute(stmt).scalars().all()
            for neighbor in neighbors:
                if neighbor not in visited:
                    queue.append(neighbor)

        return False

    def create_link(
        self,
        source_type: EvidenceNodeType | str,
        source_id: str,
        target_type: EvidenceNodeType | str,
        target_id: str,
        relationship_type: EvidenceRelationType | str = EvidenceRelationType.SUPPORTED_BY,
        research_run_id: str | None = None,
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceLink:
        """Create a validated, cycle-checked, run-scoped directed evidence link.

        In a link (source -> target), source provides evidence or derivation for target.
        For example: Analysis -> Claim (relationship: supported_by), or
        Execution -> Analysis (relationship: derived_from).

        Raises:
            NodeNotFoundError: If source or target node cannot be found.
            CrossRunEvidenceError: If source and target belong to different research runs.
            EvidenceCycleError: If the link would introduce a directed cycle.
        """
        norm_source_type = self._normalize_node_type(source_type)
        norm_target_type = self._normalize_node_type(target_type)
        norm_rel = self._normalize_relation_type(relationship_type)

        # 1. Resolve both nodes and verify their run scoping
        _, source_run = self.resolve_node(norm_source_type, source_id)
        _, target_run = self.resolve_node(norm_target_type, target_id)

        # Check cross-run boundary
        if source_run is not None and target_run is not None and source_run != target_run:
            raise CrossRunEvidenceError(
                f"Cross-run evidence link rejected: source '{source_id}' is in run '{source_run}', "
                f"but target '{target_id}' is in run '{target_run}'."
            )

        eff_run_id = research_run_id or source_run or target_run
        if (
            research_run_id
            and source_run
            and research_run_id != source_run
            or research_run_id
            and target_run
            and research_run_id != target_run
        ):
            raise CrossRunEvidenceError(
                f"Explicit research_run_id '{research_run_id}' does not match entity run scope."
            )

        # 2. Cycle detection: adding source -> target must not form a cycle.
        # If target can already reach source, then source -> target creates a cycle.
        if source_id == target_id:
            raise EvidenceCycleError(
                f"Self-referential evidence link not permitted on node '{source_id}'."
            )

        if self.has_path(target_id, source_id):
            raise EvidenceCycleError(
                f"Evidence link from '{source_id}' to '{target_id}' would create a directed cycle."
            )

        # 3. Check for existing identical link (idempotency)
        existing = self.session.execute(
            select(EvidenceLinkModel).where(
                EvidenceLinkModel.source_id == source_id,
                EvidenceLinkModel.target_id == target_id,
                EvidenceLinkModel.relationship_type == norm_rel.value,
            )
        ).scalar_one_or_none()

        if existing is not None:
            return EvidenceLink.from_persistence(existing)

        # 4. Determine claim_id for backward compatibility
        legacy_claim_id: str | None = None
        if norm_target_type == EvidenceNodeType.CLAIM:
            legacy_claim_id = target_id
        elif norm_source_type == EvidenceNodeType.CLAIM:
            legacy_claim_id = source_id

        # 5. Persist link
        link_model = self.repo.create(
            source_type=norm_source_type.value,
            source_id=source_id,
            target_type=norm_target_type.value,
            target_id=target_id,
            relationship_type=norm_rel.value,
            research_run_id=eff_run_id,
            claim_id=legacy_claim_id,
            created_by=created_by,
            metadata=metadata or {},
        )
        self.session.flush()

        return EvidenceLink.from_persistence(link_model)

    def get_links_from(
        self, source_type: EvidenceNodeType | str, source_id: str
    ) -> list[EvidenceLink]:
        """Retrieve all outbound evidence links from a source node."""
        norm_type = self._normalize_node_type(source_type)
        models = self.repo.list_by_source(norm_type.value, source_id)
        return [EvidenceLink.from_persistence(m) for m in models]

    def get_links_to(
        self, target_type: EvidenceNodeType | str, target_id: str
    ) -> list[EvidenceLink]:
        """Retrieve all inbound evidence links to a target node."""
        norm_type = self._normalize_node_type(target_type)
        models = self.repo.list_by_target(norm_type.value, target_id)
        return [EvidenceLink.from_persistence(m) for m in models]

    def get_links_for_run(self, research_run_id: str) -> list[EvidenceLink]:
        """Retrieve all evidence links within a specific research run."""
        models = self.repo.list_by_run(research_run_id)
        return [EvidenceLink.from_persistence(m) for m in models]

    def trace_claim_lineage(self, claim_id: str) -> ClaimLineage:
        """Trace the mechanical lineage from a Claim down to empirical foundations.

        Traverses:
            Claim
              ↓
            Analysis / Result
              ↓
            Execution
              ↓
            Experiment
              ↓
            Raw Artifacts

        Returns ClaimLineage containing all traversed entities and verifying
        whether the empirical chain is complete.
        """
        claim_model = self.session.get(ClaimModel, claim_id)
        if claim_model is None:
            raise NodeNotFoundError(f"Claim '{claim_id}' not found.")

        claim = Claim.from_persistence(claim_model)
        gaps: list[str] = []

        all_links: list[EvidenceLink] = []
        analyses: list[AnalysisModel] = []
        results: list[ResultModel] = []
        executions: list[ExecutionModel] = []
        experiments: list[ExperimentModel] = []
        artifacts: list[ArtifactModel] = []

        # Inbound links supporting this claim (source -> claim)
        claim_inbound = self.repo.list_by_target(EvidenceNodeType.CLAIM.value, claim_id)
        for link in claim_inbound:
            all_links.append(EvidenceLink.from_persistence(link))

        if not claim_inbound:
            gaps.append(f"Claim '{claim_id}' has no supporting evidence links.")

        for link in claim_inbound:
            source_type = link.source_type
            source_id = link.source_id

            if source_type == EvidenceNodeType.ANALYSIS.value:
                analysis = self.session.get(AnalysisModel, source_id)
                if analysis:
                    analyses.append(analysis)
                    # Trace analysis -> results
                    for res_id in analysis.input_result_ids:
                        res = self.session.get(ResultModel, res_id)
                        if res:
                            results.append(res)
                            if res.execution:
                                executions.append(res.execution)
                                if res.execution.experiment:
                                    experiments.append(res.execution.experiment)
                                artifacts.extend(res.execution.artifacts)
                        else:
                            gaps.append(
                                f"Analysis '{analysis.id}' references missing Result '{res_id}'."
                            )
                else:
                    gaps.append(f"Evidence link points to missing Analysis '{source_id}'.")

            elif source_type == EvidenceNodeType.RESULT.value:
                res = self.session.get(ResultModel, source_id)
                if res:
                    results.append(res)
                    if res.execution:
                        executions.append(res.execution)
                        if res.execution.experiment:
                            experiments.append(res.execution.experiment)
                        artifacts.extend(res.execution.artifacts)
                else:
                    gaps.append(f"Evidence link points to missing Result '{source_id}'.")

            elif source_type == EvidenceNodeType.ARTIFACT.value:
                art = self.session.get(ArtifactModel, source_id)
                if art:
                    artifacts.append(art)
                    if art.execution:
                        executions.append(art.execution)
                        if art.execution.experiment:
                            experiments.append(art.execution.experiment)
                else:
                    gaps.append(f"Evidence link points to missing Artifact '{source_id}'.")

        # Deduplicate entities while preserving order
        def _dedup(items: list[Any]) -> list[Any]:
            seen: set[str] = set()
            out: list[Any] = []
            for item in items:
                if item.id not in seen:
                    seen.add(item.id)
                    out.append(item)
            return out

        dedup_analyses = _dedup(analyses)
        dedup_results = _dedup(results)
        dedup_executions = _dedup(executions)
        dedup_experiments = _dedup(experiments)
        dedup_artifacts = _dedup(artifacts)

        # Check empirical lineage completeness:
        # A claim is empirically complete if it has:
        # 1. At least one Analysis or Result
        # 2. At least one Execution
        # 3. At least one Experiment
        # 4. No unresolved gaps
        has_empirical_base = bool(dedup_results or dedup_analyses)
        has_execution = bool(dedup_executions)
        has_experiment = bool(dedup_experiments)

        if not has_empirical_base:
            gaps.append("Claim is missing empirical measurement or statistical analysis.")
        if not has_execution:
            gaps.append("Claim lineage does not reach an execution attempt.")
        if not has_experiment:
            gaps.append("Claim lineage does not reach an experiment specification.")

        is_complete = bool(has_empirical_base and has_execution and has_experiment and not gaps)

        return ClaimLineage(
            claim=claim,
            analyses=dedup_analyses,
            results=dedup_results,
            executions=dedup_executions,
            experiments=dedup_experiments,
            artifacts=dedup_artifacts,
            links=all_links,
            is_complete=is_complete,
            gaps=gaps,
        )
