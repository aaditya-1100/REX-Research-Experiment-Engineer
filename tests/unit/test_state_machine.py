"""Unit tests for ResearchStateMachine transition rules, actors, and terminal states (REX-005)."""

import pytest

from rex.controller.exceptions import (
    ActorAuthorizationError,
    InvalidTransitionError,
    TerminalStateError,
)
from rex.controller.state_machine import (
    LEGAL_TRANSITIONS,
    ResearchStateMachine,
)
from rex.domain.models import TERMINAL_STATES, ResearchState
from rex.observability.events import ActorType


def test_complete_linear_happy_path_transitions():
    """Verify the full sequential lifecycle progression from INITIALIZE to COMPLETE."""
    linear_path = [
        (ResearchState.INITIALIZE, ResearchState.UNDERSTAND),
        (ResearchState.UNDERSTAND, ResearchState.LITERATURE),
        (ResearchState.LITERATURE, ResearchState.HYPOTHESES),
        (ResearchState.HYPOTHESES, ResearchState.DESIGN),
        (ResearchState.DESIGN, ResearchState.IMPLEMENT),
        (ResearchState.IMPLEMENT, ResearchState.EXECUTE),
        (ResearchState.EXECUTE, ResearchState.VERIFY),
        (ResearchState.VERIFY, ResearchState.ANALYZE),
        (ResearchState.ANALYZE, ResearchState.CRITIQUE),
        (ResearchState.CRITIQUE, ResearchState.DECIDE),
        (ResearchState.DECIDE, ResearchState.COMPLETE),
    ]

    for current, target in linear_path:
        assert ResearchStateMachine.can_transition(current, target)
        ResearchStateMachine.validate_transition(current, target)


def test_decide_branching_transitions():
    """Explicitly test all DECIDE branch transitions (REFINE, REPLICATE, PIVOT, STOP)."""
    # 1. DECIDE -> REFINE -> DESIGN
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.REFINE)
    ResearchStateMachine.validate_transition(ResearchState.DECIDE, ResearchState.REFINE)
    assert ResearchStateMachine.can_transition(ResearchState.REFINE, ResearchState.DESIGN)
    ResearchStateMachine.validate_transition(ResearchState.REFINE, ResearchState.DESIGN)

    # 2. DECIDE -> REPLICATE -> DESIGN
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.REPLICATE)
    ResearchStateMachine.validate_transition(ResearchState.DECIDE, ResearchState.REPLICATE)
    assert ResearchStateMachine.can_transition(ResearchState.REPLICATE, ResearchState.DESIGN)
    ResearchStateMachine.validate_transition(ResearchState.REPLICATE, ResearchState.DESIGN)

    # 3. DECIDE -> PIVOT -> HYPOTHESES / UNDERSTAND
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.PIVOT)
    ResearchStateMachine.validate_transition(ResearchState.DECIDE, ResearchState.PIVOT)
    assert ResearchStateMachine.can_transition(ResearchState.PIVOT, ResearchState.HYPOTHESES)
    assert ResearchStateMachine.can_transition(ResearchState.PIVOT, ResearchState.UNDERSTAND)
    ResearchStateMachine.validate_transition(ResearchState.PIVOT, ResearchState.HYPOTHESES)
    ResearchStateMachine.validate_transition(ResearchState.PIVOT, ResearchState.UNDERSTAND)

    # 4. DECIDE -> STOP (terminal)
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.STOP)
    ResearchStateMachine.validate_transition(ResearchState.DECIDE, ResearchState.STOP)

    # 5. Direct loops from DECIDE
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.DESIGN)
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.HYPOTHESES)
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.COMPLETE)
    assert ResearchStateMachine.can_transition(ResearchState.DECIDE, ResearchState.FAILED)


def test_verify_retry_and_failure_branches():
    """Verify that VERIFY state can retry code implementation or fail on unrecoverable crash."""
    # Run outputs verified -> proceed to analysis
    assert ResearchStateMachine.can_transition(ResearchState.VERIFY, ResearchState.ANALYZE)
    # Execution error / missing output schema -> retry code implementation
    assert ResearchStateMachine.can_transition(ResearchState.VERIFY, ResearchState.IMPLEMENT)
    # Fatal verification error -> failed
    assert ResearchStateMachine.can_transition(ResearchState.VERIFY, ResearchState.FAILED)


def test_active_states_can_transition_to_failed():
    """Verify that all non-terminal states can transition to FAILED upon fatal failure or budget exhaustion."""
    non_terminal_states = [s for s in ResearchState if s not in TERMINAL_STATES]

    for state in non_terminal_states:
        assert ResearchState.FAILED in LEGAL_TRANSITIONS[state], (
            f"State {state} cannot transition to FAILED"
        )
        assert ResearchStateMachine.can_transition(state, ResearchState.FAILED)
        ResearchStateMachine.validate_transition(state, ResearchState.FAILED)


