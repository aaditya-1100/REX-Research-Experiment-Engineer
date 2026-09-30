"""End-to-End Integration Test for Batch 3 Experimental Intelligence Pipeline.

Validates the full deterministic flow:
Experiment Specification -> Workspace Management -> Environment Capture ->
Execution Worker -> Output Artifact Collection -> Metric Extraction ->
Deterministic Statistical Analysis -> Scientific Figure Generation -> Provenance Auditing.
"""

import hashlib
import math
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rex.analysis.figures import FigureGenerator
from rex.analysis.statistics import StatisticalAnalyzer
from rex.domain.models import ArtifactType, ExecutionStatus, ExperimentStatus, ResearchState
from rex.execution.backend import ExecutionBackend
from rex.execution.models import ExecutionOutcome, ExecutionRequest
from rex.execution.worker import DockerExecutionWorker
from rex.execution.workspace import WorkspaceManager
from rex.observability.events import EventType
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import EventRepository


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class PipelineMockBackend(ExecutionBackend):
    """Simulates realistic execution runs producing output metrics and artifacts."""

    def __init__(
        self, workspace_manager: WorkspaceManager, accuracy_values: dict[str, list[float]]
    ) -> None:
        self.workspace_manager = workspace_manager
        self.accuracy_values = accuracy_values
        self.call_counts: dict[str, int] = {}

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        ws = self.workspace_manager.prepare_workspace(request)
        exp_id = request.experiment_id

        # Determine accuracy value for this run
        idx = self.call_counts.get(exp_id, 0)
        self.call_counts[exp_id] = idx + 1
        acc_list = self.accuracy_values.get(exp_id, [0.80])
        acc = acc_list[idx % len(acc_list)]
        loss = round(1.0 - acc, 3)

        # Write output files
        (ws.output_dir / "metrics.json").write_text(
            f'{{"accuracy": {acc}, "loss": {loss}}}', encoding="utf-8"
        )
        (ws.output_dir / "checkpoint.pt").write_bytes(f"weights_{exp_id}_{idx}".encode())

        artifacts = self.workspace_manager.collect_output_artifacts(ws)

        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout=f"Experiment {exp_id} run {idx} completed successfully.\nAccuracy: {acc}\n",
            stderr="",
            output_artifacts=artifacts,
            duration_seconds=1.8,
            cleaned_up=True,
        )


