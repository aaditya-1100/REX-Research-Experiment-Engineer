"""Integration test demonstrating full evidence lineage, CLI verification, and mechanical tamper detection."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from rex.analysis.statistics import StatisticalAnalyzer
from rex.cli import cli
from rex.domain.models import ClaimType, EvidenceNodeType
from rex.evidence.claims import ClaimService
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def integrated_pipeline(tmp_path: Path) -> tuple[str, str, str, Path, str]:
    """Sets up an end-to-end research run with disk artifacts and empirical chain."""
    db_file = tmp_path / "research.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    session = Session(engine)

    # 1. Research Run
    run = ResearchRunModel(
        title="Learning Rate Annealing Study",
        research_question="Does cosine annealing outperform constant rate?",
    )
    session.add(run)
    session.flush()

    # 2. Hypothesis
    hypo = HypothesisModel(
        research_run_id=run.id,
        statement="Cosine annealing yields significantly lower terminal validation loss.",
        falsification_condition="Validation loss is equal to or greater than constant schedule.",
    )
    session.add(hypo)
    session.flush()

    # 3. Experiment
    exp = ExperimentModel(
        research_run_id=run.id,
        hypothesis_id=hypo.id,
        title="Cosine vs Constant Schedule",
        parameters_json={"lr_schedule": "cosine", "epochs": 50},
    )
    session.add(exp)
    session.flush()

    # 4. Execution
    exec_m = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        command="python train_schedule.py --schedule cosine",
        git_commit="c0ffee1234567890",
        code_hash="code_sha256_mock",
        dataset_hash="dataset_sha256_mock",
        configuration_hash="cfg_sha256_mock",
        seed=101,
        environment_json={"python": "3.11.8", "cuda": "12.2"},
    )
    session.add(exec_m)
    session.flush()

    # 5. Raw Artifact on disk
    art_path = tmp_path / "loss_trajectory.csv"
    raw_bytes = b"epoch,val_loss\n1,0.50\n25,0.20\n50,0.05\n"
    art_path.write_bytes(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    art = ArtifactModel(
        research_run_id=run.id,
        execution_id=exec_m.id,
        artifact_type="metric",
        path=str(art_path),
        content_hash=content_hash,
        size_bytes=len(raw_bytes),
    )
    session.add(art)
    session.flush()

    # 6. Results
    r1 = ResultModel(execution_id=exec_m.id, metric_name="terminal_loss", metric_value=0.048)
    r2 = ResultModel(execution_id=exec_m.id, metric_name="terminal_loss", metric_value=0.052)
    session.add_all([r1, r2])
    session.flush()

    # 7. Analysis (computed via StatisticalAnalyzer)
    summary = StatisticalAnalyzer().compute_summary([r1, r2])
    an = AnalysisModel(
        research_run_id=run.id,
        analysis_type="descriptive",
        method="sample_summary_statistics",
        input_result_ids=[r1.id, r2.id],
        output_json=summary.to_dict(),
    )
    session.add(an)
    session.flush()

    # 8. Claim
    claims_service = ClaimService(session)
    claim = claims_service.create_claim(
        research_run_id=run.id,
        statement="Cosine annealing converges to mean loss of 0.05.",
        claim_type=ClaimType.OBSERVATION,
    )
    claims_service.attach_evidence(
        claim_id=claim.id,
        evidence_type=EvidenceNodeType.ANALYSIS,
        evidence_id=an.id,
    )

    session.commit()
    run_id = run.id
    exp_id = exp.id
    claim_id = claim.id
    session.close()

    return run_id, exp_id, claim_id, art_path, db_url


@pytest.mark.integration
def test_full_pipeline_verification_and_tamper_detection(
    integrated_pipeline: tuple[str, str, str, Path, str],
) -> None:
    run_id, exp_id, _claim_id, art_path, db_url = integrated_pipeline
    engine = create_engine(db_url)
    session = Session(engine)

    verifier = ResearchVerifier(session=session)

    # Step 1: Initial verification before tampering -> MUST PASS
    initial_report = verifier.verify_run(run_id)
    assert initial_report.status == VerificationStatus.PASS
    assert initial_report.is_passed is True
    assert len(initial_report.errors) == 0
    assert len(initial_report.artifacts_verified) == 1
    assert initial_report.artifacts_verified[0].is_valid is True

    # Step 2: Mechanical Tamper - mutate raw bytes of artifact file on disk
    original_bytes = art_path.read_bytes()
    tampered_bytes = original_bytes.replace(b"0.05", b"0.01")  # Tampering with metric in CSV!
    art_path.write_bytes(tampered_bytes)

    # Step 3: Verify again -> MUST FAIL and pinpoint corrupted file
    tampered_report = verifier.verify_run(run_id)
    assert tampered_report.status == VerificationStatus.FAIL
    assert tampered_report.is_passed is False
    assert len(tampered_report.errors) > 0
    assert any("Cryptographic hash mismatch" in e for e in tampered_report.errors)
    assert any(
        str(art_path.name) in str(a.path) and not a.is_valid
        for a in tampered_report.artifacts_verified
    )

    session.close()
    engine.dispose()

    # Step 4: CLI verification via Typer runner
    runner = CliRunner()

    # With tampered file, CLI verify exits with code 1
    cli_fail = runner.invoke(cli, ["verify", run_id, "--db", db_url])
    assert cli_fail.exit_code == 1
    assert "FAIL" in cli_fail.stdout

    # Restore raw bytes on disk
    art_path.write_bytes(original_bytes)

    # With restored file, CLI verify exits with code 0
    cli_pass = runner.invoke(cli, ["verify", run_id, "--db", db_url])
    assert cli_pass.exit_code == 0
    assert "PASS" in cli_pass.stdout

    # Test CLI JSON output
    cli_json = runner.invoke(cli, ["verify", run_id, "--db", db_url, "--json"])
    assert cli_json.exit_code == 0
    assert '"status": "pass"' in cli_json.stdout

    # Test CLI reproduce check-only
    cli_repro_check = runner.invoke(cli, ["reproduce", exp_id, "--db", db_url, "--check-only"])
    assert cli_repro_check.exit_code == 0
    assert "REPRODUCIBLE" in cli_repro_check.stdout
