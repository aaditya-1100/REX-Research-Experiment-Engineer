"""OpenAI Compatible LLM Provider Adapter (REX-012).

Implements the LLMProvider protocol for OpenAI and OpenAI-compatible endpoints
(including local vLLM/Ollama and Groq) using standard HTTP without vendor SDK locks.
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


class OpenAILLMProvider:
    """REST adapter for OpenAI and OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4o",
        http_client: httpx.Client | None = None,
    ) -> None:
        settings: LLMSettings = get_settings().llm
        self._api_key = api_key or (
            settings.api_key.get_secret_value() if settings.api_key else None
        )
        self._api_base = (api_base or settings.api_base or "https://api.openai.com/v1").rstrip("/")
        self._default_model = settings.model if settings.model != "mock-model" else default_model
        self._client = http_client

    @property
    def provider_name(self) -> str:
        return "openai"

    def generate(self, request: LLMRequest) -> LLMResponse:
        if (
            not self._api_key
            and "localhost" not in self._api_base
            and "127.0.0.1" not in self._api_base
        ):
            raise LLMConfigurationError(
                "OpenAI API key is missing. Configure REX_LLM_API_KEY or inject api_key.",
                provider=self.provider_name,
            )

        model = request.model or self._default_model
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        url = f"{self._api_base}/chat/completions"
        start_time = time.monotonic()

        client = self._client or httpx.Client(timeout=request.timeout_seconds)
        close_client = self._client is None

        try:
            resp = client.post(url, json=payload, headers=headers)
            latency = time.monotonic() - start_time
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"OpenAI request timed out after {request.timeout_seconds}s",
                provider=self.provider_name,
                model=model,
                timeout_seconds=request.timeout_seconds,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                f"Network communication failure contacting OpenAI: {exc}",
                provider=self.provider_name,
                model=model,
                is_transient=True,
            ) from exc
        finally:
            if close_client:
                client.close()

        if resp.status_code == 401 or resp.status_code == 403:
            raise LLMConfigurationError(
                f"OpenAI authentication failed (HTTP {resp.status_code}): {resp.text}",
                provider=self.provider_name,
                model=model,
            )
        if resp.status_code == 429:
            raise LLMProviderError(
                f"OpenAI rate limit exceeded (HTTP 429): {resp.text}",
                provider=self.provider_name,
                model=model,
                status_code=429,
                is_transient=True,
            )
        if resp.status_code >= 500:
            raise LLMProviderError(
                f"OpenAI server error (HTTP {resp.status_code}): {resp.text}",
                provider=self.provider_name,
                model=model,
                status_code=resp.status_code,
                is_transient=True,
            )
        if resp.status_code != 200:
            raise LLMProviderError(
                f"OpenAI API call failed with status {resp.status_code}: {resp.text}",
                provider=self.provider_name,
                model=model,
                status_code=resp.status_code,
                is_transient=False,
            )

        try:
            body = resp.json()
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
            usage = body.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            request_id = body.get("id")
        except Exception as exc:
            raise LLMProviderError(
                f"Failed to parse OpenAI JSON response: {exc}",
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
            request_id=request_id,
            raw_metadata={"system_fingerprint": body.get("system_fingerprint")},
        )
