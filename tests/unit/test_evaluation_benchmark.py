"""Unit test suite for REX-042 Deterministic Toy Research Benchmark.

Validates synthetic dataset generation, closed-form polynomial model fitting,
bounded empirical metric verification (MSE <= 0.05, R2 >= 0.95), full execution,
deterministic verification pass, corner/boundary cases, database persistence,
and evidence graph lineage traceability.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rex.evaluation.benchmark import (
    BenchmarkExecutionReport,
    ToyBenchmarkTask,
)
from rex.evidence.graph import EvidenceGraphService
from rex.evidence.hashing import compute_file_hash
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.persistence.database import init_db
from rex.persistence.models import (
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
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


def _execute_task_benchmark(
    task: ToyBenchmarkTask,
    session: Session,
    base_dir: Path | None = None,
) -> BenchmarkExecutionReport:
    """Helper to execute benchmark using execute_benchmark or run_benchmark."""
    import inspect

    exec_fn = getattr(task, "execute_benchmark", getattr(task, "run_benchmark", None))
    assert exec_fn is not None, "ToyBenchmarkTask must implement execute_benchmark or run_benchmark"
    sig = inspect.signature(exec_fn)
    params = sig.parameters

    kwargs: dict[str, Any] = {}
    if "session" in params:
        kwargs["session"] = session
    if "base_dir" in params and base_dir is not None:
        kwargs["base_dir"] = base_dir

    if not kwargs and len(params) > 0:
        return exec_fn(session)
    return exec_fn(**kwargs)


# ============================================================================
# Tier 1: Feature Coverage Tests
# ============================================================================


@pytest.mark.unit
def test_toy_benchmark_synthetic_data_generation(tmp_path: Path) -> None:
    """Verifies N=100, reproducible generation with seed=42, noise variance, ground-truth polynomial values."""
    task = ToyBenchmarkTask(
        task_name="toy_polynomial_regression",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    dataset_file = tmp_path / "dataset.csv"
    summary = task.generate_dataset(dataset_file)

    # File and summary schema checks
    assert dataset_file.exists(), "dataset.csv was not created on disk"
    assert summary["num_samples"] == 100
    assert abs(summary["x_min"] - (-2.0)) < 1e-6
    assert abs(summary["x_max"] - 2.0) < 1e-6

    # Verify CSV file contents
    rows: list[tuple[float, float]] = []
    with open(dataset_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["x", "y"], f"Expected header ['x', 'y'], got {header}"
        for row in reader:
            rows.append((float(row[0]), float(row[1])))

    assert len(rows) == 100, f"Expected 100 data rows, found {len(rows)}"

    xs = np.array([r[0] for r in rows])
    ys = np.array([r[1] for r in rows])

    # Assert uniform domain linspace [-2.0, 2.0]
    expected_x = np.linspace(-2.0, 2.0, 100)
    np.testing.assert_allclose(xs, expected_x, atol=1e-6)

    # Ground-truth polynomial: y* = 2.5 - 1.8*x + 0.75*x^2
    y_ground_truth = 2.5 - 1.8 * xs + 0.75 * (xs**2)
    noise = ys - y_ground_truth

    # Noise statistics checks: mean approx 0, std approx sigma=0.05
    noise_mean = float(np.mean(noise))
    noise_std = float(np.std(noise))
    assert abs(noise_mean) < 0.02, f"Expected noise mean close to 0, got {noise_mean}"
    assert 0.035 <= noise_std <= 0.065, (
        f"Expected noise standard deviation around 0.05, got {noise_std}"
    )

    # Verify deterministic reproducibility with identical seed
    dataset_file_2 = tmp_path / "dataset_reproduced.csv"
    task.generate_dataset(dataset_file_2)
    assert compute_file_hash(dataset_file) == compute_file_hash(dataset_file_2), (
        "Identical seed did not produce identical dataset hash"
    )


@pytest.mark.unit
def test_toy_benchmark_model_fitting(tmp_path: Path) -> None:
    """Verifies closed-form regression yields weights close to [2.5, -1.8, 0.75]."""
    task = ToyBenchmarkTask(
        task_name="model_fitting_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    execution_result = task.execute_in_workspace(workspace)
    metrics = execution_result["metrics"]

    # Verify estimated coefficients are close to ground truth [2.5, -1.8, 0.75]
    w0_est = metrics["w0_estimated"]
    w1_est = metrics["w1_estimated"]
    w2_est = metrics["w2_estimated"]

    assert abs(w0_est - 2.5) < 0.05, f"Intercept w0 {w0_est} deviated by more than 0.05 from 2.5"
    assert abs(w1_est - (-1.8)) < 0.05, (
        f"Linear weight w1 {w1_est} deviated by more than 0.05 from -1.8"
    )
    assert abs(w2_est - 0.75) < 0.05, (
        f"Quadratic weight w2 {w2_est} deviated by more than 0.05 from 0.75"
    )

    # Verify against direct independent normal equations calculation
    xs, ys = [], []
    with open(execution_result["dataset_file"], "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            xs.append(float(row[0]))
            ys.append(float(row[1]))

    x_arr = np.array(xs)
    y_arr = np.array(ys)
    design_matrix = np.column_stack([np.ones_like(x_arr), x_arr, x_arr**2])
    weights_independent = np.linalg.inv(design_matrix.T @ design_matrix) @ (design_matrix.T @ y_arr)

    assert abs(round(weights_independent[0], 4) - w0_est) < 1e-4
    assert abs(round(weights_independent[1], 4) - w1_est) < 1e-4
    assert abs(round(weights_independent[2], 4) - w2_est) < 1e-4


@pytest.mark.unit
def test_toy_benchmark_bounded_metrics(tmp_path: Path) -> None:
    """Asserts MSE <= 0.05 and R^2 >= 0.95."""
    task = ToyBenchmarkTask(
        task_name="bounded_metrics_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    workspace = tmp_path / "workspace_bounded"
    workspace.mkdir(parents=True, exist_ok=True)
    execution_result = task.execute_in_workspace(workspace)
    metrics = execution_result["metrics"]

    mse = metrics["mse"]
    mae = metrics["mae"]
    r2 = metrics["r2_score"]

    # Exact bound specifications
    assert 0.0 <= mse <= 0.05, f"MSE {mse} violated required upper bound of 0.05"
    assert r2 >= 0.95, f"R^2 {r2} violated required lower bound of 0.95"
    assert mae <= 0.15, f"MAE {mae} violated required upper bound of 0.15"

    # Compare with known theoretical baselines
    assert mse < task.known_baselines["linear_baseline_mse"], (
        f"Quadratic MSE {mse} failed to outperform linear baseline {task.known_baselines['linear_baseline_mse']}"
    )
    assert mse < task.known_baselines["mean_baseline_mse"], (
        f"Quadratic MSE {mse} failed to outperform mean baseline {task.known_baselines['mean_baseline_mse']}"
    )
    assert r2 > 0.99, f"Expected R^2 > 0.99 for quadratic model with sigma=0.05, got {r2}"


@pytest.mark.unit
def test_toy_benchmark_full_execution(db_session: Session) -> None:
    """Tests task.execute_benchmark() and artifact generation."""
    task = ToyBenchmarkTask(
        task_name="full_execution_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )

    report = _execute_task_benchmark(task, db_session)

    assert isinstance(report, BenchmarkExecutionReport), (
        f"Expected BenchmarkExecutionReport, got {type(report)}"
    )
    assert report.task_name == "full_execution_task"
    assert report.is_bounded is True, "Expected report.is_bounded to be True"
    assert report.run_id is not None and len(report.run_id) > 0
    assert report.experiment_id is not None and len(report.experiment_id) > 0
    assert report.execution_id is not None and len(report.execution_id) > 0

    # Verify generated artifacts
    assert len(report.artifacts_created) >= 2, (
        f"Expected at least 2 artifacts, found {len(report.artifacts_created)}"
    )
    for artifact_path_str in report.artifacts_created:
        artifact_path = Path(artifact_path_str)
        assert artifact_path.exists(), f"Artifact file does not exist: {artifact_path}"
        assert artifact_path.stat().st_size > 0, f"Artifact file is empty: {artifact_path}"

    # Verify workspace directory contains metrics and dataset
    workspace_dir = Path(report.workspace_path)
    assert workspace_dir.exists(), f"Workspace directory {workspace_dir} missing"
    assert (workspace_dir / "dataset.csv").exists(), "dataset.csv missing from workspace"
    assert (workspace_dir / "metrics.json").exists(), "metrics.json missing from workspace"


@pytest.mark.unit
def test_toy_benchmark_verifier_pass(db_session: Session) -> None:
    """Tests end-to-end execution and asserts ResearchVerifier reports VerificationStatus.PASS."""
    task = ToyBenchmarkTask(
        task_name="verifier_pass_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )

    report = _execute_task_benchmark(task, db_session)

    # 1. Assert benchmark report status
    assert report.verification_status == VerificationStatus.PASS, (
        f"Expected PASS status, got {report.verification_status}"
    )

    # 2. Assert direct independent verification via ResearchVerifier
    verifier = ResearchVerifier(session=db_session)
    direct_report = verifier.verify_run(report.run_id)

    assert direct_report.status == VerificationStatus.PASS
    assert direct_report.is_passed is True
    assert len(direct_report.errors) == 0, (
        f"Verifier reported unexpected errors: {direct_report.errors}"
    )
    assert len(direct_report.claims_verified) > 0, "Expected at least 1 verified claim"
    assert all(c.is_valid for c in direct_report.claims_verified), "Not all claims were valid"
    assert len(direct_report.artifacts_verified) > 0, "Expected at least 1 verified artifact"
    assert all(a.is_valid for a in direct_report.artifacts_verified), "Not all artifacts were valid"


# ============================================================================
# Tier 2: Boundary & Corner Case Tests
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("invalid_n", [2, 1, 0, -5])
def test_toy_benchmark_invalid_sample_size(invalid_n: int, tmp_path: Path) -> None:
    """Asserts ValueError or rejection when N <= 2."""
    # Quadratic polynomial fitting requires at least 3 distinct points to solve the 3 normal equations
    with pytest.raises((ValueError, np.linalg.LinAlgError)):
        task = ToyBenchmarkTask(
            task_name="invalid_n_task",
            num_samples=invalid_n,
            random_seed=42,
        )
        # If __init__ didn't raise, execution in workspace must raise
        task.execute_in_workspace(tmp_path / f"invalid_n_{invalid_n}")


@pytest.mark.unit
def test_toy_benchmark_zero_noise(tmp_path: Path) -> None:
    """Deterministic exact fit when sigma = 0."""
    task = ToyBenchmarkTask(
        task_name="zero_noise_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.0,
    )
    workspace = tmp_path / "zero_noise_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    execution_result = task.execute_in_workspace(workspace)
    metrics = execution_result["metrics"]

    # In zero-noise setting, weights must match ground-truth within machine precision
    assert abs(metrics["w0_estimated"] - 2.5) < 1e-4, (
        f"Expected w0 == 2.5, got {metrics['w0_estimated']}"
    )
    assert abs(metrics["w1_estimated"] - (-1.8)) < 1e-4, (
        f"Expected w1 == -1.8, got {metrics['w1_estimated']}"
    )
    assert abs(metrics["w2_estimated"] - 0.75) < 1e-4, (
        f"Expected w2 == 0.75, got {metrics['w2_estimated']}"
    )

    # Residuals must be effectively 0
    assert metrics["mse"] < 1e-6, f"Expected MSE near 0 with zero noise, got {metrics['mse']}"
    assert metrics["mae"] < 1e-4, f"Expected MAE near 0 with zero noise, got {metrics['mae']}"
    assert abs(metrics["r2_score"] - 1.0) < 1e-4, (
        f"Expected R^2 == 1.0 with zero noise, got {metrics['r2_score']}"
    )


@pytest.mark.unit
def test_toy_benchmark_deterministic_reproducibility(tmp_path: Path) -> None:
    """Identical results across repeated calls with identical seed."""
    task_a = ToyBenchmarkTask(
        task_name="repro_a", num_samples=100, random_seed=42, noise_sigma=0.05
    )
    task_b = ToyBenchmarkTask(
        task_name="repro_b", num_samples=100, random_seed=42, noise_sigma=0.05
    )

    out_a = task_a.execute_in_workspace(tmp_path / "ws_a")
    out_b = task_b.execute_in_workspace(tmp_path / "ws_b")

    # Identical dataset cryptographic hashes
    hash_a = compute_file_hash(out_a["dataset_file"])
    hash_b = compute_file_hash(out_b["dataset_file"])
    assert hash_a == hash_b, "Datasets with identical seeds had differing hashes"

    # Identical empirical metrics
    assert out_a["metrics"] == out_b["metrics"], "Metrics with identical seeds differed"

    # Distinct seed produces differing dataset hash
    task_c = ToyBenchmarkTask(
        task_name="repro_c", num_samples=100, random_seed=999, noise_sigma=0.05
    )
    out_c = task_c.execute_in_workspace(tmp_path / "ws_c")
    hash_c = compute_file_hash(out_c["dataset_file"])
    assert hash_a != hash_c, "Datasets with differing seeds unexpectedly had identical hashes"


@pytest.mark.unit
def test_toy_benchmark_db_persistence(db_session: Session) -> None:
    """Checks all DB records (ResearchRun, Experiment, Execution, Result, Artifact, Claim, EvidenceLink) are correctly populated and queryable."""
    task = ToyBenchmarkTask(
        task_name="db_persistence_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )

    report = _execute_task_benchmark(task, db_session)

    # 1. ResearchRunModel
    run = db_session.get(ResearchRunModel, report.run_id)
    assert run is not None, f"ResearchRun '{report.run_id}' not found in DB"
    assert run.status == "COMPLETED"
    assert "db_persistence_task" in run.title or "Benchmark" in run.title, (
        f"Unexpected run title: {run.title}"
    )

    # 2. HypothesisModel
    hypotheses = db_session.scalars(
        select(HypothesisModel).where(HypothesisModel.research_run_id == report.run_id)
    ).all()
    assert len(hypotheses) >= 1, f"Expected at least 1 hypothesis, found {len(hypotheses)}"
    assert "0.05" in hypotheses[0].statement or "0.05" in hypotheses[0].falsification_condition

    # 3. ExperimentModel
    experiment = db_session.get(ExperimentModel, report.experiment_id)
    assert experiment is not None, f"Experiment '{report.experiment_id}' not found in DB"
    assert experiment.research_run_id == report.run_id
    params = experiment.specification_json or experiment.parameters_json
    assert params.get("num_samples") == 100
    assert params.get("seed") == 42

    # 4. ExecutionModel
    execution = db_session.get(ExecutionModel, report.execution_id)
    assert execution is not None, f"Execution '{report.execution_id}' not found in DB"
    assert execution.experiment_id == experiment.id
    assert execution.exit_code == 0
    assert execution.seed == 42
    assert execution.code_hash is not None and len(execution.code_hash) > 0

    # 5. ResultModel
    results = db_session.scalars(
        select(ResultModel).where(ResultModel.execution_id == execution.id)
    ).all()
    assert len(results) >= 1, f"Expected at least 1 result, found {len(results)}"
    metric_names = {r.metric_name for r in results}
    has_mse = "mse" in metric_names or any("mse" in (r.result_json or {}) for r in results)
    assert has_mse, f"Results did not include MSE metric: {metric_names}"

    # 6. ArtifactModel
    artifacts = db_session.scalars(
        select(ArtifactModel).where(ArtifactModel.research_run_id == report.run_id)
    ).all()
    assert len(artifacts) >= 2, f"Expected at least 2 artifacts, found {len(artifacts)}"
    for artifact in artifacts:
        assert Path(artifact.path).exists(), f"Artifact path {artifact.path} does not exist"
        assert compute_file_hash(artifact.path) == artifact.content_hash, (
            f"Artifact content hash mismatch on {artifact.path}"
        )

    # 7. ClaimModel
    claims = db_session.scalars(
        select(ClaimModel).where(ClaimModel.research_run_id == report.run_id)
    ).all()
    assert len(claims) >= 1, f"Expected at least 1 claim, found {len(claims)}"
    assert claims[0].research_run_id == report.run_id

    # 8. EvidenceLinkModel
    links = db_session.scalars(
        select(EvidenceLinkModel).where(EvidenceLinkModel.research_run_id == report.run_id)
    ).all()
    assert len(links) >= 3, f"Expected at least 3 evidence links, found {len(links)}"
    for link in links:
        assert link.research_run_id == report.run_id
        assert link.relationship_type is not None


@pytest.mark.unit
def test_toy_benchmark_lineage_traceability(db_session: Session) -> None:
    """Verifies evidence graph links allow complete lineage trace from claim to artifacts."""
    task = ToyBenchmarkTask(
        task_name="lineage_trace_task",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )

    report = _execute_task_benchmark(task, db_session)

    # 1. Fetch claim
    claim = db_session.scalars(
        select(ClaimModel).where(ClaimModel.research_run_id == report.run_id)
    ).first()
    assert claim is not None, "No claim found for research run"

    # 2. Check links via EvidenceGraphService
    graph = EvidenceGraphService(session=db_session)
    run_links = graph.get_links_for_run(report.run_id)
    assert len(run_links) >= 3, f"Expected >= 3 links, found {len(run_links)}"

    # Scoping check: all links in this run must have run_id matching report.run_id
    for link in run_links:
        assert link.research_run_id == report.run_id

    # 3. Trace claim lineage
    lineage = graph.trace_claim_lineage(claim.id)
    assert lineage is not None
    assert lineage.claim.id == claim.id

    # Lineage must traverse down to empirical foundation
    has_foundation = (
        len(lineage.results) > 0 or len(lineage.analyses) > 0 or len(lineage.executions) > 0
    )
    assert has_foundation, "Lineage traversal failed to discover results, analyses, or executions"
    assert lineage.is_complete is True or len(lineage.gaps) == 0, (
        f"Lineage has unexpected gaps: {lineage.gaps}"
    )


# ============================================================================
# Supplementary Validation Tests
# ============================================================================


@pytest.mark.unit
def test_toy_benchmark_quadratic_superiority_over_linear(
    tmp_path: Path,
) -> None:
    """Verifies that the quadratic polynomial fit dramatically outperforms a linear degree-1 baseline."""
    task = ToyBenchmarkTask(
        task_name="superiority_test",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    out = task.execute_in_workspace(tmp_path / "superiority_ws")
    metrics = out["metrics"]

    # Read generated dataset
    xs, ys = [], []
    with open(out["dataset_file"], "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            xs.append(float(row[0]))
            ys.append(float(row[1]))

    x_arr = np.array(xs)
    y_arr = np.array(ys)

    # Degree-1 linear fit: y = c0 + c1*x
    linear_features = np.column_stack([np.ones_like(x_arr), x_arr])
    linear_weights = np.linalg.inv(linear_features.T @ linear_features) @ (
        linear_features.T @ y_arr
    )
    linear_predictions = linear_features @ linear_weights
    linear_mse = float(np.mean((y_arr - linear_predictions) ** 2))

    # Quadratic MSE should be at least 10x smaller than linear MSE
    quadratic_mse = metrics["mse"]
    assert linear_mse > 0.20, f"Expected linear MSE > 0.20, found {linear_mse:.4f}"
    assert quadratic_mse < 0.05, f"Expected quadratic MSE < 0.05, found {quadratic_mse:.4f}"
    assert linear_mse / quadratic_mse > 10.0, (
        f"Quadratic model was not sufficiently superior to linear model: "
        f"linear MSE={linear_mse:.4f}, quadratic MSE={quadratic_mse:.4f}"
    )


@pytest.mark.unit
def test_toy_benchmark_workspace_artifacts_content(tmp_path: Path) -> None:
    """Verifies workspace artifact file formats and parses their serialized content."""
    task = ToyBenchmarkTask(
        task_name="artifacts_content_test",
        num_samples=100,
        random_seed=42,
        noise_sigma=0.05,
    )
    ws = tmp_path / "artifacts_ws"
    ws.mkdir(parents=True, exist_ok=True)
    out = task.execute_in_workspace(ws)

    # 1. metrics.json
    metrics_file = out["metrics_file"]
    assert metrics_file.exists()
    with open(metrics_file, "r", encoding="utf-8") as f:
        parsed_metrics = json.load(f)
    assert parsed_metrics["status"] == "COMPLETED"
    assert "mse" in parsed_metrics
    assert "r2_score" in parsed_metrics
    assert "w0_estimated" in parsed_metrics
    assert parsed_metrics["seed"] == 42
    assert parsed_metrics["num_samples"] == 100

    # 2. curve_fit.txt
    artifact_file = out["artifact_file"]
    assert artifact_file.exists()
    content = artifact_file.read_text(encoding="utf-8")
    assert "Estimated Weights" in content
    assert "True Weights" in content
    assert "MSE" in content and "R2" in content
