"""Unit tests for ResearchRun domain model and lifecycle state vocabulary (REX-005)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rex.domain.models import (
    TERMINAL_STATES,
    ResearchRun,
    ResearchState,
)
from rex.persistence.models import ResearchRunModel


def test_all_lifecycle_states_exist_and_serialize():
    """Verify all 17 authoritative lifecycle states exist and serialize properly."""
    expected_states = {
        "INITIALIZE",
        "UNDERSTAND",
        "LITERATURE",
        "HYPOTHESES",
        "DESIGN",
        "IMPLEMENT",
        "EXECUTE",
        "VERIFY",
        "ANALYZE",
        "CRITIQUE",
        "DECIDE",
        "REFINE",
        "REPLICATE",
        "PIVOT",
        "STOP",
        "COMPLETE",
        "FAILED",
    }
    actual_states = {state.value for state in ResearchState}
    assert actual_states == expected_states

    # Type-safe enum equality and string conversion
    assert ResearchState.INITIALIZE == "INITIALIZE"
    assert str(ResearchState.INITIALIZE) == "INITIALIZE"
    assert ResearchState("DECIDE") == ResearchState.DECIDE


def test_terminal_states_definition():
    """Verify terminal states match authoritative specification."""
    expected_terminals = {
        ResearchState.COMPLETE,
        ResearchState.FAILED,
        ResearchState.STOP,
    }
    assert TERMINAL_STATES == expected_terminals


def test_research_run_creation_and_defaults():
    """Verify default values and valid creation of ResearchRun domain entity."""
    run = ResearchRun(
        research_question="Does gradient clipping reduce transformer gradient explosion?",
        title="Gradient Clipping Study",
        configuration={"clip_norm": 1.0},
        budget={"max_steps": 5000},
    )

    assert run.id.startswith("run_")
    assert run.title == "Gradient Clipping Study"
    assert run.research_question == (
        "Does gradient clipping reduce transformer gradient explosion?"
    )
    assert run.state == ResearchState.INITIALIZE
    assert run.version == 1
    assert not run.is_terminal
    assert run.created_at.tzinfo == UTC
    assert run.updated_at.tzinfo == UTC
    assert run.configuration["clip_norm"] == 1.0
    assert run.budget["max_steps"] == 5000


def test_research_question_validation():
    """Verify that empty or whitespace-only research questions are rejected."""
    with pytest.raises(ValueError, match="non-empty string"):
        ResearchRun(research_question="")

    with pytest.raises(ValueError, match="non-empty string"):
        ResearchRun(research_question="   \t\n  ")


def test_research_run_immutability():
    """Verify that ResearchRun domain objects are deeply frozen and immutable."""
    run = ResearchRun(research_question="Evaluating self-attention efficiency")

    with pytest.raises(ValidationError):
        run.state = ResearchState.UNDERSTAND  # type: ignore

    with pytest.raises(ValidationError):
        run.title = "New Title"  # type: ignore


def test_research_run_is_terminal_property():
    """Verify is_terminal property accurately reflects state."""
    active_run = ResearchRun(
        research_question="Active run",
        state=ResearchState.EXECUTE,
    )
    assert not active_run.is_terminal

    completed_run = ResearchRun(
        research_question="Completed run",
        state=ResearchState.COMPLETE,
    )
    assert completed_run.is_terminal

    failed_run = ResearchRun(
        research_question="Failed run",
        state=ResearchState.FAILED,
    )
    assert failed_run.is_terminal

    stopped_run = ResearchRun(
        research_question="Stopped run",
        state=ResearchState.STOP,
    )
    assert stopped_run.is_terminal


def test_research_run_persistence_roundtrip():
    """Verify bidirectional conversion between ResearchRun and ResearchRunModel."""
    now = datetime.now(UTC)
    domain_run = ResearchRun(
        id="run_test_roundtrip",
        title="Roundtrip Test",
        research_question="Testing domain to persistence model mapping",
        state=ResearchState.DESIGN,
        created_at=now,
        updated_at=now,
        version=3,
        configuration={"lr": 0.01},
        budget={"cost_limit": 10.0},
    )

    # Convert to SQLAlchemy model
    model = domain_run.to_persistence()
    assert isinstance(model, ResearchRunModel)
    assert model.id == "run_test_roundtrip"
    assert model.title == "Roundtrip Test"
    assert model.status == "DESIGN"
    assert model.configuration_json["lr"] == 0.01
    assert model.configuration_json["_version"] == 3
    assert model.budget_json["cost_limit"] == 10.0

    # Reconstruct from SQLAlchemy model
    reconstructed = ResearchRun.from_persistence(model)
    assert reconstructed.id == domain_run.id
    assert reconstructed.title == domain_run.title
    assert reconstructed.research_question == domain_run.research_question
    assert reconstructed.state == domain_run.state
    assert reconstructed.version == 3
    assert reconstructed.configuration["lr"] == 0.01
    assert reconstructed.budget["cost_limit"] == 10.0
