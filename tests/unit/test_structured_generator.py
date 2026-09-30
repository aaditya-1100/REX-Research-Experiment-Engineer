"""Unit tests for StructuredGenerator, markdown extraction, and bounded repair loop (REX-012)."""

import pytest
from pydantic import BaseModel, Field

from rex.llm.exceptions import (
    LLMMalformedResponseError,
    LLMSchemaValidationError,
)
from rex.llm.models import LLMRequest
from rex.llm.providers.mock import MockLLMProvider
from rex.llm.structured import StructuredGenerator, strip_markdown_code_fences


class SampleOutputModel(BaseModel):
    name: str = Field(description="Name of experiment")
    iterations: int = Field(ge=1, description="Iteration count")
    tags: list[str] = Field(default_factory=list)


def test_strip_markdown_code_fences():
    """Verify code fence stripping across various formatting styles."""
    # 1. Plain JSON
    assert strip_markdown_code_fences('{"a": 1}') == '{"a": 1}'

    # 2. ```json ... ```
    wrapped_json = '```json\n{\n  "a": 1\n}\n```'
    assert strip_markdown_code_fences(wrapped_json) == '{\n  "a": 1\n}'

    # 3. Generic ``` ... ```
    generic_wrapped = '```\n{\n  "a": 1\n}\n```'
    assert strip_markdown_code_fences(generic_wrapped) == '{\n  "a": 1\n}'

    # 4. Text with surrounding chatter
    chatter = 'Here is the response:\n```json\n{"a": 1}\n```\nHope this helps!'
    assert strip_markdown_code_fences(chatter) == '{"a": 1}'


def test_generate_structured_success_direct():
    """Verify direct valid JSON parsing into Pydantic model."""
    provider = MockLLMProvider()
    provider.enqueue_response('{"name": "test_exp", "iterations": 10, "tags": ["a", "b"]}')

    gen = StructuredGenerator(provider)
    req = LLMRequest(user_prompt="Generate test exp")
    result, _response = gen.generate_structured(req, SampleOutputModel)

    assert isinstance(result, SampleOutputModel)
    assert result.name == "test_exp"
    assert result.iterations == 10
    assert result.tags == ["a", "b"]
    assert provider.call_count == 1


def test_generate_structured_success_markdown_wrapped():
    """Verify markdown-wrapped JSON is automatically cleaned and parsed."""
    provider = MockLLMProvider()
    provider.enqueue_response(
        '```json\n{\n  "name": "wrapped_exp",\n  "iterations": 5,\n  "tags": ["ml"]\n}\n```'
    )

    gen = StructuredGenerator(provider)
    req = LLMRequest(user_prompt="Generate wrapped exp")
    result, _ = gen.generate_structured(req, SampleOutputModel)

    assert result.name == "wrapped_exp"
    assert result.iterations == 5
    assert result.tags == ["ml"]


def test_generate_structured_recovers_via_bounded_repair():
    """Verify that a schema validation error is repaired on second attempt."""
    provider = MockLLMProvider()
    # 1st response: iterations is 0 (violates ge=1 validation)
    provider.enqueue_response('{"name": "bad_exp", "iterations": 0, "tags": []}')
    # 2nd response: corrected iterations=1
    provider.enqueue_response('{"name": "bad_exp", "iterations": 1, "tags": []}')

    gen = StructuredGenerator(provider)
    req = LLMRequest(user_prompt="Generate valid exp")
    result, _ = gen.generate_structured(req, SampleOutputModel, max_repair_attempts=1)

    assert result.name == "bad_exp"
    assert result.iterations == 1
    assert provider.call_count == 2
    # Verify the repair prompt contained validation error detail
    repair_req = provider.history[1][0]
    assert "failed validation for the schema" in repair_req.user_prompt
    assert "greater than or equal to 1" in repair_req.user_prompt


def test_generate_structured_fails_when_repair_exhausted():
    """Verify exception raised when validation fails after all repair attempts."""
    provider = MockLLMProvider()
    # Both attempts return invalid iterations=0
    provider.enqueue_response('{"name": "bad_exp", "iterations": 0, "tags": []}')
    provider.enqueue_response('{"name": "still_bad", "iterations": -5, "tags": []}')

    gen = StructuredGenerator(provider)
    req = LLMRequest(user_prompt="Generate valid exp")
    with pytest.raises(LLMSchemaValidationError, match="Failed to validate"):
        gen.generate_structured(req, SampleOutputModel, max_repair_attempts=1)

    assert provider.call_count == 2


def test_generate_structured_malformed_json_fails_cleanly():
    """Verify unparseable non-JSON response raises malformed response error."""
    provider = MockLLMProvider()
    provider.enqueue_response("Definitely not JSON at all.")
    provider.enqueue_response("Still not JSON.")

    gen = StructuredGenerator(provider)
    req = LLMRequest(user_prompt="Give me JSON")
    with pytest.raises(LLMMalformedResponseError, match="Failed to parse JSON"):
        gen.generate_structured(req, SampleOutputModel, max_repair_attempts=1)
