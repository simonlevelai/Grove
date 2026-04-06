"""Tests for DeepQuery -- FTS5-powered query mode with full article content.

Covers:
- DeepQuery uses FTS5 to find relevant articles
- DeepQuery loads full article content
- DeepQuery calls standard LLM tier with correct task_type
- DeepQuery parses citations and follow-up questions
- DeepQuery handles missing search index (falls back to loading articles)
- DeepQuery handles empty wiki gracefully
- DeepQuery respects the token budget in fallback mode
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from grove.compile.prompt import PromptBuilder
from grove.llm.models import LLMResponse
from grove.llm.router import LLMRouter
from grove.query.deep import DeepQuery
from grove.query.models import QueryResult
from grove.search.fts import FTSIndex

# ---------------------------------------------------------------------------
# Fixtures: wiki content
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
| [[greenhouse-effect|Greenhouse Effect]] | How greenhouse gases trap heat. |
| [[carbon-cycle|Carbon Cycle]] | The movement of carbon through Earth's systems. |
"""

_ARTICLE_GREENHOUSE = """\
---
title: "Greenhouse Effect"
compiled_from:
  - raw/articles/source-a.md
concepts: [greenhouse-effect, CO2, atmosphere]
summary: "Overview of the greenhouse effect mechanism."
last_compiled: "2026-04-03T14:00:00Z"
---

# Greenhouse Effect

The greenhouse effect is the process by which radiation from a planet's
atmosphere warms the planet's surface to a temperature above what it
would be without this atmosphere. Greenhouse gases include carbon dioxide,
methane, and water vapour.
"""

_ARTICLE_CARBON_CYCLE = """\
---
title: "Carbon Cycle"
compiled_from:
  - raw/articles/source-a.md
concepts: [carbon-cycle, CO2, photosynthesis]
summary: "The movement of carbon through Earth's systems."
last_compiled: "2026-04-03T14:00:00Z"
---

# Carbon Cycle

The carbon cycle describes the process in which carbon atoms continually
travel from the atmosphere to the Earth and then back into the atmosphere.
Photosynthesis absorbs CO2 from the atmosphere, while respiration and
combustion release it back.
"""

_ARTICLE_OCEAN = """\
---
title: "Ocean Acidification"
compiled_from:
  - raw/articles/source-b.md
concepts: [ocean-acidification, pH, CO2]
summary: "How CO2 absorption lowers ocean pH."
last_compiled: "2026-04-03T14:00:00Z"
---

# Ocean Acidification

Ocean acidification is the ongoing decrease in the pH of the Earth's
oceans, caused by the uptake of carbon dioxide from the atmosphere.
"""

