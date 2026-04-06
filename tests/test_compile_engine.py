"""Tests for CompileEngine -- Phase 0 compilation orchestration.

Covers:
- Full pipeline: mock LLM, verify articles written and committed
- Dry run: no LLM call, returns estimates
- Ratchet failure: articles NOT committed, error reported
- NDJSON output: verify progress events emitted correctly
- Empty sources: appropriate error message
- Existing wiki loaded for recompilation
- Pinned articles preserved through compile
- Cost recorded via CostTracker
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from grove.compile.engine import (
    CompileEngine,
    CompileResult,
    NoSourcesError,
    RatchetFailedError,
)
from grove.compile.prompt import PromptBuilder
from grove.compile.ratchet import RatchetResult
from grove.config.loader import GroveConfig
from grove.llm.models import LLMResponse
from grove.llm.router import LLMRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A minimal LLM response containing two well-formed articles.
_MOCK_LLM_RESPONSE = """\
<!-- grove:article wiki/topics/overview.md -->
---
title: "Overview"
compiled_from:
  - raw/articles/source-a.md
concepts: [overview, testing]
summary: "An overview article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Overview

This is an overview compiled from sources [source: raw/articles/source-a.md].

<!-- grove:article wiki/topics/details.md -->
---
title: "Details"
compiled_from:
  - raw/articles/source-a.md
concepts: [details, testing]
summary: "A details article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Details

More detailed information [source: raw/articles/source-a.md].
"""

_DEFAULT_CONFIG_YAML = {
    "llm": {
        "providers": {
            "anthropic": {"api_key": "sk-test-key"},
            "ollama": {"base_url": "http://localhost:11434"},
        },
        "routing": {
            "fast": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
            },
            "standard": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            "powerful": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
            },
        },
    },
    "budget": {"daily_limit_usd": 5.00, "warn_at_usd": 3.00},
    "compile": {
        "quality_threshold": "partial",
        "phase": 0,
        "max_output_tokens": 65536,
    },
    "git": {"auto_commit": True, "commit_message_prefix": "grove:"},
}


def _setup_grove(tmp_path: Path) -> Path:
    """Create a minimal grove directory structure for testing."""
    grove_root = tmp_path / "test-grove"
    grove_root.mkdir()
    (grove_root / ".grove").mkdir()
    (grove_root / ".grove" / "logs").mkdir()
    (grove_root / ".grove" / "prompts").mkdir()
    (grove_root / "raw" / "articles").mkdir(parents=True)
    (grove_root / "wiki").mkdir()

    # Write config
    config_path = grove_root / ".grove" / "config.yaml"
    config_path.write_text(
        yaml.dump(_DEFAULT_CONFIG_YAML, default_flow_style=False),
        encoding="utf-8",
    )

    # Write state.json
    state_path = grove_root / ".grove" / "state.json"
    state_path.write_text(json.dumps({}) + "\n", encoding="utf-8")

    # Write an empty manifest in the format ManifestWriter produces.
    manifest_path = grove_root / "raw" / "_manifest.md"
    manifest_path.write_text(
        """\
---
total_sources: 0
last_updated: "2026-04-03T14:00:00Z"
---

| Source | Quality | Words | Concepts | Ingested |
|--------|---------|-------|----------|----------|
""",
        encoding="utf-8",
    )

    return grove_root


def _add_source(grove_root: Path, name: str = "source-a.md") -> Path:
    """Add a raw source file and update the manifest."""
    source_path = grove_root / "raw" / "articles" / name
    source_path.write_text(
        """\
---
grove_summary: "A summary of the source material covering key concepts."
grove_concepts: [testing, overview]
---

# Source A

This is the raw source content with important facts and details.
It contains enough material for the compiler to work with.
""",
        encoding="utf-8",
    )

    # Update manifest to include this source.
    # The table must have 5 columns matching ManifestWriter's format:
    # Source | Quality | Words | Concepts | Ingested
    manifest_path = grove_root / "raw" / "_manifest.md"
    rel_path = f"raw/articles/{name}"
    manifest_path.write_text(
        f"""\
