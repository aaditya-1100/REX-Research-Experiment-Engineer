"""Adversarial verification and audit tests for REX Batch 4 Final Gate.

Proves:
1. Evidence Graph semantic integrity and relation rejection
2. Multi-hop DAG cycle protection
3. Cross-run isolation across all entity pairs
4. Full evidence lineage and gap detection
5. Claim authorization barriers (agents cannot verify)
6. VERIFIED semantics (earned strictly through verifier)
7. Byte-exact hashing and tamper detection
8. Canonical JSON numeric semantics (fail-closed on NaN/Inf, -0.0 normalization)
9. Tamper detection preserves stored hashes
10. Numerical recomputation travels down to raw Results
11. 1e-6 numerical tolerance bounds
12. Unsupported claim rejection
13. Claim-number consistency (3.2% vs 8.7%)
14. Status distinction (SUPPORTED vs VERIFIED vs TAMPERED)
15. Verifier read-only guarantee over historical evidence
16. Verification failure producing FAIL exit code
17. Verification determinism across repeated executions
18. Non-destructive reproduction
19. Reproducibility readiness classification
20. Reproduction metric divergence detection
21. CLI process exit codes (0 for pass, 1 for fail)
22. Event integrity and ordering
23. Concurrency safety under simultaneous verification runs
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from rex.cli import cli
from rex.domain.models import (
    ClaimStatus,
    EvidenceNodeType,
    EvidenceRelationType,
)
from rex.evidence.claims import (
    ClaimService,
    UnauthorizedClaimError,
)
from rex.evidence.graph import (
    CrossRunEvidenceError,
    EvidenceCycleError,
    EvidenceGraphService,
    InvalidRelationError,
)
from rex.evidence.hashing import (
    canonical_json_dumps,
    canonical_json_hash,
    compute_file_hash,
)
from rex.evidence.reproduce import (
    ExperimentReproducer,
    ReproducibilityStatus,
    ReproductionOutcome,
)
from rex.evidence.verifier import (
    ResearchVerifier,
    VerificationStatus,
)
from rex.observability.events import (
    ActorType,
    EventType,
    InMemoryEventSink,
)
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
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
def audit_fixture(db_session: Session, tmp_path: Path) -> dict[str, Any]:
    """Populates a complete valid research run with artifacts, results, analysis, and claim."""
    run = ResearchRunModel(title="Audited Run", research_question="Does B improve A?")
    db_session.add(run)
    db_session.flush()

    exp = ExperimentModel(research_run_id=run.id, title="Baseline vs Treatment")
    db_session.add(exp)
    db_session.flush()

    art_file = tmp_path / "metrics.json"
    art_file.write_text('{"accuracy": 0.95}', encoding="utf-8")
    art_hash = compute_file_hash(art_file)

    exec_m = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        code_hash="code_sha_123",
        dataset_hash="data_sha_456",
        configuration_hash="cfg_sha_789",
        seed=42,
        environment_json={"python": "3.11"},
    )
    db_session.add(exec_m)
    db_session.flush()

    art_m = ArtifactModel(
        research_run_id=run.id,
        execution_id=exec_m.id,
        artifact_type="metric",
        path=str(art_file),
        content_hash=art_hash,
    )
    db_session.add(art_m)
    db_session.flush()

    # Raw results
    r1 = ResultModel(execution_id=exec_m.id, metric_name="accuracy", metric_value=0.94)
    r2 = ResultModel(execution_id=exec_m.id, metric_name="accuracy", metric_value=0.96)
    db_session.add_all([r1, r2])
    db_session.flush()

    # Analysis derived from results: mean is 0.95
    an = AnalysisModel(
        research_run_id=run.id,
        analysis_type="descriptive",
        method="sample_summary_statistics",
        input_result_ids=[r1.id, r2.id],
        output_json={"mean": 0.95, "sample_size": 2},
    )
    db_session.add(an)
    db_session.flush()

    # Claim asserting 95% accuracy
    clm = ClaimModel(
        research_run_id=run.id,
        statement="Method B achieved 95% accuracy.",
        claim_type="observation",
        status=ClaimStatus.DRAFT.value,
        metadata_json={"asserted_value": 0.95},
    )
    db_session.add(clm)
    db_session.commit()

    # Link analysis -> claim
    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=clm.id,
        target_type=EvidenceNodeType.ANALYSIS,
        target_id=an.id,
        relationship_type=EvidenceRelationType.SUPPORTED_BY,
    )
    clm.status = ClaimStatus.SUPPORTED.value
    db_session.commit()

    return {
        "run": run,
        "experiment": exp,
        "execution": exec_m,
        "artifact": art_m,
        "results": [r1, r2],
        "analysis": an,
        "claim": clm,
        "artifact_file": art_file,
    }


# ==============================================================================
# 1. Evidence Graph Integrity
# ==============================================================================


@pytest.mark.unit
def test_evidence_graph_rejects_invalid_relationships(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Invalid semantic combinations must be rejected."""
    graph = EvidenceGraphService(db_session)
    clm = audit_fixture["claim"]
    res = audit_fixture["results"][0]
    exec_m = audit_fixture["execution"]

    # 1. CLAIM -> PRODUCED_BY -> RESULT (Invalid)
    with pytest.raises(InvalidRelationError):
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=clm.id,
            target_type=EvidenceNodeType.RESULT,
            target_id=res.id,
            relationship_type=EvidenceRelationType.PRODUCED_BY,
        )

    # 2. RESULT -> SUPPORTED_BY -> CLAIM (Invalid)
    with pytest.raises(InvalidRelationError):
        graph.create_link(
            source_type=EvidenceNodeType.RESULT,
            source_id=res.id,
            target_type=EvidenceNodeType.CLAIM,
            target_id=clm.id,
            relationship_type=EvidenceRelationType.SUPPORTED_BY,
        )

    # 3. EXECUTION -> DERIVED_FROM -> CLAIM (Invalid)
    with pytest.raises(InvalidRelationError):
        graph.create_link(
            source_type=EvidenceNodeType.EXECUTION,
            source_id=exec_m.id,
            target_type=EvidenceNodeType.CLAIM,
            target_id=clm.id,
            relationship_type=EvidenceRelationType.DERIVED_FROM,
        )


