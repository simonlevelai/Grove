"""OpenAICompatibleProvider -- works with any OpenAI-compatible API.

Handles OpenAI, Mistral, and any other service that speaks the OpenAI
chat completions protocol.  Uses the ``openai`` Python SDK with a
custom ``base_url`` to route to different providers.

Requires ``pip install grove-kb[openai]`` for the OpenAI SDK dependency.
"""

from __future__ import annotations

import asyncio
import logging

from grove.llm.cost import CostTracker
from grove.llm.models import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0

try:
    import openai

    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


class OpenAICompatibleProvider:
    """Calls any OpenAI-compatible chat completions API.

    Works with OpenAI, Mistral, and other providers that implement the
    OpenAI chat completions protocol.  Uses streaming to avoid timeouts
    on large compilation requests.  Retries on rate-limit and server
    errors with exponential backoff.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        provider_name: str = "openai",
    ) -> None:
        """Initialise the provider.

        Args:
            api_key: API key for the service.
            base_url: Base URL (e.g. https://api.openai.com/v1).
            provider_name: Name used in LLMResponse and cost tracking
                (e.g. "openai", "mistral").
        """
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI-compatible providers require: pip install grove-kb[openai]  "
                "(needs the openai Python SDK)"
            )
        if not api_key:
            raise ValueError(
                f"{provider_name} API key is required.  Set it in your "
                "environment or configure it in .grove/config.yaml."
            )

        self._provider_name = provider_name
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._async_client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request with streaming and retry logic."""
        model = request.model or "gpt-4.1"

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                # Stream to avoid timeouts on large payloads
                stream = await self._async_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                )

                content_parts: list[str] = []
                input_tokens = 0
                output_tokens = 0

                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content_parts.append(chunk.choices[0].delta.content)
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens or 0
                        output_tokens = chunk.usage.completion_tokens or 0

                content = "".join(content_parts)

                return LLMResponse(
                    content=content,
                    model=model,
                    provider=self._provider_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=CostTracker.estimate_cost(
                        model, input_tokens, output_tokens
                    ),
                )

            except openai.RateLimitError as exc:
                last_error = exc
                delay = _BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "%s rate limit hit (attempt %d/%d), retrying in %.1fs",
                    self._provider_name,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

            except openai.InternalServerError as exc:
                last_error = exc
                delay = _BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "%s server error (attempt %d/%d), retrying in %.1fs",
                    self._provider_name,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

            except openai.APIError:
                raise

        raise RuntimeError(
            f"{self._provider_name} API call failed after {_MAX_RETRIES} "
            f"attempts: {last_error}"
        )

    def complete_sync(self, request: LLMRequest) -> LLMResponse:
        """Synchronous wrapper."""
        return asyncio.run(self.complete(request))
