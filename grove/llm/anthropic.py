"""AnthropicProvider -- calls the Anthropic Messages API with retry logic.

Retries with exponential backoff (1s, 2s, 4s) on rate-limit or server
errors.  Gets the API key from config with ``${ANTHROPIC_API_KEY}``
environment variable interpolation handled by ConfigLoader.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import anthropic

from grove.llm.cost import CostTracker
from grove.llm.models import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# Maximum retry attempts for transient errors
_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0


class LLMProvider(Protocol):
    """Interface that all LLM providers must implement."""

    async def complete(self, request: LLMRequest) -> LLMResponse: ...


class AnthropicProvider:
    """Calls the Anthropic Messages API with retry and exponential backoff.

    Retries on rate-limit (429) and server errors (5xx) up to three times
    with delays of 1s, 2s, 4s.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "Anthropic API key is required.  Set ANTHROPIC_API_KEY in your "
                "environment or configure it in .grove/config.yaml."
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self._async_client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request to Anthropic with retry on transient errors."""
        messages = [{"role": "user", "content": request.prompt}]

        model = request.model or "claude-sonnet-4-6"

        kwargs: dict = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": messages,
        }

        if request.system:
            kwargs["system"] = request.system

        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                # Use streaming to avoid timeout on large requests.
                # The SDK requires streaming for operations that may
                # exceed 10 minutes (compilation with many sources).
                async with self._async_client.messages.stream(**kwargs) as stream:
                    response = await stream.get_final_message()

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                content = response.content[0].text if response.content else ""

                return LLMResponse(
                    content=content,
                    model=response.model,
                    provider="anthropic",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=CostTracker.estimate_cost(
                        response.model, input_tokens, output_tokens
                    ),
                )

            except anthropic.RateLimitError as exc:
                last_error = exc
                delay = _BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Anthropic rate limit hit (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

            except anthropic.InternalServerError as exc:
                last_error = exc
                delay = _BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Anthropic server error (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

            except anthropic.APIError:
                # Non-retryable API errors (auth, bad request, etc.)
                raise

        # All retries exhausted
        raise RuntimeError(
            f"Anthropic API call failed after {_MAX_RETRIES} attempts: {last_error}"
        )

    def complete_sync(self, request: LLMRequest) -> LLMResponse:
        """Synchronous wrapper around the async complete method."""
        return asyncio.run(self.complete(request))
