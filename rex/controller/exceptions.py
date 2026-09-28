"""REX State Machine Exceptions (REX-005).

Provides a focused domain exception hierarchy for deterministic state machine errors.
"""


class StateMachineError(Exception):
    """Base exception for all research lifecycle and state machine errors."""


class InvalidTransitionError(StateMachineError):
    """Raised when an illegal or redundant lifecycle transition is attempted."""

    def __init__(
        self,
        current_state: str,
        target_state: str,
        run_id: str | None = None,
        message: str | None = None,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        self.run_id = run_id
        super().__init__(
            message
            or f"Invalid transition from '{current_state}' to '{target_state}' for run '{run_id or 'unknown'}'."
        )


class TerminalStateError(InvalidTransitionError):
    """Raised when attempting to transition a research run from a terminal state."""

    def __init__(
        self,
        current_state: str,
        target_state: str,
        run_id: str | None = None,
    ) -> None:
        super().__init__(
            current_state=current_state,
            target_state=target_state,
            run_id=run_id,
            message=(
                f"Cannot transition run '{run_id or 'unknown'}' from terminal state "
                f"'{current_state}' to '{target_state}'. Terminal states cannot transition."
            ),
        )


class MissingResearchRunError(StateMachineError):
    """Raised when an operation references a non-existent research run ID."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Research run '{run_id}' not found.")


class StaleStateError(StateMachineError):
    """Raised when an optimistic concurrency conflict is detected."""

    def __init__(self, run_id: str, expected_state: str, actual_state: str) -> None:
        self.run_id = run_id
        self.expected_state = expected_state
        self.actual_state = actual_state
        super().__init__(
            f"Concurrency conflict for run '{run_id}': expected state '{expected_state}', "
            f"but current state is '{actual_state}'."
        )


class ActorAuthorizationError(StateMachineError):
    """Raised when an actor lacks authority to trigger a specific lifecycle transition."""

    def __init__(self, actor: str, current_state: str, target_state: str) -> None:
        self.actor = actor
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Actor '{actor}' is not authorized to transition research run from "
            f"'{current_state}' to '{target_state}'."
        )


class MissingHypothesisError(StateMachineError):
    """Raised when an operation references a non-existent hypothesis ID."""

    def __init__(self, hypothesis_id: str) -> None:
        self.hypothesis_id = hypothesis_id
        super().__init__(f"Hypothesis '{hypothesis_id}' not found.")
