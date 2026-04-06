"""Integration tests: query and health (TASK-034).

End-to-end tests covering the query pipeline (quick, deep, file) and
the health reporter in a temporary directory with all LLM calls mocked.

Tests:
- Quick query: set up a grove with wiki/_index.md and _concepts.md,
  mock fast LLM tier, verify QueryResult with citations.
- Deep query: set up grove with wiki articles + FTS5 index, mock
  standard LLM, verify top-5 articles loaded.
- grove file: query then file, verify origin:query and pinned:true
  in front matter.
- Health: set up grove with articles, run health reporter, verify
  check results.

All LLM calls mocked. Tests must run without network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from grove.compile.prompt import PromptBuilder
from grove.health.reporter import HealthReporter
from grove.llm.models import LLMResponse
from grove.llm.router import LLMRouter
from grove.query.deep import DeepQuery
from grove.query.filer import QueryFiler
from grove.query.models import QueryResult
from grove.query.quick import QuickQuery
from grove.search.fts import FTSIndex

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

_SAMPLE_INDEX = """\
---
title: "Wiki Index"
compiled_from:
  - raw/articles/source-a.md
concepts: [index]
summary: "Master index of all articles in this knowledge base."
last_compiled: "2026-04-03T14:00:00Z"
---

# Wiki Index

## Topics

| Article | Summary |
|---------|---------|
| [[test-topic|Test Topic]] | An article about testing. |
| [[second-topic|Second Topic]] | An article about the second topic. |
"""

_SAMPLE_CONCEPTS = """\
---
title: "Concept Graph"
compiled_from:
  - raw/articles/source-a.md
concepts: [concept-graph]
summary: "Reverse index mapping concepts to articles."
last_compiled: "2026-04-03T14:00:00Z"
---

# Concept Graph

