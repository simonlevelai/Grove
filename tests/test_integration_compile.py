"""Integration tests: ingest to compile pipeline (TASK-033).

End-to-end tests covering the full compile pipeline in a temporary
directory with all LLM calls mocked at the provider boundary.

Tests:
- Full compile: init grove, create raw sources, mock LLM response with
  2 articles, verify articles written to wiki/, git commit exists.
- Ratchet blocks on low provenance: mock LLM response with no citations,
  verify compile raises RatchetFailedError.
- Rollback restores wiki: compile, then rollback, verify wiki state
  matches pre-compile.

All LLM calls mocked via ``LLMRouter.complete_sync``. Tests run without
network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import git
import pytest
import yaml

from grove.compile.engine import CompileEngine, RatchetFailedError
from grove.compile.prompt import PromptBuilder
from grove.config.loader import ConfigLoader, GroveConfig
from grove.git.rollback import RollbackManager
from grove.llm.models import LLMResponse
from grove.llm.router import LLMRouter

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
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

# Mock LLM response with two well-formed articles.  Uses the exact
# article separator format from ARCH.md with YAML front matter,
# [source: ...] citations, and grove:article markers.
_MOCK_TWO_ARTICLE_RESPONSE = """\
<!-- grove:article wiki/topics/test-topic.md -->
---
title: "Test Topic"
compiled_from: [raw/test.md]
concepts: [testing]
summary: "A test article."
last_compiled: "2026-04-03"
---
# Test Topic
This is about testing [source: test.md].

<!-- grove:article wiki/topics/second-topic.md -->
---
title: "Second Topic"
compiled_from: [raw/test.md]
concepts: [testing, second]
summary: "A second test article."
last_compiled: "2026-04-03"
---
# Second Topic
More content about the second topic [source: test.md].
"""

# Mock LLM response with no citations -- triggers provenance ratchet block.
_MOCK_NO_CITATIONS_RESPONSE = """\
<!-- grove:article wiki/topics/uncited-topic.md -->
---
title: "Uncited Topic"
compiled_from: [raw/test.md]
concepts: [testing]
summary: "An article with no citations."
last_compiled: "2026-04-03"
---
# Uncited Topic
The population increased by 30% in 2024.
Revenue grew more than expected.
Because of new policies, outcomes improved.
Studies show this trend is accelerating.
The budget decreased by 15% last year.
Data indicates a further 10% drop next quarter.
According to research shows the trend will continue.
The number of users rose by 50000 in 2025.
"""


def _init_grove(tmp_path: Path) -> Path:
    """Create a complete grove directory structure with git repo.

    Returns the grove root path, fully initialised with config, state,
    manifest, raw source, and an initial git commit.
    """
    grove_root = tmp_path / "integration-grove"
    grove_root.mkdir()

    # Directory structure
    (grove_root / ".grove" / "logs").mkdir(parents=True)
    (grove_root / ".grove" / "prompts").mkdir(parents=True)
    (grove_root / "raw" / "articles").mkdir(parents=True)
    (grove_root / "wiki").mkdir()
    (grove_root / "queries").mkdir()
    (grove_root / "outputs").mkdir()

    # Config
    config_path = grove_root / ".grove" / "config.yaml"
    config_path.write_text(
        yaml.dump(_DEFAULT_CONFIG, default_flow_style=False),
        encoding="utf-8",
    )

    # State
    state_path = grove_root / ".grove" / "state.json"
    state_path.write_text(json.dumps({}) + "\n", encoding="utf-8")

    # Raw source file with front matter
    source_path = grove_root / "raw" / "test.md"
    source_path.write_text(
        """\
---
grove_summary: "A summary of the test source covering key concepts."
grove_concepts: [testing, overview]
---

# Test Source

This is the raw source content with important facts and details.
It contains enough material for the compiler to work with.
""",
        encoding="utf-8",
    )

    # Manifest referencing the source
    manifest_path = grove_root / "raw" / "_manifest.md"
    manifest_path.write_text(
        """\
---
total_sources: 1
last_updated: "2026-04-03T14:00:00Z"
---

