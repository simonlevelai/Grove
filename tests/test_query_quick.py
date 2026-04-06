"""Tests for QuickQuery -- fast query mode using index files only.

Covers:
- QuickQuery loads _index.md and _concepts.md
- QuickQuery calls fast LLM tier with correct task_type
- QuickQuery parses citations from response
- QuickQuery parses follow-up questions
- QuickQuery handles missing wiki gracefully
- QuickQuery returns correct QueryResult model
- QuickQuery uses query.md prompt template
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from grove.compile.prompt import PromptBuilder
from grove.llm.models import LLMResponse
from grove.llm.router import LLMRouter
from grove.query.models import QueryResult
from grove.query.quick import QuickQuery, _parse_citations, _parse_follow_up_questions

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

- **greenhouse-effect**: [[greenhouse-effect|Greenhouse Effect]]
- **carbon-cycle**: [[carbon-cycle|Carbon Cycle]]
- **CO2**: [[greenhouse-effect|Greenhouse Effect]], [[carbon-cycle|Carbon Cycle]]
"""

_SAMPLE_LLM_RESPONSE = """\
The greenhouse effect is the process by which greenhouse gases trap heat \
in Earth's atmosphere [wiki: topics/greenhouse-effect/overview.md]. This \
process involves the carbon cycle, which moves carbon through Earth's \
systems [wiki: topics/carbon-cycle/overview.md].

**Gaps:** The wiki does not cover the specific role of methane.

**Follow-up questions:**
1. How does the carbon cycle regulate atmospheric CO2?
2. What is the relationship between greenhouse gases and ocean acidification?
3. How do human emissions compare to natural carbon sources?
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_grove(
    tmp_path: Path, *, with_index: bool = True, with_concepts: bool = True
) -> Path:
    """Create a minimal grove directory with optional wiki index files."""
    grove_root = tmp_path / "test-grove"
    grove_root.mkdir()
    (grove_root / ".grove").mkdir()
    (grove_root / ".grove" / "logs").mkdir()
    (grove_root / "wiki").mkdir()

    if with_index:
        (grove_root / "wiki" / "_index.md").write_text(_SAMPLE_INDEX, encoding="utf-8")

    if with_concepts:
        (grove_root / "wiki" / "_concepts.md").write_text(
            _SAMPLE_CONCEPTS, encoding="utf-8"
        )

    return grove_root


def _make_mock_router(content: str = _SAMPLE_LLM_RESPONSE) -> MagicMock:
    """Build a mock LLMRouter that returns a canned response."""
    router = MagicMock(spec=LLMRouter)
    router.complete_sync.return_value = LLMResponse(
        content=content,
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        input_tokens=800,
        output_tokens=200,
        cost_usd=0.002,
    )
    router.cost_tracker = MagicMock()
    return router


# ---------------------------------------------------------------------------
# Tests: loading wiki files
# ---------------------------------------------------------------------------


class TestQuickQueryLoadsFiles:
    """QuickQuery should load _index.md and _concepts.md from wiki/."""

    def test_loads_both_index_and_concepts(self, tmp_path: Path) -> None:
        """Both files should be included in the prompt sent to the LLM."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is the greenhouse effect?")

        # The LLM should have been called with a prompt containing
        # content from both index files.
        call_args = router.complete_sync.call_args[0][0]
        assert "Wiki Index" in call_args.prompt
        assert "Concept Graph" in call_args.prompt

    def test_loads_index_only_when_concepts_missing(self, tmp_path: Path) -> None:
        """Should work with only _index.md present."""
        grove_root = _setup_grove(tmp_path, with_concepts=False)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert result.mode == "quick"
        call_args = router.complete_sync.call_args[0][0]
        assert "Wiki Index" in call_args.prompt
        assert "(no concept graph available)" in call_args.prompt

    def test_loads_concepts_only_when_index_missing(self, tmp_path: Path) -> None:
        """Should work with only _concepts.md present."""
        grove_root = _setup_grove(tmp_path, with_index=False)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert result.mode == "quick"
        call_args = router.complete_sync.call_args[0][0]
        assert "(no index available)" in call_args.prompt
        assert "Concept Graph" in call_args.prompt


# ---------------------------------------------------------------------------
# Tests: LLM tier and task type
# ---------------------------------------------------------------------------


class TestQuickQueryCallsFastTier:
    """QuickQuery must use the fast LLM tier with task_type='query_quick'."""

    def test_uses_fast_tier(self, tmp_path: Path) -> None:
        """The LLM request should specify the fast tier."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.tier == "fast"

    def test_uses_correct_task_type(self, tmp_path: Path) -> None:
        """The LLM request should use task_type='query_quick'."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert call_args.task_type == "query_quick"


# ---------------------------------------------------------------------------
# Tests: citation parsing
# ---------------------------------------------------------------------------


