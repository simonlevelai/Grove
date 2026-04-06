"""Tests for grove.llm.azure_foundry -- AzureFoundryProvider and router integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from grove.config.defaults import DEFAULT_CONFIG
from grove.config.loader import ConfigLoader, GroveConfig
from grove.llm.azure_foundry import (
    _AZURE_AVAILABLE,
    AzureFoundryProvider,
    AzureFoundryUnavailableError,
)
from grove.llm.models import LLMRequest, LLMResponse

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
def azure_config(grove_root: Path, monkeypatch: pytest.MonkeyPatch) -> GroveConfig:
    """Load config with Azure AI Foundry credentials set via environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-fake")
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://test.services.ai.azure.com/models")
    monkeypatch.setenv("AZURE_AI_KEY", "azure-test-key-fake")
    loader = ConfigLoader(grove_root)
    return loader.load()


@pytest.fixture()
def sample_request() -> LLMRequest:
    """A standard LLM request for testing Azure Foundry."""
    return LLMRequest(
        prompt="Summarise this document.",
        system="You are a helpful assistant.",
        max_tokens=1024,
        temperature=0.0,
        tier="standard",
        task_type="compile",
        model="claude-sonnet-4-6",
    )


# ---------------------------------------------------------------------------
# Helper: mock the Azure SDK classes when not installed
# ---------------------------------------------------------------------------


