"""Google Gemini LLM Provider Adapter (REX-012).

Implements the LLMProvider protocol for Google Gemini REST API endpoints
without vendor SDK locks.
"""

import time
from typing import Any

import httpx

from rex.config.settings import LLMSettings, get_settings
from rex.llm.models import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)


class GeminiLLMProvider:
    """REST adapter for Google Gemini Generative Language API."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gemini-1.5-pro",
        http_client: httpx.Client | None = None,
    ) -> None:
        settings: LLMSettings = get_settings().llm
        self._api_key = api_key or (
            settings.api_key.get_secret_value() if settings.api_key else None
        )
        self._default_model = (
            settings.model if settings.model not in ("mock-model", "gpt-4o") else default_model
        )
        self._client = http_client

    @property
    def provider_name(self) -> str:
        return "gemini"

    def generate(self, request: LLMRequest) -> LLMResponse:
        if not self._api_key:
            raise LLMConfigurationError(
                "Gemini API key is missing. Configure REX_LLM_API_KEY or pass api_key.",
                provider=self.provider_name,
            )

        model = request.model or self._default_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            f"?key={self._api_key}"
        )

        # Build Gemini request payload
        contents: list[dict[str, Any]] = [
            {"role": "user", "parts": [{"text": request.user_prompt}]}
        ]

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
            },
        }
        if request.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens

        start_time = time.monotonic()
        client = self._client or httpx.Client(timeout=request.timeout_seconds)
        close_client = self._client is None

        try:
            resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
            latency = time.monotonic() - start_time
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"Gemini request timed out after {request.timeout_seconds}s",
                provider=self.provider_name,
                model=model,
                timeout_seconds=request.timeout_seconds,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                f"Network communication failure contacting Gemini: {exc}",
                provider=self.provider_name,
                model=model,
                is_transient=True,
            ) from exc
        finally:
            if close_client:
                client.close()

        if resp.status_code in (401, 403):
            raise LLMConfigurationError(
                f"Gemini authentication failed (HTTP {resp.status_code}): {resp.text}",
                provider=self.provider_name,
                model=model,
            )
        if resp.status_code == 429:
            raise LLMProviderError(
                f"Gemini rate limit exceeded (HTTP 429): {resp.text}",
                provider=self.provider_name,
                model=model,
                status_code=429,
                is_transient=True,
            )
        if resp.status_code >= 500:
            raise LLMProviderError(
                f"Gemini server error (HTTP {resp.status_code}): {resp.text}",
                provider=self.provider_name,
                model=model,
                status_code=resp.status_code,
                is_transient=True,
            )
        if resp.status_code != 200:
            raise LLMProviderError(
                f"Gemini API call failed with status {resp.status_code}: {resp.text}",
                provider=self.provider_name,
                model=model,
                status_code=resp.status_code,
                is_transient=False,
            )

        try:
            body = resp.json()
            candidates = body.get("candidates", [])
            text = ""
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "")

            usage = body.get("usageMetadata", {})
            prompt_tokens = usage.get("promptTokenCount")
            completion_tokens = usage.get("candidatesTokenCount")
            total_tokens = usage.get("totalTokenCount")
        except Exception as exc:
            raise LLMProviderError(
                f"Failed to parse Gemini JSON response: {exc}",
                provider=self.provider_name,
                model=model,
            ) from exc

        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider_name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_seconds=latency,
            raw_metadata={"modelVersion": body.get("modelVersion")},
        )
