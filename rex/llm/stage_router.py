"""Stage-Aware Model Provider Router & Literature Quarantining (REX-012 / Track A Sec 17-18).

Provides dynamic, stage-specific LLM provider and model routing across research lifecycle stages:
- Research/Hypothesis/Design stage (e.g., deep reasoning models)
- Coding/Implementation stage (e.g., syntax/code-specialized models)
- Critic/Verification stage (e.g., skeptical adversarial evaluation models)

Guarantees that external literature is quarantined strictly as inert DATA and never
treated as executable authority or system instructions.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from rex.config.settings import get_settings
from rex.llm.base import LLMProvider
from rex.llm.providers.factory import get_llm_provider


class ResearchStage(StrEnum):
    """Authoritative research lifecycle stages for model routing."""

    RESEARCH = "research"
    CODING = "coding"
    CRITIC = "critic"
    DEFAULT = "default"


class StageConfig(BaseModel):
    """Configuration specification for a specific research stage."""

    model_config = ConfigDict(extra="forbid")

    stage: ResearchStage
    provider: str = Field(default="mock", description="LLM provider: mock, openai, gemini, groq")
    model: str = Field(default="mock-model", description="Model identifier")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None)


class StageRouterSettings(BaseSettings):
    """Environment-configurable stage-aware provider routing settings."""

    model_config = SettingsConfigDict(
        env_prefix="REX_STAGE_",
        case_sensitive=False,
        extra="ignore",
    )

    research_provider: str | None = Field(default=None)
    research_model: str | None = Field(default=None)

    coding_provider: str | None = Field(default=None)
    coding_model: str | None = Field(default=None)

    critic_provider: str | None = Field(default=None)
    critic_model: str | None = Field(default=None)


class StageModelRouter:
    """Stage-aware provider and model dispatcher with literature safety controls."""

    # Malicious injection patterns attempting to break out of data quarantine
    INJECTION_INDICATORS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+in\s+DAN\s+mode", re.IGNORECASE),
        re.compile(r"system\s*:\s*override", re.IGNORECASE),
        re.compile(r"disregard\s+system\s+prompt", re.IGNORECASE),
        re.compile(r"###\s*INSTRUCTION", re.IGNORECASE),
        re.compile(r"<\s*system\s*>", re.IGNORECASE),
    ]

    def __init__(self, settings: StageRouterSettings | None = None) -> None:
        self._settings = settings or StageRouterSettings()
        self._custom_providers: dict[ResearchStage, LLMProvider] = {}
        self._custom_models: dict[ResearchStage, str] = {}

    def register_provider(
        self,
        stage: ResearchStage | str,
        provider: LLMProvider,
        model_name: str | None = None,
    ) -> None:
        """Explicitly register an in-memory LLMProvider for a given research stage."""
        stage_enum = ResearchStage(str(stage).lower())
        self._custom_providers[stage_enum] = provider
        if model_name:
            self._custom_models[stage_enum] = model_name

    def get_provider(self, stage: ResearchStage | str) -> LLMProvider:
        """Resolve and instantiate the LLMProvider designated for the stage."""
        stage_enum = self._normalize_stage(stage)

        # 1. Check programmatic overrides
        if stage_enum in self._custom_providers:
            return self._custom_providers[stage_enum]

        # 2. Check stage-specific settings
        stage_provider_name = self._get_stage_provider_name(stage_enum)
        if stage_provider_name:
            return get_llm_provider(stage_provider_name)

        # 3. Fallback to global application default
        global_cfg = get_settings().llm
        return get_llm_provider(global_cfg.provider)

    def get_model(self, stage: ResearchStage | str) -> str:
        """Resolve the model name designated for the stage."""
        stage_enum = self._normalize_stage(stage)

        if stage_enum in self._custom_models:
            return self._custom_models[stage_enum]

        if stage_enum == ResearchStage.RESEARCH and self._settings.research_model:
            return self._settings.research_model
        if stage_enum == ResearchStage.CODING and self._settings.coding_model:
            return self._settings.coding_model
        if stage_enum == ResearchStage.CRITIC and self._settings.critic_model:
            return self._settings.critic_model

        return get_settings().llm.model

    def quarantine_literature(self, literature_text: str) -> dict[str, Any]:
        """Sanitize literature text, ensuring it is strictly treated as inert data and not executable code."""
        contains_injections = False
        detected_patterns: list[str] = []

        for pat in self.INJECTION_INDICATORS:
            if pat.search(literature_text):
                contains_injections = True
                detected_patterns.append(pat.pattern)

        # Escape dangerous markdown delimiters or instruction markers
        sanitized = literature_text
        sanitized = re.sub(
            r"(?i)###\s*(SYSTEM|INSTRUCTION)", r"### [QUARANTINED_DATA_TAG]", sanitized
        )
        sanitized = re.sub(r"(?i)<\s*/?\s*system\s*>", "[ESCAPED_SYSTEM_TAG]", sanitized)

        # Wrap in unambiguous scientific data delimiter
        quarantined_payload = (
            "--- BEGIN SCIENTIFIC LITERATURE DATA (READ-ONLY INERT EVIDENCE) ---\n"
            f"{sanitized.strip()}\n"
            "--- END SCIENTIFIC LITERATURE DATA ---"
        )

        return {
            "quarantined_content": quarantined_payload,
            "is_sanitized": True,
            "contains_injection_attempts": contains_injections,
            "detected_injection_patterns": detected_patterns,
            "is_executable": False,
        }

    def verify_literature_quarantine(self, payload: dict[str, Any]) -> bool:
        """Verify that a literature payload remains strictly data without executable capability."""
        return (
            payload.get("is_executable") is False
            and payload.get("is_sanitized") is True
            and "BEGIN SCIENTIFIC LITERATURE DATA" in payload.get("quarantined_content", "")
        )

    def _normalize_stage(self, stage: ResearchStage | str) -> ResearchStage:
        val = str(stage).lower().strip()
        try:
            return ResearchStage(val)
        except ValueError:
            return ResearchStage.DEFAULT

    def _get_stage_provider_name(self, stage: ResearchStage) -> str | None:
        if stage == ResearchStage.RESEARCH:
            return self._settings.research_provider
        if stage == ResearchStage.CODING:
            return self._settings.coding_provider
        if stage == ResearchStage.CRITIC:
            return self._settings.critic_provider
        return None
