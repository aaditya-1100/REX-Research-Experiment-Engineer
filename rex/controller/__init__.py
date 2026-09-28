"""REX Controller Module (REX-005, REX-006, REX-007).

Exports the state machine, hypothesis controller, experiment controller,
domain exceptions, and lifecycle management helpers.
"""

from rex.controller.exceptions import (
    ActorAuthorizationError,
    ExperimentExecutionExistsError,
    InvalidExperimentStateTransitionError,
    InvalidTransitionError,
    MissingExperimentError,
    MissingHypothesisError,
    MissingResearchRunError,
    StaleStateError,
    StateMachineError,
    TerminalStateError,
)
from rex.controller.experiments import (
    EXPERIMENT_CREATOR_ACTORS,
    EXPERIMENT_STATUS_UPDATER_ACTORS,
    VALID_EXPERIMENT_TRANSITIONS,
    assert_experiment_mutable,
    create_experiment,
    create_experiment_run,
    update_experiment_status,
)
from rex.controller.hypotheses import (
    create_hypothesis,
    create_hypothesis_run,
    update_hypothesis_status,
)
from rex.controller.state_machine import (
    LEGAL_TRANSITIONS,
    ResearchStateMachine,
    StateTransitionResult,
    create_research_run,
    transition_run,
)

__all__ = [
    "EXPERIMENT_CREATOR_ACTORS",
    "EXPERIMENT_STATUS_UPDATER_ACTORS",
    "LEGAL_TRANSITIONS",
    "VALID_EXPERIMENT_TRANSITIONS",
    "ActorAuthorizationError",
    "ExperimentExecutionExistsError",
    "InvalidExperimentStateTransitionError",
    "InvalidTransitionError",
    "MissingExperimentError",
    "MissingHypothesisError",
    "MissingResearchRunError",
    "ResearchStateMachine",
    "StaleStateError",
    "StateMachineError",
    "StateTransitionResult",
    "TerminalStateError",
    "assert_experiment_mutable",
    "create_experiment",
    "create_experiment_run",
    "create_hypothesis",
    "create_hypothesis_run",
    "create_research_run",
    "transition_run",
    "update_experiment_status",
    "update_hypothesis_status",
]
