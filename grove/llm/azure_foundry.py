"""AzureFoundryProvider -- calls Azure AI Foundry via the azure-ai-inference SDK.

Supports Claude models deployed as serverless API endpoints in Azure AI
Foundry.  Uses streaming to avoid timeouts on large compilation requests.
Retries with exponential backoff (1s, 2s, 4s) on rate-limit (429) or
server errors (5xx), matching the AnthropicProvider pattern.

Requires ``pip install grove-kb[azure]`` for the Azure dependencies.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grove.llm.cost import CostTracker
from grove.llm.models import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# Maximum retry attempts for transient errors
_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0

# Guard the Azure SDK import so Grove still works without it installed
try:
    from azure.ai.inference import ChatCompletionsClient
    from azure.ai.inference.models import (
        SystemMessage,
        UserMessage,
    )
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import HttpResponseError

    _AZURE_AVAILABLE = True
except ImportError:
    _AZURE_AVAILABLE = False


class AzureFoundryUnavailableError(Exception):
    """Raised when the Azure AI Foundry SDK is not installed."""


def _check_azure_sdk() -> None:
    """Raise a clear error if the Azure SDK is not installed."""
    if not _AZURE_AVAILABLE:
        raise AzureFoundryUnavailableError(
            "Azure AI Foundry requires: pip install grove-kb[azure]  "
            "(needs azure-ai-inference and azure-identity)"
        )


class AzureFoundryProvider:
    """Calls models deployed in Azure AI Foundry via the AI Inference SDK.

    Supports Claude models deployed as serverless API endpoints.
    Uses streaming by default to avoid timeouts on large compilations.
    Retries on rate-limit (429) and server errors (5xx) with exponential
    backoff, matching the retry pattern used by AnthropicProvider.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str | None = None,
    ) -> None:
        """Initialise the Azure AI Foundry provider.

        Args:
            endpoint: Azure AI Foundry endpoint URL
                (e.g. https://<resource>.services.ai.azure.com/models)
            api_key: Azure AI API key (supports ${AZURE_AI_KEY} interpolation
                via ConfigLoader)
            deployment: Optional default deployment/model name.  Overridden
                by ``request.model`` when set by the router.
        """
        _check_azure_sdk()

        if not endpoint:
            raise ValueError(
                "Azure AI Foundry endpoint is required.  Set AZURE_AI_ENDPOINT "
                "in your environment or configure it in .grove/config.yaml."
            )
        if not api_key:
            raise ValueError(
                "Azure AI Foundry API key is required.  Set AZURE_AI_KEY "
                "in your environment or configure it in .grove/config.yaml."
            )

        self._endpoint = endpoint
        self._deployment = deployment or ""

        # The ChatCompletionsClient from azure-ai-inference is synchronous.
        # We wrap calls with asyncio.to_thread() in the async path.
        self._client: Any = ChatCompletionsClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(api_key),
        )

    def _build_messages(self, request: LLMRequest) -> list[Any]:
        """Build the messages list from the LLM request."""
        messages: list[Any] = []
        if request.system:
            messages.append(SystemMessage(content=request.system))
        messages.append(UserMessage(content=request.prompt))
        return messages

    def _complete_sync(self, request: LLMRequest) -> LLMResponse:
        """Synchronous completion with streaming and retry logic.

        Uses streaming to collect the response incrementally, avoiding
        timeouts on large compilation payloads.  Token usage is extracted
        from the final streamed chunk.
        """
        model = request.model or self._deployment
        messages = self._build_messages(request)

        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.complete(
                    model=model,
                    messages=messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    stream=True,
                )

                # Collect streamed chunks
                content_parts: list[str] = []
                input_tokens = 0
                output_tokens = 0

                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content_parts.append(chunk.choices[0].delta.content)
                    # Usage information arrives in the final chunk
                    if hasattr(chunk, "usage") and chunk.usage is not None:
                        input_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                        output_tokens = (
                            getattr(chunk.usage, "completion_tokens", 0) or 0
                        )

                content = "".join(content_parts)
                resolved_model = model or "azure-foundry"

                return LLMResponse(
                    content=content,
                    model=resolved_model,
                    provider="azure_foundry",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=CostTracker.estimate_cost(
                        resolved_model, input_tokens, output_tokens
                    ),
                )

            except HttpResponseError as exc:
                status = getattr(exc, "status_code", None)
                if status == 429 or (status is not None and status >= 500):
                    # Retryable: rate limit or server error
                    last_error = exc
                    delay = _BASE_DELAY_SECONDS * (2**attempt)
                    logger.warning(
                        "Azure AI Foundry HTTP %s (attempt %d/%d), retrying in %.1fs",
                        status,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    # Synchronous sleep for the sync path; the async path
                    # uses asyncio.sleep via the to_thread wrapper.
                    import time

                    time.sleep(delay)
                else:
                    # Non-retryable (auth, bad request, etc.)
                    raise

        raise RuntimeError(
            f"Azure AI Foundry API call failed after {_MAX_RETRIES} "
            f"attempts: {last_error}"
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request to Azure AI Foundry with retry logic.

        Delegates to the synchronous SDK via ``asyncio.to_thread()`` since
        the ``azure-ai-inference`` SDK is synchronous by default.
        """
        return await asyncio.to_thread(self._complete_sync, request)

    def complete_sync(self, request: LLMRequest) -> LLMResponse:
        """Synchronous wrapper -- calls the SDK directly without asyncio."""
        return self._complete_sync(request)
