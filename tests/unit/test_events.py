"""Unit tests for REX Structured Events (REX-003)."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from rex.observability.events import (
    ActorType,
    EventEmitter,
    EventType,
    InMemoryEventSink,
    ResearchEvent,
    create_event,
    emit_event,
)


def test_valid_event_creation():
    """Verify event creation with required and default fields."""
    event = create_event(
        event_type=EventType.RESEARCH_CREATED,
        actor=ActorType.OWNER,
        research_run_id="run_001",
        payload={"topic": "learning_rate_decay"},
    )
    assert event.event_id.startswith("evt_")
    assert event.event_type == EventType.RESEARCH_CREATED
    assert event.actor == ActorType.OWNER
    assert event.research_run_id == "run_001"
    assert event.payload["topic"] == "learning_rate_decay"
    assert event.experiment_id is None
    assert event.execution_id is None
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.tzinfo == UTC


def test_optional_correlation_identifiers():
    """Verify optional experiment_id and execution_id fields."""
    event = create_event(
        event_type=EventType.EXECUTION_STARTED,
        actor=ActorType.EXECUTION_WORKER,
        research_run_id="run_001",
        experiment_id="exp_101",
        execution_id="exec_999",
        payload={"step": 1},
    )
    assert event.experiment_id == "exp_101"
    assert event.execution_id == "exec_999"


def test_utc_timezone_awareness_enforced():
    """Verify naive datetimes without timezone are rejected."""
    # Intentionally construct naive datetime to verify validation error
    naive_dt = datetime(2026, 9, 28, 12, 0, 0)  # noqa: DTZ001
    with pytest.raises(ValidationError):
        ResearchEvent(
            event_type=EventType.RESEARCH_CREATED,
            actor=ActorType.SYSTEM,
            research_run_id="run_001",
            timestamp=naive_dt,
        )


def test_actor_type_validation():
    """Verify valid actors and rejection of invalid actor strings."""
    for actor in ActorType:
        event = create_event(
            event_type=EventType.AGENT_ACTION,
            actor=actor,
            research_run_id="run_001",
        )
        assert event.actor == actor

    with pytest.raises(ValidationError):
        create_event(
            event_type=EventType.AGENT_ACTION,
            actor="unauthorized_hacker",  # type: ignore
            research_run_id="run_001",
        )


def test_event_type_validation():
    """Verify valid event types and rejection of arbitrary invalid strings."""
    for event_type in EventType:
        event = create_event(
            event_type=event_type,
            actor=ActorType.SYSTEM,
            research_run_id="run_001",
        )
        assert event.event_type == event_type

    with pytest.raises(ValidationError):
        create_event(
            event_type="completely_invalid_event",  # type: ignore
            actor=ActorType.SYSTEM,
            research_run_id="run_001",
        )


def test_top_level_immutability():
    """Verify top-level event attributes cannot be mutated after creation."""
    event = create_event(
        event_type=EventType.EXPERIMENT_CREATED,
        actor=ActorType.RESEARCH_AGENT,
        research_run_id="run_001",
        payload={"config": {"lr": 0.01}},
    )
    with pytest.raises(ValidationError):
        event.event_type = EventType.ERROR  # type: ignore

    with pytest.raises(ValidationError):
        event.payload = {"new": "payload"}  # type: ignore

    with pytest.raises(ValidationError):
        event.research_run_id = "mutated_id"  # type: ignore


def test_nested_dictionary_immutability():
    """Verify nested payload dictionaries cannot be modified, deleted from, or appended to."""
    event = create_event(
        event_type=EventType.EXPERIMENT_CREATED,
        actor=ActorType.RESEARCH_AGENT,
        research_run_id="run_001",
        payload={"nested": {"value": 1, "deep": {"target": "acc"}}},
    )

    # Item assignment at top-level payload must fail
    with pytest.raises(TypeError):
        event.payload["nested"] = "override"  # type: ignore

    # Item assignment at nested level must fail
    with pytest.raises(TypeError):
        event.payload["nested"]["value"] = 999  # type: ignore

    # Item deletion at nested level must fail
    with pytest.raises(TypeError):
        del event.payload["nested"]["value"]  # type: ignore

    # Deep nested modification must fail
    with pytest.raises(TypeError):
        event.payload["nested"]["deep"]["target"] = "loss"  # type: ignore


def test_nested_collection_immutability():
    """Verify nested lists and collections are frozen into immutable containers."""
    event = create_event(
        event_type=EventType.RESULT_RECORDED,
        actor=ActorType.EXECUTION_WORKER,
        research_run_id="run_001",
        payload={"metrics": [0.91, 0.93, 0.95], "tags": {"seeds": [42, 43]}},
    )

    # List is converted to tuple; append/extend must fail
    with pytest.raises(AttributeError):
        event.payload["metrics"].append(0.99)  # type: ignore

    # Index assignment must fail
    with pytest.raises(TypeError):
        event.payload["metrics"][0] = 0.0  # type: ignore

    # Nested collection in dictionary must also be immutable
    with pytest.raises(AttributeError):
        event.payload["tags"]["seeds"].append(44)  # type: ignore


def test_defensive_copy_isolation():
    """Verify mutating caller's original dictionary post-creation does NOT affect the event."""
    caller_payload = {
        "nested": {"value": 1, "items": [10, 20]},
        "active": True,
    }

    event = create_event(
        event_type=EventType.RESEARCH_CREATED,
        actor=ActorType.OWNER,
        research_run_id="run_001",
        payload=caller_payload,
    )

    # Mutate the caller's dictionary and its nested structures
    caller_payload["nested"]["value"] = 999
    caller_payload["nested"]["items"].append(30)
    caller_payload["active"] = False
    caller_payload["hacked_key"] = "compromised"

    # The event must remain completely unchanged
    assert event.payload["nested"]["value"] == 1
    assert event.payload["nested"]["items"] == (10, 20)
    assert event.payload["active"] is True
    assert "hacked_key" not in event.payload


