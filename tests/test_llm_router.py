"""Tests for grove.llm -- router, providers, cost tracker, and models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from grove.config.defaults import DEFAULT_CONFIG
from grove.config.loader import ConfigLoader, GroveConfig
from grove.llm.anthropic import AnthropicProvider
from grove.llm.cost import BudgetExceededError, CostTracker
from grove.llm.models import LLMRequest, LLMResponse
from grove.llm.ollama import OllamaProvider, OllamaUnavailableError
from grove.llm.router import LLMRouter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def grove_root(tmp_path: Path) -> Path:
    """Create a minimal grove directory with default config."""
    grove_dir = tmp_path / ".grove"
    grove_dir.mkdir()
    (grove_dir / "logs").mkdir()

    config_path = grove_dir / "config.yaml"
    config_path.write_text(
        yaml.dump(DEFAULT_CONFIG, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    state_path = grove_dir / "state.json"
    state_path.write_text("{}\n", encoding="utf-8")

    return tmp_path


@pytest.fixture()
def config(grove_root: Path, monkeypatch: pytest.MonkeyPatch) -> GroveConfig:
    """Load a validated config from the test grove directory."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-fake")
    loader = ConfigLoader(grove_root)
    return loader.load()


@pytest.fixture()
def logs_dir(grove_root: Path) -> Path:
    """Return the logs directory path."""
    return grove_root / ".grove" / "logs"


@pytest.fixture()
def cost_tracker(logs_dir: Path) -> CostTracker:
    """Create a CostTracker pointed at the test logs directory."""
    return CostTracker(logs_dir=logs_dir, daily_limit_usd=5.00, warn_at_usd=3.00)


@pytest.fixture()
def sample_request() -> LLMRequest:
    """A standard LLM request for testing."""
    return LLMRequest(
        prompt="Summarise this document.",
        system="You are a helpful assistant.",
        max_tokens=1024,
        temperature=0.0,
        tier="fast",
        task_type="ingest_summary",
    )


@pytest.fixture()
def sample_response() -> LLMResponse:
    """A standard LLM response for testing."""
    return LLMResponse(
        content="This is a summary.",
        model="llama3.2",
        provider="ollama",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0,
    )


# ---------------------------------------------------------------------------
# LLMRequest / LLMResponse models
# ---------------------------------------------------------------------------