---
total_sources: 1
last_updated: "2026-04-03T14:00:00Z"
---

| Source | Quality | Words | Concepts | Ingested |
|--------|---------|-------|----------|----------|
| {rel_path} | good | 50 | testing, overview | 2026-04-03 |
""",
        encoding="utf-8",
    )

    return source_path


def _make_config(grove_root: Path) -> GroveConfig:
    """Load the GroveConfig from the test grove."""
    from grove.config.loader import ConfigLoader

    return ConfigLoader(grove_root).load()


def _make_mock_router() -> MagicMock:
    """Build a mock LLMRouter that returns a canned response."""
    router = MagicMock(spec=LLMRouter)
    router.complete_sync.return_value = LLMResponse(
        content=_MOCK_LLM_RESPONSE,
        model="claude-sonnet-4-6",
        provider="anthropic",
        input_tokens=5000,
        output_tokens=2000,
        cost_usd=0.045,
    )
    # Mock the cost_tracker attribute.
    router.cost_tracker = MagicMock()
    return router


# ---------------------------------------------------------------------------
# Tests: full pipeline
# ---------------------------------------------------------------------------


class TestCompileFullPipeline:
    """Full compilation: mock LLM, verify articles written and committed."""

    def test_full_compile_writes_articles(self, tmp_path: Path) -> None:
        """Articles should be written to wiki/ after a successful compile."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()

        # Use real PromptBuilder with the shipped prompts.
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        # Patch AutoCommitter so we do not need a real git repo.
        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            result = engine.compile()

        # Verify articles were written.
        assert (grove_root / "wiki" / "topics" / "overview.md").exists()
        assert (grove_root / "wiki" / "topics" / "details.md").exists()

        # Verify result.
        assert result.articles_created == 2
        assert result.articles_updated == 0
        assert result.cost_usd == 0.045
        assert result.ratchet_passed is True
        assert result.dry_run is False

        # Verify LLM was called once with standard tier.
        router.complete_sync.assert_called_once()
        call_args = router.complete_sync.call_args[0][0]
        assert call_args.tier == "standard"
        assert call_args.task_type == "compile"

    def test_full_compile_updates_state(self, tmp_path: Path) -> None:
        """State.json should be updated with compile metadata."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            engine.compile()

        state_path = grove_root / ".grove" / "state.json"
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_compile_source_count" in state_data
        assert "last_compile_timestamp" in state_data

    def test_full_compile_calls_git_commit(self, tmp_path: Path) -> None:
        """AutoCommitter.commit_compile should be called after a successful compile."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            engine.compile()

        mock_committer.commit_compile.assert_called_once()
        call_kwargs = mock_committer.commit_compile.call_args
        assert call_kwargs[1]["cost_usd"] == 0.045

    def test_recompile_counts_updates(self, tmp_path: Path) -> None:
        """Articles that already exist should be counted as updates."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Pre-create one article so it counts as an update.
        existing_path = grove_root / "wiki" / "topics" / "overview.md"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("# Old overview\n", encoding="utf-8")

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            result = engine.compile()

        assert result.articles_created == 1  # details.md
        assert result.articles_updated == 1  # overview.md


# ---------------------------------------------------------------------------
# Tests: dry run
# ---------------------------------------------------------------------------


class TestCompileDryRun:
    """Dry run: no LLM call, returns token and cost estimates."""

    def test_dry_run_returns_estimates(self, tmp_path: Path) -> None:
        """Dry run should return estimated tokens and cost."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        result = engine.compile(dry_run=True)

        assert result.dry_run is True
        assert result.estimated_tokens is not None
        assert result.estimated_tokens > 0
        assert result.estimated_cost is not None
        assert result.estimated_cost > 0

    def test_dry_run_does_not_call_llm(self, tmp_path: Path) -> None:
        """Dry run must never make an LLM call."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        engine.compile(dry_run=True)

        router.complete_sync.assert_not_called()

    def test_dry_run_does_not_write_files(self, tmp_path: Path) -> None:
        """Dry run must never modify the filesystem."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        engine.compile(dry_run=True)

        # wiki/ should remain empty.
        wiki_files = list((grove_root / "wiki").rglob("*.md"))
        assert wiki_files == []