- **testing**: [[test-topic|Test Topic]]
- **second**: [[second-topic|Second Topic]]
- **overview**: [[test-topic|Test Topic]], [[second-topic|Second Topic]]
"""


def _write_article(
    wiki_dir: Path,
    slug: str,
    body: str,
    *,
    front_matter: dict[str, object] | None = None,
    subdir: str | None = None,
) -> Path:
    """Helper to write a wiki article with optional YAML front matter."""
    fm = front_matter or {}
    fm_text = yaml.dump(fm, default_flow_style=False).rstrip("\n")
    content = f"---\n{fm_text}\n---\n\n{body}\n"

    if subdir:
        target_dir = wiki_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        article_path = target_dir / f"{slug}.md"
    else:
        article_path = wiki_dir / f"{slug}.md"

    article_path.write_text(content, encoding="utf-8")
    return article_path


def _setup_grove(tmp_path: Path) -> Path:
    """Create a minimal grove directory structure for query tests.

    Returns the grove root path with .grove/, wiki/, raw/, and queries/.
    """
    grove_root = tmp_path / "query-grove"
    grove_root.mkdir()

    (grove_root / ".grove" / "logs").mkdir(parents=True)
    (grove_root / ".grove" / "prompts").mkdir(parents=True)
    (grove_root / "wiki").mkdir()
    (grove_root / "raw" / "articles").mkdir(parents=True)
    (grove_root / "queries").mkdir()

    # Minimal config (not needed for query, but HealthReporter reads it).
    config = {
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
    }
    (grove_root / ".grove" / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )

    (grove_root / ".grove" / "state.json").write_text(
        json.dumps({}) + "\n",
        encoding="utf-8",
    )

    return grove_root


def _setup_grove_with_wiki(tmp_path: Path) -> Path:
    """Set up a grove with wiki index files and sample articles.

    Creates _index.md, _concepts.md, and two wiki articles with
    proper front matter for comprehensive testing.
    """
    grove_root = _setup_grove(tmp_path)
    wiki_dir = grove_root / "wiki"

    # Write index files.
    (wiki_dir / "_index.md").write_text(_SAMPLE_INDEX, encoding="utf-8")
    (wiki_dir / "_concepts.md").write_text(_SAMPLE_CONCEPTS, encoding="utf-8")

    # Write test articles with citations.
    _write_article(
        wiki_dir,
        "test-topic",
        (
            "# Test Topic\n\n"
            "This is about testing methodologies [source: source-a.md]. "
            "The population increased by 20% [source: source-a.md].\n\n"
            "See also [[second-topic]] for related content."
        ),
        front_matter={
            "title": "Test Topic",
            "compiled_from": ["raw/articles/source-a.md"],
            "concepts": ["testing", "overview"],
            "summary": "An article about testing.",
            "last_compiled": "2026-04-03T14:00:00Z",
        },
    )

    _write_article(
        wiki_dir,
        "second-topic",
        (
            "# Second Topic\n\n"
            "This covers the second subject area [source: source-a.md]. "
            "Revenue grew more than expected [source: source-a.md].\n\n"
            "See also [[test-topic]] for related content."
        ),
        front_matter={
            "title": "Second Topic",
            "compiled_from": ["raw/articles/source-a.md"],
            "concepts": ["second", "overview"],
            "summary": "An article about the second topic.",
            "last_compiled": "2026-04-03T14:00:00Z",
        },
    )

    return grove_root


def _make_quick_mock_router() -> MagicMock:
    """Build a mock LLMRouter returning a quick query response."""
    router = MagicMock(spec=LLMRouter)
    router.complete_sync.return_value = LLMResponse(
        content=(
            "Testing methodologies involve systematic approaches to "
            "validating software behaviour [wiki: test-topic.md]. "
            "The second topic expands on this with broader coverage "
            "[wiki: second-topic.md].\n\n"
            "**Follow-up questions:**\n"
            "1. How do testing methodologies scale?\n"
            "2. What tools support automated testing?\n"
            "3. How does the second topic relate to deployment?"
        ),
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        input_tokens=800,
        output_tokens=200,
        cost_usd=0.002,
    )
    router.cost_tracker = MagicMock()
    return router


def _make_deep_mock_router() -> MagicMock:
    """Build a mock LLMRouter returning a deep query response."""
    router = MagicMock(spec=LLMRouter)
    router.complete_sync.return_value = LLMResponse(
        content=(
            "Based on a thorough analysis of the knowledge base, "
            "testing methodologies are well-documented across multiple "
            "articles [wiki: test-topic.md]. The second topic provides "
            "complementary coverage [wiki: second-topic.md].\n\n"
            "Key findings include the 20% population increase and "
            "revenue growth patterns documented in the sources.\n\n"
            "**Follow-up questions:**\n"
            "1. What are the long-term trends in testing?\n"
            "2. How do these findings compare to industry benchmarks?"
        ),
        model="claude-sonnet-4-6",
        provider="anthropic",
        input_tokens=3000,
        output_tokens=500,
        cost_usd=0.025,
    )
    router.cost_tracker = MagicMock()
    return router


# ---------------------------------------------------------------------------
# Test: quick query
# ---------------------------------------------------------------------------


class TestQuickQueryIntegration:
    """Quick query with wiki index files, mocked fast LLM, verify QueryResult."""

    def test_quick_query_returns_result_with_citations(self, tmp_path: Path) -> None:
        """Quick query should return a QueryResult with citations."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        assert isinstance(result, QueryResult)
        assert result.mode == "quick"
        assert result.question == "What is testing?"
        assert result.answer != ""
        assert len(result.citations) >= 1
        assert "test-topic.md" in result.citations

    def test_quick_query_uses_fast_tier(self, tmp_path: Path) -> None:
        """Quick query should call the fast LLM tier."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is testing?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.tier == "fast"
        assert call_args.task_type == "query_quick"

    def test_quick_query_includes_index_in_prompt(self, tmp_path: Path) -> None:
        """The prompt should contain content from _index.md and _concepts.md."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is testing?")

        call_args = router.complete_sync.call_args[0][0]
        assert "Wiki Index" in call_args.prompt
        assert "Concept Graph" in call_args.prompt

    def test_quick_query_parses_follow_up_questions(self, tmp_path: Path) -> None:
        """Follow-up questions should be extracted from the LLM response."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        assert len(result.follow_up_questions) >= 2

    def test_quick_query_populates_metadata(self, tmp_path: Path) -> None:
        """Token usage, cost, model, and timestamp should be populated."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        assert result.model_used == "claude-haiku-4-5-20251001"
        assert result.tokens_used == 1000  # 800 input + 200 output
        assert result.cost_usd == 0.002
        assert result.timestamp != ""


