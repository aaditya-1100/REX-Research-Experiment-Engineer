"""Unit tests for Metric Extraction, Schema Validation, and Finite Float Enforcement (REX-020)."""

import math
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rex.analysis.exceptions import MalformedMetricError
from rex.analysis.metrics import MetricExtractor, RawMetric
from rex.domain.models import ExecutionStatus, ExperimentStatus, ResearchState
from rex.execution.models import ExecutionRequest
from rex.execution.workspace import WorkspaceManager
from rex.observability.events import EventType
from rex.persistence.database import Base
from rex.persistence.models import ExecutionModel, ExperimentModel, ResearchRunModel, ResultModel
from rex.persistence.repositories import EventRepository


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class TestMetricExtraction:
    """Tests for parsing, validating, and persisting empirical experiment metrics."""

    def test_raw_metric_validation_success(self) -> None:
        m = RawMetric(metric_name="accuracy", metric_value=0.95, metric_unit="%")
        assert m.metric_name == "accuracy"
        assert m.metric_value == 0.95
        assert m.metric_unit == "%"

    def test_raw_metric_rejects_empty_name(self) -> None:
        with pytest.raises(MalformedMetricError):
            RawMetric(metric_name="   ", metric_value=0.5)

    def test_raw_metric_rejects_nan_and_inf(self) -> None:
        with pytest.raises(MalformedMetricError):
            RawMetric(metric_name="loss", metric_value=float("nan"))

        with pytest.raises(MalformedMetricError):
            RawMetric(metric_name="loss", metric_value=float("inf"))

        with pytest.raises(MalformedMetricError):
            RawMetric(metric_name="loss", metric_value=float("-inf"))

    def test_parse_metrics_json_dict_format(self) -> None:
        extractor = MetricExtractor()
        data = {"accuracy": 0.94, "loss": 0.06}
        metrics = extractor.parse_metrics_json(data)
        assert len(metrics) == 2
        name_map = {m.metric_name: m.metric_value for m in metrics}
        assert name_map["accuracy"] == 0.94
        assert name_map["loss"] == 0.06

    def test_parse_metrics_json_list_format(self) -> None:
        extractor = MetricExtractor()
        data = [
            {"metric_name": "val_loss", "metric_value": 0.15, "metric_unit": "loss"},
            {"name": "f1_score", "value": 0.88, "unit": ""},
        ]
        metrics = extractor.parse_metrics_json(data)
        assert len(metrics) == 2
        assert metrics[0].metric_name == "val_loss"
        assert metrics[0].metric_value == 0.15
        assert metrics[1].metric_name == "f1_score"
        assert metrics[1].metric_value == 0.88

    def test_parse_metrics_json_rejects_nan_constants(self) -> None:
        extractor = MetricExtractor()
        json_with_nan = '{"loss": NaN, "accuracy": 0.5}'
        with pytest.raises(MalformedMetricError, match="non-finite"):
            extractor.parse_metrics_json(json_with_nan)

        json_with_inf = '{"loss": Infinity, "accuracy": 0.5}'
        with pytest.raises(MalformedMetricError, match="non-finite"):
            extractor.parse_metrics_json(json_with_inf)

    def test_parse_metrics_csv(self) -> None:
        extractor = MetricExtractor()
        csv_text = "metric_name,metric_value,metric_unit\nloss,0.12,loss\naccuracy,0.92,%\n"
        metrics = extractor.parse_metrics_csv(csv_text)
        assert len(metrics) == 2
        assert metrics[0].metric_name == "loss"
        assert metrics[0].metric_value == 0.12
        assert metrics[1].metric_name == "accuracy"
        assert metrics[1].metric_value == 0.92

    def test_extract_from_workspace(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-metrics-01",
            experiment_id="exp-01",
            research_run_id="run-01",
            command=["python", "src/main.py"],
        )
        ws = mgr.prepare_workspace(req)

        # Write output metrics.json
        (ws.output_dir / "metrics.json").write_text(
            '{"train_loss": 0.05, "eval_acc": 0.97}', encoding="utf-8"
        )

        extractor = MetricExtractor()
        extracted = extractor.extract_from_workspace(ws)
        assert len(extracted) == 2
        name_map = {m.metric_name: m.metric_value for m in extracted}
        assert name_map["train_loss"] == 0.05
        assert name_map["eval_acc"] == 0.97

    def test_persist_metrics_to_database(self, session_factory: sessionmaker[Session]) -> None:
        # Set up parent entities
        with session_factory() as session:
            run = ResearchRunModel(
                id="run-test",
                title="T",
                research_question="Q",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            exp = ExperimentModel(
                id="exp-test",
                research_run_id=run.id,
                objective="E",
                status=ExperimentStatus.RUNNING.value,
            )
            session.add(exp)
            exec_model = ExecutionModel(
                id="exec-test",
                experiment_id=exp.id,
                status=ExecutionStatus.RUNNING.value,
            )
            session.add(exec_model)
            session.commit()

        extractor = MetricExtractor()
        raw_metrics = [
            RawMetric(metric_name="accuracy", metric_value=0.91, metric_unit="%"),
            RawMetric(metric_name="latency_ms", metric_value=12.4, metric_unit="ms"),
        ]

        with session_factory() as session:
            results = extractor.persist_metrics(
                session=session,
                execution_id="exec-test",
                metrics=raw_metrics,
            )
            session.commit()

        assert len(results) == 2
        assert results[0].metric_name == "accuracy"
        assert math.isclose(results[0].metric_value, 0.91)
        assert results[1].metric_name == "latency_ms"

        # Verify DB rows and audit events
        with session_factory() as session:
            db_results = (
                session.query(ResultModel).filter(ResultModel.execution_id == "exec-test").all()
            )
            assert len(db_results) == 2

            events = EventRepository(session).list_by_run("run-test")
            result_events = [e for e in events if e.event_type == EventType.RESULT_RECORDED.value]
            assert len(result_events) == 2
