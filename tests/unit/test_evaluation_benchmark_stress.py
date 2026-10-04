"""Empirical Stress-Test Suite for REX-042 Deterministic Toy ML Research Benchmark.

Adversarial and empirical verification challenger:
- Multi-trial Monte Carlo sweep across 50+ diverse seeds.
- Sample size grid sweep (N=3 up to N=5000) verifying asymptotic convergence.
- Strict verification that MSE <= 0.05 and R2 >= 0.95 hold across trials.
- Strict verification that ResearchVerifier reports VerificationStatus.PASS.
- Multi-run database isolation stress test (multi-tenancy in same DB session).
- Boundary conditions (N <= 2, sigma=0.0, high noise breakdown).
- Negative oracles confirming verifier sensitivity (tamper detection).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.evaluation.benchmark import (
    BenchmarkExecutionReport,
    ToyBenchmarkTask,
)
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.persistence.database import init_db
from rex.persistence.models import ResultModel


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
# 1. Multi-Seed Monte Carlo Empirical Stress Tests (50 Distinct Seeds)
# ============================================================================

SEEDS_50 = [
    0,
    1,
    2,
    3,
    5,
    7,
    11,
    13,
    17,
    19,
    23,
    29,
    31,
    37,
    41,
    42,
    43,
    47,
    53,
    59,
    61,
    67,
    71,
    73,
    79,
    83,
    89,
    97,
    100,
    123,
    256,
    314,
    404,
    500,
    777,
    999,
    1024,
    1337,
    1984,
    2026,
    4096,
    5555,
    7777,
    8888,
    9999,
    12345,
    31415,
    65535,
    99999,
    123456,
]


@pytest.mark.unit
def test_monte_carlo_multi_seed_metrics_stability(tmp_path: Path) -> None:
    """Stress-test ToyBenchmarkTask across 50 distinct random seeds.

    Verifies:
    1. MSE <= 0.05 consistently holds across all 50 seeds.
    2. R2 >= 0.95 consistently holds across all 50 seeds.
    3. MAE <= 0.15 consistently holds across all 50 seeds.
    4. Fitted coefficients are tightly clustered around [2.5, -1.8, 0.75].
    """
    mse_records: list[float] = []
    mae_records: list[float] = []
    r2_records: list[float] = []
    w0_errors: list[float] = []
    w1_errors: list[float] = []
    w2_errors: list[float] = []

    for seed in SEEDS_50:
        task = ToyBenchmarkTask(
            task_name=f"mc_seed_{seed}",
            num_samples=100,
            random_seed=seed,
            noise_sigma=0.05,
        )
        ws = tmp_path / f"ws_{seed}"
        ws.mkdir(parents=True, exist_ok=True)
        res = task.execute_in_workspace(ws)
        metrics = res["metrics"]

        mse = metrics["mse"]
        mae = metrics["mae"]
        r2 = metrics["r2_score"]
        w0 = metrics["w0_estimated"]
        w1 = metrics["w1_estimated"]
        w2 = metrics["w2_estimated"]

        # Strict empirical assertion per trial
        assert 0.0 <= mse <= 0.05, f"Seed {seed}: MSE {mse} violated upper bound 0.05"
        assert r2 >= 0.95, f"Seed {seed}: R2 {r2} violated lower bound 0.95"
        assert mae <= 0.15, f"Seed {seed}: MAE {mae} violated upper bound 0.15"

        # Parameter estimation assertions
        assert abs(w0 - 2.5) < 0.10, f"Seed {seed}: w0 {w0} deviated from 2.5 by >= 0.10"
        assert abs(w1 - (-1.8)) < 0.10, f"Seed {seed}: w1 {w1} deviated from -1.8 by >= 0.10"
        assert abs(w2 - 0.75) < 0.10, f"Seed {seed}: w2 {w2} deviated from 0.75 by >= 0.10"

        mse_records.append(mse)
        mae_records.append(mae)
        r2_records.append(r2)
        w0_errors.append(abs(w0 - 2.5))
        w1_errors.append(abs(w1 - (-1.8)))
        w2_errors.append(abs(w2 - 0.75))

    # Aggregated Monte Carlo statistics
    mean_mse = float(np.mean(mse_records))
    max_mse = float(np.max(mse_records))
    min_mse = float(np.min(mse_records))
    mean_r2 = float(np.mean(r2_records))
    min_r2 = float(np.min(r2_records))
    max_w0_err = float(np.max(w0_errors))
    max_w1_err = float(np.max(w1_errors))
    max_w2_err = float(np.max(w2_errors))

    # Empirical expectations: expected MSE is sigma^2 = 0.0025
    assert 0.0010 <= mean_mse <= 0.0040, f"Mean MSE {mean_mse} out of expected range"
    assert max_mse <= 0.05, f"Max MSE {max_mse} exceeded 0.05"
    assert min_mse >= 0.0, f"Min MSE {min_mse} negative"
    assert mean_r2 >= 0.99, f"Mean R2 {mean_r2} was below 0.99"
    assert min_r2 >= 0.95, f"Min R2 {min_r2} was below 0.95"
    assert max_w0_err < 0.05, f"Max w0 error {max_w0_err} >= 0.05"
    assert max_w1_err < 0.05, f"Max w1 error {max_w1_err} >= 0.05"
    assert max_w2_err < 0.05, f"Max w2 error {max_w2_err} >= 0.05"


@pytest.mark.unit
@pytest.mark.parametrize("seed", [0, 42, 123, 1337, 2026, 9999, 12345, 65535])
def test_multi_seed_end_to_end_research_verifier_pass(seed: int, db_session: Session) -> None:
    """Stress-test end-to-end task.run_benchmark across diverse seeds asserting ResearchVerifier PASS."""
    task = ToyBenchmarkTask(
        task_name=f"benchmark_seed_{seed}",
        num_samples=100,
        random_seed=seed,
        noise_sigma=0.05,
    )

    report = task.run_benchmark(session=db_session)

    # 1. Benchmark Execution Report checks
    assert isinstance(report, BenchmarkExecutionReport)
    assert report.is_bounded is True
    assert report.verification_status == VerificationStatus.PASS
    assert 0.0 <= report.metrics["mse"] <= 0.05
    assert report.metrics["r2_score"] >= 0.95

    # 2. Independent ResearchVerifier re-verification
    verifier = ResearchVerifier(session=db_session)
    v_report = verifier.verify_run(report.run_id)

    assert v_report.status == VerificationStatus.PASS
    assert v_report.is_passed is True
    assert len(v_report.errors) == 0, f"Errors found: {v_report.errors}"
    assert len(v_report.warnings) == 0, f"Warnings found: {v_report.warnings}"
    assert len(v_report.cross_run_violations) == 0

    # 3. Claims and Artifacts integrity
    assert len(v_report.claims_verified) >= 1
    assert all(c.is_valid and c.is_lineage_intact for c in v_report.claims_verified)
    assert len(v_report.artifacts_verified) >= 2
    assert all(a.is_valid and a.file_exists for a in v_report.artifacts_verified)


# ============================================================================
# 2. Sample Size Grid Sweep (N = 3 up to 5000) & Asymptotic Convergence
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("n_samples", [10, 25, 50, 100, 250, 500, 1000, 5000])
def test_sample_size_sweep_boundedness(n_samples: int, tmp_path: Path) -> None:
    """Verifies MSE <= 0.05 and R2 >= 0.95 hold across sample sizes from 10 to 5000."""
    task = ToyBenchmarkTask(
        task_name=f"sweep_n_{n_samples}",
        num_samples=n_samples,
        random_seed=42,
        noise_sigma=0.05,
    )
    ws = tmp_path / f"ws_n_{n_samples}"
    ws.mkdir(parents=True, exist_ok=True)
    res = task.execute_in_workspace(ws)
    metrics = res["metrics"]

    mse = metrics["mse"]
    mae = metrics["mae"]
    r2 = metrics["r2_score"]

    assert 0.0 <= mse <= 0.05, f"N={n_samples}: MSE {mse} exceeded 0.05"
    assert r2 >= 0.95, f"N={n_samples}: R2 {r2} below 0.95"
    assert mae <= 0.15, f"N={n_samples}: MAE {mae} exceeded 0.15"


@pytest.mark.unit
def test_asymptotic_convergence_of_weights(tmp_path: Path) -> None:
    """Verifies asymptotic consistency: larger sample sizes yield closer weight estimates."""
    errors_n: dict[int, float] = {}
    for n in [20, 100, 500, 2500]:
        task = ToyBenchmarkTask(
            task_name=f"asymptotics_{n}",
            num_samples=n,
            random_seed=42,
            noise_sigma=0.05,
        )
        ws = tmp_path / f"ws_asymptotics_{n}"
        ws.mkdir(parents=True, exist_ok=True)
        res = task.execute_in_workspace(ws)
        m = res["metrics"]
        err = np.linalg.norm(
            [
                m["w0_estimated"] - 2.5,
                m["w1_estimated"] - (-1.8),
                m["w2_estimated"] - 0.75,
            ]
        )
        errors_n[n] = float(err)

    # N=2500 should have significantly smaller parameter estimation error than N=20
    assert errors_n[2500] < errors_n[20], (
        f"Expected asymptotic convergence: N=2500 error ({errors_n[2500]:.5f}) "
        f"should be less than N=20 error ({errors_n[20]:.5f})"
    )
    assert errors_n[2500] < 0.01, f"Expected N=2500 error < 0.01, got {errors_n[2500]}"


@pytest.mark.unit
@pytest.mark.parametrize("n_samples", [10, 50, 200, 1000])
def test_sample_size_verifier_pass(n_samples: int, db_session: Session) -> None:
    """Tests that full benchmark run and ResearchVerifier pass for different sample sizes."""
    task = ToyBenchmarkTask(
        task_name=f"benchmark_n_{n_samples}",
        num_samples=n_samples,
        random_seed=42,
        noise_sigma=0.05,
    )
    report = task.run_benchmark(session=db_session)
    assert report.verification_status == VerificationStatus.PASS
    assert report.is_bounded is True

    verifier = ResearchVerifier(session=db_session)
    direct_report = verifier.verify_run(report.run_id)
    assert direct_report.status == VerificationStatus.PASS
    assert len(direct_report.errors) == 0


# ============================================================================
# 3. Boundary & Extreme Cases (Minimal N, Zero Noise, Noise Threshold)
# ============================================================================


@pytest.mark.unit
def test_minimal_sample_size_three(tmp_path: Path) -> None:
    """With N=3 (exact minimal points for quadratic polynomial), system fits without error."""
    task = ToyBenchmarkTask(
        task_name="minimal_n_3",
        num_samples=3,
        random_seed=42,
        noise_sigma=0.05,
    )
    ws = tmp_path / "ws_n_3"
    ws.mkdir(parents=True, exist_ok=True)
    res = task.execute_in_workspace(ws)
    metrics = res["metrics"]

    # 3 points uniquely determine a quadratic curve, so MSE on training points is ~0
    assert metrics["mse"] < 1e-4, f"N=3 MSE was {metrics['mse']}, expected near 0"
    assert metrics["r2_score"] >= 0.95


@pytest.mark.unit
@pytest.mark.parametrize("n_invalid", [2, 1, 0, -10])
def test_degenerate_sample_sizes_fail_gracefully(n_invalid: int, tmp_path: Path) -> None:
    """Degenerate sample sizes (N <= 2) must cleanly raise an exception rather than silently corrupting."""
    with pytest.raises((ValueError, np.linalg.LinAlgError, IndexError)):
        task = ToyBenchmarkTask(
            task_name=f"deg_n_{n_invalid}",
            num_samples=n_invalid,
            random_seed=42,
        )
        task.execute_in_workspace(tmp_path / f"ws_deg_{n_invalid}")


@pytest.mark.unit
def test_zero_noise_exact_weights(tmp_path: Path) -> None:
    """Zero noise setting (sigma=0.0) yields ground-truth weights to floating point precision."""
    task = ToyBenchmarkTask(
        task_name="zero_noise",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.0,
    )
    ws = tmp_path / "ws_zero_noise"
    ws.mkdir(parents=True, exist_ok=True)
    res = task.execute_in_workspace(ws)
    m = res["metrics"]

    assert abs(m["w0_estimated"] - 2.5) < 1e-4
    assert abs(m["w1_estimated"] - (-1.8)) < 1e-4
    assert abs(m["w2_estimated"] - 0.75) < 1e-4
    assert m["mse"] < 1e-6
    assert abs(m["r2_score"] - 1.0) < 1e-4


@pytest.mark.unit
def test_noise_threshold_breakdown_verification(tmp_path: Path) -> None:
    """Verifies that the MSE <= 0.05 bound is non-vacuous: when sigma exceeds threshold (~0.23), MSE > 0.05."""
    task_high_noise = ToyBenchmarkTask(
        task_name="high_noise",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.30,  # Expected MSE is sigma^2 = 0.09 > 0.05
    )
    ws = tmp_path / "ws_high_noise"
    ws.mkdir(parents=True, exist_ok=True)
    res = task_high_noise.execute_in_workspace(ws)
    m = res["metrics"]

    # At sigma=0.30, MSE must exceed 0.05
    assert m["mse"] > 0.05, f"High noise sigma=0.30 should exceed 0.05, got {m['mse']}"


# ============================================================================
# 4. Multi-Run Database Isolation Stress Test (Multi-Tenancy)
# ============================================================================


@pytest.mark.unit
def test_multi_run_isolation_in_same_db(db_session: Session) -> None:
    """Executes 5 sequential benchmark runs within the SAME database session.

    Asserts:
    1. Each run receives distinct run_id, experiment_id, execution_id.
    2. Evidence links do not leak across runs.
    3. ResearchVerifier on each individual run reports PASS with 0 cross-run violations.
    """
    reports: list[BenchmarkExecutionReport] = []
    run_ids: set[str] = set()

    for idx, seed in enumerate([10, 20, 30, 40, 50]):
        task = ToyBenchmarkTask(
            task_name=f"multi_tenant_task_{idx}",
            num_samples=100,
            random_seed=seed,
            noise_sigma=0.05,
        )
        report = task.run_benchmark(session=db_session)
        reports.append(report)
        assert report.run_id not in run_ids, f"Duplicate run_id {report.run_id}"
        run_ids.add(report.run_id)

    # Now independently verify each run in the shared session
    verifier = ResearchVerifier(session=db_session)
    for report in reports:
        v_rep = verifier.verify_run(report.run_id)
        assert v_rep.status == VerificationStatus.PASS, (
            f"Run {report.run_id} failed verification: {v_rep.errors}"
        )
        assert len(v_rep.cross_run_violations) == 0, (
            f"Run {report.run_id} had cross-run violations: {v_rep.cross_run_violations}"
        )
        assert len(v_rep.errors) == 0


# ============================================================================
# 5. Negative Oracles (Verifier Sensitivity and Defense Check)
# ============================================================================


@pytest.mark.unit
def test_verifier_detects_metric_falsification(db_session: Session) -> None:
    """Verifies that if recorded MSE is tampered in DB, ResearchVerifier detects inconsistency."""
    task = ToyBenchmarkTask(
        task_name="tamper_test_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    report = task.run_benchmark(session=db_session)
    assert report.verification_status == VerificationStatus.PASS

    # Deliberately falsify the MSE Result record in the database
    res = (
        db_session.query(ResultModel)
        .filter_by(execution_id=report.execution_id, metric_name="mse")
        .first()
    )
    assert res is not None
    res.metric_value = 999.999
    res.result_json = {"mse": 999.999}
    db_session.flush()

    # Re-verify: ResearchVerifier MUST detect numerical inconsistency with the claim
    verifier = ResearchVerifier(session=db_session)
    tampered_report = verifier.verify_run(report.run_id)

    assert tampered_report.status == VerificationStatus.FAIL
    assert len(tampered_report.errors) > 0
    assert any(
        "CLAIM_NUMBER_MISMATCH" in err or "mismatch" in err.lower()
        for err in tampered_report.errors
    )


@pytest.mark.unit
def test_verifier_detects_artifact_byte_tampering(db_session: Session) -> None:
    """Verifies that if an artifact file on disk is modified, ResearchVerifier detects SHA-256 mismatch."""
    task = ToyBenchmarkTask(
        task_name="artifact_tamper_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    report = task.run_benchmark(session=db_session)
    assert report.verification_status == VerificationStatus.PASS

    # Tamper with the artifact file bytes on disk
    artifact_path = Path(report.artifacts_created[0])
    assert artifact_path.exists()
    with open(artifact_path, "a", encoding="utf-8") as f:
        f.write("\nMALICIOUS_TAMPERED_CONTENT\n")

    # Re-verify: ResearchVerifier MUST fail with cryptographic hash mismatch
    verifier = ResearchVerifier(session=db_session)
    tampered_report = verifier.verify_run(report.run_id)

    assert tampered_report.status == VerificationStatus.FAIL
    assert len(tampered_report.errors) > 0
    assert any("Cryptographic hash mismatch" in err for err in tampered_report.errors)
