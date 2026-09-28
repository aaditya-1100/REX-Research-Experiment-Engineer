"""Unit tests for REX Structured Logging (REX-003)."""

import json
from io import StringIO

from rex.observability.events import (
    ActorType,
    EventType,
    create_event,
)
from rex.observability.logging import (
    JsonFormatter,
    LoggingEventSink,
    StructuredLogger,
    TextFormatter,
    get_logger,
    log_event,
    setup_logging,
)


def test_setup_logging_json_output():
    """Verify setup_logging produces valid single-line JSON log output."""
    stream = StringIO()
    logger = setup_logging(
        level="INFO", json_output=True, stream=stream, logger_name="test_rex_json"
    )

    logger.info("Test message", extra={"research_run_id": "run_123", "actor": "system"})

    output = stream.getvalue().strip()
    assert output != ""
    data = json.loads(output)

    assert data["level"] == "INFO"
    assert data["message"] == "Test message"
    assert data["logger"] == "test_rex_json"
    assert data["research_run_id"] == "run_123"
    assert data["actor"] == "system"
    assert "timestamp" in data


def test_setup_logging_text_output():
    """Verify setup_logging with json_output=False produces readable text output."""
    stream = StringIO()
    logger = setup_logging(
        level="INFO", json_output=False, stream=stream, logger_name="test_rex_text"
    )

    logger.info("Human-readable log", extra={"research_run_id": "run_456", "actor": "owner"})

    output = stream.getvalue().strip()
    assert "[INFO   ]" in output
    assert "[run:run_456]" in output
    assert "[actor:owner]" in output
    assert "Human-readable log" in output


def test_formatters_instantiation():
    """Verify JsonFormatter and TextFormatter can be directly initialized and used."""
    json_fmt = JsonFormatter()
    text_fmt = TextFormatter()
    assert json_fmt is not None
    assert text_fmt is not None


def test_get_logger_factory():
    """Verify get_logger returns a StructuredLogger with context."""
    logger = get_logger("test_get_logger", context={"run_id": "run_001"})
    assert isinstance(logger, StructuredLogger)
    assert logger.extra["run_id"] == "run_001"


def test_logging_level_filtering():
    """Verify log messages below the configured threshold are filtered out."""
    stream = StringIO()
    logger = setup_logging(
        level="WARNING", json_output=True, stream=stream, logger_name="test_levels"
    )

    logger.debug("Debug should be omitted")
    logger.info("Info should be omitted")
    logger.warning("Warning should be recorded")
    logger.error("Error should be recorded")

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["level"] == "WARNING"
    assert lines[0]["message"] == "Warning should be recorded"
    assert lines[1]["level"] == "ERROR"
    assert lines[1]["message"] == "Error should be recorded"


def test_secret_sanitization_in_log_extras():
    """Verify sensitive keys in extra logging fields are automatically masked."""
    stream = StringIO()
    logger = setup_logging(
        level="INFO", json_output=True, stream=stream, logger_name="test_secrets"
    )

    raw_secret = "sk-super-secret-token-do-not-leak"
    logger.info(
        "Service authentication attempted",
        extra={
            "api_key": raw_secret,
            "nested_auth": {
                "token": "bearer-secret-777",
                "password": "plain_password_123",
                "safe_user": "researcher_1",
            },
        },
    )

    output = stream.getvalue().strip()
    # Confirm raw secrets never appear anywhere in the output
    assert raw_secret not in output
    assert "bearer-secret-777" not in output
    assert "plain_password_123" not in output

    data = json.loads(output)
    assert data["extra"]["api_key"] == "[REDACTED]"
    assert data["extra"]["nested_auth"]["token"] == "[REDACTED]"
    assert data["extra"]["nested_auth"]["password"] == "[REDACTED]"
    assert data["extra"]["nested_auth"]["safe_user"] == "researcher_1"


def test_log_event_routing_by_type():
    """Verify log_event routes events to appropriate log levels."""
    stream = StringIO()
    logger = setup_logging(
        level="DEBUG", json_output=True, stream=stream, logger_name="test_routing"
    )

    # Regular info event
    evt_info = create_event(
        event_type=EventType.RESEARCH_CREATED,
        actor=ActorType.OWNER,
        research_run_id="run_001",
        payload={"query": "test"},
    )
    log_event(evt_info, logger=logger)

    # Warning event
    evt_warn = create_event(
        event_type=EventType.WARNING,
        actor=ActorType.SYSTEM,
        research_run_id="run_001",
        payload={"reason": "disk_space_low"},
    )
    log_event(evt_warn, logger=logger)

    # Error event
    evt_err = create_event(
        event_type=EventType.ERROR,
        actor=ActorType.EXECUTION_WORKER,
        research_run_id="run_001",
        payload={"error": "subprocess_crashed"},
    )
    log_event(evt_err, logger=logger)

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines() if line]
    assert len(lines) == 3

    assert lines[0]["level"] == "INFO"
    assert lines[0]["event_type"] == "research_created"
    assert lines[0]["payload"]["query"] == "test"

    assert lines[1]["level"] == "WARNING"
    assert lines[1]["event_type"] == "warning"

    assert lines[2]["level"] == "ERROR"
    assert lines[2]["event_type"] == "error"


def test_logging_event_sink():
    """Verify LoggingEventSink properly forwards events to the logger."""
    stream = StringIO()
    logger = setup_logging(level="INFO", json_output=True, stream=stream, logger_name="test_sink")
    sink = LoggingEventSink(logger=logger)

    event = create_event(
        event_type=EventType.HYPOTHESIS_CREATED,
        actor=ActorType.RESEARCH_AGENT,
        research_run_id="run_001",
        payload={"hypothesis_id": "HYP-001"},
    )
    sink.emit(event)

    output = stream.getvalue().strip()
    data = json.loads(output)
    assert data["event_type"] == "hypothesis_created"
    assert data["actor"] == "research_agent"
    assert data["research_run_id"] == "run_001"


def test_structured_logger_kwargs():
    """Verify StructuredLogger forwards kwargs into extra context."""
    stream = StringIO()
    base_logger = setup_logging(
        level="INFO", json_output=True, stream=stream, logger_name="test_adapter"
    )
    logger = StructuredLogger(base_logger, extra={"default_tag": "research_suite"})

    logger.info(
        "Experiment progress",
        event_type=EventType.EXECUTION_COMPLETED,
        research_run_id="run_999",
        experiment_id="exp_01",
        execution_id="exec_01",
        actor=ActorType.EXECUTION_WORKER,
        payload={"score": 0.88},
    )

    output = stream.getvalue().strip()
    data = json.loads(output)
    assert data["event_type"] == "execution_completed"
    assert data["research_run_id"] == "run_999"
    assert data["experiment_id"] == "exp_01"
    assert data["execution_id"] == "exec_01"
    assert data["actor"] == "execution_worker"
    assert data["payload"]["score"] == 0.88


def test_setup_logging_respects_settings(monkeypatch):
    """Verify setup_logging adopts REX_LOG_LEVEL from settings if level is None."""
    monkeypatch.setenv("REX_LOG_LEVEL", "CRITICAL")
    stream = StringIO()
    logger = setup_logging(
        level=None, json_output=True, stream=stream, logger_name="test_env_level"
    )

    logger.error("Error should not appear when level is CRITICAL")
    logger.critical("Critical error recorded")

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines() if line]
    assert len(lines) == 1
    assert lines[0]["level"] == "CRITICAL"
