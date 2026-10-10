"""REX Observability Subsystem (REX-003).

Provides structured event models and machine-readable logging.
"""

from rex.observability.events import (
    ActorType,
    EventEmitter,
    EventSink,
    EventType,
    InMemoryEventSink,
    ResearchEvent,
    create_event,
    emit_event,
    sanitize_value,
)
from rex.observability.logging import (
    JsonFormatter,
    LoggingEventSink,
    StructuredLogger,
    TextFormatter,
    get_correlation_id,
    get_logger,
    log_event,
    set_correlation_id,
    setup_logging,
)

__all__ = [
    "ActorType",
    "EventEmitter",
    "EventSink",
    "EventType",
    "InMemoryEventSink",
    "JsonFormatter",
    "LoggingEventSink",
    "ResearchEvent",
    "StructuredLogger",
    "TextFormatter",
    "create_event",
    "emit_event",
    "get_correlation_id",
    "get_logger",
    "log_event",
    "sanitize_value",
    "set_correlation_id",
    "setup_logging",
]
