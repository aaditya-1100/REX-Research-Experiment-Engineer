"""Unit tests for Deterministic Statistical Analysis Engine (REX-021)."""

import math

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rex.analysis.exceptions import InsufficientDataError
from rex.analysis.statistics import StatisticalAnalyzer
from rex.domain.models import ResearchState, Result
from rex.observability.events import EventType
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
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


class TestStatisticalAnalysisEngine:
    """Tests for deterministic statistics, baseline comparisons, and analysis persistence."""

    def test_compute_summary_standard_sample(self) -> None:
        analyzer = StatisticalAnalyzer()
        data = [10.0, 12.0, 14.0, 16.0, 18.0]
        summary = analyzer.compute_summary(data, metric_name="accuracy")

        assert summary.metric_name == "accuracy"
        assert summary.sample_size == 5
        assert math.isclose(summary.mean, 14.0)
        assert math.isclose(summary.median, 14.0)
        assert math.isclose(summary.min_value, 10.0)
        assert math.isclose(summary.max_value, 18.0)

        # Sample variance with Bessel correction (N-1 = 4): sum((x-14)^2)/4 = (16+4+0+4+16)/4 = 10.0
        assert summary.variance is not None
        assert math.isclose(summary.variance, 10.0)

        # std_dev = sqrt(10.0) = 3.162277
        assert summary.std_dev is not None
        assert math.isclose(summary.std_dev, math.sqrt(10.0))

        # SEM = std_dev / sqrt(5) = sqrt(2.0) = 1.414213
        assert summary.standard_error is not None
        assert math.isclose(summary.standard_error, math.sqrt(2.0))

    def test_compute_summary_single_sample(self) -> None:
        analyzer = StatisticalAnalyzer()
        summary = analyzer.compute_summary([42.0], metric_name="latency")

        assert summary.sample_size == 1
        assert math.isclose(summary.mean, 42.0)
        assert math.isclose(summary.median, 42.0)
        assert summary.variance is None
        assert summary.std_dev is None
        assert summary.standard_error is None

    def test_compute_summary_empty_sample_fails(self) -> None:
        analyzer = StatisticalAnalyzer()
        with pytest.raises(InsufficientDataError):
            analyzer.compute_summary([], metric_name="loss")

    def test_compute_summary_from_result_domain_objects(self) -> None:
        analyzer = StatisticalAnalyzer()
        results = [
            Result(id="res-1", execution_id="exec-1", metric_name="score", metric_value=80.0),
            Result(id="res-2", execution_id="exec-2", metric_name="score", metric_value=90.0),
        ]
        summary = analyzer.compute_summary(results)

        assert summary.metric_name == "score"
        assert summary.sample_size == 2
        assert math.isclose(summary.mean, 85.0)
        assert summary.source_result_ids == ["res-1", "res-2"]

    def test_compare_groups_standard(self) -> None:
        analyzer = StatisticalAnalyzer()
        baseline = [10.0, 11.0, 12.0, 10.5]  # mean = 10.875
        treatment = [14.0, 15.0, 16.0, 15.5]  # mean = 15.125

        comp = analyzer.compare_groups(baseline, treatment, metric_name="f1", alpha=0.05)

        assert comp.metric_name == "f1"
        assert math.isclose(comp.baseline_summary.mean, 10.875)
        assert math.isclose(comp.treatment_summary.mean, 15.125)

        assert math.isclose(comp.absolute_difference, 15.125 - 10.875)
        assert comp.relative_difference is not None
        assert math.isclose(comp.relative_difference, (15.125 - 10.875) / 10.875)
        assert comp.zero_baseline_warning is False

        # Statistical test: treatment significantly higher than baseline
        assert comp.t_statistic is not None
        assert comp.t_statistic > 0
        assert comp.p_value is not None
        assert comp.p_value < 0.05
        assert comp.statistically_significant is True

    def test_compare_groups_zero_baseline_handling(self) -> None:
        analyzer = StatisticalAnalyzer()
        baseline = [0.0, 0.0, 0.0]
        treatment = [5.0, 6.0, 7.0]

        comp = analyzer.compare_groups(baseline, treatment, metric_name="throughput")

        assert comp.zero_baseline_warning is True
        assert comp.relative_difference is None
        assert comp.relative_difference_percent is None
        assert math.isclose(comp.absolute_difference, 6.0)

    def test_record_analysis_persists_to_database(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-analysis-01",
                title="Run",
                research_question="Question",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-analysis-01",
                research_run_id=run.id,
                objective="Obj",
            )
            session.add(exp)
            exec_row = ExecutionModel(id="exec-analysis-01", experiment_id=exp.id)
            session.add(exec_row)

            res1 = ResultModel(
                id="res-01",
                execution_id=exec_row.id,
                metric_name="acc",
                metric_value=0.85,
            )
            res2 = ResultModel(
                id="res-02",
                execution_id=exec_row.id,
                metric_name="acc",
                metric_value=0.89,
            )
            session.add_all([res1, res2])
            session.commit()

        analyzer = StatisticalAnalyzer()

        with session_factory() as session:
            db_res1 = session.get(ResultModel, "res-01")
            db_res2 = session.get(ResultModel, "res-02")
            assert db_res1 and db_res2
            summary = analyzer.compute_summary([db_res1, db_res2])

            persisted = analyzer.record_analysis(
                session=session,
                research_run_id="run-analysis-01",
                analysis=summary,
            )
            session.commit()

            analysis_id = persisted.id

        # Verify DB record
        with session_factory() as session:
            an_row = session.get(AnalysisModel, analysis_id)
            assert an_row is not None
            assert an_row.research_run_id == "run-analysis-01"
            assert an_row.analysis_type == "summary"
            assert an_row.input_result_ids == ["res-01", "res-02"]
            assert math.isclose(an_row.output_json["mean"], 0.87)

            # Verify audit event
            events = EventRepository(session).list_by_run("run-analysis-01")
            an_events = [e for e in events if e.event_type == EventType.ANALYSIS_COMPLETED.value]
            assert len(an_events) == 1
            assert an_events[0].payload_json["analysis_id"] == analysis_id
