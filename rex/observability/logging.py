"""REX Structured Logging (REX-003).

Provides machine-readable JSON logging based on the Python standard logging library,
with structured research context, secret sanitization, and level configuration.
"""

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, ClassVar, TextIO

from rex.config import get_settings
from rex.observability.events import (
    EventType,
    ResearchEvent,
    sanitize_value,
)

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the current task's correlation ID or empty string."""
    return correlation_id_ctx.get()


def set_correlation_id(correlation_id: str) -> None:
    """Set the current task's correlation ID."""
    correlation_id_ctx.set(correlation_id)


class JsonFormatter(logging.Formatter):
    """Formats LogRecords as machine-readable single-line JSON with sanitized fields."""

    # Standard attributes of LogRecord to exclude from structured extra payload
    _RESERVED_ATTRS: ClassVar[set[str]] = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "event_type",
        "research_run_id",
        "experiment_id",
        "execution_id",
        "actor",
        "payload",
        "correlation_id",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Build UTC ISO 8601 timestamp
        record_time = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        log_entry: dict[str, Any] = {
            "timestamp": record_time,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add structured context fields if present
        for field in ("event_type", "research_run_id", "experiment_id", "execution_id", "actor"):
            val = getattr(record, field, None)
            if val is not None:
                log_entry[field] = str(val.value) if hasattr(val, "value") else str(val)

        # Include correlation_id if set on record or in current context
        corr_id = getattr(record, "correlation_id", None) or get_correlation_id()
        if corr_id:
            log_entry["correlation_id"] = str(corr_id)

        # Include sanitized payload if present
        payload = getattr(record, "payload", None)
        if isinstance(payload, (dict, Mapping)):
            log_entry["payload"] = sanitize_value(payload)

        # Collect and sanitize any remaining non-standard extra attributes
        extras_raw: dict[str, Any] = {}
        for k, v in record.__dict__.items():
            if k not in self._RESERVED_ATTRS and not k.startswith("_"):
                extras_raw[k] = v
        if extras_raw:
            log_entry["extra"] = sanitize_value(extras_raw)

        # Handle exception tracebacks if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-friendly text formatter for local development console logging."""

    def format(self, record: logging.LogRecord) -> str:
        record_time = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        run_id = getattr(record, "research_run_id", None)
        actor = getattr(record, "actor", None)
        context = ""
        if run_id:
            context += f" [run:{run_id}]"
        if actor:
            context += f" [actor:{actor}]"

        msg = f"[{record_time}] [{record.levelname:<7}]{context} {record.getMessage()}"
        if record.exc_info:
            msg += f"\n{self.formatException(record.exc_info)}"
        return msg


class StructuredLogger(logging.LoggerAdapter):
    """LoggerAdapter providing convenient kwargs for REX structured fields."""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})

        # Merge adapter's default context if present
        if self.extra:
            for k, v in self.extra.items():
                extra.setdefault(k, v)

        # Extract structured keyword arguments into extra
        for key in (
            "event_type",
            "research_run_id",
            "experiment_id",
            "execution_id",
            "actor",
            "payload",
            "correlation_id",
        ):
            if key in kwargs:
                extra[key] = kwargs.pop(key)

        return msg, kwargs


def setup_logging(
    level: str | None = None,
    json_output: bool = True,
    stream: TextIO | None = None,
    logger_name: str = "rex",
) -> logging.Logger:
    """Configure and return the primary REX logger with structured formatting."""
    logger = logging.getLogger(logger_name)

    # Determine log level (from argument or REX settings)
    if level is None:
        try:
            level = get_settings().app.log_level
        except (ValueError, AttributeError, RuntimeError):
            level = "INFO"

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup is called multiple times
    logger.handlers.clear()

    target_stream = stream or sys.stderr
    handler = logging.StreamHandler(target_stream)
    handler.setLevel(numeric_level)

    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())

    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str = "rex", context: dict[str, Any] | None = None) -> StructuredLogger:
    """Get a StructuredLogger bound to a name and optional default context."""
    base_logger = logging.getLogger(name)
    # Ensure at least a NullHandler exists if no setup has been called
    if not base_logger.handlers and not logging.getLogger().handlers:
        base_logger.addHandler(logging.NullHandler())
    return StructuredLogger(base_logger, extra=context or {})


def log_event(
    event: ResearchEvent, logger: logging.Logger | StructuredLogger | None = None
) -> None:
    """Log a ResearchEvent at an appropriate log level with structured context."""
    target_logger = logger or get_logger("rex")

    msg = f"Event emitted: {event.event_type} by {event.actor} (id: {event.event_id})"
    extra_context = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "research_run_id": event.research_run_id,
        "experiment_id": event.experiment_id,
        "execution_id": event.execution_id,
        "payload": event.payload,
    }

    if event.event_type == EventType.ERROR:
        target_logger.error(msg, extra=extra_context)
    elif event.event_type == EventType.WARNING:
        target_logger.warning(msg, extra=extra_context)
    else:
        target_logger.info(msg, extra=extra_context)


class LoggingEventSink:
    """Event sink that routes emitted ResearchEvents directly to the structured logger."""

    def __init__(self, logger: logging.Logger | StructuredLogger | None = None) -> None:
        self._logger = logger or get_logger("rex")

    def emit(self, event: ResearchEvent) -> None:
        """Route event to structured logging."""
        log_event(event, self._logger)
