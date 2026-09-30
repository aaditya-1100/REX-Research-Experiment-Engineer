"""Unit tests for Experiment Visualization and Deterministic Figure Generation (REX-022)."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rex.analysis.figures import FigureGenerator
from rex.domain.models import ArtifactType, ResearchState, Result
from rex.persistence.database import Base
from rex.persistence.models import ArtifactModel, ExecutionModel, ExperimentModel, ResearchRunModel


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class TestFigureGeneration:
    """Tests for generating deterministic scientific charts, SVG/PNG formats, hashes, and artifact registration."""

    def test_generate_comparison_bar_chart_png_and_svg(self, tmp_path: Path) -> None:
        generator = FigureGenerator()
        baseline = [
            Result(id="b1", execution_id="ex-1", metric_name="acc", metric_value=0.80),
            Result(id="b2", execution_id="ex-1", metric_name="acc", metric_value=0.82),
        ]
        treatment = [
            Result(id="t1", execution_id="ex-2", metric_name="acc", metric_value=0.88),
            Result(id="t2", execution_id="ex-2", metric_name="acc", metric_value=0.90),
        ]

        # 1. Test PNG format
        fig_png = generator.generate_comparison_bar_chart(
            baseline_results=baseline,
            treatment_results=treatment,
            output_dir=tmp_path,
            filename="comparison.png",
            metric_name="Accuracy",
            format="png",
        )

        assert fig_png.file_path.exists()
        assert fig_png.size_bytes > 0
        content_png = fig_png.file_path.read_bytes()
        assert hashlib.sha256(content_png).hexdigest() == fig_png.content_hash
        assert fig_png.artifact_type == ArtifactType.FIGURE
        assert set(fig_png.source_result_ids) == {"b1", "b2", "t1", "t2"}

        # 2. Test SVG format
        fig_svg = generator.generate_comparison_bar_chart(
            baseline_results=baseline,
            treatment_results=treatment,
            output_dir=tmp_path,
            filename="comparison.svg",
            metric_name="Accuracy",
            format="svg",
        )

        assert fig_svg.file_path.exists()
        assert fig_svg.size_bytes > 0
        content_svg = fig_svg.file_path.read_bytes()
        assert hashlib.sha256(content_svg).hexdigest() == fig_svg.content_hash
        assert fig_svg.format == "svg"

    def test_generate_progression_plot(self, tmp_path: Path) -> None:
        generator = FigureGenerator()
        series_data = {
            "Baseline": [0.5, 0.4, 0.35, 0.30],
            "Treatment": [0.5, 0.3, 0.22, 0.18],
        }

        fig = generator.generate_progression_plot(
            series_data=series_data,
            output_dir=tmp_path,
            filename="loss_curve.png",
            title="Training Loss over Epochs",
            xlabel="Epoch",
            ylabel="Cross-Entropy Loss",
        )

        assert fig.file_path.exists()
        assert fig.size_bytes > 0
        assert fig.figure_type == "progression"
        assert hashlib.sha256(fig.file_path.read_bytes()).hexdigest() == fig.content_hash

    def test_generate_distribution_boxplot(self, tmp_path: Path) -> None:
        generator = FigureGenerator()
        groups = {
            "Control": [10.0, 11.2, 9.8, 10.5, 10.1],
            "Method A": [12.0, 12.5, 11.8, 12.2, 12.9],
            "Method B": [15.1, 14.8, 15.5, 16.0, 15.2],
        }

        fig = generator.generate_distribution_boxplot(
            groups=groups,
            output_dir=tmp_path,
            filename="boxplot.png",
            title="Throughput Distributions",
            ylabel="Ops/sec",
        )

        assert fig.file_path.exists()
        assert fig.size_bytes > 0
        assert fig.figure_type == "boxplot"
        assert hashlib.sha256(fig.file_path.read_bytes()).hexdigest() == fig.content_hash

    def test_record_figure_artifact_persists_to_database(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-fig-01",
                title="Run",
                research_question="Question",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-fig-01",
                research_run_id=run.id,
                objective="Objective",
            )
            session.add(exp)
            exec_row = ExecutionModel(id="exec-fig-01", experiment_id=exp.id)
            session.add(exec_row)
            session.commit()

        generator = FigureGenerator()
        fig_info = generator.generate_comparison_bar_chart(
            baseline_results=[1.0, 2.0],
            treatment_results=[3.0, 4.0],
            output_dir=tmp_path,
            filename="chart.png",
        )

        with session_factory() as session:
            art = generator.record_figure_artifact(
                session=session,
                execution_id="exec-fig-01",
                figure_info=fig_info,
            )
            session.commit()
            art_id = art.id

        with session_factory() as session:
            db_art = session.get(ArtifactModel, art_id)
            assert db_art is not None
            assert db_art.artifact_type == ArtifactType.FIGURE.value
            assert db_art.execution_id == "exec-fig-01"
            assert db_art.content_hash == fig_info.content_hash
            assert db_art.size_bytes == fig_info.size_bytes
            assert db_art.metadata_json["figure_type"] == "comparison_bar"
