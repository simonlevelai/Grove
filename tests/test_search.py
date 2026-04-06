"""Tests for the search module — Chunker and FTSIndex.

Covers:
- Chunker splits article into correct number of chunks
- Chunker respects overlap between consecutive chunks
- Chunker handles empty content
- Chunker handles short content (single chunk)
- FTSIndex.build creates database and indexes articles
- FTSIndex.search returns matching articles
- FTSIndex.search deduplicates by article (returns best chunk)
- FTSIndex.search returns empty list for no matches
- FTSIndex.search handles missing database gracefully
- CLI grove search displays results
- Index rebuild after compile (engine integration)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from grove.search.chunker import Chunker, _estimate_tokens
from grove.search.fts import FTSIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def _make_article(title: str, summary: str, body: str) -> str:
    """Build a wiki article with YAML front matter."""
    return f"""\
---
title: "{title}"
summary: "{summary}"
compiled_from:
  - raw/articles/source.md
concepts: [testing]
last_compiled: "2026-04-03T14:00:00Z"
---

{body}
"""


def _build_wiki(tmp_path: Path, articles: dict[str, str]) -> Path:
    """Create a fake grove with wiki articles.

    *articles* maps relative paths (e.g. "wiki/topics/foo.md") to content.
    Returns the grove root.
    """
    grove_root = tmp_path / "grove"
    (grove_root / ".grove").mkdir(parents=True)
    for rel_path, content in articles.items():
        full_path = grove_root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
    return grove_root


# ---------------------------------------------------------------------------
# Tests: Chunker
# ---------------------------------------------------------------------------


class TestChunker:
    """Unit tests for the Chunker class."""

    def test_single_short_article(self) -> None:
        """A short article should produce a single chunk."""
        chunker = Chunker(chunk_size=512, overlap=64)
        content = "This is a short article with only a few words."
        chunks = chunker.chunk_article("wiki/test.md", content)

        assert len(chunks) == 1
        assert chunks[0].article_path == "wiki/test.md"
        assert chunks[0].position == 0
        assert chunks[0].content == content

    def test_empty_content_returns_no_chunks(self) -> None:
        """Empty content should produce zero chunks."""
        chunker = Chunker()
        chunks = chunker.chunk_article("wiki/empty.md", "")
        assert chunks == []

    def test_whitespace_only_returns_no_chunks(self) -> None:
        """Whitespace-only content should produce zero chunks."""
        chunker = Chunker()
        chunks = chunker.chunk_article("wiki/blank.md", "   \n\t  ")
        assert chunks == []

    def test_multiple_chunks_created(self) -> None:
        """A long article should be split into multiple chunks."""
        # Create content with ~1000 words (should produce 2+ chunks at 512 tokens).
        words = ["word"] * 1000
        content = " ".join(words)
        chunker = Chunker(chunk_size=512, overlap=64)
        chunks = chunker.chunk_article("wiki/long.md", content)

        assert len(chunks) > 1
        # All chunks should reference the same article.
        for chunk in chunks:
            assert chunk.article_path == "wiki/long.md"

    def test_chunk_positions_are_sequential(self) -> None:
        """Chunk positions should be 0, 1, 2, ... in order."""
        words = ["test"] * 1000
        content = " ".join(words)
        chunker = Chunker(chunk_size=512, overlap=64)
        chunks = chunker.chunk_article("wiki/seq.md", content)

        positions = [c.position for c in chunks]
        assert positions == list(range(len(chunks)))

    def test_overlap_between_chunks(self) -> None:
        """Consecutive chunks should share overlapping words."""
        # Use enough words to produce multiple chunks.
        # With chunk_size=100, overlap=20, and numbered words for tracing.
        words = [f"w{i}" for i in range(500)]
        content = " ".join(words)
        chunker = Chunker(chunk_size=100, overlap=20)
        chunks = chunker.chunk_article("wiki/overlap.md", content)

        assert len(chunks) >= 2

        # Check overlap: the end of chunk 0 should appear at the start of chunk 1.
        words_c0 = chunks[0].content.split()
        words_c1 = chunks[1].content.split()

        # The last N words of chunk 0 should match the first N words of chunk 1.
        # The overlap in words is approximately overlap_tokens / 1.3 ~ 15 words.
        overlap_size = min(len(words_c0), len(words_c1), 15)
        tail_c0 = words_c0[-overlap_size:]
        head_c1 = words_c1[:overlap_size]
        assert tail_c0 == head_c1, "Consecutive chunks should overlap"

    def test_token_estimate(self) -> None:
        """Token estimates should be approximately words * 1.3."""
        tokens = _estimate_tokens("one two three four five")
        # 5 words * 1.3 = 6.5 -> int = 6
        assert tokens == 6

    def test_all_content_covered(self) -> None:
        """All words from the original content should appear in at least one chunk."""
        words = [f"unique{i}" for i in range(300)]
        content = " ".join(words)
        chunker = Chunker(chunk_size=200, overlap=30)
        chunks = chunker.chunk_article("wiki/cover.md", content)

        all_chunk_words: set[str] = set()
        for chunk in chunks:
            all_chunk_words.update(chunk.content.split())

        for word in words:
            assert word in all_chunk_words


# ---------------------------------------------------------------------------
# Tests: FTSIndex.build
# ---------------------------------------------------------------------------


class TestFTSIndexBuild:
    """Tests for building the FTS5 search index."""

    def test_build_creates_database(self, tmp_path: Path) -> None:
        """Build should create the search.db file."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/test.md": _make_article(
                    "Test Article",
                    "A test article for search.",
                    "# Test\n\nThis is searchable content about testing.",
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        chunks_indexed = index.build(grove_root / "wiki")

        assert db_path.exists()
        assert chunks_indexed > 0

    def test_build_indexes_multiple_articles(self, tmp_path: Path) -> None:
        """Build should index chunks from all wiki articles."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/alpha.md": _make_article(
                    "Alpha",
                    "First article.",
                    "# Alpha\n\nContent about alpha topic.",
                ),
                "wiki/topics/beta.md": _make_article(
                    "Beta",
                    "Second article.",
                    "# Beta\n\nContent about beta topic.",
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        chunks_indexed = index.build(grove_root / "wiki")

        # At minimum one chunk per article.
        assert chunks_indexed >= 2

    def test_build_handles_empty_wiki(self, tmp_path: Path) -> None:
        """Build should handle an empty wiki directory gracefully."""
        grove_root = tmp_path / "grove"
        (grove_root / ".grove").mkdir(parents=True)
        (grove_root / "wiki").mkdir(parents=True)

        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        chunks_indexed = index.build(grove_root / "wiki")

        assert chunks_indexed == 0

    def test_build_handles_missing_wiki(self, tmp_path: Path) -> None:
        """Build should handle a missing wiki directory gracefully."""
        grove_root = tmp_path / "grove"
        (grove_root / ".grove").mkdir(parents=True)

        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        chunks_indexed = index.build(grove_root / "wiki")

        assert chunks_indexed == 0

    def test_rebuild_drops_old_data(self, tmp_path: Path) -> None:
        """Rebuilding should drop old index data and start fresh."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/first.md": _make_article(
                    "First",
                    "First article.",
                    "# First\n\nOriginal content about the first topic.",
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)

        # Build once.
        index.build(grove_root / "wiki")

        # Now remove that article and add a new one.
        (grove_root / "wiki" / "topics" / "first.md").unlink()
        new_article = grove_root / "wiki" / "topics" / "second.md"
        new_article.write_text(
            _make_article(
                "Second",
                "Second article.",
                "# Second\n\nReplacement content about the second topic.",
            ),
            encoding="utf-8",
        )

        # Rebuild.
        index.build(grove_root / "wiki")

        # Old article should not be findable; new one should be.
        results_old = index.search("first")
        results_new = index.search("second")

        # "First" may appear in residual front-matter text, but the article
        # path should not be present.
        old_paths = [r.article_path for r in results_old]
        assert "wiki/topics/first.md" not in old_paths

        assert len(results_new) >= 1
        assert results_new[0].article_path == "wiki/topics/second.md"

    def test_article_without_front_matter_uses_filename(self, tmp_path: Path) -> None:
        """Articles without front matter should fall back to filename for title."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/bare-article.md": (
                    "# Bare\n\nContent without front matter."
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("bare content")
        assert len(results) >= 1
        assert results[0].title == "Bare Article"


# ---------------------------------------------------------------------------
# Tests: FTSIndex.search
# ---------------------------------------------------------------------------


class TestFTSIndexSearch:
    """Tests for querying the FTS5 search index."""

    def test_search_returns_matching_articles(self, tmp_path: Path) -> None:
        """Search should return articles matching the query."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/python.md": _make_article(
                    "Python Programming",
                    "Guide to Python.",
                    "# Python\n\nPython is a programming language"
                    " used for data science and web development.",
                ),
                "wiki/topics/rust.md": _make_article(
                    "Rust Programming",
                    "Guide to Rust.",
                    "# Rust\n\nRust is a systems programming language"
                    " focused on safety and performance.",
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("data science")
        assert len(results) >= 1
        assert any(r.article_path == "wiki/topics/python.md" for r in results)

    def test_search_deduplicates_by_article(self, tmp_path: Path) -> None:
        """Multiple chunks from the same article should yield one result."""
        # Create a long article that will produce multiple chunks.
        long_body = "# Repeated Topic\n\n"
        long_body += "Machine learning is used extensively. " * 500

        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/ml.md": _make_article(
                    "Machine Learning",
                    "All about ML.",
                    long_body,
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("machine learning")

        # Should have exactly one result despite multiple chunk matches.
        article_paths = [r.article_path for r in results]
        assert article_paths.count("wiki/topics/ml.md") == 1

    def test_search_returns_empty_for_no_matches(self, tmp_path: Path) -> None:
        """Search for a term not in any article should return empty."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/geology.md": _make_article(
                    "Geology",
                    "About rocks.",
                    "# Geology\n\nRocks and minerals are fascinating.",
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("quantum computing blockchain")
        assert results == []

    def test_search_handles_missing_database(self, tmp_path: Path) -> None:
        """Search with no database should return empty list, not crash."""
        db_path = tmp_path / "nonexistent" / "search.db"
        index = FTSIndex(db_path)
        results = index.search("anything")
        assert results == []

    def test_search_respects_limit(self, tmp_path: Path) -> None:
        """Search should respect the limit parameter."""
        articles = {}
        for i in range(15):
            articles[f"wiki/topics/article-{i}.md"] = _make_article(
                f"Article {i}",
                f"Summary {i}.",
                f"# Article {i}\n\nShared keyword content about testing.",
            )

        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("testing content", limit=5)
        assert len(results) <= 5

    def test_search_result_has_correct_fields(self, tmp_path: Path) -> None:
        """Each SearchResult should carry title, summary, best_chunk, and score."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/fields.md": _make_article(
                    "Field Test",
                    "Tests all fields.",
                    "# Fields\n\nThis article tests that"
                    " search result fields are populated.",
                ),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("fields populated")
        assert len(results) >= 1

        result = results[0]
        assert result.title == "Field Test"
        assert result.summary == "Tests all fields."
        assert result.article_path == "wiki/topics/fields.md"
        assert result.best_chunk  # non-empty
        assert isinstance(result.score, float)

    def test_search_empty_query_returns_empty(self, tmp_path: Path) -> None:
        """An empty query string should return no results."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/any.md": _make_article("Any", "Any.", "# Any\n\nContent."),
            },
        )
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        results = index.search("")
        assert results == []


# ---------------------------------------------------------------------------
# Tests: CLI grove search command
# ---------------------------------------------------------------------------


class TestSearchCLI:
    """Tests for the ``grove search`` CLI command."""

    def test_search_displays_results(self, tmp_path: Path) -> None:
        """The search command should display results when the index exists."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/cli-test.md": _make_article(
                    "CLI Test Article",
                    "Testing the CLI search.",
                    "# CLI Test\n\nThis article verifies"
                    " the search command works correctly.",
                ),
            },
        )

        # Add config.yaml so _find_grove_root succeeds.
        config_path = grove_root / ".grove" / "config.yaml"
        config_path.write_text("llm:\n  providers: {}\n", encoding="utf-8")

        # Build the index.
        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        from grove.cli import app

        with patch("grove.cli._find_grove_root", return_value=grove_root):
            result = runner.invoke(app, ["search", "CLI test"])

        assert result.exit_code == 0
        assert "CLI Test Article" in result.output

    def test_search_missing_index_suggests_compile(self, tmp_path: Path) -> None:
        """When search.db is missing, the command should suggest running compile."""
        grove_root = tmp_path / "grove"
        (grove_root / ".grove").mkdir(parents=True)
        config_path = grove_root / ".grove" / "config.yaml"
        config_path.write_text("llm:\n  providers: {}\n", encoding="utf-8")

        from grove.cli import app

        with patch("grove.cli._find_grove_root", return_value=grove_root):
            result = runner.invoke(app, ["search", "anything"])

        assert result.exit_code == 1
        assert "grove compile" in result.output

    def test_search_no_results(self, tmp_path: Path) -> None:
        """When no results match, the command should print a message."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/specific.md": _make_article(
                    "Specific",
                    "Very specific.",
                    "# Specific\n\nExtremely niche content.",
                ),
            },
        )
        config_path = grove_root / ".grove" / "config.yaml"
        config_path.write_text("llm:\n  providers: {}\n", encoding="utf-8")

        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        from grove.cli import app

        with patch("grove.cli._find_grove_root", return_value=grove_root):
            result = runner.invoke(app, ["search", "xylophone"])

        assert result.exit_code == 0
        assert "No results" in result.output

    def test_search_with_limit_flag(self, tmp_path: Path) -> None:
        """The --limit flag should be accepted."""
        grove_root = _build_wiki(
            tmp_path,
            {
                "wiki/topics/limit-test.md": _make_article(
                    "Limit Test",
                    "Testing limit.",
                    "# Limit\n\nSome content for limit testing.",
                ),
            },
        )
        config_path = grove_root / ".grove" / "config.yaml"
        config_path.write_text("llm:\n  providers: {}\n", encoding="utf-8")

        db_path = grove_root / ".grove" / "search.db"
        index = FTSIndex(db_path)
        index.build(grove_root / "wiki")

        from grove.cli import app

        with patch("grove.cli._find_grove_root", return_value=grove_root):
            result = runner.invoke(app, ["search", "limit", "--limit", "3"])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Tests: Index rebuild after compile (engine integration)
# ---------------------------------------------------------------------------


class TestSearchIndexRebuildAfterCompile:
    """CompileEngine should rebuild the search index after compile."""

    def test_compile_rebuilds_search_index(self, tmp_path: Path) -> None:
        """After a successful compile, search.db should exist and be queryable."""
        import json

        import yaml

        from grove.compile.engine import CompileEngine
        from grove.compile.prompt import PromptBuilder
        from grove.config.loader import ConfigLoader
        from grove.llm.models import LLMResponse

        # Set up a minimal grove.
        grove_root = tmp_path / "test-grove"
        grove_root.mkdir()
        (grove_root / ".grove").mkdir()
        (grove_root / ".grove" / "logs").mkdir()
        (grove_root / ".grove" / "prompts").mkdir()
        (grove_root / "raw" / "articles").mkdir(parents=True)
        (grove_root / "wiki").mkdir()

        config_yaml = {
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
        (grove_root / ".grove" / "config.yaml").write_text(
            yaml.dump(config_yaml, default_flow_style=False), encoding="utf-8"
        )
        (grove_root / ".grove" / "state.json").write_text(
            json.dumps({}) + "\n", encoding="utf-8"
        )

        # Add a source.
        source_path = grove_root / "raw" / "articles" / "source.md"
        source_path.write_text(
            """\