| Source | Quality | Words | Concepts | Ingested |
|--------|---------|-------|----------|----------|
| raw/test.md | good | 50 | testing, overview | 2026-04-03 |
""",
        encoding="utf-8",
    )

    # Initialise git repo with initial commit
    repo = git.Repo.init(grove_root)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@grove.test").release()
    repo.git.add("--all")
    repo.index.commit("initial: grove init")

    return grove_root


def _load_config(grove_root: Path) -> GroveConfig:
    """Load and validate GroveConfig from the test grove."""
    return ConfigLoader(grove_root).load()


def _make_mock_router(response_content: str) -> MagicMock:
    """Build a mock LLMRouter returning the given content.

    The contradiction check in the ratchet also calls complete_sync,
    so we return "NONE" for any call after the first (compilation).
    """
    router = MagicMock(spec=LLMRouter)

    compile_response = LLMResponse(
        content=response_content,
        model="claude-sonnet-4-6",
        provider="anthropic",
        input_tokens=5000,
        output_tokens=2000,
        cost_usd=0.045,
    )

    contradiction_response = LLMResponse(
        content="NONE",
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        input_tokens=100,
        output_tokens=10,
        cost_usd=0.001,
    )

    # First call is the compile; subsequent calls are contradiction checks.
    router.complete_sync.side_effect = [
        compile_response,
        contradiction_response,
        contradiction_response,
        contradiction_response,
    ]

    router.cost_tracker = MagicMock()
    return router


# ---------------------------------------------------------------------------
# Test: full compile pipeline
# ---------------------------------------------------------------------------


class TestFullCompilePipeline:
    """Init grove, create raw sources, mock LLM, verify articles and git commit."""

    def test_full_compile_writes_articles_and_commits(self, tmp_path: Path) -> None:
        """Articles should be written to wiki/ and a git commit should exist."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_TWO_ARTICLE_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        result = engine.compile()

        # Verify articles were written to disk.
        topic_path = grove_root / "wiki" / "topics" / "test-topic.md"
        second_path = grove_root / "wiki" / "topics" / "second-topic.md"
        assert topic_path.exists(), "test-topic.md should exist in wiki/topics/"
        assert second_path.exists(), "second-topic.md should exist in wiki/topics/"

        # Verify article content includes front matter and body.
        topic_content = topic_path.read_text(encoding="utf-8")
        assert 'title: "Test Topic"' in topic_content
        assert "[source: test.md]" in topic_content

        second_content = second_path.read_text(encoding="utf-8")
        assert 'title: "Second Topic"' in second_content
        assert "[source: test.md]" in second_content

        # Verify result statistics.
        assert result.articles_created == 2
        assert result.articles_updated == 0
        assert result.ratchet_passed is True
        assert result.cost_usd == 0.045

        # Verify a git commit exists for the compile.
        repo = git.Repo(grove_root)
        head_message = repo.head.commit.message
        assert "grove:" in head_message

    def test_full_compile_updates_state_json(self, tmp_path: Path) -> None:
        """State.json should record the compile metadata after compilation."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_TWO_ARTICLE_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        engine.compile()

        state_path = grove_root / ".grove" / "state.json"
        state_data = json.loads(state_path.read_text(encoding="utf-8"))

        assert "last_compile_source_count" in state_data
        assert "last_compile_timestamp" in state_data
        assert state_data["last_compile_source_count"] > 0

    def test_full_compile_llm_called_with_correct_tier(self, tmp_path: Path) -> None:
        """The compile LLM call should use the standard tier."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_TWO_ARTICLE_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        engine.compile()

        # First call is the compile call.
        first_call = router.complete_sync.call_args_list[0]
        request = first_call[0][0]
        assert request.tier == "standard"
        assert request.task_type == "compile"

    def test_full_compile_ratchet_report_written(self, tmp_path: Path) -> None:
        """A ratchet report JSON file should be created in .grove/logs/."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_TWO_ARTICLE_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        engine.compile()

        logs_dir = grove_root / ".grove" / "logs"
        ratchet_files = list(logs_dir.glob("ratchet-*.json"))
        assert len(ratchet_files) >= 1, "At least one ratchet report should exist"

        # Verify it is valid JSON with the expected structure.
        report_data = json.loads(ratchet_files[0].read_text(encoding="utf-8"))
        assert "passed" in report_data
        assert "details" in report_data


# ---------------------------------------------------------------------------
# Test: ratchet blocks on low provenance
# ---------------------------------------------------------------------------


class TestRatchetBlocksOnLowProvenance:
    """Mock LLM response with no citations, verify RatchetFailedError raised."""

    def test_ratchet_blocks_compile_with_no_citations(self, tmp_path: Path) -> None:
        """Compile should raise RatchetFailedError when provenance is below 50%."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_NO_CITATIONS_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with pytest.raises(RatchetFailedError) as exc_info:
            engine.compile()

        # Verify the error carries provenance_coverage as a blocking failure.
        assert "provenance_coverage" in exc_info.value.result.blocking_failures

    def test_ratchet_block_prevents_git_commit(self, tmp_path: Path) -> None:
        """When ratchet blocks, no grove: compile commit should be created."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_NO_CITATIONS_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        repo = git.Repo(grove_root)
        commit_count_before = len(list(repo.iter_commits()))

        with pytest.raises(RatchetFailedError):
            engine.compile()

        # No new compile commit should have been created.
        commit_count_after = len(list(repo.iter_commits()))
        assert commit_count_after == commit_count_before

    def test_ratchet_report_saved_even_on_failure(self, tmp_path: Path) -> None:
        """The ratchet report should be written even when the ratchet blocks."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_NO_CITATIONS_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with pytest.raises(RatchetFailedError):
            engine.compile()

        logs_dir = grove_root / ".grove" / "logs"
        ratchet_files = list(logs_dir.glob("ratchet-*.json"))
        assert len(ratchet_files) >= 1

        report_data = json.loads(ratchet_files[0].read_text(encoding="utf-8"))
        assert report_data["passed"] is False


