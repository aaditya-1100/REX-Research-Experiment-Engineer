"""REX Structured Output Parsing and Pydantic Validation (REX-012).

Provides reliable extraction of structured scientific data from LLM text responses,
with authoritative Pydantic validation, explicit error reporting, and bounded repair attempts.
"""

import json
import logging
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from rex.llm.base import LLMProvider
from rex.llm.models import (
    LLMMalformedResponseError,
    LLMRequest,
    LLMResponse,
    LLMSchemaValidationError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def extract_json_candidate(text: str) -> str:
    """Extract a candidate JSON substring from raw model output.

    Strips markdown code fences (```json ... ```) or extracts the outermost JSON block.
    """
    cleaned = text.strip()
    if not cleaned:
        raise LLMMalformedResponseError("LLM returned an empty response.")

    # 1. Check for markdown code fences with ```json ... ``` or ``` ... ```
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(fence_pattern, cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. Extract outermost JSON object { ... } or array [ ... ]
    start_brace = cleaned.find("{")
    start_bracket = cleaned.find("[")

    if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
        end_brace = cleaned.rfind("}")
        if end_brace != -1 and end_brace > start_brace:
            return cleaned[start_brace : end_brace + 1].strip()

    if start_bracket != -1:
        end_bracket = cleaned.rfind("]")
        if end_bracket != -1 and end_bracket > start_bracket:
            return cleaned[start_bracket : end_bracket + 1].strip()

    # Return raw text as fallback
    return cleaned


strip_markdown_code_fences = extract_json_candidate


class StructuredGenerator:
    """Orchestrates structured LLM generation with validation and bounded error correction."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def generate_structured(
        self,
        request: LLMRequest,
        response_model: type[T],
        max_repair_attempts: int = 1,
    ) -> tuple[T, LLMResponse]:
        """Generate, extract, validate, and return a Pydantic domain object.

        Fails deterministically on malformed output or schema violations.
        """
        # Inject schema instruction if not already present in prompt
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        instruction_suffix = (
            f"\n\nCRITICAL INSTRUCTION: You must respond ONLY with a valid JSON object "
            f"conforming strictly to this JSON Schema:\n{schema_json}\n"
            f"Do not include any conversational preamble or postscript."
        )

        effective_request = request.model_copy(
            update={
                "system_prompt": (request.system_prompt + instruction_suffix).strip(),
                "response_schema": response_model,
            }
        )

        response = self.provider.generate(effective_request)
        parsed_obj, errors = self._try_parse_and_validate(response.text, response_model)

        if parsed_obj is not None:
            updated_response = response.model_copy(update={"parsed": parsed_obj})
            return parsed_obj, updated_response

        # Bounded structured correction attempt
        if max_repair_attempts > 0:
            logger.warning(
                "Structured validation failed for %s. Attempting 1 bounded correction prompt. Errors: %s",
                response_model.__name__,
                errors,
            )
            repair_prompt = (
                f"Your previous response was rejected because it failed validation for "
                f"the schema '{response_model.__name__}':\n"
                f"Validation errors:\n"
                + "\n".join(f"- {e}" for e in errors)
                + f"\n\nYour previous response was:\n{response.text}\n\n"
                f"Please fix all errors and output ONLY the corrected, valid JSON object."
            )

            repair_request = effective_request.model_copy(
                update={"user_prompt": repair_prompt, "action_name": "structured_repair"}
            )
            repair_response = self.provider.generate(repair_request)
            repaired_obj, repair_errors = self._try_parse_and_validate(
                repair_response.text, response_model
            )

            if repaired_obj is not None:
                updated_response = repair_response.model_copy(update={"parsed": repaired_obj})
                return repaired_obj, updated_response
            errors = repair_errors

        # Fail deterministically with specific exception type
        if any("JSON parsing error" in e for e in errors):
            raise LLMMalformedResponseError(
                f"Failed to parse JSON from model response for {response_model.__name__}: {'; '.join(errors)}",
                provider=self.provider.provider_name,
                model=response.model,
            )

        raise LLMSchemaValidationError(
            f"Failed to validate model response against {response_model.__name__}: {'; '.join(errors)}",
            raw_text=response.text,
            validation_errors=errors,
            provider=self.provider.provider_name,
            model=response.model,
        )

    def _try_parse_and_validate(self, text: str, model_cls: type[T]) -> tuple[T | None, list[str]]:
        try:
            json_str = extract_json_candidate(text)
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return None, [f"JSON parsing error: {exc}"]

        if not isinstance(data, (dict, list)):
            return None, [f"Expected JSON object/array, got {type(data).__name__}"]

        try:
            if isinstance(data, dict):
                obj = model_cls.model_validate(data)
            else:
                # If root model or list expected
                obj = model_cls.model_validate(data)
            return obj, []
        except ValidationError as val_err:
            error_msgs = [f"{err['loc']}: {err['msg']}" for err in val_err.errors()]
            return None, error_msgs
        except (ValueError, TypeError) as exc:
            return None, [f"Model validation error: {exc}"]