def _make_mock_chunk(
    content: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> MagicMock:
    """Create a mock streaming chunk with optional content and usage."""
    chunk = MagicMock()

    if content is not None:
        choice = MagicMock()
        choice.delta.content = content
        chunk.choices = [choice]
    else:
        chunk.choices = []

    if prompt_tokens is not None:
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = prompt_tokens
        chunk.usage.completion_tokens = completion_tokens or 0
    else:
        chunk.usage = None

    return chunk


# ---------------------------------------------------------------------------
# AzureFoundryProvider -- unit tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _AZURE_AVAILABLE, reason="azure-ai-inference not installed")
class TestAzureFoundryProvider:
    """Verify the Azure AI Foundry provider with mocked SDK calls."""

    def test_missing_endpoint_raises(self) -> None:
        """Empty endpoint produces a clear ValueError."""
        with pytest.raises(ValueError, match="endpoint is required"):
            AzureFoundryProvider(endpoint="", api_key="test-key")

    def test_missing_api_key_raises(self) -> None:
        """Empty API key produces a clear ValueError."""
        with pytest.raises(ValueError, match="API key is required"):
            AzureFoundryProvider(
                endpoint="https://test.services.ai.azure.com/models",
                api_key="",
            )

    @pytest.mark.asyncio
    async def test_complete_returns_response(self, sample_request: LLMRequest) -> None:
        """A successful Azure call returns a well-formed LLMResponse."""
        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        # Mock the streaming response: two content chunks + final usage chunk
        chunks = [
            _make_mock_chunk(content="The answer "),
            _make_mock_chunk(content="is 42."),
            _make_mock_chunk(content=None, prompt_tokens=50, completion_tokens=20),
        ]

        provider._client = MagicMock()
        provider._client.complete.return_value = iter(chunks)

        response = await provider.complete(sample_request)

        assert response.content == "The answer is 42."
        assert response.provider == "azure_foundry"
        assert response.model == "claude-sonnet-4-6"
        assert response.input_tokens == 50
        assert response.output_tokens == 20
        assert response.cost_usd > 0

    @pytest.mark.asyncio
    async def test_system_prompt_passed_correctly(
        self, sample_request: LLMRequest
    ) -> None:
        """The system prompt is included in the messages sent to Azure."""
        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        chunks = [
            _make_mock_chunk(content="Done."),
            _make_mock_chunk(content=None, prompt_tokens=10, completion_tokens=5),
        ]

        provider._client = MagicMock()
        provider._client.complete.return_value = iter(chunks)

        await provider.complete(sample_request)

        call_kwargs = provider._client.complete.call_args[1]
        messages = call_kwargs["messages"]

        # First message should be the system message
        assert len(messages) == 2
        # Check the system message content via the SDK model
        assert messages[0].content == "You are a helpful assistant."
        assert messages[1].content == "Summarise this document."

    @pytest.mark.asyncio
    async def test_no_system_prompt_omits_system_message(self) -> None:
        """When no system prompt is set, only the user message is sent."""
        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        chunks = [
            _make_mock_chunk(content="OK"),
            _make_mock_chunk(content=None, prompt_tokens=5, completion_tokens=2),
        ]

        provider._client = MagicMock()
        provider._client.complete.return_value = iter(chunks)

        request = LLMRequest(
            prompt="Hello",
            model="claude-sonnet-4-6",
            tier="standard",
            task_type="test",
        )
        await provider.complete(request)

        call_kwargs = provider._client.complete.call_args[1]
        messages = call_kwargs["messages"]
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_deployment_used_as_fallback_model(self) -> None:
        """When request.model is empty, the deployment name is used."""
        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
            deployment="my-claude-deployment",
        )

        chunks = [
            _make_mock_chunk(content="Result"),
            _make_mock_chunk(content=None, prompt_tokens=10, completion_tokens=5),
        ]

        provider._client = MagicMock()
        provider._client.complete.return_value = iter(chunks)

        request = LLMRequest(
            prompt="Test",
            model="",  # Empty -- should fall back to deployment
            tier="standard",
            task_type="test",
        )
        response = await provider.complete(request)

        call_kwargs = provider._client.complete.call_args[1]
        assert call_kwargs["model"] == "my-claude-deployment"
        assert response.model == "my-claude-deployment"

    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self, sample_request: LLMRequest) -> None:
        """Rate limit (429) triggers retry with exponential backoff."""
        from azure.core.exceptions import HttpResponseError

        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        rate_limit_exc = HttpResponseError(message="Rate limited", response=MagicMock())
        rate_limit_exc.status_code = 429

        success_chunks = [
            _make_mock_chunk(content="OK"),
            _make_mock_chunk(content=None, prompt_tokens=10, completion_tokens=5),
        ]

        call_count = {"n": 0}

        def mock_complete(**kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise rate_limit_exc
            return iter(success_chunks)

        provider._client = MagicMock()
        provider._client.complete.side_effect = mock_complete

        with patch("time.sleep"):
            response = await provider.complete(sample_request)

        assert response.content == "OK"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_retry_on_server_error(self, sample_request: LLMRequest) -> None:
        """Server errors (5xx) trigger retry."""
        from azure.core.exceptions import HttpResponseError

        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        server_err = HttpResponseError(message="Internal error", response=MagicMock())
        server_err.status_code = 500

        success_chunks = [
            _make_mock_chunk(content="Recovered"),
            _make_mock_chunk(content=None, prompt_tokens=10, completion_tokens=5),
        ]

        call_count = {"n": 0}

        def mock_complete(**kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise server_err
            return iter(success_chunks)

        provider._client = MagicMock()
        provider._client.complete.side_effect = mock_complete

        with patch("time.sleep"):
            response = await provider.complete(sample_request)

        assert response.content == "Recovered"
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, sample_request: LLMRequest) -> None:
        """Exhausting all retries raises a RuntimeError."""
        from azure.core.exceptions import HttpResponseError

        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        server_err = HttpResponseError(message="Persistent error", response=MagicMock())
        server_err.status_code = 500

        provider._client = MagicMock()
        provider._client.complete.side_effect = server_err

        with (
            patch("time.sleep"),
            pytest.raises(RuntimeError, match="failed after 3 attempts"),
        ):
            await provider.complete(sample_request)

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(
        self, sample_request: LLMRequest
    ) -> None:
        """Authentication errors (401) are not retried."""
        from azure.core.exceptions import HttpResponseError

        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        auth_err = HttpResponseError(message="Unauthorised", response=MagicMock())
        auth_err.status_code = 401

        provider._client = MagicMock()
        provider._client.complete.side_effect = auth_err

        with pytest.raises(HttpResponseError, match="Unauthorised"):
            await provider.complete(sample_request)

        # Should only have been called once (no retries)
        assert provider._client.complete.call_count == 1

    def test_complete_sync_works(self, sample_request: LLMRequest) -> None:
        """The synchronous complete_sync wrapper functions correctly."""
        provider = AzureFoundryProvider(
            endpoint="https://test.services.ai.azure.com/models",
            api_key="test-key",
        )

        chunks = [
            _make_mock_chunk(content="Sync result"),
            _make_mock_chunk(content=None, prompt_tokens=10, completion_tokens=5),
        ]

        provider._client = MagicMock()
        provider._client.complete.return_value = iter(chunks)

        response = provider.complete_sync(sample_request)

        assert response.content == "Sync result"
        assert response.provider == "azure_foundry"


# ---------------------------------------------------------------------------
# Graceful handling when azure-ai-inference is not installed
# ---------------------------------------------------------------------------


class TestAzureFoundryImportGuard:
    """Verify clear error when the Azure SDK is missing."""

    def test_unavailable_error_when_sdk_missing(self) -> None:
        """AzureFoundryUnavailableError is raised with a helpful message."""
        with (
            patch("grove.llm.azure_foundry._AZURE_AVAILABLE", False),
            pytest.raises(
                AzureFoundryUnavailableError,
                match="pip install grove-kb\\[azure\\]",
            ),
        ):
            AzureFoundryProvider(
                endpoint="https://test.services.ai.azure.com/models",
                api_key="test-key",
            )


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestAzureFoundryConfig:
    """Verify config loading with azure_foundry section."""

    def test_default_config_includes_azure_foundry(self) -> None:
        """The default config includes the azure_foundry provider section."""
        llm = DEFAULT_CONFIG["llm"]
        assert isinstance(llm, dict)
        providers = llm["providers"]
        assert "azure_foundry" in providers
        assert providers["azure_foundry"]["endpoint"] == "${AZURE_AI_ENDPOINT}"
        assert providers["azure_foundry"]["api_key"] == "${AZURE_AI_KEY}"

    def test_config_loads_azure_foundry_from_env(
        self, azure_config: GroveConfig
    ) -> None:
        """Azure Foundry config picks up environment variables correctly."""
        cfg = azure_config.llm.providers.azure_foundry
        assert cfg.endpoint == "https://test.services.ai.azure.com/models"
        assert cfg.api_key == "azure-test-key-fake"
        assert cfg.deployment == ""

    def test_config_azure_foundry_defaults_to_empty(
        self, grove_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without env vars set, Azure Foundry config fields are empty strings."""
        monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_AI_KEY", raising=False)
        loader = ConfigLoader(grove_root)
        config = loader.load()
        cfg = config.llm.providers.azure_foundry
        assert cfg.endpoint == ""
        assert cfg.api_key == ""


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _AZURE_AVAILABLE, reason="azure-ai-inference not installed")
class TestAzureFoundryRouting:
    """Verify the router dispatches to AzureFoundryProvider correctly."""

    @pytest.mark.asyncio
    async def test_router_routes_to_azure_foundry(
        self, grove_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a tier is configured for azure_foundry, the router uses it."""
        from grove.llm.router import LLMRouter

        # Write a config that routes standard tier to azure_foundry
        config_data = {
            "llm": {
                "providers": {
                    "anthropic": {"api_key": "sk-ant-test"},
                    "ollama": {"base_url": "http://localhost:11434"},
                    "azure_foundry": {
                        "endpoint": "https://test.services.ai.azure.com/models",
                        "api_key": "azure-test-key",
                        "deployment": "claude-sonnet-4-6",
                    },
                },
                "routing": {
                    "fast": {
                        "provider": "ollama",
                        "model": "llama3.2",
                        "fallback": {
                            "provider": "anthropic",
                            "model": "claude-haiku-4-5-20251001",
                        },
                    },
                    "standard": {
                        "provider": "azure_foundry",
                        "model": "claude-sonnet-4-6",
                    },
                    "powerful": {
                        "provider": "azure_foundry",
                        "model": "claude-opus-4-6",
                    },
                },
            },
            "budget": {"daily_limit_usd": 5.00, "warn_at_usd": 3.00},
        }

        config_path = grove_root / ".grove" / "config.yaml"
        config_path.write_text(
            yaml.dump(config_data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        loader = ConfigLoader(grove_root)
        config = loader.load()
        router = LLMRouter(config, grove_root)

        # Mock the Azure provider's complete method
        mock_response = LLMResponse(
            content="Azure result",
            model="claude-sonnet-4-6",
            provider="azure_foundry",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
        )

        with patch.object(
            AzureFoundryProvider,
            "complete",
            return_value=mock_response,
        ):
            request = LLMRequest(
                prompt="Test Azure routing",
                tier="standard",
                task_type="compile",
            )
            response = await router.complete(request)

        assert response.provider == "azure_foundry"
        assert response.model == "claude-sonnet-4-6"
        assert response.content == "Azure result"
