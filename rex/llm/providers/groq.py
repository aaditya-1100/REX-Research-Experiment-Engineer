"""Groq LLM Provider Adapter (REX-012).

Implements the LLMProvider protocol for Groq high-throughput endpoints
using the OpenAI-compatible REST specification.
"""

import httpx

from rex.config.settings import LLMSettings, get_settings
from rex.llm.providers.openai import OpenAILLMProvider


class GroqLLMProvider(OpenAILLMProvider):
    """REST adapter for Groq high-speed inference API."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "llama-3.3-70b-versatile",
        http_client: httpx.Client | None = None,
    ) -> None:
        settings: LLMSettings = get_settings().llm
        key = api_key or (settings.api_key.get_secret_value() if settings.api_key else None)
        base = settings.api_base or "https://api.groq.com/openai/v1"
        super().__init__(
            api_key=key,
            api_base=base,
            default_model=default_model,
            http_client=http_client,
        )

    @property
    def provider_name(self) -> str:
        return "groq"