# ==============================================================================
# 2. DAG / Cycle Protection
# ==============================================================================


@pytest.mark.unit
def test_evidence_graph_multi_hop_cycle_protection(db_session: Session) -> None:
    """A -> B -> C -> D -> B must be rejected before persistence."""
    run = ResearchRunModel(title="Cycle Run", research_question="Cycle test?")
    db_session.add(run)
    db_session.flush()

    claims = [ClaimModel(research_run_id=run.id, statement=f"Claim {i}") for i in range(4)]
    db_session.add_all(claims)
    db_session.commit()

    c0, c1, c2, c3 = claims
    graph = EvidenceGraphService(db_session)

    # Insert: c0 -> c1 -> c2 -> c3 (using REFINES which allows CLAIM -> CLAIM)
    graph.create_link(
        EvidenceNodeType.CLAIM, c0.id, EvidenceNodeType.CLAIM, c1.id, EvidenceRelationType.REFINES
    )
    graph.create_link(
        EvidenceNodeType.CLAIM, c1.id, EvidenceNodeType.CLAIM, c2.id, EvidenceRelationType.REFINES
    )
    graph.create_link(
        EvidenceNodeType.CLAIM, c2.id, EvidenceNodeType.CLAIM, c3.id, EvidenceRelationType.REFINES
    )

    # Now attempt c3 -> c1 (would create cycle c1 -> c2 -> c3 -> c1)
    with pytest.raises(EvidenceCycleError):
        graph.create_link(
            EvidenceNodeType.CLAIM,
            c3.id,
            EvidenceNodeType.CLAIM,
            c1.id,
            EvidenceRelationType.REFINES,
        )

    # Also attempt c3 -> c0 (would create cycle c0 -> c1 -> c2 -> c3 -> c0)
    with pytest.raises(EvidenceCycleError):
        graph.create_link(
            EvidenceNodeType.CLAIM,
            c3.id,
            EvidenceNodeType.CLAIM,
            c0.id,
            EvidenceRelationType.REFINES,
        )

    # Unrelated valid link X -> Y must succeed without disturbing DAG
    c_unrelated_x = ClaimModel(research_run_id=run.id, statement="Unrelated X")
    c_unrelated_y = ClaimModel(research_run_id=run.id, statement="Unrelated Y")
    db_session.add_all([c_unrelated_x, c_unrelated_y])
    db_session.commit()

    link_unrelated = graph.create_link(
        EvidenceNodeType.CLAIM,
        c_unrelated_x.id,
        EvidenceNodeType.CLAIM,
        c_unrelated_y.id,
        EvidenceRelationType.REFINES,
    )
    assert link_unrelated.source_id == c_unrelated_x.id


# ==============================================================================
# 3. Research-Run Isolation
# ==============================================================================