# ---------------------------------------------------------------------------
# Test: rollback restores wiki
# ---------------------------------------------------------------------------


class TestRollbackRestoresWiki:
    """Compile, then rollback, verify wiki state matches pre-compile."""

    def test_rollback_last_reverts_compile(self, tmp_path: Path) -> None:
        """Rolling back the last compile should restore wiki/ to pre-compile."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)
        router = _make_mock_router(_MOCK_TWO_ARTICLE_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)

        # Record wiki state before compile (should be empty).
        wiki_dir = grove_root / "wiki"
        wiki_files_before = set(
            str(f.relative_to(grove_root)) for f in wiki_dir.rglob("*.md")
        )

        engine = CompileEngine(grove_root, config, router, prompt_builder)
        engine.compile()

        # Verify articles exist after compile.
        assert (wiki_dir / "topics" / "test-topic.md").exists()
        assert (wiki_dir / "topics" / "second-topic.md").exists()

        # Rollback the last grove commit.
        rollback = RollbackManager(grove_root)
        revert_sha = rollback.rollback_last()

        # Verify the revert commit was created.
        assert revert_sha is not None
        repo = git.Repo(grove_root)
        assert "Revert" in repo.head.commit.message

        # Verify wiki articles were removed by the revert.
        assert not (wiki_dir / "topics" / "test-topic.md").exists()
        assert not (wiki_dir / "topics" / "second-topic.md").exists()

        # Verify wiki state matches pre-compile.
        wiki_files_after = set(
            str(f.relative_to(grove_root)) for f in wiki_dir.rglob("*.md")
        )
        assert wiki_files_after == wiki_files_before

    def test_rollback_to_specific_commit(self, tmp_path: Path) -> None:
        """Rolling back to a specific commit should restore wiki/ to that state."""
        grove_root = _init_grove(tmp_path)
        config = _load_config(grove_root)

        # First compile.
        router_1 = _make_mock_router(_MOCK_TWO_ARTICLE_RESPONSE)
        prompt_builder = PromptBuilder(grove_root)
        engine = CompileEngine(grove_root, config, router_1, prompt_builder)
        engine.compile()

        repo = git.Repo(grove_root)
        first_compile_sha = repo.head.commit.hexsha

        # Verify first compile articles.
        wiki_dir = grove_root / "wiki"
        assert (wiki_dir / "topics" / "test-topic.md").exists()

        # Second compile with a different response (adds a third article).
        second_response = _MOCK_TWO_ARTICLE_RESPONSE + """\

<!-- grove:article wiki/topics/third-topic.md -->
---
title: "Third Topic"
compiled_from: [raw/test.md]
concepts: [testing, third]
summary: "A third article."
last_compiled: "2026-04-03"
---
# Third Topic
Content for the third topic [source: test.md].
"""
        router_2 = _make_mock_router(second_response)
        engine_2 = CompileEngine(grove_root, config, router_2, prompt_builder)
        engine_2.compile()

        # Third article should now exist.
        assert (wiki_dir / "topics" / "third-topic.md").exists()

        # Rollback to the first compile commit.
        rollback = RollbackManager(grove_root)
        rollback.rollback_to(first_compile_sha)

        # The third article should be gone; the first two should remain.
        assert (wiki_dir / "topics" / "test-topic.md").exists()
        assert (wiki_dir / "topics" / "second-topic.md").exists()
        assert not (wiki_dir / "topics" / "third-topic.md").exists()
