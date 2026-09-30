"""Base Agent Infrastructure for Research Reasoning (REX-013, REX-014, REX-015, REX-016).

Provides shared reasoning infrastructure, budget verification hooks, structured generation,
and audit trail recording across all specialized scientific agents.
"""

import logging
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from rex.llm.accounting import check_llm_budget, record_llm_usage_event
from rex.llm.base import LLMProvider
from rex.llm.models import LLMRequest, LLMResponse
from rex.llm.providers.factory import get_llm_provider
from rex.llm.structured import StructuredGenerator
from rex.observability.events import ActorType, EventSink

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    """Foundational class for reasoning-plane scientific agents."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.provider = provider or get_llm_provider()
        self.event_sink = event_sink
        self.structured_generator = StructuredGenerator(self.provider)

    @property
    def agent_name(self) -> str:
        return self.__class__.__name__

    def _generate_structured(
        self,
        request: LLMRequest,
        response_model: type[T],
        session: Session | None = None,
        actor: ActorType = ActorType.RESEARCH_AGENT,
        max_repair_attempts: int = 1,
    ) -> tuple[T, LLMResponse]:
        """Execute a structured generation request with pre-flight budget enforcement and telemetry logging."""
        # 1. Enforce LLM budget if session and run_id provided
        if session is not None and request.research_run_id is not None:
            check_llm_budget(
                session=session,
                research_run_id=request.research_run_id,
                actor=actor,
                event_sink=self.event_sink,
            )

        # 2. Tag request metadata
        tagged_request = request.model_copy(
            update={
                "agent_name": self.agent_name,
                "action_name": request.action_name or "generate",
            }
        )

        # 3. Perform generation and validation
        parsed_obj, response = self.structured_generator.generate_structured(
            request=tagged_request,
            response_model=response_model,
            max_repair_attempts=max_repair_attempts,
        )

        # 4. Record usage event in persistent audit log
        if session is not None and request.research_run_id is not None:
            record_llm_usage_event(
                session=session,
                request=tagged_request,
                response=response,
                actor=actor,
                event_sink=self.event_sink,
            )

        return parsed_obj, response
