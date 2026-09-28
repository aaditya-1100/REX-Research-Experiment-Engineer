"""REX Controller Module (REX-005).

Exports the state machine, lifecycle transitions, domain exceptions, and run management helpers.
"""

from rex.controller.exceptions import (
    ActorAuthorizationError,
    InvalidTransitionError,
    MissingResearchRunError,
    StaleStateError,
    StateMachineError,
    TerminalStateError,
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
    "MissingResearchRunError",
    "ResearchStateMachine",
    "StaleStateError",
    "StateMachineError",
    "StateTransitionResult",
    "TerminalStateError",
    "create_research_run",
    "transition_run",
]
