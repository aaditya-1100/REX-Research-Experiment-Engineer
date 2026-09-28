"""REX Controller Module (REX-005, REX-006).

Exports the state machine, hypothesis controller, domain exceptions, and lifecycle management helpers.
"""

from rex.controller.exceptions import (
    ActorAuthorizationError,
    InvalidTransitionError,
    MissingHypothesisError,
    MissingResearchRunError,
    StaleStateError,
    StateMachineError,
    TerminalStateError,
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
    "LEGAL_TRANSITIONS",
    "ActorAuthorizationError",
    "InvalidTransitionError",
    "MissingHypothesisError",
    "MissingResearchRunError",
    "ResearchStateMachine",
    "StaleStateError",
    "StateMachineError",
    "StateTransitionResult",
    "TerminalStateError",
    "create_hypothesis",
    "create_hypothesis_run",
    "create_research_run",
    "transition_run",
    "update_hypothesis_status",
]