# ---------------------------------------------------------------------------
# Test: deep query
# ---------------------------------------------------------------------------


class TestDeepQueryIntegration:
    """Deep query with wiki articles + FTS5 index, verify top-5 articles loaded."""

    def test_deep_query_with_fts_index(self, tmp_path: Path) -> None:
        """Deep query should use FTS5 index and return a QueryResult."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_deep_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Build the FTS5 search index from wiki articles.
        db_path = grove_root / ".grove" / "search.db"
        fts = FTSIndex(db_path)
        chunks_indexed = fts.build(grove_root / "wiki")
        assert chunks_indexed > 0

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is testing?")

        assert isinstance(result, QueryResult)
        assert result.mode == "deep"
        assert result.answer != ""
        assert len(result.citations) >= 1

    def test_deep_query_uses_standard_tier(self, tmp_path: Path) -> None:
        """Deep query should call the standard LLM tier."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_deep_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Build FTS5 index.
        db_path = grove_root / ".grove" / "search.db"
        FTSIndex(db_path).build(grove_root / "wiki")

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is testing?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.tier == "standard"
        assert call_args.task_type == "query_deep"

    def test_deep_query_loads_article_content_in_prompt(self, tmp_path: Path) -> None:
        """The prompt should contain full article content from FTS5 results."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_deep_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Build FTS5 index.
        db_path = grove_root / ".grove" / "search.db"
        FTSIndex(db_path).build(grove_root / "wiki")

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is testing?")

        # The prompt should contain content from at least one article.
        call_args = router.complete_sync.call_args[0][0]
        # Article content or path should appear in the prompt.
        prompt = call_args.prompt
        assert "testing" in prompt.lower()

    def test_deep_query_fallback_without_fts(self, tmp_path: Path) -> None:
        """Without FTS5 index, deep query should fall back to all articles."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_deep_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Do NOT build the FTS5 index -- verify fallback path.
        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is testing?")

        assert isinstance(result, QueryResult)
        assert result.mode == "deep"
        assert result.answer != ""

    def test_deep_query_respects_top_5_limit(self, tmp_path: Path) -> None:
        """FTS5 should return at most 5 articles even if more exist."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        wiki_dir = grove_root / "wiki"

        # Create 7 additional articles to exceed the top-5 limit.
        for i in range(7):
            _write_article(
                wiki_dir,
                f"extra-topic-{i}",
                f"Content about extra topic {i} with testing details.",
                front_matter={
                    "title": f"Extra Topic {i}",
                    "compiled_from": ["raw/articles/source-a.md"],
                    "concepts": ["testing"],
                    "summary": f"Extra article {i}.",
                    "last_compiled": "2026-04-03T14:00:00Z",
                },
            )

        # Build FTS5 index with all articles.
        db_path = grove_root / ".grove" / "search.db"
        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        # Search should return at most 5 results.
        results = fts.search("testing", limit=5)
        assert len(results) <= 5

        # Deep query should still work.
        router = _make_deep_mock_router()
        prompt_builder = PromptBuilder(grove_root)
        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is testing?")

        assert isinstance(result, QueryResult)


# ---------------------------------------------------------------------------
# Test: grove file (query then file)
# ---------------------------------------------------------------------------


class TestQueryFileIntegration:
    """Query then file, verify origin:query and pinned:true in front matter."""

    def test_file_promotes_query_with_correct_front_matter(
        self, tmp_path: Path
    ) -> None:
        """Filing a query should add origin:query and pinned:true to front matter."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        # Run a query.
        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        # Save the query result.
        filer = QueryFiler(grove_root)
        saved_path = filer.save_query(result)
        assert saved_path.exists()

        # File to wiki (suppress git commit by patching AutoCommitter
        # where it is lazily imported inside _commit_filed_query).
        from unittest.mock import patch

        with patch("grove.git.auto_commit.AutoCommitter"):
            wiki_path = filer.file_to_wiki(saved_path)

        # Verify the filed article exists.
        assert wiki_path.exists()
        assert "wiki" in str(wiki_path)

        # Verify front matter has origin:query and pinned:true.
        content = wiki_path.read_text(encoding="utf-8")
        assert "origin: query" in content
        assert "pinned: true" in content

    def test_file_preserves_query_content(self, tmp_path: Path) -> None:
        """The filed article should contain the original query answer."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        filer = QueryFiler(grove_root)
        saved_path = filer.save_query(result)

        from unittest.mock import patch

        with patch("grove.git.auto_commit.AutoCommitter"):
            wiki_path = filer.file_to_wiki(saved_path)

        content = wiki_path.read_text(encoding="utf-8")
        # The answer text should be preserved in the filed article.
        assert "testing methodologies" in content.lower()

    def test_saved_query_auto_save(self, tmp_path: Path) -> None:
        """Every query result should be auto-saved to queries/."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        filer = QueryFiler(grove_root)
        saved_path = filer.save_query(result)

        assert saved_path.exists()
        assert saved_path.parent.name == "queries"
        assert saved_path.suffix == ".md"

        # Verify the saved file has front matter with the question.
        content = saved_path.read_text(encoding="utf-8")
        assert "What is testing?" in content

    def test_get_latest_query(self, tmp_path: Path) -> None:
        """get_latest_query should return the most recent saved query."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        router = _make_quick_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is testing?")

        filer = QueryFiler(grove_root)
        saved_path = filer.save_query(result)

        latest = filer.get_latest_query()
        assert latest is not None
        assert latest == saved_path


# ---------------------------------------------------------------------------
# Test: health reporter
# ---------------------------------------------------------------------------


class TestHealthReporterIntegration:
    """Set up grove with articles, run health reporter, verify check results."""

    def test_healthy_wiki_reports_healthy(self, tmp_path: Path) -> None:
        """A well-structured wiki with citations and cross-links should be healthy."""
        grove_root = _setup_grove_with_wiki(tmp_path)

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        assert report.overall_status in ("healthy", "warnings")
        assert report.total_articles >= 2
        assert "provenance" in report.checks
        assert "staleness" in report.checks
        assert "gaps" in report.checks
        assert "orphans" in report.checks
        assert "contradictions" in report.checks

    def test_provenance_check_passes_with_citations(self, tmp_path: Path) -> None:
        """Articles with proper [source:...] citations should pass provenance."""
        grove_root = _setup_grove_with_wiki(tmp_path)

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        provenance = report.checks["provenance"]
        # The test articles have citations, so provenance should not fail.
        assert provenance.status in ("pass", "warn")

    def test_provenance_check_fails_without_citations(self, tmp_path: Path) -> None:
        """Articles with no citations should trigger a provenance failure."""
        grove_root = _setup_grove(tmp_path)
        wiki_dir = grove_root / "wiki"

        # Create poorly cited articles.
        for i in range(5):
            _write_article(
                wiki_dir,
                f"uncited-{i}",
                (
                    f"The population increased by {i * 10}% in 2024.\n"
                    "Revenue grew more than expected.\n"
                    "Because of new policies, outcomes improved.\n"
                    "Studies show this trend is accelerating.\n"
                    "The budget decreased by 15% last year.\n"
                ),
                front_matter={
                    "title": f"Uncited {i}",
                    "compiled_from": [],
                    "concepts": ["testing"],
                    "summary": f"Uncited article {i}.",
                    "last_compiled": "2026-04-03T14:00:00Z",
                },
            )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        provenance = report.checks["provenance"]
        assert provenance.status == "fail"

    def test_orphan_detection(self, tmp_path: Path) -> None:
        """An article with no incoming links should be detected as an orphan."""
        grove_root = _setup_grove(tmp_path)
        wiki_dir = grove_root / "wiki"

        _write_article(
            wiki_dir,
            "linked",
            "See [[linked]] for details.",
            front_matter={"title": "Linked"},
        )
        _write_article(
            wiki_dir,
            "orphan-article",
            "Nobody links to this article.",
            front_matter={"title": "Orphan Article"},
        )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        orphans = report.checks["orphans"]
        assert orphans.status == "warn"
        assert "orphan-article" in orphans.details

    def test_gap_detection_broken_links(self, tmp_path: Path) -> None:
        """A [[link]] to a non-existent article should be detected as a gap."""
        grove_root = _setup_grove(tmp_path)
        wiki_dir = grove_root / "wiki"

        _write_article(
            wiki_dir,
            "referrer",
            "See [[missing-concept]] for more details.",
            front_matter={"title": "Referrer"},
        )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        gaps = report.checks["gaps"]
        assert gaps.status == "warn"
        assert "missing-concept" in gaps.details

    def test_health_report_total_articles(self, tmp_path: Path) -> None:
        """Article count should exclude _index.md, _concepts.md, _health.md."""
        grove_root = _setup_grove_with_wiki(tmp_path)
        wiki_dir = grove_root / "wiki"

        # Write a _health.md meta file that should be excluded.
        (wiki_dir / "_health.md").write_text(
            "# Health Report\nOld report.\n", encoding="utf-8"
        )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        # Should count only the 2 real articles, not _index, _concepts, _health.
        assert report.total_articles == 2

    def test_health_report_write_to_file(self, tmp_path: Path) -> None:
        """write_health_report should create wiki/_health.md on disk."""
        grove_root = _setup_grove_with_wiki(tmp_path)

        reporter = HealthReporter(grove_root)
        report = reporter.run()
        path = reporter.write_health_report(report)

        assert path.exists()
        assert path.name == "_health.md"

        content = path.read_text(encoding="utf-8")
        assert "Health Report" in content
        assert report.overall_status in content

    def test_health_fix_creates_stubs(self, tmp_path: Path) -> None:
        """The fix method should create stub articles for broken wiki-links."""
        grove_root = _setup_grove(tmp_path)
        wiki_dir = grove_root / "wiki"

        _write_article(
            wiki_dir,
            "referrer",
            "See [[stub-target]] for more info.",
            front_matter={"title": "Referrer"},
        )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        # Confirm the gap is detected before fix.
        assert report.checks["gaps"].status == "warn"
        assert "stub-target" in report.checks["gaps"].details

        # Apply fixes.
        fixes = reporter.fix(report)

        assert len(fixes) >= 1
        assert any("stub-target" in f for f in fixes)

        # Verify the stub was created on disk.
        stub_path = wiki_dir / "stub-target.md"
        assert stub_path.exists()
        content = stub_path.read_text(encoding="utf-8")
        assert "status: stub" in content

    def test_contradiction_check_skipped_without_router(self, tmp_path: Path) -> None:
        """Contradiction check should pass (skipped) when no LLM router is provided."""
        grove_root = _setup_grove_with_wiki(tmp_path)

        # No router or prompt_builder -- contradiction check should be skipped.
        reporter = HealthReporter(grove_root)
        report = reporter.run()

        contradictions = report.checks["contradictions"]
        assert contradictions.status == "pass"
        assert "Skipped" in contradictions.message
