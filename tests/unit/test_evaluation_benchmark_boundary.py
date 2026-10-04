"""Adversarial, Boundary, and Corrupted Input Verification Suite for ToyBenchmarkTask.

Empirical Challenger 2 test suite for Milestone 1 (REX-042 & Verifier Harmonization).
Validates:
1. Invalid sample sizes (N <= 2, N=0, N < 0) and boundary sample sizes (N=3, N=5000).
2. Zero noise (sigma=0.0), negative noise (sigma < 0), and extreme noise (sigma=10.0).
3. Negative and out-of-range random seeds.
4. Missing physical artifact files on disk (dataset.csv, curve_fit.txt, whole directory).
5. Byte-level artifact corruption and cryptographic hash tampering.
6. Result metric falsification, fabricated claim statements, broken links, cross-run links.
7. Graceful error handling across all failure modes and verifier discrepancy detection.
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rex.evaluation.benchmark import (
    BenchmarkExecutionReport,
    ToyBenchmarkTask,
)
from rex.evidence.verifier import (
    DeterministicVerifier,
    ResearchVerifier,
    VerificationStatus,
)
from rex.persistence.database import init_db
from rex.persistence.models import (
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
    ResultModel,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


# ============================================================================
# Category 1: Invalid & Boundary Sample Sizes (N <= 2, N=3, Large N)
# ============================================================================


class TestBoundarySampleSizes:
    """Tests sample size boundary conditions: N <= 2, N = 3, and extreme N."""

    @pytest.mark.unit
    @pytest.mark.parametrize("invalid_n", [2, 1, 0, -1, -5, -100])
    def test_sample_size_le_2_workspace_raises_gracefully(
        self, invalid_n: int, tmp_path: Path
    ) -> None:
        """Solving degree-2 polynomial with N <= 2 is mathematically underdetermined.

        Must raise ValueError (from linspace/data gen) or LinAlgError (singular normal equations).
        """
        task = ToyBenchmarkTask(
            task_name=f"invalid_n_{invalid_n}",
            num_samples=invalid_n,
            random_seed=42,
        )
        workspace = tmp_path / f"ws_invalid_n_{invalid_n}"
        workspace.mkdir(parents=True, exist_ok=True)

        with pytest.raises((ValueError, np.linalg.LinAlgError)):
            task.execute_in_workspace(workspace)

    @pytest.mark.unit
    @pytest.mark.parametrize("invalid_n", [2, 1, 0, -1])
    def test_sample_size_le_2_run_benchmark_raises_gracefully(
        self, invalid_n: int, db_session: Session
    ) -> None:
        """Full end-to-end benchmark execution must fail cleanly without unhandled state corruption."""
        task = ToyBenchmarkTask(
            task_name=f"invalid_run_n_{invalid_n}",
            num_samples=invalid_n,
            random_seed=42,
        )
        with pytest.raises((ValueError, np.linalg.LinAlgError)):
            task.run_benchmark(session=db_session)

    @pytest.mark.unit
    def test_sample_size_boundary_n_equals_3(self, tmp_path: Path, db_session: Session) -> None:
        """N = 3 is the exact minimum sample size to uniquely fit a degree-2 polynomial (3 equations, 3 unknowns).

        Verifies that normal equations are non-singular and the verifier passes.
        """
        task = ToyBenchmarkTask(
            task_name="boundary_n_3",
            num_samples=3,
            random_seed=42,
            noise_sigma=0.0,  # Zero noise to verify exact interpolation
        )
        report = task.run_benchmark(session=db_session)

        assert isinstance(report, BenchmarkExecutionReport)
        assert report.verification_status == VerificationStatus.PASS
        assert report.is_bounded is True
        assert report.metrics["num_samples"] == 3
        # Residuals for 3 points with degree 2 must be exactly zero
        assert report.metrics["mse"] < 1e-6
        assert abs(report.metrics["r2_score"] - 1.0) < 1e-4

    @pytest.mark.unit
    def test_sample_size_large_scale(self, db_session: Session) -> None:
        """Stress tests numerical stability with N = 5,000 points."""
        task = ToyBenchmarkTask(
            task_name="large_scale_n5000",
            num_samples=5000,
            random_seed=42,
            noise_sigma=0.05,
        )
        report = task.run_benchmark(session=db_session)

        assert report.verification_status == VerificationStatus.PASS
        assert report.is_bounded is True
        # Under law of large numbers with sigma=0.05, MSE converges to 0.05^2 = 0.0025
        assert abs(report.metrics["mse"] - 0.0025) < 0.0005
        # Recovered coefficients should be very close to ground truth [2.5, -1.8, 0.75]
        assert abs(report.metrics["w0_estimated"] - 2.5) < 0.01
        assert abs(report.metrics["w1_estimated"] - (-1.8)) < 0.01
        assert abs(report.metrics["w2_estimated"] - 0.75) < 0.01


# ============================================================================
# Category 2: Noise Sigma Variations (Zero, Negative, Extreme)
# ============================================================================


class TestNoiseSigmaVariations:
    """Tests noise parameter boundaries: zero, negative, and extreme noise."""

    @pytest.mark.unit
    def test_zero_noise_exact_reconstruction(self, tmp_path: Path, db_session: Session) -> None:
        """When sigma = 0, the synthetic generator produces perfect uncorrupted data.

        Weights must equal ground truth to machine precision, MSE must be 0, and verifier must PASS.
        """
        task = ToyBenchmarkTask(
            task_name="zero_noise_task",
            num_samples=100,
            random_seed=42,
            noise_sigma=0.0,
        )
        report = task.run_benchmark(session=db_session)

        assert report.verification_status == VerificationStatus.PASS
        assert report.is_bounded is True

        metrics = report.metrics
        assert abs(metrics["w0_estimated"] - 2.5) < 1e-4
        assert abs(metrics["w1_estimated"] - (-1.8)) < 1e-4
        assert abs(metrics["w2_estimated"] - 0.75) < 1e-4
        assert metrics["mse"] < 1e-6
        assert metrics["mae"] < 1e-4
        assert abs(metrics["r2_score"] - 1.0) < 1e-4

        # Direct verification check
        verifier = ResearchVerifier(session=db_session)
        v_report = verifier.verify_run(report.run_id)
        assert v_report.status == VerificationStatus.PASS
        assert len(v_report.errors) == 0

    @pytest.mark.unit
    @pytest.mark.parametrize("neg_sigma", [-0.0001, -0.05, -1.0, -50.0])
    def test_negative_noise_sigma_raises_value_error(
        self, neg_sigma: float, tmp_path: Path
    ) -> None:
        """Standard deviation cannot be negative. NumPy must raise ValueError."""
        task = ToyBenchmarkTask(
            task_name="negative_sigma",
            num_samples=100,
            random_seed=42,
            noise_sigma=neg_sigma,
        )
        with pytest.raises(ValueError):
            task.generate_dataset(tmp_path / "neg_dataset.csv")

    @pytest.mark.unit
    def test_extreme_noise_sigma_violates_bounds_gracefully(self, db_session: Session) -> None:
        """When sigma = 10.0, noise dominates signal.

        MSE will exceed 0.05 and R^2 will drop below 0.95.
        The report must accurately report is_bounded=False while preserving evidence integrity.
        """
        task = ToyBenchmarkTask(
            task_name="extreme_noise_task",
            num_samples=100,
            random_seed=42,
            noise_sigma=10.0,
        )
        report = task.run_benchmark(session=db_session)

        # Verification of empirical lineage still passes because claims match empirical numbers
        assert report.verification_status == VerificationStatus.PASS
        # But empirical bounds are violated
        assert report.is_bounded is False
        assert report.metrics["mse"] > 0.05
        assert report.metrics["r2_score"] < 0.95


# ============================================================================
# Category 3: Random Seed Boundaries & Corrupted Seeds
# ============================================================================


class TestRandomSeedBoundaries:
    """Tests random seed boundary conditions and error handling."""

    @pytest.mark.unit
    @pytest.mark.parametrize("invalid_seed", [-1, -42, 2**32 + 1])
    def test_invalid_random_seed_raises_value_error(
        self, invalid_seed: int, tmp_path: Path
    ) -> None:
        """NumPy RandomState requires 0 <= seed < 2**32. Invalid seeds must raise ValueError."""
        task = ToyBenchmarkTask(
            task_name="invalid_seed",
            num_samples=100,
            random_seed=invalid_seed,
        )
        with pytest.raises(ValueError):
            task.generate_dataset(tmp_path / "invalid_seed_dataset.csv")

    @pytest.mark.unit
    def test_boundary_seed_zero(self, db_session: Session) -> None:
        """Seed 0 is valid and must produce deterministic results."""
        task = ToyBenchmarkTask(
            task_name="seed_zero_task",
            num_samples=100,
            random_seed=0,
            noise_sigma=0.05,
        )
        report = task.run_benchmark(session=db_session)
        assert report.verification_status == VerificationStatus.PASS
        assert report.is_bounded is True


# ============================================================================
# Category 4: Missing Files on Disk (Artifact Deletion Stress)
# ============================================================================


class TestMissingArtifactFiles:
    """Tests verifier response when disk artifacts are deleted post-execution."""

    @pytest.mark.unit
    def test_missing_dataset_file_causes_verifier_failure(self, db_session: Session) -> None:
        """Physically deleting dataset.csv must cause verifier to fail with missing file error."""
        task = ToyBenchmarkTask(task_name="missing_dataset_task")
        report = task.run_benchmark(session=db_session)

        dataset_path = Path(report.workspace_path) / "dataset.csv"
        assert dataset_path.exists(), "dataset.csv was not created"
        dataset_path.unlink()  # Delete file on disk

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert v_report.is_passed is False
        assert any(
            "missing on disk" in err.lower() and "dataset.csv" in err for err in v_report.errors
        )

    @pytest.mark.unit
    def test_missing_curve_fit_artifact_causes_verifier_failure(self, db_session: Session) -> None:
        """Physically deleting curve_fit.txt must cause verifier to fail."""
        task = ToyBenchmarkTask(task_name="missing_curve_task")
        report = task.run_benchmark(session=db_session)

        curve_path = Path(report.workspace_path) / "curve_fit.txt"
        assert curve_path.exists()
        curve_path.unlink()

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert v_report.is_passed is False
        assert any(
            "missing on disk" in err.lower() and "curve_fit.txt" in err for err in v_report.errors
        )

    @pytest.mark.unit
    def test_missing_entire_workspace_causes_verifier_failure(self, db_session: Session) -> None:
        """Deleting the entire workspace directory causes multiple missing file errors."""
        task = ToyBenchmarkTask(task_name="missing_workspace_task")
        report = task.run_benchmark(session=db_session)

        shutil.rmtree(report.workspace_path)

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        missing_errors = [e for e in v_report.errors if "missing on disk" in e.lower()]
        assert len(missing_errors) >= 2


# ============================================================================
# Category 5: Corrupted Hashes & Tampered Artifacts
# ============================================================================


class TestArtifactByteAndHashTampering:
    """Tests verifier cryptographic byte integrity verification."""

    @pytest.mark.unit
    def test_tampered_dataset_file_bytes_causes_hash_mismatch(self, db_session: Session) -> None:
        """Modifying raw bytes in dataset.csv must trigger cryptographic hash mismatch."""
        task = ToyBenchmarkTask(task_name="tampered_dataset_task")
        report = task.run_benchmark(session=db_session)

        dataset_path = Path(report.workspace_path) / "dataset.csv"
        # Append corrupt line
        with open(dataset_path, "a", encoding="utf-8") as f:
            f.write("999.0,999.0\n")

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert v_report.is_passed is False
        assert any("cryptographic hash mismatch" in err.lower() for err in v_report.errors)

    @pytest.mark.unit
    def test_tampered_curve_fit_bytes_causes_hash_mismatch(self, db_session: Session) -> None:
        """Modifying curve_fit.txt bytes must trigger hash mismatch."""
        task = ToyBenchmarkTask(task_name="tampered_curve_task")
        report = task.run_benchmark(session=db_session)

        curve_path = Path(report.workspace_path) / "curve_fit.txt"
        curve_path.write_text("FALSIFIED_CURVE_DATA", encoding="utf-8")

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert any("cryptographic hash mismatch" in err.lower() for err in v_report.errors)

    @pytest.mark.unit
    def test_tampered_database_artifact_hash_causes_hash_mismatch(
        self, db_session: Session
    ) -> None:
        """Mutating the content_hash stored in ArtifactModel directly causes verifier failure."""
        task = ToyBenchmarkTask(task_name="tampered_db_hash_task")
        report = task.run_benchmark(session=db_session)

        art = db_session.scalars(
            select(ArtifactModel).where(ArtifactModel.research_run_id == report.run_id)
        ).first()
        assert art is not None
        art.content_hash = "deadbeef" * 8  # 64-char fake hash
        db_session.flush()

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert any("cryptographic hash mismatch" in err.lower() for err in v_report.errors)


# ============================================================================
# Category 6: Discrepancy Detection (Metric, Claim, Lineage Corruption)
# ============================================================================


class TestDiscrepancyDetection:
    """Tests verifier detection of metric falsification, unsupported claims, and cross-run links."""

    @pytest.mark.unit
    def test_falsified_result_metric_causes_claim_mismatch(self, db_session: Session) -> None:
        """Modifying recorded metric_value in ResultModel causes CLAIM_NUMBER_MISMATCH."""
        task = ToyBenchmarkTask(task_name="falsified_metric_task")
        report = task.run_benchmark(session=db_session)

        # Falsify MSE result in DB
        res_mse = db_session.scalars(
            select(ResultModel).where(
                ResultModel.metric_name == "mse",
            )
        ).first()
        assert res_mse is not None
        res_mse.metric_value = 999.999
        res_mse.result_json = {"mse": 999.999}
        db_session.flush()

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert any("claim_number_mismatch" in err.lower() for err in v_report.errors)

    @pytest.mark.unit
    def test_fabricated_claim_statement_causes_claim_mismatch(self, db_session: Session) -> None:
        """Fabricating a claim asserting an impossible MSE causes CLAIM_NUMBER_MISMATCH."""
        task = ToyBenchmarkTask(task_name="fabricated_claim_task")
        report = task.run_benchmark(session=db_session)

        claim = db_session.scalars(
            select(ClaimModel).where(ClaimModel.research_run_id == report.run_id)
        ).first()
        assert claim is not None
        claim.statement = "Quadratic polynomial regression achieved MSE of 0.0000000001."
        claim.metadata_json = {"asserted_value": 0.0000000001, "metric_name": "mse"}
        db_session.flush()

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert any("claim_number_mismatch" in err.lower() for err in v_report.errors)

    @pytest.mark.unit
    def test_broken_evidence_link_causes_unsupported_claim(self, db_session: Session) -> None:
        """Deleting the evidence link connecting Claim to Result causes UNSUPPORTED_CLAIM."""
        task = ToyBenchmarkTask(task_name="broken_link_task")
        report = task.run_benchmark(session=db_session)

        # Delete all links for the claim
        links = db_session.scalars(
            select(EvidenceLinkModel).where(
                EvidenceLinkModel.source_type == "claim",
            )
        ).all()
        for link in links:
            db_session.delete(link)
        db_session.flush()

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert any("unsupported_claim" in err.lower() for err in v_report.errors)

    @pytest.mark.unit
    def test_cross_run_evidence_link_causes_verifier_rejection(self, db_session: Session) -> None:
        """Injecting a cross-run evidence link between two distinct runs must trigger cross-run violation."""
        task1 = ToyBenchmarkTask(task_name="run_1")
        report1 = task1.run_benchmark(session=db_session)

        task2 = ToyBenchmarkTask(task_name="run_2")
        report2 = task2.run_benchmark(session=db_session)

        # Create illegal cross-run link: Run 1 claim pointing to Run 2 result
        claim1 = db_session.scalars(
            select(ClaimModel).where(ClaimModel.research_run_id == report1.run_id)
        ).first()
        res2 = db_session.scalars(
            select(ResultModel)
            .join(EvidenceLinkModel, EvidenceLinkModel.target_id == ResultModel.id, isouter=True)
            .where(EvidenceLinkModel.research_run_id == report2.run_id)
        ).first()

        assert claim1 is not None and res2 is not None

        illegal_link = EvidenceLinkModel(
            claim_id=claim1.id,
            source_type="claim",
            source_id=claim1.id,
            target_type="result",
            target_id=res2.id,
            relationship_type="supported_by",
            research_run_id=report1.run_id,
        )
        db_session.add(illegal_link)
        db_session.flush()

        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify(report1.run_id)

        assert v_report.status == VerificationStatus.FAIL
        assert len(v_report.cross_run_violations) > 0 or any(
            "cross-run" in err.lower() for err in v_report.errors
        )


# ============================================================================
# Category 7: Verifier Resilience & Error Handling
# ============================================================================


class TestVerifierResilience:
    """Tests graceful handling when nonexistent IDs or empty datasets are queried."""

    @pytest.mark.unit
    def test_verifier_nonexistent_run_id_returns_fail_gracefully(self, db_session: Session) -> None:
        """Querying a nonexistent research run ID must return FAIL report rather than crashing."""
        verifier = DeterministicVerifier(session=db_session)
        v_report = verifier.verify("non-existent-run-uuid-00000000")

        assert v_report.status == VerificationStatus.FAIL
        assert v_report.is_passed is False
        assert any("not found" in err.lower() for err in v_report.errors)