class TestExperimentalPipelineEndToEnd:
    """Comprehensive test validating the end-to-end deterministic experimental pipeline."""

    def test_full_pipeline_baseline_vs_treatment(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        # 1. Initialize Research Run in database
        run_id = "run-pipeline-01"
        with session_factory() as session:
            run = ResearchRunModel(
                id=run_id,
                title="Optimizer Benchmark",
                research_question="Does AdamW outperform SGD on benchmark dataset?",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)

            # 2. Design Experiments: Baseline (SGD) vs Treatment (AdamW)
            base_exp = ExperimentModel(
                id="exp-baseline-sgd",
                research_run_id=run.id,
                objective="Train baseline model using SGD",
                specification_json={"optimizer": "SGD", "command": "python src/train.py"},
                status=ExperimentStatus.DESIGNED.value,
            )
            treat_exp = ExperimentModel(
                id="exp-treatment-adamw",
                research_run_id=run.id,
                objective="Train candidate model using AdamW",
                specification_json={"optimizer": "AdamW", "command": "python src/train.py"},
                status=ExperimentStatus.DESIGNED.value,
            )
            session.add_all([base_exp, treat_exp])

            # Create seed repetitions as child experiments
            child_exps = []
            for s in [1, 2, 3]:
                child_exps.append(
                    ExperimentModel(
                        id=f"exp-baseline-sgd-s{s}",
                        research_run_id=run.id,
                        parent_experiment_id=base_exp.id,
                        objective=f"Train baseline model using SGD (seed {s})",
                        specification_json={
                            "optimizer": "SGD",
                            "seed": s,
                            "command": "python src/train.py",
                        },
                        status=ExperimentStatus.DESIGNED.value,
                    )
                )
                child_exps.append(
                    ExperimentModel(
                        id=f"exp-treatment-adamw-s{s}",
                        research_run_id=run.id,
                        parent_experiment_id=treat_exp.id,
                        objective=f"Train candidate model using AdamW (seed {s})",
                        specification_json={
                            "optimizer": "AdamW",
                            "seed": s,
                            "command": "python src/train.py",
                        },
                        status=ExperimentStatus.DESIGNED.value,
                    )
                )
            session.add_all(child_exps)
            session.commit()

        # Simulated accuracies across multiple seeds
        accuracies = {
            "exp-baseline-sgd-s1": [0.81],
            "exp-baseline-sgd-s2": [0.82],
            "exp-baseline-sgd-s3": [0.80],
            "exp-treatment-adamw-s1": [0.89],
            "exp-treatment-adamw-s2": [0.91],
            "exp-treatment-adamw-s3": [0.90],
        }

        wm = WorkspaceManager(base_root=tmp_path / "workspaces")
        backend = PipelineMockBackend(workspace_manager=wm, accuracy_values=accuracies)
        worker = DockerExecutionWorker(
            session_factory=session_factory,
            backend=backend,
            workspace_manager=wm,
        )

        code_base = {"train.py": "print('Training SGD')", "config.json": '{"lr": 0.01}'}
        code_treat = {"train.py": "print('Training AdamW')", "config.json": '{"lr": 0.001}'}

        # 3. Execute Baseline repetitions (3 seeds)
        baseline_records = []
        for s in [1, 2, 3]:
            rec = worker.execute_experiment(
                experiment=f"exp-baseline-sgd-s{s}",
                code_files=code_base,
                seed=s,
            )
            baseline_records.append(rec)
            assert rec.status == ExecutionStatus.COMPLETED

        # 4. Execute Treatment repetitions (3 seeds)
        treatment_records = []
        for s in [1, 2, 3]:
            rec = worker.execute_experiment(
                experiment=f"exp-treatment-adamw-s{s}",
                code_files=code_treat,
                seed=s,
            )
            treatment_records.append(rec)
            assert rec.status == ExecutionStatus.COMPLETED

        # 5. Extract and Validate Stored Empirical Results
        with session_factory() as session:
            all_results = session.query(ResultModel).all()
            assert len(all_results) == 12  # 6 runs * 2 metrics (accuracy + loss)

            base_acc_models = (
                session.query(ResultModel)
                .join(ExecutionModel)
                .filter(
                    ExecutionModel.experiment_id.like("exp-baseline-sgd%"),
                    ResultModel.metric_name == "accuracy",
                )
                .all()
            )
            assert len(base_acc_models) == 3

            treat_acc_models = (
                session.query(ResultModel)
                .join(ExecutionModel)
                .filter(
                    ExecutionModel.experiment_id.like("exp-treatment-adamw%"),
                    ResultModel.metric_name == "accuracy",
                )
                .all()
            )
            assert len(treat_acc_models) == 3

            # 6. Statistical Analysis Engine (REX-021)
            analyzer = StatisticalAnalyzer()
            comparison = analyzer.compare_groups(
                baseline_results=base_acc_models,
                treatment_results=treat_acc_models,
                metric_name="accuracy",
                alpha=0.01,
            )

            assert math.isclose(comparison.baseline_summary.mean, 0.81)
            assert math.isclose(comparison.treatment_summary.mean, 0.90)
            assert math.isclose(comparison.absolute_difference, 0.09)
            assert comparison.relative_difference is not None
            assert math.isclose(comparison.relative_difference, 0.09 / 0.81)
            assert comparison.t_statistic is not None and comparison.t_statistic > 0
            assert comparison.p_value is not None and comparison.p_value < 0.01
            assert comparison.statistically_significant is True

            # Persist Analysis entity
            persisted_analysis = analyzer.record_analysis(
                session=session,
                research_run_id=run_id,
                analysis=comparison,
                analysis_type="hypothesis_comparison",
            )
            session.commit()
            analysis_id = persisted_analysis.id

            # Verify analysis record in database
            an_db = session.get(AnalysisModel, analysis_id)
            assert an_db is not None
            assert an_db.analysis_type == "hypothesis_comparison"
            assert len(an_db.input_result_ids) == 6

        # 7. Scientific Figure Generation (REX-022)
        fig_gen = FigureGenerator(analyzer=analyzer)
        fig_info = fig_gen.generate_comparison_bar_chart(
            baseline_results=base_acc_models,
            treatment_results=treat_acc_models,
            output_dir=tmp_path / "figures",
            filename="adamw_vs_sgd_accuracy.png",
            title="AdamW vs SGD Benchmark Accuracy",
            baseline_label="SGD Baseline",
            treatment_label="AdamW Treatment",
            metric_name="Accuracy",
            metric_unit="%",
        )

        assert fig_info.file_path.exists()
        assert fig_info.size_bytes > 0
        content = fig_info.file_path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == fig_info.content_hash

        # Register Figure Artifact in database linked to final execution
        with session_factory() as session:
            final_exec_id = treatment_records[-1].execution_id
            art = fig_gen.record_figure_artifact(
                session=session,
                execution_id=final_exec_id,
                figure_info=fig_info,
            )
            session.commit()
            art_id = art.id

        with session_factory() as session:
            art_db = session.get(ArtifactModel, art_id)
            assert art_db is not None
            assert art_db.artifact_type == ArtifactType.FIGURE.value
            assert art_db.content_hash == fig_info.content_hash
            assert art_db.size_bytes == fig_info.size_bytes

            # 8. Complete Audit Trail Verification
            events = EventRepository(session).list_by_run(run_id)
            event_types = [e.event_type for e in events]
            assert EventType.EXECUTION_STARTED.value in event_types
            assert EventType.RESULT_RECORDED.value in event_types
            assert EventType.ANALYSIS_COMPLETED.value in event_types
            assert EventType.ARTIFACT_CREATED.value in event_types