class TestModels:
    """Verify the Pydantic request/response models."""

    def test_request_defaults(self) -> None:
        req = LLMRequest(prompt="Hello")
        assert req.temperature == 0.0
        assert req.max_tokens == 4096
        assert req.tier == "standard"
        assert req.task_type == "unknown"
        assert req.system is None
        assert req.model == ""

    def test_request_with_all_fields(self) -> None:
        req = LLMRequest(
            prompt="Test",
            system="Be concise.",
            max_tokens=512,
            temperature=0.5,
            tier="powerful",
            task_type="compile",
            model="claude-opus-4-6",
        )
        assert req.prompt == "Test"
        assert req.system == "Be concise."
        assert req.model == "claude-opus-4-6"

    def test_response_cost_field(self) -> None:
        resp = LLMResponse(
            content="Answer",
            model="claude-sonnet-4-6",
            provider="anthropic",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.0045,
        )
        assert resp.cost_usd == 0.0045
        assert resp.provider == "anthropic"

    def test_response_defaults(self) -> None:
        resp = LLMResponse(
            content="X",
            model="llama3.2",
            provider="ollama",
        )
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.cost_usd == 0.0


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class TestCostTracker:
    """Verify cost estimation, recording, and budget enforcement."""

    def test_estimate_cost_anthropic_sonnet(self) -> None:
        # Sonnet: $3/1M input, $15/1M output
        cost = CostTracker.estimate_cost("claude-sonnet-4-6", 1000, 500)
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert cost == round(expected, 6)

    def test_estimate_cost_anthropic_haiku(self) -> None:
        cost = CostTracker.estimate_cost("claude-haiku-4-5-20251001", 10000, 1000)
        expected = (10000 * 0.80 + 1000 * 4.0) / 1_000_000
        assert cost == round(expected, 6)

    def test_estimate_cost_ollama_is_free(self) -> None:
        cost = CostTracker.estimate_cost("llama3.2", 50000, 10000)
        assert cost == 0.0

    def test_estimate_cost_unknown_model_is_free(self) -> None:
        cost = CostTracker.estimate_cost("unknown-model", 1000, 1000)
        assert cost == 0.0

    def test_record_creates_jsonl_file(
        self, cost_tracker: CostTracker, sample_response: LLMResponse
    ) -> None:
        cost_tracker.record(sample_response, "ingest_summary")
        assert cost_tracker.costs_path.exists()

        lines = cost_tracker.costs_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["task_type"] == "ingest_summary"
        assert entry["model"] == "llama3.2"
        assert entry["provider"] == "ollama"
        assert entry["input_tokens"] == 100
        assert entry["output_tokens"] == 50

    def test_record_appends_multiple_entries(
        self, cost_tracker: CostTracker, sample_response: LLMResponse
    ) -> None:
        cost_tracker.record(sample_response, "ingest_summary")
        cost_tracker.record(sample_response, "compile")

        lines = cost_tracker.costs_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[1])["task_type"] == "compile"

    def test_get_today_spend_empty(self, cost_tracker: CostTracker) -> None:
        assert cost_tracker.get_today_spend() == 0.0

    def test_get_today_spend_sums_correctly(self, logs_dir: Path) -> None:
        tracker = CostTracker(logs_dir=logs_dir)
        today = datetime.now(UTC).date().isoformat()

        entries = [
            {"timestamp": f"{today}T10:00:00+00:00", "cost_usd": 1.50},
            {"timestamp": f"{today}T11:00:00+00:00", "cost_usd": 0.75},
            # Yesterday's entry should not count
            {"timestamp": "2020-01-01T00:00:00+00:00", "cost_usd": 99.0},
        ]

        with tracker.costs_path.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")

        assert tracker.get_today_spend() == 2.25

    def test_check_budget_passes_when_under_limit(
        self, cost_tracker: CostTracker
    ) -> None:
        # No spend recorded -- should not raise
        cost_tracker.check_budget()

    def test_check_budget_raises_when_exceeded(self, logs_dir: Path) -> None:
        tracker = CostTracker(logs_dir=logs_dir, daily_limit_usd=2.00, warn_at_usd=1.00)
        today = datetime.now(UTC).date().isoformat()

        entry = {"timestamp": f"{today}T10:00:00+00:00", "cost_usd": 2.50}
        with tracker.costs_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        with pytest.raises(BudgetExceededError, match="Daily budget exceeded"):
            tracker.check_budget()

    def test_check_budget_warns_at_threshold(
        self, logs_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tracker = CostTracker(logs_dir=logs_dir, daily_limit_usd=5.00, warn_at_usd=1.00)
        today = datetime.now(UTC).date().isoformat()

        entry = {"timestamp": f"{today}T10:00:00+00:00", "cost_usd": 1.50}
        with tracker.costs_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        import logging

        with caplog.at_level(logging.WARNING, logger="grove.llm.cost"):
            tracker.check_budget()

        assert "Budget warning" in caplog.text

    def test_logs_dir_created_if_missing(self, tmp_path: Path) -> None:
        new_logs = tmp_path / "new" / "logs"
        assert not new_logs.exists()
        CostTracker(logs_dir=new_logs)
        assert new_logs.exists()

    def test_get_today_spend_handles_malformed_lines(self, logs_dir: Path) -> None:
        tracker = CostTracker(logs_dir=logs_dir)
        today = datetime.now(UTC).date().isoformat()

        with tracker.costs_path.open("w", encoding="utf-8") as fh:
            fh.write("not valid json\n")
            fh.write(
                json.dumps({"timestamp": f"{today}T12:00:00+00:00", "cost_usd": 0.5})
                + "\n"
            )
            fh.write("\n")  # blank line

        assert tracker.get_today_spend() == 0.5


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


def _mock_stream(mock_message: MagicMock) -> MagicMock:
    """Create a mock that simulates messages.stream() as an async context manager."""
    mock_stream_obj = MagicMock()
    mock_stream_obj.get_final_message = AsyncMock(return_value=mock_message)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_stream_obj)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