# ---------------------------------------------------------------------------
# Tests: ratchet failure
# ---------------------------------------------------------------------------


class TestCompileRatchetFailure:
    """Ratchet failure: articles NOT committed, error raised."""

    def test_ratchet_failure_raises_error(self, tmp_path: Path) -> None:
        """A failing ratchet should raise RatchetFailedError."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        # Mock the ratchet to return a failure.
        failing_result = RatchetResult(
            timestamp="2026-04-03T14:00:00Z",
            passed=False,
            blocking_failures=["provenance_coverage"],
            warnings=[],
            details={
                "provenance_coverage": {
                    "score": 0.30,
                    "threshold": 0.50,
                    "severity": "BLOCK",
                }
            },
        )

        with (
            patch("grove.compile.engine.AutoCommitter") as mock_committer_cls,
            patch("grove.compile.engine.QualityRatchet") as mock_ratchet_cls,
        ):
            mock_ratchet = MagicMock()
            mock_ratchet.check.return_value = failing_result
            mock_ratchet.save_report.return_value = Path("/tmp/report.json")
            mock_ratchet_cls.return_value = mock_ratchet

            mock_committer = MagicMock()
            mock_committer_cls.return_value = mock_committer

            with pytest.raises(RatchetFailedError) as exc_info:
                engine.compile()

            # Verify the error carries the ratchet result.
            assert "provenance_coverage" in exc_info.value.result.blocking_failures

            # Verify git commit was NOT called.
            mock_committer.commit_compile.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: NDJSON progress events
# ---------------------------------------------------------------------------


class TestCompileNDJSON:
    """NDJSON output: verify progress events emitted correctly."""

    def test_progress_callback_receives_all_steps(self, tmp_path: Path) -> None:
        """The progress callback should receive events for each pipeline stage."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        events: list[tuple[str, int, str]] = []

        def _capture(step: str, pct: int, detail: str) -> None:
            events.append((step, pct, detail))

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            engine.compile(progress_callback=_capture)

        # Extract step names.
        steps = [e[0] for e in events]
        assert "loading_sources" in steps
        assert "building_prompt" in steps
        assert "llm_call" in steps
        assert "parsing_articles" in steps
        assert "writing_articles" in steps
        assert "quality_ratchet" in steps
        assert "git_commit" in steps

        # Progress percentages should be monotonically increasing.
        pcts = [e[1] for e in events]
        assert pcts == sorted(pcts)

    def test_dry_run_progress_callback_stops_early(self, tmp_path: Path) -> None:
        """Dry run should emit loading and building events but not LLM or later."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        events: list[tuple[str, int, str]] = []

        def _capture(step: str, pct: int, detail: str) -> None:
            events.append((step, pct, detail))

        engine.compile(dry_run=True, progress_callback=_capture)

        steps = [e[0] for e in events]
        assert "loading_sources" in steps
        assert "building_prompt" in steps
        # LLM call and later steps should NOT be present.
        assert "llm_call" not in steps
        assert "writing_articles" not in steps


# ---------------------------------------------------------------------------
# Tests: empty sources
# ---------------------------------------------------------------------------


class TestCompileEmptySources:
    """Empty sources: appropriate error message."""

    def test_no_sources_raises_error(self, tmp_path: Path) -> None:
        """Compiling with no ingested sources should raise NoSourcesError."""
        grove_root = _setup_grove(tmp_path)
        # Do NOT add any sources.
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with pytest.raises(NoSourcesError, match="No sources found"):
            engine.compile()


# ---------------------------------------------------------------------------
# Tests: existing wiki loaded for recompilation
# ---------------------------------------------------------------------------


class TestCompileExistingWiki:
    """Existing wiki articles should be included in the recompilation context."""

    def test_existing_wiki_included_in_prompt(self, tmp_path: Path) -> None:
        """The prompt should contain existing wiki content when recompiling."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Create an existing wiki article.
        existing_article = grove_root / "wiki" / "existing.md"
        existing_article.write_text(
            "# Existing Article\n\nSome existing content.\n",
            encoding="utf-8",
        )

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            engine.compile()

        # Verify the LLM received the existing wiki content in the prompt.
        call_args = router.complete_sync.call_args[0][0]
        assert "Existing Article" in call_args.prompt
        assert "wiki:file" in call_args.prompt

    def test_empty_wiki_passes_empty_string(self, tmp_path: Path) -> None:
        """When wiki/ is empty, the existing_wiki variable should be empty."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)

        engine = CompileEngine(
            grove_root,
            _make_config(grove_root),
            _make_mock_router(),
            PromptBuilder(grove_root),
        )

        result = engine._load_existing_wiki()
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: pinned articles preserved
# ---------------------------------------------------------------------------


class TestCompilePinnedArticles:
    """Pinned articles should be preserved through compile."""

    def test_pinned_article_not_overwritten(self, tmp_path: Path) -> None:
        """An article with pinned: true should survive recompilation."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Create a pinned article at a path the LLM will try to write.
        pinned_path = grove_root / "wiki" / "topics" / "overview.md"
        pinned_path.parent.mkdir(parents=True, exist_ok=True)
        pinned_content = """\
---
title: "Pinned Overview"
compiled_from:
  - raw/articles/source-a.md
concepts: [overview]
summary: "A pinned article."
last_compiled: "2026-04-03T14:00:00Z"
pinned: true
---

# Pinned Overview

This content must not be overwritten.
"""
        pinned_path.write_text(pinned_content, encoding="utf-8")

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            result = engine.compile()

        # The pinned article should still have its original content.
        actual_content = pinned_path.read_text(encoding="utf-8")
        assert "This content must not be overwritten." in actual_content

        # Result should report the pinned skip.
        assert result.articles_skipped_pinned == 1


