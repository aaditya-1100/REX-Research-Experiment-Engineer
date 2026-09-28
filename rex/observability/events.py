"""REX Structured Events Model (REX-003).

Provides strongly typed, deeply immutable research lifecycle events with timezone-aware
UTC timestamps, explicit actor attribution, recursive secret sanitization,
and deterministic serialization.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, field_validator


class EventType(StrEnum):
    """Explicit vocabulary for research lifecycle and operational events."""

    RESEARCH_CREATED = "research_created"
    RESEARCH_STATE_CHANGED = "research_state_changed"
    HYPOTHESIS_CREATED = "hypothesis_created"
    EXPERIMENT_CREATED = "experiment_created"
    EXPERIMENT_STATUS_CHANGED = "experiment_status_changed"
    EXECUTION_CREATED = "execution_created"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    RESULT_RECORDED = "result_recorded"
    ANALYSIS_COMPLETED = "analysis_completed"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    CLAIM_CREATED = "claim_created"
    EVIDENCE_LINKED = "evidence_linked"
    ARTIFACT_CREATED = "artifact_created"
    BUDGET_EXCEEDED = "budget_exceeded"
    AGENT_ACTION = "agent_action"
    WARNING = "warning"
    ERROR = "error"


class ActorType(StrEnum):
    """Explicit vocabulary for actors triggering research actions."""

    OWNER = "owner"
    RESEARCH_AGENT = "research_agent"
    EXECUTION_WORKER = "execution_worker"
    VERIFIER = "verifier"
    SYSTEM = "system"


# Sensitive key patterns for recursive secret sanitization
SENSITIVE_KEY_PATTERNS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "auth_token",
    "authorization",
    "password",
    "passwd",
    "secret",
    "credential",
    "credentials",
    "private_key",
}


def sanitize_value(val: Any) -> Any:
    """Recursively sanitize an object, masking sensitive keys and SecretStr instances."""
    if isinstance(val, SecretStr):
        return "[REDACTED]"
    if isinstance(val, (dict, Mapping)):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k).lower().replace("-", "_")
            if any(pattern in k_str for pattern in SENSITIVE_KEY_PATTERNS):
                sanitized_dict[str(k)] = "[REDACTED]"
            else:
                sanitized_dict[str(k)] = sanitize_value(v)
        return sanitized_dict
    if isinstance(val, (list, tuple)):
        return [sanitize_value(item) for item in val]
    if isinstance(val, (set, frozenset)):
        return [sanitize_value(item) for item in sorted(val, key=str)]
    # Ensure JSON-primitive types
    if isinstance(val, (str, int, float, bool)) or val is None:
        return val
    # Fallback to string representation for non-primitive types if not serializable
    return str(val)


def freeze_value(val: Any) -> Any:
    """Recursively freeze a value into immutable containers (MappingProxyType, tuple, frozenset).

    Defensively copies caller-supplied dictionaries and collections so mutations on
    the original objects have zero effect on the frozen representation.
    """
    if isinstance(val, (dict, Mapping)):
        return MappingProxyType({str(k): freeze_value(v) for k, v in val.items()})
    if isinstance(val, (list, tuple)):
        return tuple(freeze_value(item) for item in val)
    if isinstance(val, (set, frozenset)):
        return frozenset(freeze_value(item) for item in val)
    return val


def unfreeze_value(val: Any) -> Any:
    """Recursively convert immutable containers back to standard JSON-compatible Python dicts and lists."""
    if isinstance(val, (dict, Mapping)):
        return {str(k): unfreeze_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set, frozenset)):
        return [unfreeze_value(item) for item in val]
    return val


class ResearchEvent(BaseModel):
    """Immutable, strongly typed event representing a historical research lifecycle fact.

    Both the top-level event attributes and the nested payload structures (mappings,
    tuples) are deeply immutable, preventing mutation of historical research records.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,
        arbitrary_types_allowed=True,
    )

    event_id: str = Field(
        default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}",
        description="Unique event identifier",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timezone-aware UTC timestamp of event occurrence",
    )
    event_type: EventType = Field(description="Explicit event type")
    actor: ActorType = Field(description="Actor responsible for the event")
    research_run_id: str = Field(description="Associated research run ID")
    experiment_id: str | None = Field(default=None, description="Optional associated experiment ID")
    execution_id: str | None = Field(
        default=None, description="Optional associated execution run ID"
    )
    payload: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Structured, sanitized, deeply immutable JSON-serializable event payload",
    )

    @field_validator("timestamp")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware (UTC)")
        return v.astimezone(UTC)

    @field_validator("payload", mode="after")
    @classmethod
    def _validate_and_sanitize_payload(cls, v: Mapping[str, Any]) -> Mapping[str, Any]:
        sanitized = sanitize_value(v)
        return freeze_value(sanitized)

    @field_serializer("payload")
    def _serialize_payload(self, v: Mapping[str, Any]) -> dict[str, Any]:
        """Serialize payload into standard JSON-compatible Python dict."""
        return unfreeze_value(v)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to a standard JSON-compatible dictionary."""
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Convert event to a deterministic JSON string."""
        return self.model_dump_json()


def create_event(
    event_type: EventType | str,
    actor: ActorType | str,
    research_run_id: str,
    payload: dict[str, Any] | None = None,
    experiment_id: str | None = None,
    execution_id: str | None = None,
    event_id: str | None = None,
    timestamp: datetime | None = None,
) -> ResearchEvent:
    """Factory function for creating validated, deeply immutable ResearchEvents."""
    kwargs: dict[str, Any] = {
        "event_type": event_type,
        "actor": actor,
        "research_run_id": research_run_id,
        "payload": payload or {},
        "experiment_id": experiment_id,
        "execution_id": execution_id,
    }
    if event_id is not None:
        kwargs["event_id"] = event_id
    if timestamp is not None:
        kwargs["timestamp"] = timestamp

    return ResearchEvent(**kwargs)


class EventSink(Protocol):
    """Protocol for event consumers/sinks."""

    def emit(self, event: ResearchEvent) -> None:
        """Emit or record an event."""
        ...


class InMemoryEventSink:
    """In-memory event sink for unit testing, inspection, and local workflows."""

    def __init__(self) -> None:
        self._events: list[ResearchEvent] = []

    def emit(self, event: ResearchEvent) -> None:
        """Record event in memory."""
        self._events.append(event)

    @property
    def events(self) -> list[ResearchEvent]:
        """Return shallow copy of recorded events."""
        return list(self._events)

    def clear(self) -> None:
        """Clear recorded events."""
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)


class EventEmitter:
    """Synchronous event dispatcher supporting multiple sinks without background queues."""

    def __init__(self, sinks: list[EventSink] | None = None) -> None:
        self._sinks: list[EventSink] = list(sinks) if sinks else []

    def add_sink(self, sink: EventSink) -> None:
        """Register an event sink."""
        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_sink(self, sink: EventSink) -> None:
        """Unregister an event sink."""
        if sink in self._sinks:
            self._sinks.remove(sink)

    def emit(self, event: ResearchEvent) -> None:
        """Synchronously dispatch event to all registered sinks."""
        for sink in self._sinks:
            sink.emit(event)


def emit_event(event: ResearchEvent, sink: EventSink | None = None) -> None:
    """Convenience function to emit an event to a specified sink or the default logger sink."""
    if sink is not None:
        sink.emit(event)
    else:
        # Default sink is the structured logger sink
        from rex.observability.logging import LoggingEventSink

        LoggingEventSink().emit(event)
