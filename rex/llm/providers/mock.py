"""Mock LLM Provider for Deterministic Unit Testing (REX-012).

Provides fully programmable, deterministic LLM responses without external network calls
or credential requirements. Supports simulating latency, token usage, errors, and schema outputs.
"""

import json
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from rex.llm.models import (
    LLMRequest,
    LLMResponse,
)

DEFAULT_STRUCTURED_ACTIONS: dict[str, dict[str, Any]] = {
    "experiment_design": {
        "name": "emp_benchmark_eval",
        "description": "Empirical evaluation of proposed model against benchmark baseline",
        "method": "empirical_evaluation",
        "variables": {"learning_rate": 0.001},
        "controls": {"epochs": 10},
        "baseline": {"name": "standard_baseline", "value": 0.50},
        "datasets": [{"name": "standard_benchmark", "split": "test"}],
        "metrics": [{"name": "accuracy", "direction": "maximize"}],
        "parameters": {"batch_size": 32},
        "seeds": [42],
        "repetitions": 1,
        "analysis_methods": ["t_test"],
        "success_criteria": "accuracy > 0.80",
        "falsification_criteria": "accuracy <= 0.50",
    },
    "code_generation": {
        "entrypoint": "main.py",
        "source_files": {
            "main.py": (
                "import json\nimport sys\n\n"
                "print('REX generated experiment running...')\n"
                "results = [{'metric_name': 'accuracy', 'metric_value': 0.88, 'unit': 'ratio'}]\n"
                "with open('results.json', 'w') as f:\n"
                "    json.dump(results, f)\n"
                "print('Experiment complete.')\n"
            )
        },
        "command": ["python", "main.py"],
        "dependencies": [],
        "configuration": {"seed": 42},
        "expected_metrics": ["accuracy"],
    },
    "hypothesis_generation": {
        "hypotheses": [
            {
                "statement": "Increasing model depth improves benchmark accuracy by at least 5%",
                "rationale": "Empirical depth scaling in literature demonstrates improved representation capacity",
                "expected_direction": "increase",
                "falsification_condition": "accuracy improvement <= 0.0",
                "independent_variables": ["depth"],
                "dependent_variables": ["accuracy"],
                "baseline_reference": "standard_depth",
            }
        ]
    },
    "competing_hypothesis_generation": {
        "hypotheses": [
            {
                "statement": "Cosine annealing learning rate schedule improves test accuracy by at least 1.5% compared to constant LR on CIFAR-10.",
                "rationale": "Gradual temperature decay enables deeper convergence in parameter loss landscapes.",
                "expected_direction": "increase",
                "falsification_condition": "Test accuracy fails to exceed constant LR baseline by >= 1.5% with p < 0.05.",
                "independent_variables": ["lr_schedule"],
                "dependent_variables": ["accuracy"],
                "baseline_reference": "constant_learning_rate",
                "competing_hypothesis": "Observed gains are attributable to extended training iterations rather than schedule curvature.",
            },
            {
                "statement": "Observed gains are attributable to extended training iterations rather than schedule curvature.",
                "rationale": "More total gradient update steps permit greater loss descent regardless of schedule.",
                "expected_direction": "increase",
                "falsification_condition": "Test accuracy is identical when total update steps are matched between schedules.",
                "independent_variables": ["total_gradient_steps"],
                "dependent_variables": ["accuracy"],
                "baseline_reference": "matched_steps_baseline",
                "competing_hypothesis": "Cosine annealing schedule dynamics provide intrinsic optimization benefits.",
            },
        ]
    },
    "criticism": {
        "decision": "COMPLETE",
        "overall_score": 0.92,
        "critique": "Evidence is fully verified and hypothesis criteria met with high statistical significance.",
        "methodological_soundness": 0.95,
        "reproducibility_assessment": 0.90,
        "flaws_detected": [],
        "recommended_actions": [],
    },
}