def test_invalid_transitions_rejected():
    """Verify representative illegal transitions across all major lifecycle stages."""
    invalid_cases = [
        (ResearchState.INITIALIZE, ResearchState.EXECUTE),  # Illegal skip
        (ResearchState.EXECUTE, ResearchState.HYPOTHESES),  # Illegal rewind
        (ResearchState.ANALYZE, ResearchState.INITIALIZE),  # Illegal reset
        (ResearchState.UNDERSTAND, ResearchState.DESIGN),  # Skipping literature/hypotheses
        (ResearchState.DESIGN, ResearchState.VERIFY),  # Skipping implement/execute
        (ResearchState.CRITIQUE, ResearchState.IMPLEMENT),  # Skipping decide
        (ResearchState.IMPLEMENT, ResearchState.ANALYZE),  # Skipping execution
    ]

    for current, target in invalid_cases:
        assert not ResearchStateMachine.can_transition(current, target)
        with pytest.raises(InvalidTransitionError) as exc_info:
            ResearchStateMachine.validate_transition(current, target, run_id="run_test_invalid")
        assert exc_info.value.current_state == current.value
        assert exc_info.value.target_state == target.value


def test_terminal_states_cannot_transition():
    """Verify that attempting to transition out of any terminal state raises TerminalStateError."""
    for terminal in TERMINAL_STATES:
        for target in ResearchState:
            assert not ResearchStateMachine.can_transition(terminal, target)
            with pytest.raises(TerminalStateError) as exc_info:
                ResearchStateMachine.validate_transition(terminal, target, run_id="run_test_term")
            assert exc_info.value.current_state == terminal.value
            assert exc_info.value.target_state == target.value
            assert exc_info.value.run_id == "run_test_term"


def test_redundant_transitions_rejected_for_idempotency():
    """Verify that attempting to transition to the current state is rejected."""
    non_terminal_states = [s for s in ResearchState if s not in TERMINAL_STATES]
    for state in non_terminal_states:
        assert not ResearchStateMachine.can_transition(state, state)
        with pytest.raises(InvalidTransitionError, match="Redundant transitions are rejected"):
            ResearchStateMachine.validate_transition(state, state, run_id="run_idempotency")

    for terminal in TERMINAL_STATES:
        assert not ResearchStateMachine.can_transition(terminal, terminal)
        with pytest.raises(TerminalStateError):
            ResearchStateMachine.validate_transition(terminal, terminal, run_id="run_idempotency")


def test_actor_authorization_enforcement():
    """Verify least privilege actor boundaries match security model."""
    # RESEARCH_AGENT permissions
    # Authorized for reasoning transitions
    assert ResearchStateMachine.can_transition(
        ResearchState.INITIALIZE,
        ResearchState.UNDERSTAND,
        actor=ActorType.RESEARCH_AGENT,
    )
    assert ResearchStateMachine.can_transition(
        ResearchState.DECIDE,
        ResearchState.REFINE,
        actor=ActorType.RESEARCH_AGENT,
    )
    # Unauthorized for worker execution transition
    assert not ResearchStateMachine.can_transition(
        ResearchState.IMPLEMENT,
        ResearchState.EXECUTE,
        actor=ActorType.RESEARCH_AGENT,
    )
    with pytest.raises(ActorAuthorizationError):
        ResearchStateMachine.validate_transition(
            ResearchState.IMPLEMENT,
            ResearchState.EXECUTE,
            actor=ActorType.RESEARCH_AGENT,
        )

    # EXECUTION_WORKER permissions
    # Authorized for execution launch and verify handover
    assert ResearchStateMachine.can_transition(
        ResearchState.IMPLEMENT,
        ResearchState.EXECUTE,
        actor=ActorType.EXECUTION_WORKER,
    )
    assert ResearchStateMachine.can_transition(
        ResearchState.EXECUTE,
        ResearchState.VERIFY,
        actor=ActorType.EXECUTION_WORKER,
    )
    # Unauthorized for analysis critique or decision
    assert not ResearchStateMachine.can_transition(
        ResearchState.ANALYZE,
        ResearchState.CRITIQUE,
        actor=ActorType.EXECUTION_WORKER,
    )
    with pytest.raises(ActorAuthorizationError):
        ResearchStateMachine.validate_transition(
            ResearchState.ANALYZE,
            ResearchState.CRITIQUE,
            actor=ActorType.EXECUTION_WORKER,
        )

    # VERIFIER permissions
    # Authorized for verification handover
    assert ResearchStateMachine.can_transition(
        ResearchState.VERIFY,
        ResearchState.ANALYZE,
        actor=ActorType.VERIFIER,
    )
    assert ResearchStateMachine.can_transition(
        ResearchState.VERIFY,
        ResearchState.IMPLEMENT,
        actor=ActorType.VERIFIER,
    )
    # Unauthorized for experiment design
    assert not ResearchStateMachine.can_transition(
        ResearchState.DESIGN,
        ResearchState.IMPLEMENT,
        actor=ActorType.VERIFIER,
    )
    with pytest.raises(ActorAuthorizationError):
        ResearchStateMachine.validate_transition(
            ResearchState.DESIGN,
            ResearchState.IMPLEMENT,
            actor=ActorType.VERIFIER,
        )

    # OWNER and SYSTEM possess global administrative authority
    for actor in (ActorType.OWNER, ActorType.SYSTEM):
        assert ResearchStateMachine.can_transition(
            ResearchState.INITIALIZE, ResearchState.UNDERSTAND, actor=actor
        )
        assert ResearchStateMachine.can_transition(
            ResearchState.IMPLEMENT, ResearchState.EXECUTE, actor=actor
        )
        assert ResearchStateMachine.can_transition(
            ResearchState.VERIFY, ResearchState.ANALYZE, actor=actor
        )
        assert ResearchStateMachine.can_transition(
            ResearchState.DECIDE, ResearchState.COMPLETE, actor=actor
        )