class TestQuickQueryParsesCitations:
    """QuickQuery should extract [wiki: path.md] citations from the response."""

    def test_parses_citations_from_response(self, tmp_path: Path) -> None:
        """Citations should be extracted and included in the QueryResult."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert "topics/greenhouse-effect/overview.md" in result.citations
        assert "topics/carbon-cycle/overview.md" in result.citations

    def test_deduplicates_citations(self) -> None:
        """Duplicate citations should appear only once."""
        text = (
            "First mention [wiki: foo.md] and second [wiki: foo.md] "
            "plus [wiki: bar.md]."
        )
        citations = _parse_citations(text)
        assert citations == ["foo.md", "bar.md"]

    def test_no_citations_returns_empty_list(self) -> None:
        """A response with no citations should return an empty list."""
        citations = _parse_citations("No citations here.")
        assert citations == []

    def test_citation_with_whitespace(self) -> None:
        """Whitespace around the path should be stripped."""
        citations = _parse_citations("[wiki:  topics/foo.md  ]")
        assert citations == ["topics/foo.md"]


# ---------------------------------------------------------------------------
# Tests: follow-up question parsing
# ---------------------------------------------------------------------------


class TestQuickQueryParsesFollowUps:
    """QuickQuery should extract follow-up questions from the LLM response."""

    def test_parses_follow_up_questions(self, tmp_path: Path) -> None:
        """Follow-up questions should be extracted and included in the result."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert len(result.follow_up_questions) >= 2
        assert any("carbon" in q.lower() for q in result.follow_up_questions)

    def test_max_three_follow_ups(self) -> None:
        """No more than 3 follow-up questions should be returned."""
        text = """\
**Follow-up questions:**
1. Question one?
2. Question two?
3. Question three?
4. Question four?
5. Question five?
"""
        follow_ups = _parse_follow_up_questions(text)
        assert len(follow_ups) == 3

    def test_no_follow_ups_returns_empty_list(self) -> None:
        """A response with no numbered list should return an empty list."""
        follow_ups = _parse_follow_up_questions("Just a plain answer with no lists.")
        assert follow_ups == []


# ---------------------------------------------------------------------------
# Tests: missing wiki
# ---------------------------------------------------------------------------


class TestQuickQueryMissingWiki:
    """QuickQuery should handle a missing or empty wiki gracefully."""

    def test_no_wiki_files_returns_helpful_error(self, tmp_path: Path) -> None:
        """When neither _index.md nor _concepts.md exist, guide the user."""
        grove_root = _setup_grove(tmp_path, with_index=False, with_concepts=False)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert "grove compile" in result.answer
        assert result.mode == "quick"
        assert result.citations == []
        assert result.follow_up_questions == []

        # The LLM should NOT have been called.
        router.complete_sync.assert_not_called()

    def test_no_wiki_directory_returns_helpful_error(self, tmp_path: Path) -> None:
        """When wiki/ directory does not exist at all."""
        grove_root = tmp_path / "test-grove"
        grove_root.mkdir()
        (grove_root / ".grove").mkdir()

        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert "grove compile" in result.answer
        router.complete_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: QueryResult model
# ---------------------------------------------------------------------------


class TestQueryResultModel:
    """Verify the QueryResult Pydantic model returned by QuickQuery."""

    def test_returns_query_result(self, tmp_path: Path) -> None:
        """QuickQuery.query() should return a QueryResult instance."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert isinstance(result, QueryResult)

    def test_result_fields_populated(self, tmp_path: Path) -> None:
        """All key fields should be populated in a successful query."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        assert result.question == "What is the greenhouse effect?"
        assert result.mode == "quick"
        assert result.answer != ""
        assert result.model_used == "claude-haiku-4-5-20251001"
        assert result.tokens_used == 1000  # 800 input + 200 output
        assert result.cost_usd == 0.002
        assert result.timestamp != ""

    def test_result_serialises_to_dict(self, tmp_path: Path) -> None:
        """QueryResult should serialise cleanly to a dictionary."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        result = qq.query("What is the greenhouse effect?")

        data = result.model_dump()
        assert "question" in data
        assert "answer" in data
        assert "citations" in data
        assert "follow_up_questions" in data
        assert isinstance(data["citations"], list)


# ---------------------------------------------------------------------------
# Tests: prompt template usage
# ---------------------------------------------------------------------------


class TestQuickQueryUsesPromptTemplate:
    """QuickQuery should use the query.md prompt template via PromptBuilder."""

    def test_uses_query_md_template(self, tmp_path: Path) -> None:
        """The prompt should be built from the query.md template."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is the greenhouse effect?")

        # The query.md template contains "knowledge base query engine"
        # in its system-level instructions.
        call_args = router.complete_sync.call_args[0][0]
        assert "knowledge base query engine" in call_args.prompt

    def test_question_substituted_in_prompt(self, tmp_path: Path) -> None:
        """The user's question should appear in the rendered prompt."""
        grove_root = _setup_grove(tmp_path)
        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("Explain the carbon cycle in detail")

        call_args = router.complete_sync.call_args[0][0]
        assert "Explain the carbon cycle in detail" in call_args.prompt

    def test_user_override_prompt_takes_precedence(self, tmp_path: Path) -> None:
        """A user override in .grove/prompts/query.md should replace the default."""
        grove_root = _setup_grove(tmp_path)

        # Create a user override prompt.
        user_prompts_dir = grove_root / ".grove" / "prompts"
        user_prompts_dir.mkdir(parents=True, exist_ok=True)
        (user_prompts_dir / "query.md").write_text(
            "Custom prompt: $question\n\nIndex: $wiki_index\n\nArticles: $articles",
            encoding="utf-8",
        )

        router = _make_mock_router()
        prompt_builder = PromptBuilder(grove_root)

        qq = QuickQuery(grove_root, router, prompt_builder)
        qq.query("What is the greenhouse effect?")

        call_args = router.complete_sync.call_args[0][0]
        assert "Custom prompt:" in call_args.prompt
