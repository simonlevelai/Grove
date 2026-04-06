"""LLMRouter -- selects provider and model by tier, handles fallback.

The router is the single entry point for all LLM calls in Grove.  It
reads the routing table from config, instantiates providers lazily, and
delegates to the correct provider based on the request's tier.  When the
primary provider fails and a fallback is configured (e.g. fast tier falls
back from Ollama to Haiku), the router retries transparently.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from grove.config.loader import GroveConfig
from grove.llm.anthropic import AnthropicProvider
from grove.llm.azure_foundry import AzureFoundryProvider
from grove.llm.cost import CostTracker
from grove.llm.models import LLMRequest, LLMResponse
from grove.llm.ollama import OllamaProvider, OllamaUnavailableError
from grove.llm.openai_compat import OpenAICompatibleProvider  # noqa: E402

logger = logging.getLogger(__name__)


class LLMRouter:
    """Routes LLM requests to the appropriate provider based on tier.

    Providers are created lazily on first use.  The router checks the
    budget before each call and logs costs after each successful call.
    """

    def __init__(self, config: GroveConfig, project_root: Path) -> None:
        self._config = config
        self._cost_tracker = CostTracker(
            logs_dir=project_root / ".grove" / "logs",
            daily_limit_usd=config.budget.daily_limit_usd,
            warn_at_usd=config.budget.warn_at_usd,
        )

        # Providers are created lazily and cached
        self._anthropic: AnthropicProvider | None = None
        self._ollama: OllamaProvider | None = None
        self._azure_foundry: AzureFoundryProvider | None = None
        self._openai: OpenAICompatibleProvider | None = None
        self._mistral: OpenAICompatibleProvider | None = None

    # ------------------------------------------------------------------
    # Provider access (lazy initialisation)
    # ------------------------------------------------------------------

    def _get_anthropic(self) -> AnthropicProvider:
        """Return the Anthropic provider, creating it on first use."""
        if self._anthropic is None:
            api_key = self._config.llm.providers.anthropic.api_key
            self._anthropic = AnthropicProvider(api_key=api_key)
        return self._anthropic

    def _get_ollama(self) -> OllamaProvider:
        """Return the Ollama provider, creating it on first use."""
        if self._ollama is None:
            base_url = self._config.llm.providers.ollama.base_url
            self._ollama = OllamaProvider(base_url=base_url)
        return self._ollama

    def _get_azure_foundry(self) -> AzureFoundryProvider:
        """Return the Azure AI Foundry provider, creating it on first use."""
        if self._azure_foundry is None:
            cfg = self._config.llm.providers.azure_foundry
            self._azure_foundry = AzureFoundryProvider(
                endpoint=cfg.endpoint,
                api_key=cfg.api_key,
                deployment=cfg.deployment or None,
            )
        return self._azure_foundry

    def _get_openai(self) -> OpenAICompatibleProvider:
        """Return the OpenAI provider, creating it on first use."""
        if self._openai is None:
            cfg = self._config.llm.providers.openai
            self._openai = OpenAICompatibleProvider(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                provider_name="openai",
            )
        return self._openai

    def _get_mistral(self) -> OpenAICompatibleProvider:
        """Return the Mistral provider, creating it on first use."""
        if self._mistral is None:
            cfg = self._config.llm.providers.mistral
            self._mistral = OpenAICompatibleProvider(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                provider_name="mistral",
            )
        return self._mistral

    def _get_provider(
        self,
        provider_name: str,
    ) -> (
        AnthropicProvider
        | OllamaProvider
        | AzureFoundryProvider
        | OpenAICompatibleProvider
    ):
        """Return a provider instance by name."""
        if provider_name == "anthropic":
            return self._get_anthropic()
        if provider_name == "ollama":
            return self._get_ollama()
        if provider_name == "azure_foundry":
            return self._get_azure_foundry()
        if provider_name == "openai":
            return self._get_openai()
        if provider_name == "mistral":
            return self._get_mistral()
        raise ValueError(f"Unknown provider: {provider_name}")

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _resolve_tier(self, tier: str) -> tuple[str, str, tuple[str, str] | None]:
        """Look up the routing config for a tier.

        Returns ``(provider_name, model_name, fallback_or_none)`` where
        fallback is ``(provider_name, model_name)`` or ``None``.
        """
        routing = self._config.llm.routing

        if tier == "fast":
            tier_config = routing.fast
        elif tier == "standard":
            tier_config = routing.standard
        elif tier == "powerful":
            tier_config = routing.powerful
        else:
            raise ValueError(
                f"Unknown tier '{tier}'. Valid tiers: fast, standard, powerful"
            )

        fallback: tuple[str, str] | None = None
        if tier_config.fallback:
            fallback = (tier_config.fallback.provider, tier_config.fallback.model)

        return tier_config.provider, tier_config.model, fallback

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Route a request to the correct provider based on tier.

        Checks the budget before making the call.  If the primary
        provider fails and a fallback is configured, retries with the
        fallback.  Logs the cost after each successful call.
        """
        # Budget gate -- fail fast if daily limit is exceeded
        self._cost_tracker.check_budget()

        provider_name, model_name, fallback = self._resolve_tier(request.tier)

        # Stamp the model onto the request so the provider knows which to call
        request = request.model_copy(update={"model": model_name})

        try:
            provider = self._get_provider(provider_name)
            response = await provider.complete(request)
        except OllamaUnavailableError:
            if fallback is None:
                raise
            fb_provider_name, fb_model_name = fallback
            logger.warning(
                "Primary provider '%s' unavailable for tier '%s', "
                "falling back to %s/%s",
                provider_name,
                request.tier,
                fb_provider_name,
                fb_model_name,
            )
            request = request.model_copy(update={"model": fb_model_name})
            provider = self._get_provider(fb_provider_name)
            response = await provider.complete(request)

        # Record cost after successful completion
        self._cost_tracker.record(response, request.task_type)

        return response

    def complete_sync(self, request: LLMRequest) -> LLMResponse:
        """Synchronous wrapper for callers that are not in an async context."""
        return asyncio.run(self.complete(request))

    @property
    def cost_tracker(self) -> CostTracker:
        """Expose the cost tracker for external budget queries."""
        return self._cost_tracker
