"""Tests for grove.config — ConfigLoader, StateManager, and defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from grove.config.defaults import DEFAULT_CONFIG, GROVE_DIRS
from grove.config.loader import ConfigLoader, GroveConfig
from grove.config.state import StateManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def grove_root(tmp_path: Path) -> Path:
    """Create a minimal grove directory with default config and state."""
    grove_dir = tmp_path / ".grove"
    grove_dir.mkdir()

    config_path = grove_dir / "config.yaml"
    config_path.write_text(
        yaml.dump(DEFAULT_CONFIG, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    state_path = grove_dir / "state.json"
    state_path.write_text("{}\n", encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    """Verify the default configuration matches the ARCH.md schema."""

    def test_default_config_has_all_top_level_keys(self) -> None:
        expected_keys = {"llm", "budget", "compile", "search", "git"}
        assert set(DEFAULT_CONFIG.keys()) == expected_keys

    def test_default_config_has_nested_providers(self) -> None:
        llm = DEFAULT_CONFIG["llm"]
        assert isinstance(llm, dict)
        assert "providers" in llm
        assert "anthropic" in llm["providers"]
        assert "ollama" in llm["providers"]

    def test_default_config_has_nested_routing(self) -> None:
        llm = DEFAULT_CONFIG["llm"]
        assert isinstance(llm, dict)
        routing = llm["routing"]
        assert "fast" in routing
        assert "standard" in routing
        assert "powerful" in routing

    def test_fast_tier_has_fallback(self) -> None:
        llm = DEFAULT_CONFIG["llm"]
        assert isinstance(llm, dict)
        fast = llm["routing"]["fast"]
        assert "fallback" in fast
        assert fast["fallback"]["provider"] == "anthropic"
        assert fast["fallback"]["model"] == "claude-haiku-4-5-20251001"

    def test_budget_defaults(self) -> None:
        budget = DEFAULT_CONFIG["budget"]
        assert isinstance(budget, dict)
        assert budget["daily_limit_usd"] == 5.00
        assert budget["warn_at_usd"] == 3.00

    def test_compile_defaults(self) -> None:
        compile_cfg = DEFAULT_CONFIG["compile"]
        assert isinstance(compile_cfg, dict)
        assert compile_cfg["quality_threshold"] == "partial"
        assert compile_cfg["phase"] == 0
        assert compile_cfg["max_output_tokens"] == 65536

    def test_grove_dirs_contains_required_directories(self) -> None:
        required = {
            ".grove",
            ".grove/prompts",
            ".grove/logs",
            "raw",
            "wiki",
            "queries",
            "outputs",
        }
        assert set(GROVE_DIRS) == required


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------


class TestConfigLoader:
    """Test reading and validating config.yaml."""

    def test_load_default_config(self, grove_root: Path) -> None:
        """The default config round-trips through YAML and validates."""
        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert isinstance(config, GroveConfig)
        assert config.llm.routing.fast.provider == "ollama"
        assert config.llm.routing.fast.model == "llama3.2"
        assert config.llm.routing.standard.provider == "anthropic"
        assert config.llm.routing.standard.model == "claude-sonnet-4-6"
        assert config.llm.routing.powerful.provider == "anthropic"
        assert config.llm.routing.powerful.model == "claude-opus-4-6"

    def test_load_preserves_nested_structure(self, grove_root: Path) -> None:
        """Config must keep the nested provider/routing structure."""
        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.llm.providers.ollama.base_url == "http://localhost:11434"
        assert config.llm.routing.fast.fallback is not None
        assert config.llm.routing.fast.fallback.provider == "anthropic"

    def test_load_budget_values(self, grove_root: Path) -> None:
        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.budget.daily_limit_usd == 5.00
        assert config.budget.warn_at_usd == 3.00

    def test_load_compile_values(self, grove_root: Path) -> None:
        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.compile.quality_threshold == "partial"
        assert config.compile.phase == 0
        assert config.compile.max_output_tokens == 65536

    def test_load_search_values(self, grove_root: Path) -> None:
        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.search.embedding_model == "nomic-embed-text"
        assert config.search.hybrid_alpha == 0.5

    def test_load_git_values(self, grove_root: Path) -> None:
        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.git.auto_commit is True
        assert config.git.commit_message_prefix == "grove:"

    def test_env_var_interpolation(
        self, grove_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """${ANTHROPIC_API_KEY} in config is replaced with env value."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123")

        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.llm.providers.anthropic.api_key == "sk-ant-test-key-123"

    def test_missing_env_var_becomes_empty_string(
        self, grove_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing env vars resolve to empty string, not the placeholder."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        loader = ConfigLoader(grove_root)
        config = loader.load()

        assert config.llm.providers.anthropic.api_key == ""

    def test_missing_config_file_raises(self, tmp_path: Path) -> None:
        """ConfigLoader raises FileNotFoundError if config.yaml is absent."""
        loader = ConfigLoader(tmp_path)

        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            loader.load()

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """ConfigLoader raises ValueError if the YAML root is not a mapping."""
        grove_dir = tmp_path / ".grove"
        grove_dir.mkdir()
        (grove_dir / "config.yaml").write_text("- just a list\n", encoding="utf-8")

        loader = ConfigLoader(tmp_path)

        with pytest.raises(ValueError, match="Expected a YAML mapping"):
            loader.load()

    def test_invalid_quality_threshold_raises(self, tmp_path: Path) -> None:
        """Invalid quality_threshold value is rejected by Pydantic."""
        import copy

        config_data = copy.deepcopy(DEFAULT_CONFIG)
        config_data["compile"]["quality_threshold"] = "excellent"

        grove_dir = tmp_path / ".grove"
        grove_dir.mkdir()
        (grove_dir / "config.yaml").write_text(
            yaml.dump(config_data, default_flow_style=False),
            encoding="utf-8",
        )

        loader = ConfigLoader(tmp_path)

        with pytest.raises(ValueError):
            loader.load()


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------


class TestStateManager:
    """Test reading and writing .grove/state.json."""

    def test_read_empty_state(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        assert sm.read_all() == {}

    def test_get_returns_default_for_missing_key(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        assert sm.get("nonexistent") is None
        assert sm.get("nonexistent", "fallback") == "fallback"

    def test_set_and_get(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        sm.set("checksums", {"file1.md": "abc123"})

        assert sm.get("checksums") == {"file1.md": "abc123"}

    def test_set_persists_to_disk(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        sm.set("test_key", "test_value")

        # Read directly from disk to verify persistence
        raw = json.loads(
            (grove_root / ".grove" / "state.json").read_text(encoding="utf-8")
        )
        assert raw["test_key"] == "test_value"

    def test_delete_removes_key(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        sm.set("to_delete", "value")
        sm.delete("to_delete")

        assert sm.get("to_delete") is None

    def test_delete_nonexistent_key_is_safe(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        sm.delete("never_existed")  # should not raise

    def test_write_all_replaces_state(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        sm.set("old_key", "old_value")
        sm.write_all({"new_key": "new_value"})

        assert sm.get("old_key") is None
        assert sm.get("new_key") == "new_value"

    def test_write_all_rejects_non_dict(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)

        with pytest.raises(TypeError, match="State must be a dict"):
            sm.write_all([1, 2, 3])  # type: ignore[arg-type]

    def test_invalidate_cache_forces_disk_read(self, grove_root: Path) -> None:
        sm = StateManager(grove_root)
        sm.set("cached", "value")

        # Tamper with the file directly
        state_path = grove_root / ".grove" / "state.json"
        state_path.write_text('{"tampered": true}\n', encoding="utf-8")

        # Cache still has old value
        assert sm.get("cached") == "value"

        # After invalidation, reads from disk
        sm.invalidate_cache()
        assert sm.get("cached") is None
        assert sm.get("tampered") is True

    def test_missing_state_file_returns_empty(self, tmp_path: Path) -> None:
        """StateManager returns empty dict if state.json does not exist."""
        grove_dir = tmp_path / ".grove"
        grove_dir.mkdir()
        # Do not create state.json

        sm = StateManager(tmp_path)
        assert sm.read_all() == {}

    def test_atomic_write(self, grove_root: Path) -> None:
        """Verify no .tmp file is left behind after a write."""
        sm = StateManager(grove_root)
        sm.set("atomic_test", True)

        tmp_file = grove_root / ".grove" / "state.json.tmp"
        assert not tmp_file.exists()


# ---------------------------------------------------------------------------
# grove init CLI command
# ---------------------------------------------------------------------------


class TestGroveInit:
    """Test the grove init command via the Typer test runner."""

    def test_init_creates_directory_structure(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

        assert result.exit_code == 0

        for dir_name in GROVE_DIRS:
            assert (tmp_path / dir_name).is_dir(), f"Missing directory: {dir_name}"

    def test_init_writes_config_yaml(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        runner.invoke(app, ["init", "--dir", str(tmp_path)])

        config_path = tmp_path / ".grove" / "config.yaml"
        assert config_path.exists()

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "llm" in data
        assert "providers" in data["llm"]
        assert "routing" in data["llm"]

    def test_init_writes_state_json(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        runner.invoke(app, ["init", "--dir", str(tmp_path)])

        state_path = tmp_path / ".grove" / "state.json"
        assert state_path.exists()
        assert json.loads(state_path.read_text(encoding="utf-8")) == {}

    def test_init_writes_gitignore(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        runner.invoke(app, ["init", "--dir", str(tmp_path)])

        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()

        content = gitignore.read_text(encoding="utf-8")
        assert ".grove/search.db" in content
        assert ".grove/logs/" in content

    def test_init_with_name(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["init", "My Research", "--dir", str(tmp_path)])

        assert result.exit_code == 0

        config_path = tmp_path / ".grove" / "config.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert data["name"] == "My Research"

    def test_init_refuses_reinit(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        runner.invoke(app, ["init", "--dir", str(tmp_path)])

        # Second init should fail
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
        assert result.exit_code == 1

    def test_init_config_is_loadable(self, tmp_path: Path) -> None:
        """Config written by init can be loaded by ConfigLoader."""
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        runner.invoke(app, ["init", "--dir", str(tmp_path)])

        loader = ConfigLoader(tmp_path)
        config = loader.load()

        assert isinstance(config, GroveConfig)
        assert config.llm.routing.fast.provider == "ollama"

    def test_init_output_mentions_success(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from grove.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["init", "Test KB", "--dir", str(tmp_path)])

        assert "initialised successfully" in result.output
        assert "Test KB" in result.output