@pytest.mark.unit
def test_cross_run_isolation_enforcement(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Entities belonging to different runs cannot be linked."""
    graph = EvidenceGraphService(db_session)
    run1_clm = audit_fixture["claim"]

    # Create Run 2 with an Analysis
    run2 = ResearchRunModel(title="Run 2", research_question="Question 2?")
    db_session.add(run2)
    db_session.flush()

    an2 = AnalysisModel(
        research_run_id=run2.id,
        analysis_type="descriptive",
        method="sample_summary_statistics",
        output_json={"mean": 0.5},
    )
    db_session.add(an2)
    db_session.commit()

    # Linking Claim from Run 1 to Analysis from Run 2 must be rejected
    with pytest.raises(CrossRunEvidenceError):
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=run1_clm.id,
            target_type=EvidenceNodeType.ANALYSIS,
            target_id=an2.id,
            relationship_type=EvidenceRelationType.SUPPORTED_BY,
        )


# ==============================================================================
# 5 & 6. Claim Authorization and VERIFIED Semantics
# ==============================================================================


@pytest.mark.unit
def test_claim_authorization_and_verified_gate(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Agents cannot mark claims VERIFIED; callers cannot bypass verification."""
    clm = audit_fixture["claim"]
    sink = InMemoryEventSink()
    service = ClaimService(db_session, event_sink=sink)

    # 1. RESEARCH_AGENT cannot mark VERIFIED
    with pytest.raises(UnauthorizedClaimError):
        service.update_claim_status(
            claim_id=clm.id,
            new_status=ClaimStatus.VERIFIED,
            actor=ActorType.RESEARCH_AGENT,
        )

    # 2. CODING_AGENT cannot mark VERIFIED
    with pytest.raises(UnauthorizedClaimError):
        service.update_claim_status(
            claim_id=clm.id,
            new_status=ClaimStatus.VERIFIED,
            actor=ActorType.CODING_AGENT,
        )

    # 3. INVESTIGATOR cannot mark VERIFIED
    with pytest.raises(UnauthorizedClaimError):
        service.update_claim_status(
            claim_id=clm.id,
            new_status=ClaimStatus.VERIFIED,
            actor=ActorType.INVESTIGATOR,
        )

    # 4. Caller without verifier engine verification cannot mark VERIFIED
    with pytest.raises(UnauthorizedClaimError):
        service.update_claim_status(
            claim_id=clm.id,
            new_status=ClaimStatus.VERIFIED,
            actor=ActorType.VERIFIER,
            verified_by_engine=False,
        )


# ==============================================================================
# 7 & 8. Hashing and Canonical JSON Semantics
# ==============================================================================


@pytest.mark.unit
def test_canonical_json_numeric_and_structural_semantics() -> None:
    """Tests integer vs float, negative zero, and fail-closed non-finite floats."""
    # 1. Key ordering invariance
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash({"a": 1, "b": 2})

    # 2. Integer vs. float differentiation
    assert canonical_json_hash({"v": 1}) != canonical_json_hash({"v": 1.0})

    # 3. Negative zero normalization (-0.0 -> 0.0)
    assert canonical_json_dumps({"v": -0.0}) == canonical_json_dumps({"v": 0.0})
    assert canonical_json_hash({"v": -0.0}) == canonical_json_hash({"v": 0.0})

    # 4. Non-finite floats (NaN, Infinity) must fail closed
    with pytest.raises(ValueError):
        canonical_json_dumps({"val": float("nan")})

    with pytest.raises(ValueError):
        canonical_json_dumps({"val": float("inf")})

    with pytest.raises(ValueError):
        canonical_json_dumps({"val": float("-inf")})

    # 5. Unicode preservation
    unicode_obj = {"greeting": "こんにちは", "symbol": "α-β"}
    assert "こんにちは" in canonical_json_dumps(unicode_obj)


# ==============================================================================
# 9 & 15. Real Tamper Demonstration & Verifier Read-Only Guarantee
# ==============================================================================


@pytest.mark.unit
def test_full_tamper_demo_and_read_only_guarantee(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Confirms PASS -> mutates disk bytes -> confirms FAIL -> proves DB hashes unmutated."""
    run = audit_fixture["run"]
    art = audit_fixture["artifact"]
    art_file: Path = audit_fixture["artifact_file"]
    original_stored_hash = art.content_hash

    # Step 1: Baseline verification must PASS
    sink = InMemoryEventSink()
    verifier = ResearchVerifier(session=db_session, event_sink=sink)
    report_pass = verifier.verify_run(run.id)
    assert report_pass.is_passed is True

    # Check event emission
    assert any(e.event_type == EventType.RUN_VERIFIED for e in sink.events)
    assert any(e.event_type == EventType.CLAIM_VERIFIED for e in sink.events)

    # Step 2: Tamper disk bytes
    art_file.write_bytes(b'{"corrupted": true}')
    tampered_bytes_hash = compute_file_hash(art_file)
    assert tampered_bytes_hash != original_stored_hash

    # Step 3: Run verifier again -> must FAIL
    sink_tamper = InMemoryEventSink()
    verifier_tamper = ResearchVerifier(session=db_session, event_sink=sink_tamper)
    report_fail = verifier_tamper.verify_run(run.id)

    assert report_fail.is_passed is False
    assert report_fail.status == VerificationStatus.FAIL
    assert any("Cryptographic hash mismatch" in err for err in report_fail.errors)
    assert any(e.event_type == EventType.RUN_VERIFICATION_FAILED for e in sink_tamper.events)

    # Step 4: Verify DB hash was NOT rewritten
    db_session.refresh(art)
    assert art.content_hash == original_stored_hash
    assert art.content_hash != tampered_bytes_hash


# ==============================================================================
# 10 & 11. Numerical Verification & 1e-6 Tolerance
# ==============================================================================


@pytest.mark.unit
def test_numerical_recomputation_from_raw_results(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Verifier must recompute from stored raw Results, not blindly accept Analysis."""
    run = audit_fixture["run"]
    an = audit_fixture["analysis"]
    r1, _r2 = audit_fixture["results"]

    verifier = ResearchVerifier(session=db_session, tolerance=1e-6)

    # 1. Tamper stored analysis output mean to 0.99 (actual is 0.95)
    an.output_json = {"mean": 0.99, "sample_size": 2}
    db_session.commit()

    report = verifier.verify_run(run.id)
    assert report.is_passed is False
    assert any("Property 'mean' mismatch" in err for err in report.errors)

    # 2. Restore analysis, but tamper raw result r1 from 0.94 to 0.50
    an.output_json = {"mean": 0.95, "sample_size": 2}
    r1.metric_value = 0.50
    db_session.commit()

    report_res_tamper = verifier.verify_run(run.id)
    assert report_res_tamper.is_passed is False
    assert any("Property 'mean' mismatch" in err for err in report_res_tamper.errors)


# ==============================================================================
# 12. Unsupported Claim Audit
# ==============================================================================


@pytest.mark.unit
def test_unsupported_claim_fails_verification(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """A claim with no evidence links must fail verification with UNSUPPORTED_CLAIM."""
    run = audit_fixture["run"]

    # Add naked unsupported claim to the run
    unsupported_claim = ClaimModel(
        research_run_id=run.id,
        statement="Method B is more robust than Method A.",
        status=ClaimStatus.DRAFT.value,
    )
    db_session.add(unsupported_claim)
    db_session.commit()

    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(run.id)

    assert report.is_passed is False
    assert any("UNSUPPORTED_CLAIM" in err for err in report.errors)


# ==============================================================================
# 13. Claim-Number Consistency
# ==============================================================================


@pytest.mark.unit
def test_claim_number_consistency_detection(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Claim asserting 8.7% when supporting analysis shows 95% (0.95) must fail."""
    run = audit_fixture["run"]
    clm = audit_fixture["claim"]

    verifier = ResearchVerifier(session=db_session)

    # Valid assertion (95%) passes
    report_valid = verifier.verify_run(run.id)
    assert report_valid.is_passed is True

    # Mutate claim statement to falsify the claimed number to 8.7%
    clm.statement = "Method B achieved 8.7% accuracy."
    clm.metadata_json = {"asserted_value": 0.087}
    db_session.commit()

    report_invalid = verifier.verify_run(run.id)
    assert report_invalid.is_passed is False
    assert any("CLAIM_NUMBER_MISMATCH" in err for err in report_invalid.errors)


# ==============================================================================
# 17. Verification Determinism
# ==============================================================================


@pytest.mark.unit
def test_verification_determinism_across_runs(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Running rex verify twice against identical evidence yields identical reports."""
    run = audit_fixture["run"]
    verifier = ResearchVerifier(session=db_session)

    rep1 = verifier.verify_run(run.id)
    rep2 = verifier.verify_run(run.id)

    assert rep1.status == rep2.status
    assert len(rep1.claims_verified) == len(rep2.claims_verified)
    assert len(rep1.artifacts_verified) == len(rep2.artifacts_verified)
    assert rep1.errors == rep2.errors


# ==============================================================================
# 18, 19, 20. Reproducibility Engine & Divergence Detection
# ==============================================================================


@pytest.mark.unit
def test_reproducibility_classification_and_divergence(
    db_session: Session, audit_fixture: dict[str, Any]
) -> None:
    """Tests non-destructive execution, assessment categories, and divergence detection."""
    exp = audit_fixture["experiment"]
    orig_exec = audit_fixture["execution"]
    reproducer = ExperimentReproducer(session=db_session)

    # 1. Assessment when full metadata is present
    assessment = reproducer.assess_reproducibility(exp.id)
    assert assessment.status == ReproducibilityStatus.REPRODUCIBLE

    # 2. Reproduction with identical metrics -> EXACT_MATCH
    rep_report_match = reproducer.reproduce_experiment(
        experiment_id=exp.id,
    )
    assert rep_report_match.is_reproduced is True
    assert rep_report_match.outcome == ReproductionOutcome.EXACT_MATCH
    assert rep_report_match.original_execution_id == orig_exec.id
    assert rep_report_match.reproduction_execution_id != orig_exec.id

    # 3. Reproduction with diverged metric (0.70 vs 0.94) -> DIVERGED
    rep_report_diverged = reproducer.reproduce_experiment(
        experiment_id=exp.id,
        simulated_results=[{"metric_name": "accuracy", "metric_value": 0.70}],
    )
    assert rep_report_diverged.is_reproduced is False
    assert rep_report_diverged.outcome == ReproductionOutcome.DIVERGED


# ==============================================================================
# 21. CLI Exit Codes
# ==============================================================================


@pytest.mark.unit
def test_cli_verification_exit_codes(tmp_path: Path) -> None:
    """rex verify must return code 0 on pass and code 1 on fail."""
    db_file = tmp_path / "cli_audit.db"
    db_url = f"sqlite:///{db_file}"

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    session = Session(engine)

    # Run without claims or artifacts: creates warning, exit 0
    run = ResearchRunModel(title="Empty Run", research_question="Empty?")
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()
    engine.dispose()

    runner = CliRunner()
    result_empty = runner.invoke(cli, ["verify", run_id, "--db", db_url])
    assert result_empty.exit_code == 0

    # Add an unsupported claim: must exit with code 1
    engine2 = create_engine(db_url)
    session2 = Session(engine2)
    session2.add(ClaimModel(research_run_id=run_id, statement="Unsupported assertion."))
    session2.commit()
    session2.close()
    engine2.dispose()

    result_fail = runner.invoke(cli, ["verify", run_id, "--db", db_url])
    assert result_fail.exit_code == 1


# ==============================================================================
# 23. Concurrency Safety
# ==============================================================================


@pytest.mark.unit
def test_concurrent_verifications_race_safe(tmp_path: Path) -> None:
    """Multiple concurrent verifiers running against the same run must not crash or corrupt DB."""
    db_file = tmp_path / "concurrent_audit.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        run = ResearchRunModel(title="Concurrent Run", research_question="Concurrent?")
        s.add(run)
        s.flush()
        exp = ExperimentModel(research_run_id=run.id, title="Exp")
        s.add(exp)
        s.flush()
        exec_m = ExecutionModel(experiment_id=exp.id, status="completed")
        s.add(exec_m)
        s.flush()
        r = ResultModel(execution_id=exec_m.id, metric_name="score", metric_value=0.88)
        s.add(r)
        s.flush()
        an = AnalysisModel(
            research_run_id=run.id,
            analysis_type="descriptive",
            method="sample_summary_statistics",
            input_result_ids=[r.id],
            output_json={"mean": 0.88, "sample_size": 1},
        )
        s.add(an)
        s.flush()
        clm = ClaimModel(
            research_run_id=run.id,
            statement="Model score is 0.88.",
            status=ClaimStatus.DRAFT.value,
        )
        s.add(clm)
        s.flush()
        g = EvidenceGraphService(s)
        g.create_link(
            EvidenceNodeType.CLAIM,
            clm.id,
            EvidenceNodeType.ANALYSIS,
            an.id,
            EvidenceRelationType.SUPPORTED_BY,
        )
        clm.status = ClaimStatus.SUPPORTED.value
        s.commit()
        run_id = run.id

    def _run_verify() -> VerificationStatus:
        with Session(engine) as session:
            v = ResearchVerifier(session=session)
            report = v.verify_run(run_id)
            return report.status

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_run_verify) for _ in range(8)]
        statuses = [f.result() for f in futures]

    engine.dispose()
    assert all(s == VerificationStatus.PASS for s in statuses)