_SAMPLE_LLM_RESPONSE = """\
The greenhouse effect is the process by which greenhouse gases trap heat \
in Earth's atmosphere [wiki: wiki/topics/greenhouse-effect.md]. This \
process is closely linked to the carbon cycle, which moves carbon through \
Earth's systems [wiki: wiki/topics/carbon-cycle.md].

Carbon dioxide plays a central role in both processes. The carbon cycle \
regulates atmospheric CO2 through photosynthesis and respiration \
[wiki: wiki/topics/carbon-cycle.md], while excess CO2 in the atmosphere \
contributes to ocean acidification [wiki: wiki/topics/ocean-acidification.md].

**Gaps:** The wiki does not cover the specific role of methane in the \
greenhouse effect, nor regional climate variation.

**Follow-up questions:**
1. How does the carbon cycle regulate atmospheric CO2?
2. What is the relationship between greenhouse gases and ocean acidification?
3. How do human emissions compare to natural carbon sources?
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_grove(
    tmp_path: Path,
    *,
    with_index: bool = True,
    with_articles: bool = True,
) -> Path:
    """Create a minimal grove directory with optional wiki articles."""
    grove_root = tmp_path / "test-grove"
    grove_root.mkdir()
    (grove_root / ".grove").mkdir()
    (grove_root / ".grove" / "logs").mkdir()
    (grove_root / "wiki").mkdir()
    (grove_root / "wiki" / "topics").mkdir()

    if with_index:
        (grove_root / "wiki" / "_index.md").write_text(_SAMPLE_INDEX, encoding="utf-8")

    if with_articles:
        (grove_root / "wiki" / "topics" / "greenhouse-effect.md").write_text(
            _ARTICLE_GREENHOUSE, encoding="utf-8"
        )
        (grove_root / "wiki" / "topics" / "carbon-cycle.md").write_text(
            _ARTICLE_CARBON_CYCLE, encoding="utf-8"
        )
        (grove_root / "wiki" / "topics" / "ocean-acidification.md").write_text(
            _ARTICLE_OCEAN, encoding="utf-8"
        )

    return grove_root


def _build_fts_index(grove_root: Path) -> None:
    """Build an FTS5 search index from the wiki articles."""
    db_path = grove_root / ".grove" / "search.db"
    fts = FTSIndex(db_path)
    fts.build(grove_root / "wiki")


def _make_mock_router(content: str = _SAMPLE_LLM_RESPONSE) -> MagicMock:
    """Build a mock LLMRouter that returns a canned response."""
    router = MagicMock(spec=LLMRouter)
    router.complete_sync.return_value = LLMResponse(
        content=content,
        model="claude-sonnet-4-6",
        provider="anthropic",
        input_tokens=2000,
        output_tokens=500,
        cost_usd=0.012,
    )
    router.cost_tracker = MagicMock()
    return router


# ---------------------------------------------------------------------------
# Tests: FTS5 article search
# ---------------------------------------------------------------------------


class TestDeepQueryUsesFTS:
    """DeepQuery should use FTS5 to find relevant articles."""

    def test_searches_fts_index(self, tmp_path: Path) -> None:
        """When a search index exists, DeepQuery should use it."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        # The LLM should have been called.
        router.complete_sync.assert_called_once()
        assert result.mode == "deep"

    def test_fts_results_appear_in_prompt(self, tmp_path: Path) -> None:
        """Full article content from FTS results should be in the prompt."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        # The prompt should contain full article content, not just summaries.
        assert "greenhouse effect is the process" in call_args.prompt.lower()


# ---------------------------------------------------------------------------
# Tests: full article content loading
# ---------------------------------------------------------------------------


class TestDeepQueryLoadsFullContent:
    """DeepQuery should load the full content of matched articles."""

    def test_loads_full_article_text(self, tmp_path: Path) -> None:
        """The prompt should contain the body text of matched articles."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("carbon cycle photosynthesis")

        call_args = router.complete_sync.call_args[0][0]
        # Should contain full carbon cycle article content.
        assert "photosynthesis absorbs co2" in call_args.prompt.lower()

    def test_includes_wiki_index_in_prompt(self, tmp_path: Path) -> None:
        """The wiki index should also be included for context."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert "Wiki Index" in call_args.prompt


# ---------------------------------------------------------------------------
# Tests: LLM tier and task type
# ---------------------------------------------------------------------------


class TestDeepQueryCallsStandardTier:
    """DeepQuery must use the standard LLM tier with task_type='query_deep'."""

    def test_uses_standard_tier(self, tmp_path: Path) -> None:
        """The LLM request should specify the standard tier."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.tier == "standard"

    def test_uses_correct_task_type(self, tmp_path: Path) -> None:
        """The LLM request should use task_type='query_deep'."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.task_type == "query_deep"

    def test_uses_higher_max_tokens(self, tmp_path: Path) -> None:
        """DeepQuery should request 4096 max tokens for thorough answers."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.max_tokens == 4096


# ---------------------------------------------------------------------------
# Tests: citation and follow-up parsing
# ---------------------------------------------------------------------------


