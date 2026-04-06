"""Tests for the OpenAI-compatible provider (OpenAI, Mistral, etc.)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grove.llm.models import LLMRequest

# Skip all tests if openai SDK is not installed
openai = pytest.importorskip("openai")

from grove.llm.openai_compat import OpenAICompatibleProvider  # noqa: E402


class _AsyncChunkIter:
    """Wraps a list of chunks as a proper async iterator for `async for`."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._idx = 0

    def __aiter__(self) -> _AsyncChunkIter:
        return self

    async def __anext__(self) -> object:
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


def _make_request(**overrides: object) -> LLMRequest:
    defaults = {
        "prompt": "Hello",
        "model": "gpt-4.1-mini",
        "max_tokens": 256,
        "tier": "standard",
        "task_type": "test",
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


def _mock_stream_chunk(
    content: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> MagicMock:
    """Create a mock streaming chunk."""
    chunk = MagicMock()
    if content is not None:
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = content
    else:
        chunk.choices = []

    if prompt_tokens is not None:
        chunk.usage = MagicMock(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens or 0,
        )
    else:
        chunk.usage = None
    return chunk


class TestOpenAICompatibleProvider:
    """Verify the OpenAI-compatible provider with mocked SDK calls."""

    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="API key is required"):
            OpenAICompatibleProvider(
                api_key="",
                base_url="https://api.openai.com/v1",
            )

    @pytest.mark.asyncio
    async def test_complete_returns_response(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            provider_name="openai",
        )

        chunks = [
            _mock_stream_chunk(content="Hello "),
            _mock_stream_chunk(content="world!"),
            _mock_stream_chunk(prompt_tokens=10, completion_tokens=5),
        ]

        mock_stream = _AsyncChunkIter(chunks)

        provider._async_client.chat.completions.create = AsyncMock(
            return_value=mock_stream
        )

        response = await provider.complete(_make_request())

        assert response.content == "Hello world!"
        assert response.provider == "openai"
        assert response.input_tokens == 10
        assert response.output_tokens == 5

    @pytest.mark.asyncio
    async def test_system_prompt_passed(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
        )

        chunks = [_mock_stream_chunk(content="OK")]
        mock_stream = _AsyncChunkIter(chunks)

        mock_create = AsyncMock(return_value=mock_stream)
        provider._async_client.chat.completions.create = mock_create

        request = _make_request(system="You are helpful.")
        await provider.complete(request)

        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."

    @pytest.mark.asyncio
    async def test_mistral_provider_name(self) -> None:
        """Provider name flows through to LLMResponse."""
        provider = OpenAICompatibleProvider(
            api_key="sk-test",
            base_url="https://api.mistral.ai/v1",
            provider_name="mistral",
        )

        chunks = [_mock_stream_chunk(content="Bonjour")]
        mock_stream = _AsyncChunkIter(chunks)
        provider._async_client.chat.completions.create = AsyncMock(
            return_value=mock_stream
        )

        response = await provider.complete(_make_request(model="mistral-small-latest"))
        assert response.provider == "mistral"
        assert response.model == "mistral-small-latest"

    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
        )

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        rate_err = openai.RateLimitError(
            message="Rate limited",
            response=mock_response,
            body=None,
        )

        ok_stream = _AsyncChunkIter([_mock_stream_chunk(content="OK")])

        mock_create = AsyncMock(side_effect=[rate_err, ok_stream])
        provider._async_client.chat.completions.create = mock_create

        with patch(
            "grove.llm.openai_compat.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            response = await provider.complete(_make_request())

        assert response.content == "OK"
        assert mock_create.call_count == 2


class TestRouterIntegration:
    """Verify the router can route to openai and mistral providers."""

    def test_router_recognises_openai(self) -> None:
        from grove.llm.router import LLMRouter

        config = MagicMock()
        config.budget.daily_limit_usd = 10.0
        config.budget.warn_at_usd = 5.0
        config.llm.providers.openai.api_key = "sk-test"
        config.llm.providers.openai.base_url = "https://api.openai.com/v1"

        router = LLMRouter(config=config, project_root=MagicMock())
        provider = router._get_openai()
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_router_recognises_mistral(self) -> None:
        from grove.llm.router import LLMRouter

        config = MagicMock()
        config.budget.daily_limit_usd = 10.0
        config.budget.warn_at_usd = 5.0
        config.llm.providers.mistral.api_key = "sk-test"
        config.llm.providers.mistral.base_url = "https://api.mistral.ai/v1"

        router = LLMRouter(config=config, project_root=MagicMock())
        provider = router._get_mistral()
        assert isinstance(provider, OpenAICompatibleProvider)
