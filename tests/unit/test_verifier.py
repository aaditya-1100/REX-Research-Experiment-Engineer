"""Unit tests for REX-026 Research Verifier Engine."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.domain.models import ClaimStatus, EvidenceNodeType
from rex.evidence.verifier import (
    ResearchVerifier,
    VerificationStatus,
)
from rex.observability.events import EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
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
def run_setup(db_session: Session, tmp_path: Path) -> tuple[str, Path, InMemoryEventSink]:
    sink = InMemoryEventSink()
    run = ResearchRunModel(title="Verified Run", research_question="Does optimizer improve loss?")
    db_session.add(run)
    db_session.flush()

    exp = ExperimentModel(research_run_id=run.id, title="SGD vs AdamW")
    db_session.add(exp)
    db_session.flush()

    exec_m = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        code_hash="code_sha256",
        dataset_hash="data_sha256",
        configuration_hash="cfg_sha256",
    )
    db_session.add(exec_m)
    db_session.flush()

    # Create real artifact file on disk
    art_file = tmp_path / "summary.json"
    art_bytes = b'{"final_loss": 0.042}'
    art_file.write_bytes(art_bytes)
    art_hash = hashlib.sha256(art_bytes).hexdigest()

    art = ArtifactModel(
        research_run_id=run.id,
        execution_id=exec_m.id,
        artifact_type="output",
        path=str(art_file),
        content_hash=art_hash,
        size_bytes=len(art_bytes),
    )
    db_session.add(art)
    db_session.flush()

    # Results
    res1 = ResultModel(execution_id=exec_m.id, metric_name="loss", metric_value=0.04)
    res2 = ResultModel(execution_id=exec_m.id, metric_name="loss", metric_value=0.06)
    db_session.add_all([res1, res2])
    db_session.flush()

    # Analysis: mean = 0.05
    an = AnalysisModel(
        research_run_id=run.id,
        analysis_type="descriptive",
        method="sample_summary_statistics",
        input_result_ids=[res1.id, res2.id],
        output_json={
            "mean": 0.05,
            "median": 0.05,
            "variance": 0.0002,
            "std_dev": 0.0141421356,
            "sample_size": 2,
        },
    )
    db_session.add(an)
    db_session.flush()

    # Claim
    claim = ClaimModel(
        research_run_id=run.id,
        statement="Mean loss is 0.05 across trials.",
        claim_type="observation",
        status=ClaimStatus.VERIFIED.value,
    )
    db_session.add(claim)
    db_session.flush()

    # Link analysis -> claim
    link = EvidenceLinkModel(
        research_run_id=run.id,
        claim_id=claim.id,
        source_type=EvidenceNodeType.ANALYSIS.value,
        source_id=an.id,
        target_type=EvidenceNodeType.CLAIM.value,
        target_id=claim.id,
        relationship_type="supported_by",
    )
    db_session.add(link)
    db_session.commit()

    return run.id, tmp_path, sink


@pytest.mark.unit
def test_verify_run_pass(
    db_session: Session, run_setup: tuple[str, Path, InMemoryEventSink]
) -> None:
    run_id, _, sink = run_setup
    verifier = ResearchVerifier(session=db_session, event_sink=sink, tolerance=1e-4)

    report = verifier.verify_run(run_id)

    assert report.status == VerificationStatus.PASS
    assert report.is_passed is True
    assert len(report.errors) == 0
    assert len(report.claims_verified) == 1
    assert report.claims_verified[0].is_lineage_intact is True
    assert len(report.artifacts_verified) == 1
    assert report.artifacts_verified[0].is_valid is True
    assert len(report.analyses_recomputed) == 1
    assert report.analyses_recomputed[0].is_deterministic is True

    # Check event emission
    events = sink.get_by_type(EventType.VERIFICATION_COMPLETED)
    assert len(events) == 1
    assert events[0].payload["status"] == "pass"


@pytest.mark.unit
def test_verify_run_tampered_artifact(
    db_session: Session, run_setup: tuple[str, Path, InMemoryEventSink]
) -> None:
    run_id, tmp_path, sink = run_setup

    # Tamper with the artifact on disk!
    art_file = tmp_path / "summary.json"
    art_file.write_bytes(b'{"final_loss": 0.001}')  # Mutated bytes!

    verifier = ResearchVerifier(session=db_session, event_sink=sink)
    report = verifier.verify_run(run_id)

    assert report.status == VerificationStatus.FAIL
    assert report.is_passed is False
    assert any("Cryptographic hash mismatch" in err for err in report.errors)

    # Check failure event
    events = sink.get_by_type(EventType.VERIFICATION_FAILED)
    assert len(events) == 1
    assert events[0].payload["status"] == "fail"


@pytest.mark.unit
def test_verify_run_missing_artifact(
    db_session: Session, run_setup: tuple[str, Path, InMemoryEventSink]
) -> None:
    run_id, tmp_path, sink = run_setup

    # Delete the artifact file
    art_file = tmp_path / "summary.json"
    art_file.unlink()

    verifier = ResearchVerifier(session=db_session, event_sink=sink)
    report = verifier.verify_run(run_id)

    assert report.status == VerificationStatus.FAIL
    assert any("Artifact file missing on disk" in err for err in report.errors)


@pytest.mark.unit
def test_verify_run_tampered_analysis_statistics(
    db_session: Session, run_setup: tuple[str, Path, InMemoryEventSink]
) -> None:
    run_id, _, sink = run_setup

    # Tamper with the persisted AnalysisModel output_json: falsify the mean to 0.99
    an = db_session.query(AnalysisModel).filter_by(research_run_id=run_id).first()
    assert an is not None
    an.output_json = {
        "mean": 0.99,  # True mean from (0.04, 0.06) is 0.05
        "sample_size": 2,
    }
    db_session.commit()

    verifier = ResearchVerifier(session=db_session, event_sink=sink, tolerance=1e-4)
    report = verifier.verify_run(run_id)

    assert report.status == VerificationStatus.FAIL
    assert any("non-deterministic or tampered" in err for err in report.errors)


@pytest.mark.unit
def test_verify_run_broken_claim_lineage(
    db_session: Session, run_setup: tuple[str, Path, InMemoryEventSink]
) -> None:
    run_id, _, sink = run_setup

    # Add a claim claiming VERIFIED without any evidence links
    phantom_claim = ClaimModel(
        research_run_id=run_id,
        statement="Phantom claim without empirical lineage.",
        status=ClaimStatus.VERIFIED.value,
    )
    db_session.add(phantom_claim)
    db_session.commit()

    verifier = ResearchVerifier(session=db_session, event_sink=sink)
    report = verifier.verify_run(run_id)

    assert report.status == VerificationStatus.FAIL
    assert any("lineage is broken" in err for err in report.errors)
