"""LLM Provider Factory (REX-012).

Dispatches provider instances according to application configuration or explicit selection.
"""

from rex.config.settings import LLMSettings, get_settings
from rex.llm.base import LLMProvider
from rex.llm.models import LLMUnsupportedProviderError
from rex.llm.providers.gemini import GeminiLLMProvider
from rex.llm.providers.groq import GroqLLMProvider
from rex.llm.providers.mock import MockLLMProvider
from rex.llm.providers.openai import OpenAILLMProvider


def get_llm_provider(
    provider_name: str | None = None,
    settings: LLMSettings | None = None,
) -> LLMProvider:
    """Instantiate and return the configured LLMProvider.

    Defaults to settings.llm.provider (default 'mock' in development/tests).
    """
    cfg = settings or get_settings().llm
    selected = (provider_name or cfg.provider).strip().lower()

    if selected in ("mock", "fake", "test"):
        return MockLLMProvider()
    if selected in ("openai", "vllm", "ollama"):
        return OpenAILLMProvider()
    if selected in ("gemini", "google"):
        return GeminiLLMProvider()
    if selected in ("groq",):
        return GroqLLMProvider()

    raise LLMUnsupportedProviderError(
        f"Unsupported or unrecognized LLM provider '{selected}'. "
        "Supported providers: mock, openai, gemini, groq.",
        provider=selected,
    )