def _mock_stream_factory(
    side_effects: list,
) -> MagicMock:
    """Create a messages.stream mock that returns different results per call.

    Each item in *side_effects* is either a MagicMock message (success) or an
    Exception (failure raised inside the context manager).
    """
    call_idx = {"i": 0}

    def _stream_side_effect(**_kwargs: object) -> MagicMock:
        idx = call_idx["i"]
        call_idx["i"] += 1
        item = side_effects[idx]
        if isinstance(item, Exception):
            # Raise during __aenter__ so the retry logic catches it
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(side_effect=item)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx
        return _mock_stream(item)

    mock = MagicMock(side_effect=_stream_side_effect)
    return mock


class TestAnthropicProvider:
    """Verify the Anthropic provider with mocked SDK calls."""

    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="Anthropic API key is required"):
            AnthropicProvider(api_key="")

    @pytest.mark.asyncio
    async def test_complete_returns_response(self) -> None:
        """A successful Anthropic call returns a well-formed LLMResponse."""
        provider = AnthropicProvider(api_key="sk-ant-test")

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="The answer is 42.")]
        mock_message.model = "claude-sonnet-4-6"
        mock_message.usage = MagicMock(input_tokens=50, output_tokens=20)

        provider._async_client.messages.stream = MagicMock(
            return_value=_mock_stream(mock_message)
        )

        request = LLMRequest(
            prompt="What is the meaning of life?",
            model="claude-sonnet-4-6",
            max_tokens=256,
            tier="standard",
            task_type="query",
        )
        response = await provider.complete(request)

        assert response.content == "The answer is 42."
        assert response.provider == "anthropic"
        assert response.model == "claude-sonnet-4-6"
        assert response.input_tokens == 50
        assert response.output_tokens == 20
        assert response.cost_usd > 0

    @pytest.mark.asyncio
    async def test_complete_includes_system_prompt(self) -> None:
        """The system prompt is passed through to the API."""
        provider = AnthropicProvider(api_key="sk-ant-test")

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Done.")]
        mock_message.model = "claude-sonnet-4-6"
        mock_message.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_stream_fn = MagicMock(return_value=_mock_stream(mock_message))
        provider._async_client.messages.stream = mock_stream_fn

        request = LLMRequest(
            prompt="Summarise",
            system="You are a research assistant.",
            model="claude-sonnet-4-6",
            tier="standard",
            task_type="compile",
        )
        await provider.complete(request)

        call_kwargs = mock_stream_fn.call_args[1]
        assert call_kwargs["system"] == "You are a research assistant."

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit(self) -> None:
        """Rate limit errors trigger exponential backoff retries."""
        import anthropic as anthropic_sdk

        provider = AnthropicProvider(api_key="sk-ant-test")

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="OK")]
        mock_message.model = "claude-sonnet-4-6"
        mock_message.usage = MagicMock(input_tokens=10, output_tokens=5)

        # First call raises rate limit, second succeeds
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {}
        rate_limit_exc = anthropic_sdk.RateLimitError(
            message="Rate limited",
            response=rate_limit_response,
            body=None,
        )

        stream_mock = _mock_stream_factory([rate_limit_exc, mock_message])
        provider._async_client.messages.stream = stream_mock

        request = LLMRequest(
            prompt="Test",
            model="claude-sonnet-4-6",
            tier="standard",
            task_type="test",
        )

        with patch("grove.llm.anthropic.asyncio.sleep", new_callable=AsyncMock):
            response = await provider.complete(request)

        assert response.content == "OK"
        assert stream_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Exhausting all retries raises a RuntimeError."""
        import anthropic as anthropic_sdk

        provider = AnthropicProvider(api_key="sk-ant-test")

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.headers = {}

        server_err = anthropic_sdk.InternalServerError(
            message="Server error",
            response=error_response,
            body=None,
        )
        provider._async_client.messages.stream = _mock_stream_factory(
            [server_err, server_err, server_err]
        )

        request = LLMRequest(
            prompt="Test",
            model="claude-sonnet-4-6",
            tier="standard",
            task_type="test",
        )

        with (
            patch("grove.llm.anthropic.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(RuntimeError, match="failed after 3 attempts"),
        ):
            await provider.complete(request)

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self) -> None:
        """Authentication errors are not retried."""
        import anthropic as anthropic_sdk

        provider = AnthropicProvider(api_key="sk-ant-test")

        error_response = MagicMock()
        error_response.status_code = 401
        error_response.headers = {}

        auth_err = anthropic_sdk.AuthenticationError(
            message="Invalid key",
            response=error_response,
            body=None,
        )
        stream_mock = _mock_stream_factory([auth_err])
        provider._async_client.messages.stream = stream_mock

        request = LLMRequest(
            prompt="Test",
            model="claude-sonnet-4-6",
            tier="standard",
            task_type="test",
        )

        with pytest.raises(anthropic_sdk.AuthenticationError):
            await provider.complete(request)

        # Should only have been called once (no retries)
        assert stream_mock.call_count == 1


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    """Verify the Ollama provider with mocked HTTP calls."""

    @pytest.mark.asyncio
    async def test_check_availability_when_running(self) -> None:
        """Availability check succeeds when Ollama responds with 200."""
        provider = OllamaProvider(base_url="http://localhost:11434")

        with patch("grove.llm.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.check_availability()

        assert result is True
        assert provider.available is True

    @pytest.mark.asyncio
    async def test_check_availability_when_not_running(self) -> None:
        """Availability check returns False when Ollama is unreachable."""
        import httpx

        provider = OllamaProvider(base_url="http://localhost:11434")

        with patch("grove.llm.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            result = await provider.check_availability()

        assert result is False
        assert provider.available is False

    @pytest.mark.asyncio
    async def test_complete_returns_response(self) -> None:
        """A successful Ollama call returns a well-formed LLMResponse."""
        provider = OllamaProvider(base_url="http://localhost:11434")
        provider._available = True  # Skip availability check

        ollama_response_data = {
            "response": "Here is the summary.",
            "prompt_eval_count": 200,
            "eval_count": 80,
        }

        with patch("grove.llm.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_response = MagicMock()
            mock_http_response.status_code = 200
            mock_http_response.json.return_value = ollama_response_data
            mock_http_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_http_response)
            mock_client_cls.return_value = mock_client

            request = LLMRequest(
                prompt="Summarise this.",
                model="llama3.2",
                tier="fast",
                task_type="ingest_summary",
            )
            response = await provider.complete(request)

        assert response.content == "Here is the summary."
        assert response.provider == "ollama"
        assert response.model == "llama3.2"
        assert response.input_tokens == 200
        assert response.output_tokens == 80
        assert response.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_complete_raises_when_unavailable(self) -> None:
        """Calling complete when Ollama is down raises OllamaUnavailableError."""
        provider = OllamaProvider(base_url="http://localhost:11434")
        provider._available = False

        request = LLMRequest(
            prompt="Test",
            model="llama3.2",
            tier="fast",
            task_type="test",
        )

        with pytest.raises(OllamaUnavailableError, match="not available"):
            await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_marks_unavailable_on_connection_error(self) -> None:
        """A connection error during generation marks the provider as unavailable."""
        import httpx

        provider = OllamaProvider(base_url="http://localhost:11434")
        provider._available = True

        with patch("grove.llm.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("connection lost")
            )
            mock_client_cls.return_value = mock_client

            request = LLMRequest(
                prompt="Test",
                model="llama3.2",
                tier="fast",
                task_type="test",
            )

            with pytest.raises(OllamaUnavailableError, match="unreachable"):
                await provider.complete(request)

        assert provider.available is False

    @pytest.mark.asyncio
    async def test_complete_passes_system_prompt(self) -> None:
        """The system prompt is included in the Ollama request payload."""
        provider = OllamaProvider(base_url="http://localhost:11434")
        provider._available = True

        with patch("grove.llm.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_response = MagicMock()
            mock_http_response.json.return_value = {
                "response": "OK",
                "prompt_eval_count": 10,
                "eval_count": 5,
            }
            mock_http_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_http_response)
            mock_client_cls.return_value = mock_client

            request = LLMRequest(
                prompt="Summarise",
                system="Be brief.",
                model="llama3.2",
                tier="fast",
                task_type="ingest_summary",
            )
            await provider.complete(request)

            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["json"]["system"] == "Be brief."


# ---------------------------------------------------------------------------
# LLMRouter
# ---------------------------------------------------------------------------


class TestLLMRouter:
    """Verify routing, fallback, and budget integration."""

    @pytest.mark.asyncio
    async def test_routes_fast_tier_to_ollama(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """Fast tier routes to Ollama by default."""
        router = LLMRouter(config, grove_root)

        mock_response = LLMResponse(
            content="Summary",
            model="llama3.2",
            provider="ollama",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0,
        )

        with patch.object(
            OllamaProvider,
            "complete",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = LLMRequest(
                prompt="Summarise",
                tier="fast",
                task_type="ingest_summary",
            )
            response = await router.complete(request)

        assert response.provider == "ollama"
        assert response.model == "llama3.2"

    @pytest.mark.asyncio
    async def test_routes_standard_tier_to_anthropic(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """Standard tier routes to Anthropic Sonnet."""
        router = LLMRouter(config, grove_root)

        mock_response = LLMResponse(
            content="Compiled output",
            model="claude-sonnet-4-6",
            provider="anthropic",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.0045,
        )

        with patch.object(
            AnthropicProvider,
            "complete",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = LLMRequest(
                prompt="Compile these sources",
                tier="standard",
                task_type="compile",
            )
            response = await router.complete(request)

        assert response.provider == "anthropic"
        assert response.model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_routes_powerful_tier_to_opus(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """Powerful tier routes to Anthropic Opus."""
        router = LLMRouter(config, grove_root)

        mock_response = LLMResponse(
            content="Deep analysis",
            model="claude-opus-4-6",
            provider="anthropic",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.0525,
        )

        with patch.object(
            AnthropicProvider,
            "complete",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = LLMRequest(
                prompt="Deep research query",
                tier="powerful",
                task_type="query",
            )
            response = await router.complete(request)

        assert response.provider == "anthropic"
        assert response.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_fast_tier_falls_back_to_haiku(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """When Ollama is unavailable, fast tier falls back to Haiku."""
        router = LLMRouter(config, grove_root)

        fallback_response = LLMResponse(
            content="Fallback summary",
            model="claude-haiku-4-5-20251001",
            provider="anthropic",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.00028,
        )

        with (
            patch.object(
                OllamaProvider,
                "complete",
                new_callable=AsyncMock,
                side_effect=OllamaUnavailableError("not available"),
            ),
            patch.object(
                AnthropicProvider,
                "complete",
                new_callable=AsyncMock,
                return_value=fallback_response,
            ),
        ):
            request = LLMRequest(
                prompt="Summarise",
                tier="fast",
                task_type="ingest_summary",
            )
            response = await router.complete(request)

        assert response.provider == "anthropic"
        assert response.model == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_standard_tier_no_fallback_raises(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """Standard tier has no fallback -- errors propagate."""
        router = LLMRouter(config, grove_root)

        with patch.object(
            AnthropicProvider,
            "complete",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API call failed"),
        ):
            request = LLMRequest(
                prompt="Compile",
                tier="standard",
                task_type="compile",
            )
            with pytest.raises(RuntimeError, match="API call failed"):
                await router.complete(request)

    @pytest.mark.asyncio
    async def test_invalid_tier_raises(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """An unknown tier name raises ValueError."""
        router = LLMRouter(config, grove_root)

        request = LLMRequest(
            prompt="Test",
            tier="ultra",
            task_type="test",
        )
        with pytest.raises(ValueError, match="Unknown tier"):
            await router.complete(request)

    @pytest.mark.asyncio
    async def test_budget_check_blocks_call(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """Budget exceeded prevents the LLM call from being made."""
        router = LLMRouter(config, grove_root)

        # Write a cost entry that exceeds the daily limit
        today = datetime.now(UTC).date().isoformat()
        entry = {"timestamp": f"{today}T10:00:00+00:00", "cost_usd": 10.00}
        costs_path = grove_root / ".grove" / "logs" / "costs.jsonl"
        with costs_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        request = LLMRequest(
            prompt="This should be blocked",
            tier="standard",
            task_type="compile",
        )
        with pytest.raises(BudgetExceededError):
            await router.complete(request)

    @pytest.mark.asyncio
    async def test_cost_recorded_after_successful_call(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """The router records cost via the CostTracker after each call."""
        router = LLMRouter(config, grove_root)

        mock_response = LLMResponse(
            content="Result",
            model="llama3.2",
            provider="ollama",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0,
        )

        with patch.object(
            OllamaProvider,
            "complete",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = LLMRequest(
                prompt="Test",
                tier="fast",
                task_type="ingest_summary",
            )
            await router.complete(request)

        costs_path = grove_root / ".grove" / "logs" / "costs.jsonl"
        assert costs_path.exists()

        lines = costs_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["task_type"] == "ingest_summary"
        assert entry["model"] == "llama3.2"

    @pytest.mark.asyncio
    async def test_model_stamped_on_request(
        self, config: GroveConfig, grove_root: Path
    ) -> None:
        """The router stamps the model from routing config onto the request."""
        router = LLMRouter(config, grove_root)
        captured_request: LLMRequest | None = None

        async def capture_complete(
            self_provider: OllamaProvider, req: LLMRequest
        ) -> LLMResponse:
            nonlocal captured_request
            captured_request = req
            return LLMResponse(
                content="OK",
                model="llama3.2",
                provider="ollama",
            )

        with patch.object(OllamaProvider, "complete", capture_complete):
            request = LLMRequest(
                prompt="Test",
                tier="fast",
                task_type="test",
            )
            await router.complete(request)

        assert captured_request is not None
        assert captured_request.model == "llama3.2"

    def test_cost_tracker_exposed(self, config: GroveConfig, grove_root: Path) -> None:
        """The cost_tracker property returns the CostTracker instance."""
        router = LLMRouter(config, grove_root)
        assert isinstance(router.cost_tracker, CostTracker)

    def test_complete_sync_wrapper(self, config: GroveConfig, grove_root: Path) -> None:
        """The synchronous complete_sync wrapper works correctly."""
        router = LLMRouter(config, grove_root)

        mock_response = LLMResponse(
            content="Sync result",
            model="llama3.2",
            provider="ollama",
        )

        with patch.object(
            OllamaProvider,
            "complete",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = LLMRequest(
                prompt="Test sync",
                tier="fast",
                task_type="test",
            )
            response = router.complete_sync(request)

        assert response.content == "Sync result"
