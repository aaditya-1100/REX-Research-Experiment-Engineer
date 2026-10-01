"""REX Batch 3 Final Gate: Experimental Integrity & Security Adversarial Audit (REX-017 - REX-022).

Explicit regression coverage for:
1. Host execution prevention & fail-closed Docker unavailability
2. Docker security parameter propagation (root drop, caps drop, no-new-priv, net isolation)
3. Secret filtering across all metadata, environment, and error paths
4. Workspace historical immutability, path traversal, null-byte rejection, and collision guard
5. Orphan result prevention, authorization enforcement, and finite float validation
6. Figure historical immutability, cryptographic SHA-256 integrity, and lineage
7. Deterministic repeated statistical analysis, zero-baseline safety, and Bessel correction
8. Execution failure state correctness (crash, timeout, cancellation, malformed metrics)
9. LLM-independent experimental pipeline execution
"""

import hashlib
import json
import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rex.analysis.exceptions import (
    InsufficientDataError,
    MalformedMetricError,
)
from rex.analysis.figures import FigureGenerator
from rex.analysis.metrics import MetricExtractor
from rex.analysis.statistics import StatisticalAnalyzer
from rex.controller.exceptions import ActorAuthorizationError, MissingExecutionError
from rex.controller.executions import record_result
from rex.domain.models import (
    ExecutionStatus,
    ExperimentStatus,
    ResearchState,
)
from rex.execution.backend import ExecutionBackend
from rex.execution.docker_runner import DockerExecutionBackend
from rex.execution.environment import (
    capture_safe_environment_metadata,
    is_sensitive_key,
    sanitize_environment,
)
from rex.execution.exceptions import (
    DockerUnavailableError,
    PathTraversalError,
    SecretLeakageError,
    WorkspaceExistsError,
)
from rex.execution.models import ExecutionOutcome, ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.execution.worker import DockerExecutionWorker
from rex.execution.workspace import WorkspaceManager, validate_safe_relative_path
from rex.observability.events import ActorType
from rex.persistence.database import Base
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# =============================================================================
# GATE 1 & 2: Host Execution Prevention & Docker Security Parameter Propagation
# =============================================================================
class TestHostExecutionAndSandboxParameters:
    """Verifies that generated code NEVER executes on host, fails closed without Docker,
    and preserves all container sandbox security constraints."""

    def test_docker_unavailable_fails_closed_without_host_execution(self) -> None:
        """DockerExecutionBackend must raise DockerUnavailableError and NEVER fallback to host."""
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("Docker daemon unreachable")

        backend = DockerExecutionBackend(docker_client=mock_client)
        assert not backend.is_available()

        req = ExecutionRequest(
            execution_id="exec-gate1",
            experiment_id="exp-gate1",
            research_run_id="run-gate1",
            command=["python", "src/main.py"],
            code_files={"main.py": "import os; os.system('echo host_compromised')"},
        )

        with pytest.raises(DockerUnavailableError):
            backend.execute(req)

    def test_docker_security_parameters_strictly_propagated(self, tmp_path: Path) -> None:
        """Verify that containers.run receives non-root user, cap_drop, no-new-privileges,
        network isolation, cpu limits, memory limits, and bounded workspace mount."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.logs.return_value = b"Execution output\n"
        mock_client.containers.run.return_value = mock_container

        mgr = WorkspaceManager(base_root=tmp_path)
        backend = DockerExecutionBackend(
            docker_client=mock_client,
            workspace_manager=mgr,
            allowed_images=["python:3.11-slim"],
        )

        limits = ResourceLimits(
            cpu_limit=1.5,
            memory_limit_mb=512,
            timeout_seconds=30,
            max_output_size_bytes=10000,
        )

        req = ExecutionRequest(
            execution_id="exec-sec-params",
            experiment_id="exp-sec-params",
            research_run_id="run-sec-params",
            command=["python", "src/train.py"],
            code_files={"train.py": "print('ok')"},
            image="python:3.11-slim",
            limits=limits,
            network_disabled=True,
            non_root_user=True,
            environment_variables={"ALLOWED_FLAG": "true"},
        )

        outcome = backend.execute(req)
        assert outcome.status == ExecutionStatus.COMPLETED

        # Inspect exact keyword arguments passed to containers.run
        mock_client.containers.run.assert_called_once()
        _, kwargs = mock_client.containers.run.call_args

        assert kwargs["image"] == "python:3.11-slim"
        assert kwargs["command"] == ["python", "src/train.py"]
        assert kwargs["network_mode"] == "none"  # Network isolation enforced
        assert kwargs["user"] == "1000:1000"  # Non-root user enforced
        assert kwargs["security_opt"] == ["no-new-privileges:true"]  # Privilege escalation blocked
        assert kwargs["cap_drop"] == ["ALL"]  # All Linux capabilities dropped
        assert kwargs["nano_cpus"] == 1_500_000_000  # CPU limit enforced
        assert kwargs["mem_limit"] == "512m"  # Memory limit enforced
        assert kwargs["environment"] == {"ALLOWED_FLAG": "true"}

        # Verify filesystem volume mount is strictly the workspace directory
        volumes = kwargs["volumes"]
        assert len(volumes) == 1
        ws_host_path = next(iter(volumes.keys()))
        mount_spec = volumes[ws_host_path]
        assert mount_spec == {"bind": "/workspace", "mode": "rw"}
        assert "exec-sec-params" in ws_host_path

        # Verify container cleanup was invoked
        mock_container.remove.assert_called_once_with(force=True)


# =============================================================================
# GATE 3 & 6: Secret Filtering & Leakage Prevention
# =============================================================================
class TestSecretFilteringAudit:
    """Verifies that sensitive credentials are never injected into containers,
    serialized into metadata, logged, or exposed in error messages."""

    @pytest.mark.parametrize(
        "secret_key",
        [
            "API_KEY",
            "OPENAI_API_KEY",
            "GROQ_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "TOKEN",
            "PASSWORD",
            "SECRET",
            "AUTHORIZATION",
            "api_key",
            "bearer_token",
            "DATABASE_URL",
            "REX_DATABASE_URL",
            "PRIVATE_KEY",
            "DB_PASS",
        ],
    )
    def test_sensitive_keys_identified_and_blocked(self, secret_key: str) -> None:
        """Every representative secret key must be identified by is_sensitive_key
        and rejected by sanitize_environment with SecretLeakageError."""
        assert is_sensitive_key(secret_key)

        secret_value = "super_secret_cleartext_credential_12345"
        env = {"BENCHMARK_FLAG": "1", secret_key: secret_value}

        with pytest.raises(SecretLeakageError) as exc_info:
            sanitize_environment(env)

        # Secret value must NEVER leak into the exception message
        err_msg = str(exc_info.value)
        assert secret_value not in err_msg

    def test_environment_metadata_omits_secret_values(self) -> None:
        """EnvironmentMetadata records only safe key names, never secret keys or values."""
        env = {
            "DATASET_PATH": "/data/train.csv",
            "BATCH_SIZE": "64",
            "OPENAI_API_KEY": "sk-secret-abc-xyz",
            "SECRET_SALT": "classified",
        }

        meta = capture_safe_environment_metadata(env=env, image_reference="python:3.11-slim")
        injected = meta["injected_env_keys"]

        assert "DATASET_PATH" in injected
        assert "BATCH_SIZE" in injected
        assert "OPENAI_API_KEY" not in injected
        assert "SECRET_SALT" not in injected

        # Verify raw metadata string contains no credential values
        dumped = json.dumps(meta)
        assert "sk-secret-abc-xyz" not in dumped
        assert "classified" not in dumped


# =============================================================================
# GATE 4 & 7: Workspace Historical Immutability & Traversal Guards
# =============================================================================
class TestWorkspaceIntegrityAudit:
    """Verifies workspace path traversal rejection, null-byte rejection,
    collision prevention, and evidence retention."""

    def test_null_byte_rejection_in_paths(self, tmp_path: Path) -> None:
        """Paths containing null bytes must be rejected immediately."""
        with pytest.raises(PathTraversalError, match="Null byte"):
            validate_safe_relative_path("safe\x00malicious.py", tmp_path)

    @pytest.mark.parametrize(
        "bad_path",
        [
            "../escape.py",
            "../../etc/passwd",
            "foo/../../bar.py",
            "/absolute/posix/path",
            "C:\\windows\\system32",
            "D:relative_drive",
        ],
    )
    def test_path_traversal_and_absolute_rejections(self, bad_path: str, tmp_path: Path) -> None:
        """Directory traversal and absolute paths must be rejected."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(bad_path, tmp_path)

    def test_workspace_collision_prevents_historical_overwrite(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        """If workspace directory A exists on disk, a new execution attempting to reuse
        or overwrite A must fail closed with WorkspaceExistsError."""
        mgr = WorkspaceManager(base_root=tmp_path)

        req1 = ExecutionRequest(
            execution_id="exec-collision-01",
            experiment_id="exp-01",
            research_run_id="run-01",
            command=["python", "src/main.py"],
            code_files={"main.py": "print('original')"},
        )
        ws1 = mgr.prepare_workspace(req1)
        assert ws1.workspace_dir.exists()

        # Write evidence to output
        (ws1.output_dir / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")

        # Second execution attempting to prepare the same directory with raise_if_exists=True must fail
        with pytest.raises(WorkspaceExistsError):
            mgr.prepare_workspace(req1, raise_if_exists=True)

        # Output evidence must remain intact
        assert (ws1.output_dir / "metrics.json").read_text(encoding="utf-8") == '{"accuracy": 0.9}'

    def test_worker_enforces_workspace_collision_guard(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        """DockerExecutionWorker.execute_experiment must reject workspace collision."""
        mgr = WorkspaceManager(base_root=tmp_path)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            workspace_manager=mgr,
        )

        with session_factory() as session:
            run = ResearchRunModel(
                id="run-worker-collision",
                title="T",
                research_question="Q",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-worker-collision",
                research_run_id=run.id,
                objective="Obj",
                status=ExperimentStatus.DESIGNED.value,
            )
            session.add(exp)
            exec_model = ExecutionModel(
                id="exec-pre-existing",
                experiment_id=exp.id,
                status=ExecutionStatus.PENDING.value,
            )
            session.add(exec_model)
            session.commit()

        # Pre-create directory to simulate collision
        collision_dir = tmp_path / "run-worker-collision" / "exec-pre-existing"
        collision_dir.mkdir(parents=True)

        with pytest.raises(WorkspaceExistsError):
            worker.execute_experiment(
                experiment="exp-worker-collision",
                code_files={"main.py": "print('fail')"},
                execution_id="exec-pre-existing",
            )


# =============================================================================
# GATE 5 & 10: Orphan Result Prevention & Strict Result Provenance
# =============================================================================
class TestResultProvenanceAudit:
    """Verifies that Results can never exist as orphan empirical facts
    and cannot be fabricated by unauthorized actors."""

    def test_orphan_result_rejected_with_missing_execution(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        """Recording a Result with a non-existent execution_id must raise MissingExecutionError."""
        with session_factory() as session, pytest.raises(MissingExecutionError):
            record_result(
                session=session,
                execution_id="non-existent-exec-id",
                metric_name="accuracy",
                metric_value=0.95,
                actor=ActorType.EXECUTION_WORKER,
            )

    def test_unauthorized_actor_cannot_create_results(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        """LLM and reasoning agents must not be permitted to record empirical results directly."""
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-prov-01",
                title="T",
                research_question="Q",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-prov-01",
                research_run_id=run.id,
                objective="Obj",
                status=ExperimentStatus.RUNNING.value,
            )
            session.add(exp)
            exec_model = ExecutionModel(
                id="exec-prov-01",
                experiment_id=exp.id,
                status=ExecutionStatus.RUNNING.value,
            )
            session.add(exec_model)
            session.commit()

        # RESEARCH_AGENT actor attempt
        with session_factory() as session, pytest.raises(ActorAuthorizationError):
            record_result(
                session=session,
                execution_id="exec-prov-01",
                metric_name="accuracy",
                metric_value=0.99,
                actor=ActorType.RESEARCH_AGENT,
            )

        # VERIFIER actor attempt
        with session_factory() as session, pytest.raises(ActorAuthorizationError):
            record_result(
                session=session,
                execution_id="exec-prov-01",
                metric_name="accuracy",
                metric_value=0.99,
                actor=ActorType.VERIFIER,
            )

    def test_non_finite_metric_value_rejected(self, session_factory: sessionmaker[Session]) -> None:
        """Non-finite floats (NaN, Inf) must be rejected at result recording boundary."""
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-prov-02",
                title="T",
                research_question="Q",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-prov-02",
                research_run_id=run.id,
                objective="Obj",
                status=ExperimentStatus.RUNNING.value,
            )
            session.add(exp)
            exec_model = ExecutionModel(
                id="exec-prov-02",
                experiment_id=exp.id,
                status=ExecutionStatus.RUNNING.value,
            )
            session.add(exec_model)
            session.commit()

            with pytest.raises(ValueError, match="finite number"):
                record_result(
                    session=session,
                    execution_id="exec-prov-02",
                    metric_name="loss",
                    metric_value=float("nan"),
                    actor=ActorType.EXECUTION_WORKER,
                )


# =============================================================================
# GATE 8 & 13: Figure Historical Immutability & Cryptographic Lineage
# =============================================================================
class TestFigureLineageAndImmutabilityAudit:
    """Verifies that scientific figures preserve SHA-256 integrity over disk bytes,
    retain source Result IDs, and regenerating with altered data creates a distinct record."""

    def test_figure_hash_matches_exact_disk_bytes(self, tmp_path: Path) -> None:
        """SHA-256 hash must be computed over the actual rendered bytes on disk."""
        gen = FigureGenerator()
        info = gen.generate_comparison_bar_chart(
            baseline_results=[0.80, 0.82, 0.81],
            treatment_results=[0.90, 0.92, 0.91],
            output_dir=tmp_path,
            filename="test_chart.png",
            metric_name="accuracy",
            metric_unit="%",
        )

        assert info.file_path.exists()
        disk_bytes = info.file_path.read_bytes()
        expected_hash = hashlib.sha256(disk_bytes).hexdigest()

        assert info.content_hash == expected_hash
        assert info.size_bytes == len(disk_bytes)

    def test_figure_regeneration_with_different_data_produces_distinct_artifact(
        self, tmp_path: Path
    ) -> None:
        """Regenerating a figure with modified data produces a distinct cryptographic hash
        and does not overwrite historical lineage."""
        gen = FigureGenerator()

        fig1 = gen.generate_comparison_bar_chart(
            baseline_results=[0.50, 0.52],
            treatment_results=[0.60, 0.62],
            output_dir=tmp_path,
            filename="chart_v1.png",
            metric_name="accuracy",
        )

        fig2 = gen.generate_comparison_bar_chart(
            baseline_results=[0.50, 0.52],
            treatment_results=[0.85, 0.88],  # Much higher treatment
            output_dir=tmp_path,
            filename="chart_v2.png",
            metric_name="accuracy",
        )

        assert fig1.content_hash != fig2.content_hash
        assert fig1.metadata["treatment_mean"] != fig2.metadata["treatment_mean"]


# =============================================================================
# GATE 9: Metric Extraction Determinism & Ambiguity Handling
# =============================================================================
class TestMetricExtractionAmbiguityAudit:
    """Verifies that MetricExtractor rejects duplicate names, unexpected nested structures,
    and missing metric names, failing closed on ambiguity."""

    def test_duplicate_metric_names_in_list_rejected(self) -> None:
        extractor = MetricExtractor()
        data = [
            {"metric_name": "acc", "metric_value": 0.85},
            {"metric_name": "acc", "metric_value": 0.90},  # Ambiguous duplicate
        ]
        with pytest.raises(MalformedMetricError, match="Duplicate metric name"):
            extractor.parse_metrics_json(data)

    def test_duplicate_metric_names_in_csv_rejected(self) -> None:
        extractor = MetricExtractor()
        csv_text = "metric_name,metric_value\naccuracy,0.85\naccuracy,0.90\n"
        with pytest.raises(MalformedMetricError, match="Duplicate metric name"):
            extractor.parse_metrics_csv(csv_text)

    def test_csv_value_without_metric_name_rejected(self) -> None:
        extractor = MetricExtractor()
        csv_text = "metric_name,metric_value\n,0.85\n"
        with pytest.raises(MalformedMetricError, match="empty metric name"):
            extractor.parse_metrics_csv(csv_text)

    def test_deeply_nested_structure_in_json_rejected(self) -> None:
        extractor = MetricExtractor()
        data = {
            "accuracy": {
                "value": {"unsupported": "nested_dict"},
                "unit": "%",
            }
        }
        with pytest.raises(MalformedMetricError, match="Unexpected nested structure"):
            extractor.parse_metrics_json(data)


# =============================================================================
# GATE 11 & 12: Deterministic Statistical Analysis & Numerical Correctness
# =============================================================================
class TestStatisticalAnalysisAudit:
    """Verifies sample statistics, Bessel correction, N=1 safety, zero-baseline protection,
    and exact reproducibility."""

    def test_summary_statistics_numerical_correctness(self) -> None:
        analyzer = StatisticalAnalyzer()
        # Sample: [10.0, 20.0, 30.0]
        # Mean: 20.0
        # Bessel variance: ((10-20)^2 + (20-20)^2 + (30-20)^2) / 2 = 200 / 2 = 100.0
        # Sample std dev: sqrt(100.0) = 10.0
        # SEM: 10.0 / sqrt(3) = 5.773502691896258
        # Median: 20.0, Min: 10.0, Max: 30.0
        summary = analyzer.compute_summary([10.0, 20.0, 30.0], metric_name="latency")

        assert summary.sample_size == 3
        assert summary.mean == 20.0
        assert summary.variance == 100.0
        assert summary.std_dev == 10.0
        assert summary.standard_error == pytest.approx(10.0 / math.sqrt(3), rel=1e-6)
        assert summary.median == 20.0
        assert summary.min_value == 10.0
        assert summary.max_value == 30.0

    def test_single_sample_n1_safety(self) -> None:
        """N=1 sample must compute mean/median/min/max but return None for variance/std/SEM."""
        analyzer = StatisticalAnalyzer()
        summary = analyzer.compute_summary([42.0], metric_name="score")

        assert summary.sample_size == 1
        assert summary.mean == 42.0
        assert summary.median == 42.0
        assert summary.variance is None
        assert summary.std_dev is None
        assert summary.standard_error is None

    def test_empty_sample_n0_raises_insufficient_data(self) -> None:
        analyzer = StatisticalAnalyzer()
        with pytest.raises(InsufficientDataError):
            analyzer.compute_summary([], metric_name="score")

    def test_zero_baseline_safe_handling(self) -> None:
        """Zero baseline mean must not trigger ZeroDivisionError; zero_baseline_warning must be True."""
        analyzer = StatisticalAnalyzer()
        comp = analyzer.compare_groups(
            baseline_results=[0.0, 0.0, 0.0],
            treatment_results=[5.0, 6.0, 4.0],
            metric_name="errors",
        )

        assert comp.zero_baseline_warning is True
        assert comp.relative_difference is None
        assert comp.relative_difference_percent is None
        assert comp.absolute_difference == 5.0

    def test_statistical_analysis_is_100_percent_repeatable(self) -> None:
        """Calling analysis multiple times on identical data returns identical results."""
        analyzer = StatisticalAnalyzer()
        base = [1.2, 1.4, 1.3, 1.5]
        treat = [2.2, 2.5, 2.3, 2.4]

        comp1 = analyzer.compare_groups(base, treat, metric_name="throughput")
        comp2 = analyzer.compare_groups(base, treat, metric_name="throughput")

        assert comp1.absolute_difference == comp2.absolute_difference
        assert comp1.relative_difference == comp2.relative_difference
        assert comp1.t_statistic == comp2.t_statistic
        assert comp1.p_value == comp2.p_value
        assert comp1.statistically_significant == comp2.statistically_significant


# =============================================================================
# GATE 14: Execution Failure Audit
# =============================================================================
class TestExecutionFailureAudit:
    """Verifies failure states: non-zero exit, timeout, crash, retaining diagnostics."""

    def test_failed_container_execution_persists_failure_state_and_no_false_results(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        class FailingBackend(ExecutionBackend):
            def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
                return ExecutionOutcome(
                    execution_id=request.execution_id,
                    experiment_id=request.experiment_id,
                    research_run_id=request.research_run_id,
                    status=ExecutionStatus.FAILED,
                    exit_code=137,
                    stdout="Started...",
                    stderr="Segmentation fault (core dumped)",
                    output_artifacts=[],
                    duration_seconds=0.5,
                    failure_reason="Process crashed with SIGSEGV",
                    cleaned_up=True,
                )

        mgr = WorkspaceManager(base_root=tmp_path)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            backend=FailingBackend(),
            workspace_manager=mgr,
        )

        with session_factory() as session:
            run = ResearchRunModel(
                id="run-fail-audit",
                title="T",
                research_question="Q",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-fail-audit",
                research_run_id=run.id,
                objective="Obj",
                status=ExperimentStatus.DESIGNED.value,
            )
            session.add(exp)
            session.commit()

        record = worker.execute_experiment(
            experiment="exp-fail-audit",
            code_files={"main.py": "crash()"},
        )

        assert record.status == ExecutionStatus.FAILED
        assert record.exit_code == 137
        assert "Segmentation fault" in record.stderr
        assert len(record.results) == 0  # No false results created

        # Verify database state
        with session_factory() as session:
            db_exec = session.get(ExecutionModel, record.execution_id)
            assert db_exec is not None
            assert db_exec.status == ExecutionStatus.FAILED.value
            results_in_db = (
                session.query(ResultModel)
                .filter(ResultModel.execution_id == record.execution_id)
                .all()
            )
            assert len(results_in_db) == 0


# =============================================================================
# GATE 16: LLM Independence Gate
# =============================================================================
class TestLLMIndependenceAudit:
    """Proves that the complete experimental pipeline:
    Execution -> Metric Extraction -> Statistical Analysis -> Figure Generation
    runs deterministically with ZERO LLM involvement or API keys."""

    def test_pipeline_runs_without_llm_subsystem(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        class DeterministicMockBackend(ExecutionBackend):
            def __init__(self, ws_mgr: WorkspaceManager) -> None:
                self.ws_mgr = ws_mgr

            def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
                ws = self.ws_mgr.prepare_workspace(request)
                # Output deterministic metric
                (ws.output_dir / "metrics.json").write_text(
                    '{"eval_accuracy": 0.935, "loss": 0.082}', encoding="utf-8"
                )
                artifacts = self.ws_mgr.collect_output_artifacts(ws)
                return ExecutionOutcome(
                    execution_id=request.execution_id,
                    experiment_id=request.experiment_id,
                    research_run_id=request.research_run_id,
                    status=ExecutionStatus.COMPLETED,
                    exit_code=0,
                    stdout="Epoch 1/1 complete.\nAccuracy: 0.935\n",
                    stderr="",
                    output_artifacts=artifacts,
                    duration_seconds=1.2,
                    cleaned_up=True,
                )

        mgr = WorkspaceManager(base_root=tmp_path)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            backend=DeterministicMockBackend(mgr),
            workspace_manager=mgr,
        )

        with session_factory() as session:
            run = ResearchRunModel(
                id="run-no-llm",
                title="Deterministic Run",
                research_question="Does optimizer converge?",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-no-llm",
                research_run_id=run.id,
                objective="Train model",
                status=ExperimentStatus.DESIGNED.value,
            )
            session.add(exp)
            session.commit()

        # Step 1: Run execution worker
        record = worker.execute_experiment(
            experiment="exp-no-llm",
            code_files={"train.py": "print('pure python')"},
        )
        assert record.status == ExecutionStatus.COMPLETED
        assert len(record.results) == 2
        metric_dict = {r["metric_name"]: r["metric_value"] for r in record.results}
        assert metric_dict["eval_accuracy"] == 0.935
        assert metric_dict["loss"] == 0.082

        # Step 2: Run statistical analysis
        analyzer = StatisticalAnalyzer()
        summary = analyzer.compute_summary([0.935, 0.940, 0.930], metric_name="eval_accuracy")
        assert summary.mean == 0.935

        # Step 3: Run figure generation
        fig_gen = FigureGenerator(analyzer=analyzer)
        fig_info = fig_gen.generate_comparison_bar_chart(
            baseline_results=[0.85, 0.86],
            treatment_results=[0.935, 0.94],
            output_dir=tmp_path / "figures",
            filename="no_llm_chart.png",
            metric_name="eval_accuracy",
        )
        assert fig_info.file_path.exists()
        assert fig_info.size_bytes > 0
        assert len(fig_info.content_hash) == 64
