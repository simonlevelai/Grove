"""ConfigLoader — reads, validates, and env-interpolates .grove/config.yaml.

The Pydantic models enforce the nested provider/routing structure from
ARCH.md.  Environment variable interpolation replaces ``${VAR_NAME}``
placeholders with the corresponding value from ``os.environ``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pydantic config models — nested structure matches ARCH.md exactly
# ---------------------------------------------------------------------------


class ProviderRef(BaseModel):
    """A provider + model reference used in routing and fallback entries."""

    provider: str
    model: str


class RoutingTier(BaseModel):
    """Routing entry for a single LLM tier (fast / standard / powerful)."""

    provider: str
    model: str
    fallback: ProviderRef | None = None


class RoutingConfig(BaseModel):
    """LLM routing table — three tiers with optional fallback."""

    fast: RoutingTier
    standard: RoutingTier
    powerful: RoutingTier


class AnthropicProviderConfig(BaseModel):
    """Anthropic provider settings."""

    api_key: str = ""


class OllamaProviderConfig(BaseModel):
    """Ollama provider settings."""

    base_url: str = "http://localhost:11434"


class AzureFoundryProviderConfig(BaseModel):
    """Azure AI Foundry provider settings.

    Supports Claude models deployed as serverless API endpoints
    in Azure AI Foundry via the azure-ai-inference SDK.
    """

    endpoint: str = ""
    api_key: str = ""
    deployment: str = ""


class OpenAICompatibleProviderConfig(BaseModel):
    """Config for any OpenAI-compatible API (OpenAI, Mistral, etc.).

    The ``openai`` Python SDK supports custom ``base_url``, so a single
    provider class handles OpenAI, Mistral, and any other service that
    speaks the OpenAI chat completions protocol.
    """

    api_key: str = ""
    base_url: str = ""  # e.g. https://api.openai.com/v1 or https://api.mistral.ai/v1


class ProvidersConfig(BaseModel):
    """Top-level provider block — one entry per provider."""

    anthropic: AnthropicProviderConfig = Field(default_factory=AnthropicProviderConfig)
    ollama: OllamaProviderConfig = Field(default_factory=OllamaProviderConfig)
    azure_foundry: AzureFoundryProviderConfig = Field(
        default_factory=AzureFoundryProviderConfig
    )
    openai: OpenAICompatibleProviderConfig = Field(
        default_factory=lambda: OpenAICompatibleProviderConfig(
            base_url="https://api.openai.com/v1"
        )
    )
    mistral: OpenAICompatibleProviderConfig = Field(
        default_factory=lambda: OpenAICompatibleProviderConfig(
            base_url="https://api.mistral.ai/v1"
        )
    )


class LLMConfig(BaseModel):
    """LLM section — providers and routing."""

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    routing: RoutingConfig


class BudgetConfig(BaseModel):
    """Spend limits for API calls."""

    daily_limit_usd: float = 5.00
    warn_at_usd: float = 3.00


class CompileConfig(BaseModel):
    """Compilation settings."""

    quality_threshold: Literal["good", "partial", "poor"] = "partial"
    phase: int = 0
    max_output_tokens: int = 65536


class SearchConfig(BaseModel):
    """Search / embedding settings."""

    embedding_model: str = "nomic-embed-text"
    hybrid_alpha: float = 0.5


class GitConfig(BaseModel):
    """Git automation settings."""

    auto_commit: bool = True
    commit_message_prefix: str = "grove:"


class GroveConfig(BaseModel):
    """Root configuration model — validated against config.yaml."""

    llm: LLMConfig
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    git: GitConfig = Field(default_factory=GitConfig)


# ---------------------------------------------------------------------------
# Environment variable interpolation
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: object) -> object:
    """Recursively replace ``${VAR}`` placeholders with env values.

    If the environment variable is not set the placeholder is replaced
    with an empty string so Pydantic can still validate the structure.
    """
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------


class ConfigLoader:
    """Loads and validates ``.grove/config.yaml``.

    Typical usage::

        loader = ConfigLoader(project_root)
        config = loader.load()
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.config_path = project_root / ".grove" / "config.yaml"

    def load(self) -> GroveConfig:
        """Read config.yaml, interpolate env vars, and return a validated model."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        raw_text = self.config_path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(raw_text)

        if not isinstance(raw_data, dict):
            raise ValueError(
                f"Expected a YAML mapping in {self.config_path}, "
                f"got {type(raw_data).__name__}"
            )

        interpolated = _interpolate_env(raw_data)
        return GroveConfig.model_validate(interpolated)
