"""REX Claims Subsystem (REX-024).

Distinguishes human and agent claims from raw empirical results and analyses.
Enforces claim lifecycle state transitions, verification authority restrictions,
and evidence-attachment guarantees.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rex.domain.models import (
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceLink,
    EvidenceNodeType,
    EvidenceRelationType,
)
from rex.evidence.graph import EvidenceGraphService
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
)
from rex.persistence.models import ClaimModel, ResearchRunModel
from rex.persistence.repositories import ClaimRepository, EventRepository


class ClaimError(Exception):
    """Base exception for claim operations."""


class ClaimNotFoundError(ClaimError):
    """Raised when a claim does not exist."""


class UnauthorizedClaimError(ClaimError):
    """Raised when an actor lacks permission to perform a claim action."""


class UnsupportedClaimError(ClaimError):
    """Raised when a claim lacks supporting empirical evidence for verification."""


class InvalidClaimStatusTransitionError(ClaimError):
    """Raised when an invalid status transition is requested."""


CLAIM_CREATOR_ACTORS: frozenset[ActorType] = frozenset(
    {
        ActorType.OWNER,
        ActorType.SYSTEM,
        ActorType.RESEARCH_AGENT,
    }
)


class ClaimService:
    """Service managing scientific claims, authority boundaries, and evidence linkages."""

    def __init__(self, session: Session, event_sink: EventSink | None = None) -> None:
        self.session = session
        self.repo = ClaimRepository(session)
        self.graph = EvidenceGraphService(session)
        self.event_sink = event_sink if event_sink is not None else EventRepository(session)

    def _normalize_actor(self, actor: ActorType | str) -> ActorType:
        if isinstance(actor, ActorType):
            return actor
        try:
            return ActorType(str(actor).strip().lower())
        except ValueError:
            raise UnauthorizedClaimError(f"Unknown actor type: {actor}") from None

    def _normalize_status(self, status: ClaimStatus | str) -> ClaimStatus:
        if isinstance(status, ClaimStatus):
            return status
        try:
            return ClaimStatus(str(status).strip().lower())
        except ValueError:
            raise InvalidClaimStatusTransitionError(f"Unknown claim status: {status}") from None

    def _normalize_claim_type(self, claim_type: ClaimType | str) -> ClaimType:
        if isinstance(claim_type, ClaimType):
            return claim_type
        try:
            return ClaimType(str(claim_type).strip().lower())
        except ValueError:
            return ClaimType.OBSERVATION

    def create_claim(
        self,
        research_run_id: str,
        statement: str,
        claim_type: ClaimType | str = ClaimType.OBSERVATION,
        status: ClaimStatus | str = ClaimStatus.DRAFT,
        confidence_score: float | None = None,
        created_by: str = "system",
        actor: ActorType | str = ActorType.RESEARCH_AGENT,
        metadata: dict[str, Any] | None = None,
    ) -> Claim:
        """Create a new scientific claim within a research run.

        Enforces:
        - Research run existence.
        - Authorized creator actor.
        - Prohibition of direct creation with VERIFIED status.
        """
        actor_enum = self._normalize_actor(actor)
        if actor_enum not in CLAIM_CREATOR_ACTORS:
            raise UnauthorizedClaimError(
                f"Actor '{actor_enum.value}' is not authorized to create scientific claims."
            )

        run = self.session.get(ResearchRunModel, research_run_id)
        if run is None:
            raise ClaimError(f"Research run '{research_run_id}' not found.")

        initial_status = self._normalize_status(status)
        if initial_status == ClaimStatus.VERIFIED:
            raise UnauthorizedClaimError(
                "Claims cannot be created directly with VERIFIED status. "
                "Verification must be performed through the verifier engine."
            )

        norm_type = self._normalize_claim_type(claim_type)

        model = ClaimModel(
            research_run_id=research_run_id,
            statement=statement.strip(),
            claim_type=norm_type.value,
            status=initial_status.value,
            confidence_score=confidence_score,
            created_by=created_by,
            metadata_json=metadata or {},
        )
        created_model = self.repo.create(model)
        self.session.flush()

        # Emit CLAIM_CREATED event
        event = create_event(
            event_type=EventType.CLAIM_CREATED,
            research_run_id=research_run_id,
            actor=actor_enum,
            payload={
                "claim_id": created_model.id,
                "statement": created_model.statement,
                "claim_type": created_model.claim_type,
                "status": created_model.status,
                "created_by": created_by,
            },
        )
        self.event_sink.emit(event)

        return Claim.from_persistence(created_model)

    def attach_evidence(
        self,
        claim_id: str,
        evidence_type: EvidenceNodeType | str,
        evidence_id: str,
        relationship_type: EvidenceRelationType | str = EvidenceRelationType.SUPPORTED_BY,
        created_by: str = "system",
        actor: ActorType | str = ActorType.RESEARCH_AGENT,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceLink:
        """Attach an empirical evidence node (Analysis, Result, Artifact) to a claim."""
        actor_enum = self._normalize_actor(actor)
        claim_model = self.session.get(ClaimModel, claim_id)
        if claim_model is None:
            raise ClaimNotFoundError(f"Claim '{claim_id}' not found.")

        # Create link in evidence graph (source -> claim)
        link = self.graph.create_link(
            source_type=evidence_type,
            source_id=evidence_id,
            target_type=EvidenceNodeType.CLAIM,
            target_id=claim_id,
            relationship_type=relationship_type,
            research_run_id=claim_model.research_run_id,
            created_by=created_by,
            metadata=metadata,
        )

        # If claim is in DRAFT, transition to SUPPORTED
        if claim_model.status == ClaimStatus.DRAFT.value:
            claim_model.status = ClaimStatus.SUPPORTED.value
            self.session.flush()

        # Emit EVIDENCE_LINKED event
        event = create_event(
            event_type=EventType.EVIDENCE_LINKED,
            research_run_id=claim_model.research_run_id,
            actor=actor_enum,
            payload={
                "claim_id": claim_id,
                "evidence_type": str(evidence_type),
                "evidence_id": evidence_id,
                "relationship_type": str(relationship_type),
                "link_id": link.id,
            },
        )
        self.event_sink.emit(event)

        return link

    def update_claim_status(
        self,
        claim_id: str,
        new_status: ClaimStatus | str,
        actor: ActorType | str,
        reason: str | None = None,
    ) -> Claim:
        """Update claim status according to authority rules and empirical requirements.

        Rules:
        - Only ActorType.VERIFIER (or SYSTEM during verifier runs) can transition to VERIFIED.
        - Transition to VERIFIED requires that supporting evidence actually exists.
        - Agents cannot directly verify claims.
        """
        actor_enum = self._normalize_actor(actor)
        target_status = self._normalize_status(new_status)

        claim_model = self.session.get(ClaimModel, claim_id)
        if claim_model is None:
            raise ClaimNotFoundError(f"Claim '{claim_id}' not found.")

        # Security gate for VERIFIED status
        if target_status == ClaimStatus.VERIFIED:
            if actor_enum not in (ActorType.VERIFIER, ActorType.SYSTEM):
                raise UnauthorizedClaimError(
                    f"Actor '{actor_enum.value}' is not authorized to verify claims. "
                    "Only ActorType.VERIFIER may verify claims."
                )

            # Check that supporting evidence links exist
            supporting_links = self.graph.get_links_to(EvidenceNodeType.CLAIM, claim_id)
            if not supporting_links:
                raise UnsupportedClaimError(
                    f"Claim '{claim_id}' has no supporting evidence and cannot be verified."
                )

        claim_model.status = target_status.value
        if reason:
            meta = dict(claim_model.metadata_json or {})
            meta["status_reason"] = reason
            claim_model.metadata_json = meta

        self.session.flush()

        # Emit audit event
        event = create_event(
            event_type=EventType.AGENT_ACTION,
            research_run_id=claim_model.research_run_id,
            actor=actor_enum,
            payload={
                "action": "claim_status_updated",
                "claim_id": claim_id,
                "new_status": target_status.value,
                "reason": reason or "",
            },
        )
        self.event_sink.emit(event)

        return Claim.from_persistence(claim_model)

    def get_claim(self, claim_id: str) -> Claim:
        """Fetch a domain Claim by ID."""
        model = self.session.get(ClaimModel, claim_id)
        if model is None:
            raise ClaimNotFoundError(f"Claim '{claim_id}' not found.")
        return Claim.from_persistence(model)

    def list_claims(self, research_run_id: str) -> list[Claim]:
        """List all claims for a given research run."""
        stmt = (
            select(ClaimModel)
            .where(ClaimModel.research_run_id == research_run_id)
            .order_by(ClaimModel.created_at.asc())
        )
        models = self.session.execute(stmt).scalars().all()
        return [Claim.from_persistence(m) for m in models]