class MockLLMProvider:
    """Configurable mock provider implementing LLMProvider protocol for testing."""

    def __init__(
        self,
        default_text: str = '{"status": "ok"}',
        responses: Mapping[str, str | BaseModel | Mapping[str, Any]] | None = None,
        exceptions: list[Exception] | None = None,
        input_tokens: int = 50,
        output_tokens: int = 50,
        cost_per_call: float = 0.001,
        latency_seconds: float = 0.0,
        default_latency: float | None = None,
        default_prompt_tokens: int | None = None,
        default_completion_tokens: int | None = None,
        cost_per_1k_tokens: float | None = None,
    ) -> None:
        self.default_text = default_text
        self.responses = dict(responses or {})
        self.response_queue: list[str | BaseModel | Mapping[str, Any]] = []
        self.exceptions = list(exceptions or [])
        self.input_tokens = (
            default_prompt_tokens if default_prompt_tokens is not None else input_tokens
        )
        self.output_tokens = (
            default_completion_tokens if default_completion_tokens is not None else output_tokens
        )
        if cost_per_1k_tokens is not None:
            self.cost_per_call = (
                (self.input_tokens + self.output_tokens) / 1000.0
            ) * cost_per_1k_tokens
        else:
            self.cost_per_call = cost_per_call
        self.latency_seconds = default_latency if default_latency is not None else latency_seconds
        self.recorded_requests: list[LLMRequest] = []
        self.history: list[tuple[LLMRequest, LLMResponse | None]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def call_count(self) -> int:
        return len(self.recorded_requests)

    def set_response(self, key: str, value: str | BaseModel | Mapping[str, Any]) -> None:
        """Register a canned response keyed by agent name, action, or prompt substring."""
        self.responses[key] = value

    def enqueue_response(self, value: str | BaseModel | Mapping[str, Any]) -> None:
        """Enqueue a canned response to be returned by next call (FIFO)."""
        self.response_queue.append(value)

    def queue_exception(self, exc: Exception) -> None:
        """Queue an exception to be raised on the next generate() call."""
        self.exceptions.append(exc)

    def enqueue_error(self, exc: Exception) -> None:
        """Alias for queue_exception."""
        self.queue_exception(exc)

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.recorded_requests.append(request)

        if self.latency_seconds > 0:
            time.sleep(self.latency_seconds)

        if self.exceptions:
            exc = self.exceptions.pop(0)
            self.history.append((request, None))
            raise exc

        # 1. Check response_queue first (FIFO)
        if self.response_queue:
            selected_content = self._format_value(self.response_queue.pop(0))
        # 2. Look for matching response by agent_name, action_name, or prompt substring
        elif request.agent_name and request.agent_name in self.responses:
            selected_content = self._format_value(self.responses[request.agent_name])
        elif request.action_name and request.action_name in self.responses:
            selected_content = self._format_value(self.responses[request.action_name])
        elif request.action_name and request.action_name in DEFAULT_STRUCTURED_ACTIONS:
            selected_content = self._format_value(DEFAULT_STRUCTURED_ACTIONS[request.action_name])
        else:
            selected_content = self.default_text
            for key, val in self.responses.items():
                if key in request.user_prompt or (
                    request.system_prompt and key in request.system_prompt
                ):
                    selected_content = self._format_value(val)
                    break

        total = self.input_tokens + self.output_tokens
        resp = LLMResponse(
            text=selected_content,
            model=request.model or "mock-model",
            provider=self.provider_name,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=total,
            estimated_cost=self.cost_per_call,
            latency_seconds=self.latency_seconds,
            request_id=f"mock_req_{len(self.recorded_requests)}",
            raw_metadata={"mock": True, "call_index": len(self.recorded_requests)},
        )
        self.history.append((request, resp))
        return resp

    def _format_value(self, val: str | BaseModel | Mapping[str, Any]) -> str:
        if isinstance(val, str):
            return val
        if isinstance(val, BaseModel):
            return val.model_dump_json(indent=2)
        if isinstance(val, Mapping):
            return json.dumps(val, indent=2)
        return str(val)
