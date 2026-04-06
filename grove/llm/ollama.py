"""OllamaProvider -- calls a local Ollama instance via its REST API.

Uses ``httpx`` to call ``POST /api/generate`` on the configured base URL.
Detects availability on first use and caches the result so subsequent
calls fail fast when Ollama is unreachable.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from grove.llm.cost import CostTracker
from grove.llm.models import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# Timeout for the availability check (quick ping)
_PING_TIMEOUT_SECONDS = 3.0

# Timeout for generation requests (can be slow on large models)
_GENERATE_TIMEOUT_SECONDS = 120.0


class OllamaUnavailableError(Exception):
    """Raised when the local Ollama instance cannot be reached."""


class OllamaProvider:
    """Calls a local Ollama instance for LLM completions.

    Detects whether Ollama is reachable on first use and caches the
    result.  If unreachable, raises ``OllamaUnavailableError`` so the
    router can fall back to an alternative provider.
    """

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._base_url = base_url.rstrip("/")
        self._available: bool | None = None

    @property
    def available(self) -> bool | None:
        """Cached availability state.  ``None`` means not yet checked."""
        return self._available

    async def check_availability(self) -> bool:
        """Ping Ollama to determine whether it is running.

        Sends a GET to the root endpoint.  Ollama returns 200 with
        'Ollama is running' when healthy.
        """
        try:
            async with httpx.AsyncClient(timeout=_PING_TIMEOUT_SECONDS) as client:
                response = await client.get(self._base_url)
                self._available = response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            self._available = False

        if self._available:
            logger.debug("Ollama is available at %s", self._base_url)
        else:
            logger.info("Ollama is not available at %s", self._base_url)

        return self._available

    async def _ensure_available(self) -> None:
        """Check availability on first use; raise if unreachable."""
        if self._available is None:
            await self.check_availability()

        if not self._available:
            raise OllamaUnavailableError(
                f"Ollama is not available at {self._base_url}.  "
                "Start Ollama or configure a fallback provider."
            )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request to Ollama's generate endpoint."""
        await self._ensure_available()

        model = request.model or "llama3.2"

        payload: dict = {
            "model": model,
            "prompt": request.prompt,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }

        if request.system:
            payload["system"] = request.system

        try:
            async with httpx.AsyncClient(timeout=_GENERATE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            # Mark as unavailable for subsequent calls
            self._available = False
            raise OllamaUnavailableError(
                f"Ollama became unreachable during generation: {exc}"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc

        data = response.json()

        # Ollama returns token counts in the response when stream=false
        input_tokens = data.get("prompt_eval_count", 0) or 0
        output_tokens = data.get("eval_count", 0) or 0

        return LLMResponse(
            content=data.get("response", ""),
            model=model,
            provider="ollama",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=CostTracker.estimate_cost(model, input_tokens, output_tokens),
        )

    def complete_sync(self, request: LLMRequest) -> LLMResponse:
        """Synchronous wrapper around the async complete method."""
        return asyncio.run(self.complete(request))
