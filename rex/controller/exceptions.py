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


class MissingExperimentError(StateMachineError):
    """Raised when an operation references a non-existent experiment ID."""

    def __init__(self, experiment_id: str) -> None:
        self.experiment_id = experiment_id
        super().__init__(f"Experiment '{experiment_id}' not found.")


class InvalidExperimentStateTransitionError(StateMachineError):
    """Raised when an invalid experiment status transition is attempted."""

    def __init__(
        self,
        experiment_id: str,
        current_status: str,
        target_status: str,
        message: str | None = None,
    ) -> None:
        self.experiment_id = experiment_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            message
            or f"Cannot transition experiment '{experiment_id}' from '{current_status}' to '{target_status}'."
        )


class ExperimentExecutionExistsError(StateMachineError):
    """Raised when attempting to modify an experiment specification that already has executions."""

    def __init__(self, experiment_id: str, execution_count: int = 1) -> None:
        self.experiment_id = experiment_id
        self.execution_count = execution_count
        super().__init__(
            f"Cannot modify experiment '{experiment_id}' because {execution_count} execution(s) "
            "already exist. Scientific experiment specifications are immutable once execution begins."
        )


class MissingExecutionError(StateMachineError):
    """Raised when an operation references a non-existent execution ID."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"Execution '{execution_id}' not found.")


class MissingResultError(StateMachineError):
    """Raised when an operation references a non-existent result ID."""

    def __init__(self, result_id: str) -> None:
        self.result_id = result_id
        super().__init__(f"Result '{result_id}' not found.")


class MissingArtifactError(StateMachineError):
    """Raised when an operation references a non-existent artifact ID."""

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(f"Artifact '{artifact_id}' not found.")


class InvalidExecutionStateTransitionError(StateMachineError):
    """Raised when an invalid execution status transition is attempted."""

    def __init__(
        self,
        execution_id: str,
        current_status: str,
        target_status: str,
        message: str | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            message
            or f"Cannot transition execution '{execution_id}' from '{current_status}' to '{target_status}'."
        )