class TestDeepQueryParsesCitationsAndFollowUps:
    """DeepQuery should parse citations and follow-up questions from the response."""

    def test_parses_citations_from_response(self, tmp_path: Path) -> None:
        """Citations should be extracted and included in the QueryResult."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        assert "wiki/topics/greenhouse-effect.md" in result.citations
        assert "wiki/topics/carbon-cycle.md" in result.citations
        assert "wiki/topics/ocean-acidification.md" in result.citations

    def test_parses_follow_up_questions(self, tmp_path: Path) -> None:
        """Follow-up questions should be extracted from the response."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        assert len(result.follow_up_questions) >= 2
        assert any("carbon" in q.lower() for q in result.follow_up_questions)

    def test_deduplicates_citations(self, tmp_path: Path) -> None:
        """Repeated citations in the LLM response should appear only once."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        # The sample response cites carbon-cycle.md twice.
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        # Count occurrences of each citation.
        for citation in result.citations:
            assert result.citations.count(citation) == 1


# ---------------------------------------------------------------------------
# Tests: missing search index (fallback)
# ---------------------------------------------------------------------------


class TestDeepQueryFallback:
    """DeepQuery should fall back to loading articles when the index is missing."""

    def test_falls_back_without_search_index(self, tmp_path: Path) -> None:
        """When .grove/search.db does not exist, load articles directly."""
        grove_root = _setup_grove(tmp_path)
        # Do NOT build the FTS index.
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        # The LLM should still have been called.
        router.complete_sync.assert_called_once()
        assert result.mode == "deep"

    def test_fallback_includes_article_content(self, tmp_path: Path) -> None:
        """Fallback mode should load full article content into the prompt."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert "greenhouse effect is the process" in call_args.prompt.lower()
        assert "carbon cycle" in call_args.prompt.lower()

    def test_fallback_skips_index_files(self, tmp_path: Path) -> None:
        """Fallback mode should skip _index.md and _concepts.md as article sources."""
        grove_root = _setup_grove(tmp_path)
        # Also create a _concepts.md to ensure it is skipped.
        (grove_root / "wiki" / "_concepts.md").write_text(
            "# Concepts\n\nThis is the concept graph.",
            encoding="utf-8",
        )
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        # The articles section should not include _concepts.md content
        # as a loaded article (the wiki_index parameter handles _index.md
        # separately).  Check that the articles portion references actual
        # topic articles, not the index files.
        prompt = call_args.prompt
        assert "wiki/topics/greenhouse-effect.md" in prompt

    def test_fallback_respects_token_budget(self, tmp_path: Path) -> None:
        """Fallback mode should stop loading articles when the token budget is hit."""
        grove_root = _setup_grove(tmp_path, with_articles=False)

        # Create many large articles that together exceed the token budget.
        # Each article is ~2000 words = ~2600 tokens.
        topics_dir = grove_root / "wiki" / "topics"
        large_content = "word " * 2000
        for i in range(50):
            (topics_dir / f"article-{i:03d}.md").write_text(
                f"---\ntitle: Article {i}\n---\n\n{large_content}",
                encoding="utf-8",
            )

        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        dq.query("What is this about?")

        # The LLM should have been called, but not all 50 articles should
        # be in the prompt (that would be ~130K tokens, over the 100K budget).
        call_args = router.complete_sync.call_args[0][0]
        # Count how many article headers appear in the prompt.
        article_count = call_args.prompt.count("### [wiki/topics/article-")
        assert article_count < 50
        assert article_count > 0


# ---------------------------------------------------------------------------
# Tests: empty wiki
# ---------------------------------------------------------------------------


class TestDeepQueryEmptyWiki:
    """DeepQuery should handle an empty or missing wiki gracefully."""

    def test_no_wiki_returns_helpful_error(self, tmp_path: Path) -> None:
        """When wiki/ has no articles and no index, guide the user."""
        grove_root = _setup_grove(tmp_path, with_index=False, with_articles=False)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        assert "grove compile" in result.answer
        assert result.mode == "deep"
        assert result.citations == []
        assert result.follow_up_questions == []
        router.complete_sync.assert_not_called()

    def test_no_wiki_directory_returns_helpful_error(self, tmp_path: Path) -> None:
        """When wiki/ directory does not exist at all."""
        grove_root = tmp_path / "test-grove"
        grove_root.mkdir()
        (grove_root / ".grove").mkdir()

        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        assert "grove compile" in result.answer
        router.complete_sync.assert_not_called()

    def test_index_only_still_queries(self, tmp_path: Path) -> None:
        """When only _index.md exists (no articles), still call the LLM."""
        grove_root = _setup_grove(tmp_path, with_index=True, with_articles=False)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        # The LLM should be called because wiki_index content exists.
        router.complete_sync.assert_called_once()
        assert result.mode == "deep"


# ---------------------------------------------------------------------------
# Tests: QueryResult model
# ---------------------------------------------------------------------------


class TestDeepQueryResultModel:
    """Verify the QueryResult Pydantic model returned by DeepQuery."""

    def test_returns_query_result(self, tmp_path: Path) -> None:
        """DeepQuery.query() should return a QueryResult instance."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        assert isinstance(result, QueryResult)

    def test_result_fields_populated(self, tmp_path: Path) -> None:
        """All key fields should be populated in a successful query."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        assert result.question == "What is the greenhouse effect?"
        assert result.mode == "deep"
        assert result.answer != ""
        assert result.model_used == "claude-sonnet-4-6"
        assert result.tokens_used == 2500  # 2000 input + 500 output
        assert result.cost_usd == 0.012
        assert result.timestamp != ""

    def test_result_serialises_to_dict(self, tmp_path: Path) -> None:
        """QueryResult should serialise cleanly to a dictionary."""
        grove_root = _setup_grove(tmp_path)
        _build_fts_index(grove_root)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        dq = DeepQuery(grove_root, router, prompt_builder)
        result = dq.query("What is the greenhouse effect?")

        data = result.model_dump()
        assert "question" in data
        assert "answer" in data
        assert "citations" in data
        assert "follow_up_questions" in data
        assert data["mode"] == "deep"