---
grove_summary: "A summary about search indexing."
grove_concepts: [search, indexing]
---

# Source

Content about search indexing and retrieval.
""",
            encoding="utf-8",
        )
        # Manifest.
        (grove_root / "raw" / "_manifest.md").write_text(
            """\
---
total_sources: 1
last_updated: "2026-04-03T14:00:00Z"
---

| Source | Quality | Words | Concepts | Ingested |
|--------|---------|-------|----------|----------|
| raw/articles/source.md | good | 20 | search, indexing | 2026-04-03 |
""",
            encoding="utf-8",
        )

        # Mock LLM response with an article about search.
        mock_response = LLMResponse(
            content="""\
<!-- grove:article wiki/topics/search.md -->
---
title: "Search and Retrieval"
compiled_from:
  - raw/articles/source.md
concepts: [search, indexing]
summary: "How search indexing works."
last_compiled: "2026-04-03T14:00:00Z"
---

# Search and Retrieval

Search indexing enables fast retrieval of information [source: raw/articles/source.md].
""",
            model="claude-sonnet-4-6",
            provider="anthropic",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.01,
        )

        config = ConfigLoader(grove_root).load()
        router = MagicMock()
        router.complete_sync.return_value = mock_response
        router.cost_tracker = MagicMock()
        prompt_builder = PromptBuilder(grove_root)

        engine = CompileEngine(grove_root, config, router, prompt_builder)

        with patch("grove.compile.engine.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer.has_changes.return_value = True
            mock_committer.commit_compile.return_value = "abc123sha"
            mock_committer_cls.return_value = mock_committer

            engine.compile()

        # Verify search.db was created.
        db_path = grove_root / ".grove" / "search.db"
        assert db_path.exists(), "search.db should be created after compile"

        # Verify we can search the compiled article.
        index = FTSIndex(db_path)
        results = index.search("search retrieval")
        assert len(results) >= 1
        assert results[0].article_path == "wiki/topics/search.md"