# ---------------------------------------------------------------------------
# Tests: zero articles from LLM
# ---------------------------------------------------------------------------


class TestCompileZeroArticles:
    """When the LLM returns unparseable output, compile should fail gracefully."""

    def test_zero_articles_raises_compile_error(self, tmp_path: Path) -> None:
        """If the parser returns zero articles, CompileError should be raised."""
        grove_root = _setup_grove(tmp_path)
        _add_source(grove_root)
        config = _make_config(grove_root)
        prompt_builder = PromptBuilder(grove_root)

        # Router returns garbage that the parser cannot parse.
        router = _make_mock_router()
        router.complete_sync.return_value = LLMResponse(
            content="This is not a valid article output.",
            model="claude-sonnet-4-6",
            provider="anthropic",
            input_tokens=100,
            output_tokens=10,
            cost_usd=0.001,
        )

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        from grove.compile.engine import CompileError

        with pytest.raises(CompileError, match="no parseable articles"):
            engine.compile()


# ---------------------------------------------------------------------------
# Tests: CompileResult model
# ---------------------------------------------------------------------------


class TestCompileResultModel:
    """Verify the CompileResult Pydantic model."""

    def test_default_values(self) -> None:
        """CompileResult should have sensible defaults."""
        result = CompileResult()
        assert result.articles_created == 0
        assert result.articles_updated == 0
        assert result.cost_usd == 0.0
        assert result.ratchet_passed is True
        assert result.dry_run is False
        assert result.estimated_tokens is None
        assert result.estimated_cost is None

    def test_dry_run_result(self) -> None:
        """Dry run results should carry estimates."""
        result = CompileResult(
            dry_run=True,
            estimated_tokens=50000,
            estimated_cost=0.45,
        )
        assert result.dry_run is True
        assert result.estimated_tokens == 50000
        assert result.estimated_cost == 0.45
