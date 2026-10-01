"""Unit tests for REX-024 Claims Subsystem."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.domain.models import ClaimStatus, ClaimType, EvidenceNodeType
from rex.evidence.claims import (
    ClaimService,
    UnauthorizedClaimError,
    UnsupportedClaimError,
)
from rex.observability.events import ActorType, EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def run_and_sink(db_session: Session) -> tuple[str, InMemoryEventSink]:
    sink = InMemoryEventSink()
    run = ResearchRunModel(title="Research Run", research_question="Testing Claims")
    db_session.add(run)
    db_session.commit()
    return run.id, sink


@pytest.mark.unit
def test_create_claim_success(
    db_session: Session, run_and_sink: tuple[str, InMemoryEventSink]
) -> None:
    run_id, sink = run_and_sink
    service = ClaimService(db_session, event_sink=sink)

    claim = service.create_claim(
        research_run_id=run_id,
        statement="Model accuracy exceeds 90%.",
        claim_type=ClaimType.OBSERVATION,
        actor=ActorType.RESEARCH_AGENT,
    )

    assert claim.id.startswith("clm") or claim.id.startswith("claim")
    assert claim.statement == "Model accuracy exceeds 90%."
    assert claim.status == ClaimStatus.DRAFT

    # Verify event emission
    events = sink.get_by_type(EventType.CLAIM_CREATED)
    assert len(events) == 1
    assert events[0].payload["claim_id"] == claim.id


@pytest.mark.unit
def test_create_claim_forbidden_initial_verified(
    db_session: Session, run_and_sink: tuple[str, InMemoryEventSink]
) -> None:
    run_id, sink = run_and_sink
    service = ClaimService(db_session, event_sink=sink)

    with pytest.raises(UnauthorizedClaimError, match="VERIFIED"):
        service.create_claim(
            research_run_id=run_id,
            statement="Unverified statement trying to sneak in verified.",
            status=ClaimStatus.VERIFIED,
            actor=ActorType.RESEARCH_AGENT,
        )


@pytest.mark.unit
def test_create_claim_unauthorized_actor(
    db_session: Session, run_and_sink: tuple[str, InMemoryEventSink]
) -> None:
    run_id, sink = run_and_sink
    service = ClaimService(db_session, event_sink=sink)

    with pytest.raises(UnauthorizedClaimError):
        service.create_claim(
            research_run_id=run_id,
            statement="Worker trying to make a scientific claim.",
            actor=ActorType.EXECUTION_WORKER,
        )


@pytest.mark.unit
def test_attach_evidence_promotes_draft_to_supported(
    db_session: Session, run_and_sink: tuple[str, InMemoryEventSink]
) -> None:
    run_id, sink = run_and_sink
    service = ClaimService(db_session, event_sink=sink)

    exp = ExperimentModel(research_run_id=run_id, title="Exp")
    db_session.add(exp)
    db_session.flush()

    exec_m = ExecutionModel(experiment_id=exp.id, status="completed")
    db_session.add(exec_m)
    db_session.flush()

    res = ResultModel(execution_id=exec_m.id, metric_name="acc", metric_value=0.92)
    db_session.add(res)
    db_session.flush()

    claim = service.create_claim(
        research_run_id=run_id,
        statement="Accuracy is 92%.",
        actor=ActorType.RESEARCH_AGENT,
    )
    assert claim.status == ClaimStatus.DRAFT

    # Attach result evidence
    link = service.attach_evidence(
        claim_id=claim.id,
        evidence_type=EvidenceNodeType.RESULT,
        evidence_id=res.id,
        actor=ActorType.RESEARCH_AGENT,
    )

    assert link.target_id == claim.id
    updated_claim = service.get_claim(claim.id)
    assert updated_claim.status == ClaimStatus.SUPPORTED


@pytest.mark.unit
def test_verify_claim_authority_and_preconditions(
    db_session: Session, run_and_sink: tuple[str, InMemoryEventSink]
) -> None:
    run_id, sink = run_and_sink
    service = ClaimService(db_session, event_sink=sink)

    claim = service.create_claim(
        research_run_id=run_id,
        statement="Unsupported hypothesis assertion.",
        actor=ActorType.RESEARCH_AGENT,
    )

    # 1. Non-verifier cannot mark VERIFIED
    with pytest.raises(UnauthorizedClaimError, match="Only ActorType.VERIFIER"):
        service.update_claim_status(
            claim_id=claim.id,
            new_status=ClaimStatus.VERIFIED,
            actor=ActorType.RESEARCH_AGENT,
        )

    # 2. Verifier actor cannot mark VERIFIED if no supporting evidence exists
    with pytest.raises(UnsupportedClaimError, match="no supporting evidence"):
        service.update_claim_status(
            claim_id=claim.id,
            new_status=ClaimStatus.VERIFIED,
            actor=ActorType.VERIFIER,
        )

    # 3. Add empirical evidence
    exp = ExperimentModel(research_run_id=run_id, title="Exp")
    db_session.add(exp)
    db_session.flush()

    exec_m = ExecutionModel(experiment_id=exp.id, status="completed")
    db_session.add(exec_m)
    db_session.flush()

    res = ResultModel(execution_id=exec_m.id, metric_name="loss", metric_value=0.01)
    db_session.add(res)
    db_session.flush()

    service.attach_evidence(
        claim_id=claim.id,
        evidence_type=EvidenceNodeType.RESULT,
        evidence_id=res.id,
        actor=ActorType.RESEARCH_AGENT,
    )

    # 4. Now Verifier can verify
    verified_claim = service.update_claim_status(
        claim_id=claim.id,
        new_status=ClaimStatus.VERIFIED,
        actor=ActorType.VERIFIER,
        reason="Lineage and results verified",
    )
    assert verified_claim.status == ClaimStatus.VERIFIED