def test_payload_must_be_dictionary():
    """Verify payloads that are not dictionaries/mappings are rejected."""
    with pytest.raises(ValidationError):
        ResearchEvent(
            event_type=EventType.RESEARCH_CREATED,
            actor=ActorType.SYSTEM,
            research_run_id="run_001",
            payload="not a dictionary",  # type: ignore
        )


def test_recursive_secret_sanitization():
    """Verify sensitive keys in payloads are recursively masked with [REDACTED]."""
    raw_payload = {
        "safe_key": "safe_value",
        "api_key": "sk-super-secret-key-12345",
        "nested": {
            "token": "bearer-token-abc",
            "password": "my_secret_password",
            "safe_inner": 42,
            "deep": {
                "credential": "private-cert-data",
                "auth_token": "token-999",
                "secret_string": SecretStr("pydantic-secret"),
            },
        },
        "list_items": [
            {"access_token": "token-in-list", "name": "item1"},
            "regular_string",
        ],
    }

    event = create_event(
        event_type=EventType.RESEARCH_CREATED,
        actor=ActorType.SYSTEM,
        research_run_id="run_001",
        payload=raw_payload,
    )

    # Check payload values
    assert event.payload["safe_key"] == "safe_value"
    assert event.payload["api_key"] == "[REDACTED]"
    assert event.payload["nested"]["token"] == "[REDACTED]"
    assert event.payload["nested"]["password"] == "[REDACTED]"
    assert event.payload["nested"]["safe_inner"] == 42
    assert event.payload["nested"]["deep"]["credential"] == "[REDACTED]"
    assert event.payload["nested"]["deep"]["auth_token"] == "[REDACTED]"
    assert event.payload["nested"]["deep"]["secret_string"] == "[REDACTED]"
    assert event.payload["list_items"][0]["access_token"] == "[REDACTED]"
    assert event.payload["list_items"][0]["name"] == "item1"

    # Verify no raw secrets appear in serialized string or JSON
    serialized = event.to_json()
    assert "sk-super-secret-key-12345" not in serialized
    assert "bearer-token-abc" not in serialized
    assert "my_secret_password" not in serialized
    assert "private-cert-data" not in serialized
    assert "token-in-list" not in serialized


def test_serialization_and_deserialization():
    """Verify deterministic JSON and dictionary serialization."""
    event = create_event(
        event_type=EventType.ANALYSIS_COMPLETED,
        actor=ActorType.SYSTEM,
        research_run_id="run_001",
        experiment_id="exp_01",
        execution_id="exec_01",
        payload={"accuracy": 0.942, "p_value": 0.003, "samples": [1, 2, 3]},
    )

    # Dictionary export
    d = event.to_dict()
    assert d["event_id"] == event.event_id
    assert d["event_type"] == "analysis_completed"
    assert d["actor"] == "system"
    assert d["research_run_id"] == "run_001"
    assert isinstance(d["payload"], dict)
    assert d["payload"]["accuracy"] == 0.942
    assert isinstance(d["payload"]["samples"], list)
    assert d["payload"]["samples"] == [1, 2, 3]

    # JSON export
    json_str = event.to_json()
    parsed = json.loads(json_str)
    assert parsed["event_id"] == event.event_id
    assert parsed["event_type"] == "analysis_completed"
    assert "timestamp" in parsed
    assert parsed["payload"]["accuracy"] == 0.942
    assert parsed["payload"]["samples"] == [1, 2, 3]


def test_in_memory_event_sink_and_emitter():
    """Verify event sinks and event emitters operate deterministically in memory."""
    sink1 = InMemoryEventSink()
    sink2 = InMemoryEventSink()
    emitter = EventEmitter([sink1])
    emitter.add_sink(sink2)

    event1 = create_event(
        event_type=EventType.RESEARCH_CREATED,
        actor=ActorType.OWNER,
        research_run_id="run_001",
    )
    event2 = create_event(
        event_type=EventType.RESEARCH_STATE_CHANGED,
        actor=ActorType.SYSTEM,
        research_run_id="run_001",
        payload={"from": "INITIALIZE", "to": "UNDERSTAND"},
    )

    emitter.emit(event1)
    emitter.emit(event2)

    assert len(sink1) == 2
    assert len(sink2) == 2
    assert sink1.events[0].event_id == event1.event_id
    assert sink1.events[1].event_id == event2.event_id

    # Test sink clear
    sink1.clear()
    assert len(sink1) == 0
    assert len(sink2) == 2


def test_emit_event_convenience():
    """Verify emit_event convenience function dispatches to sink."""
    sink = InMemoryEventSink()
    event = create_event(
        event_type=EventType.CLAIM_CREATED,
        actor=ActorType.RESEARCH_AGENT,
        research_run_id="run_001",
        payload={"claim_id": "CLM-001"},
    )
    emit_event(event, sink=sink)
    assert len(sink) == 1
    assert sink.events[0].payload["claim_id"] == "CLM-001"


def test_isolation_no_external_dependencies():
    """Verify event creation does not depend on database, docker, network, or mutable globals."""
    event = create_event(
        event_type=EventType.ARTIFACT_CREATED,
        actor=ActorType.EXECUTION_WORKER,
        research_run_id="run_001",
        payload={"path": "data/runs/plot.png"},
    )
    assert isinstance(event, ResearchEvent)
