"""Tests for AnswerFormatter, QueryFiler, grove query auto-save, and grove file.

Covers:
- AnswerFormatter.format_markdown produces valid markdown with front matter
- AnswerFormatter.format_slides produces Marp-compatible output
- AnswerFormatter.format_terminal produces Rich-compatible output
- QueryFiler.save_query creates file in queries/ with correct name
- QueryFiler.file_to_wiki adds origin:query and pinned:true
- QueryFiler.file_to_wiki commits via AutoCommitter
- QueryFiler.get_latest_query returns most recent file
- grove file CLI promotes latest query
- grove query auto-saves result
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from grove.query.filer import (
    QueryFiler,
    _add_wiki_front_matter,
    _slugify_question,
    _timestamp_prefix,
)
from grove.query.formatter import AnswerFormatter
from grove.query.models import QueryResult

# ---------------------------------------------------------------------------
# Fixtures: reusable QueryResult instances
# ---------------------------------------------------------------------------

_SAMPLE_RESULT = QueryResult(
    question="What is the transformer architecture?",
    answer=(
        "The transformer architecture is a neural network design "
        "that uses self-attention mechanisms [wiki: topics/transformers/overview.md]."
        "\n\n## Key Components\n\n"
        "The main components are the encoder and decoder stacks."
    ),
    mode="deep",
    citations=[
        "topics/transformers/overview.md",
        "topics/attention/self-attention.md",
    ],
    follow_up_questions=[
        "How does self-attention work?",
        "What are the efficiency improvements?",
    ],
    model_used="claude-sonnet-4-6",
    tokens_used=2500,
    cost_usd=0.02,
    timestamp="2026-04-03T14:22:00Z",
)

_MINIMAL_RESULT = QueryResult(
    question="Simple question",
    answer="Simple answer.",
    mode="quick",
    citations=[],
    follow_up_questions=[],
    model_used="",
    tokens_used=0,
    cost_usd=0.0,
    timestamp="2026-04-03T10:00:00Z",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_grove(tmp_path: Path) -> Path:
    """Create a minimal grove directory structure."""
    grove_root = tmp_path / "test-grove"
    grove_root.mkdir()
    (grove_root / ".grove").mkdir()
    (grove_root / ".grove" / "logs").mkdir()
    (grove_root / "wiki").mkdir()
    (grove_root / "queries").mkdir()
    return grove_root


# ===========================================================================
# AnswerFormatter tests
# ===========================================================================


class TestAnswerFormatterMarkdown:
    """format_markdown should produce valid markdown with front matter."""

    def test_contains_yaml_front_matter(self) -> None:
        """Output should start with --- delimited YAML front matter."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        assert output.startswith("---\n")
        # Find the closing delimiter.
        second_delimiter = output.index("---", 4)
        assert second_delimiter > 4

    def test_front_matter_contains_required_fields(self) -> None:
        """Front matter should include question, mode, and timestamp."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        # Extract YAML front matter.
        fm_end = output.index("---", 4)
        fm_raw = output[4:fm_end]
        fm = yaml.safe_load(fm_raw)

        assert fm["question"] == "What is the transformer architecture?"
        assert fm["mode"] == "deep"
        assert fm["timestamp"] == "2026-04-03T14:22:00Z"

    def test_front_matter_contains_citations(self) -> None:
        """Front matter should include the citation list when present."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        fm_end = output.index("---", 4)
        fm_raw = output[4:fm_end]
        fm = yaml.safe_load(fm_raw)

        assert "topics/transformers/overview.md" in fm["citations"]
        assert "topics/attention/self-attention.md" in fm["citations"]

    def test_front_matter_contains_model_and_cost(self) -> None:
        """Front matter should include model_used and cost_usd when non-zero."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        fm_end = output.index("---", 4)
        fm_raw = output[4:fm_end]
        fm = yaml.safe_load(fm_raw)

        assert fm["model_used"] == "claude-sonnet-4-6"
        assert fm["cost_usd"] == 0.02

    def test_body_contains_question_heading(self) -> None:
        """Body should contain the question as an H1 heading."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        assert "# What is the transformer architecture?" in output

    def test_body_contains_answer(self) -> None:
        """Body should contain the full answer text."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        assert "self-attention mechanisms" in output

    def test_body_contains_follow_up_questions(self) -> None:
        """Body should contain a Follow-up Questions section when present."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)

        assert "## Follow-up Questions" in output
        assert "1. How does self-attention work?" in output
        assert "2. What are the efficiency improvements?" in output

    def test_minimal_result_omits_optional_sections(self) -> None:
        """A result with no citations or follow-ups should still be valid."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_MINIMAL_RESULT)

        assert output.startswith("---\n")
        assert "# Simple question" in output
        assert "Simple answer." in output
        # No follow-up section.
        assert "## Follow-up Questions" not in output

    def test_ends_with_newline(self) -> None:
        """Output should end with a trailing newline (POSIX compliance)."""
        formatter = AnswerFormatter()
        output = formatter.format_markdown(_SAMPLE_RESULT)
        assert output.endswith("\n")


class TestAnswerFormatterSlides:
    """AnswerFormatter.format_slides should produce Marp-compatible output."""

    def test_starts_with_marp_front_matter(self) -> None:
        """Output should begin with Marp YAML front matter."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)

        assert output.startswith("---\nmarp: true\n")

    def test_contains_marp_directive(self) -> None:
        """Front matter should contain marp: true."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)

        assert "marp: true" in output

    def test_contains_title_slide(self) -> None:
        """Should contain a title slide with the question as H1."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)

        assert "# What is the transformer architecture?" in output

    def test_contains_slide_separators(self) -> None:
        """Should contain --- slide separators."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)

        # Count separators (excluding the front matter delimiters).
        lines = output.split("\n")
        separator_count = sum(1 for line in lines[5:] if line.strip() == "---")
        # At least: one for the answer, one more for the ## heading split.
        assert separator_count >= 2

    def test_contains_sources_slide(self) -> None:
        """Should contain a Sources slide when citations exist."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)

        assert "## Sources" in output
        assert "`topics/transformers/overview.md`" in output

    def test_contains_follow_up_slide(self) -> None:
        """Should contain a Follow-up Questions slide when present."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)

        assert "## Follow-up Questions" in output
        assert "How does self-attention work?" in output

    def test_minimal_result_produces_valid_slides(self) -> None:
        """A minimal result should still produce valid Marp output."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_MINIMAL_RESULT)

        assert "marp: true" in output
        assert "# Simple question" in output
        # No Sources or Follow-up slides.
        assert "## Sources" not in output
        assert "## Follow-up Questions" not in output

    def test_ends_with_newline(self) -> None:
        """Output should end with a trailing newline."""
        formatter = AnswerFormatter()
        output = formatter.format_slides(_SAMPLE_RESULT)
        assert output.endswith("\n")


class TestAnswerFormatterTerminal:
    """AnswerFormatter.format_terminal should produce Rich-compatible output."""

    def test_contains_question_in_bold(self) -> None:
        """Output should contain the question wrapped in [bold] tags."""
        formatter = AnswerFormatter()
        output = formatter.format_terminal(_SAMPLE_RESULT)

        assert "[bold]What is the transformer architecture?[/bold]" in output

    def test_contains_answer_text(self) -> None:
        """Output should contain the answer body."""
        formatter = AnswerFormatter()
        output = formatter.format_terminal(_SAMPLE_RESULT)

        assert "self-attention mechanisms" in output

    def test_contains_metadata(self) -> None:
        """Output should contain mode, model, and cost metadata."""
        formatter = AnswerFormatter()
        output = formatter.format_terminal(_SAMPLE_RESULT)

        assert "mode=deep" in output
        assert "model=claude-sonnet-4-6" in output
        assert "cost=$0.0200" in output

    def test_contains_citations(self) -> None:
        """Output should list citations when present."""
        formatter = AnswerFormatter()
        output = formatter.format_terminal(_SAMPLE_RESULT)

        assert "[wiki: topics/transformers/overview.md]" in output


# ===========================================================================
# QueryFiler tests
# ===========================================================================


class TestQueryFilerSaveQuery:
    """QueryFiler.save_query should create a file in queries/ with correct naming."""

    def test_creates_file_in_queries_dir(self, tmp_path: Path) -> None:
        """The saved file should exist under queries/."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)

        assert saved_path.exists()
        assert saved_path.parent == grove_root / "queries"

    def test_filename_contains_timestamp_and_slug(self, tmp_path: Path) -> None:
        """The filename should follow <timestamp>-<slug>.md pattern."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)

        # Timestamp prefix: 2026-04-03T142200
        assert "2026-04-03T142200" in saved_path.name
        # Slug from first 5 words: what-is-the-transformer-architecture
        assert "what-is-the-transformer-architecture" in saved_path.name
        assert saved_path.suffix == ".md"

    def test_saved_content_is_valid_markdown(self, tmp_path: Path) -> None:
        """The saved file should contain valid markdown with front matter."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)
        content = saved_path.read_text(encoding="utf-8")

        assert content.startswith("---\n")
        assert "# What is the transformer architecture?" in content

    def test_creates_queries_dir_if_missing(self, tmp_path: Path) -> None:
        """Should create queries/ if it does not exist."""
        grove_root = tmp_path / "test-grove"
        grove_root.mkdir()
        (grove_root / ".grove").mkdir()
        # Do NOT create queries/.

        filer = QueryFiler(grove_root)
        saved_path = filer.save_query(_SAMPLE_RESULT)

        assert saved_path.exists()
        assert (grove_root / "queries").is_dir()


class TestQueryFilerFileToWiki:
    """QueryFiler.file_to_wiki should promote with origin:query and pinned:true."""

    def test_adds_origin_query(self, tmp_path: Path) -> None:
        """The filed wiki article should have origin: query in front matter."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        # First save a query.
        saved_path = filer.save_query(_SAMPLE_RESULT)

        # Patch AutoCommitter to avoid needing a real git repo.
        with patch("grove.query.filer.QueryFiler._commit_filed_query"):
            wiki_path = filer.file_to_wiki(saved_path)

        content = wiki_path.read_text(encoding="utf-8")
        fm_end = content.index("---", 4)
        fm = yaml.safe_load(content[4:fm_end])

        assert fm["origin"] == "query"

    def test_adds_pinned_true(self, tmp_path: Path) -> None:
        """The filed wiki article should have pinned: true in front matter."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)

        with patch("grove.query.filer.QueryFiler._commit_filed_query"):
            wiki_path = filer.file_to_wiki(saved_path)

        content = wiki_path.read_text(encoding="utf-8")
        fm_end = content.index("---", 4)
        fm = yaml.safe_load(content[4:fm_end])

        assert fm["pinned"] is True

    def test_preserves_existing_front_matter(self, tmp_path: Path) -> None:
        """Existing front matter fields should be preserved after filing."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)

        with patch("grove.query.filer.QueryFiler._commit_filed_query"):
            wiki_path = filer.file_to_wiki(saved_path)

        content = wiki_path.read_text(encoding="utf-8")
        fm_end = content.index("---", 4)
        fm = yaml.safe_load(content[4:fm_end])

        # Original fields should still be present.
        assert fm["question"] == "What is the transformer architecture?"
        assert fm["mode"] == "deep"

    def test_files_to_wiki_queries_dir(self, tmp_path: Path) -> None:
        """The filed article should be in wiki/queries/."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)

        with patch("grove.query.filer.QueryFiler._commit_filed_query"):
            wiki_path = filer.file_to_wiki(saved_path)

        assert wiki_path.parent == grove_root / "wiki" / "queries"
        assert wiki_path.name == saved_path.name

    def test_commits_via_auto_committer(self, tmp_path: Path) -> None:
        """Filing should call AutoCommitter.commit_file_query."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        saved_path = filer.save_query(_SAMPLE_RESULT)

        with patch("grove.git.auto_commit.AutoCommitter") as mock_committer_cls:
            mock_committer = MagicMock()
            mock_committer_cls.return_value = mock_committer

            filer.file_to_wiki(saved_path)

            mock_committer.commit_file_query.assert_called_once()
            # The argument should be a relative path inside wiki/.
            call_arg = mock_committer.commit_file_query.call_args[0][0]
            assert call_arg.startswith("wiki/queries/")

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for a non-existent query path."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        import pytest

        with pytest.raises(FileNotFoundError):
            filer.file_to_wiki(grove_root / "queries" / "nonexistent.md")


class TestQueryFilerGetLatestQuery:
    """QueryFiler.get_latest_query should return the most recent file."""

    def test_returns_most_recent_file(self, tmp_path: Path) -> None:
        """Should return the file that sorts last by name (most recent timestamp)."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        # Create two query files with different timestamps.
        queries_dir = grove_root / "queries"
        (queries_dir / "2026-04-03T100000-first-query.md").write_text(
            "first", encoding="utf-8"
        )
        (queries_dir / "2026-04-03T140000-second-query.md").write_text(
            "second", encoding="utf-8"
        )

        latest = filer.get_latest_query()

        assert latest is not None
        assert "second-query" in latest.name

    def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        """Should return None when queries/ is empty."""
        grove_root = _setup_grove(tmp_path)
        filer = QueryFiler(grove_root)

        latest = filer.get_latest_query()
        assert latest is None

    def test_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        """Should return None when queries/ does not exist."""
        grove_root = tmp_path / "test-grove"
        grove_root.mkdir()
        (grove_root / ".grove").mkdir()

        filer = QueryFiler(grove_root)
        latest = filer.get_latest_query()
        assert latest is None


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestTimestampPrefix:
    """_timestamp_prefix should produce filesystem-safe prefixes."""

    def test_removes_colons_and_z(self) -> None:
        """ISO timestamp should have colons and Z stripped."""
        assert _timestamp_prefix("2026-04-03T14:22:00Z") == "2026-04-03T142200"

    def test_handles_fractional_seconds(self) -> None:
        """Fractional seconds should be stripped."""
        assert _timestamp_prefix("2026-04-03T14:22:00.123Z") == "2026-04-03T142200"


class TestSlugifyQuestion:
    """_slugify_question should produce filesystem-safe slugs."""

    def test_basic_question(self) -> None:
        """A normal question should produce a clean slug."""
        assert (
            _slugify_question("What is the transformer architecture?")
            == "what-is-the-transformer-architecture"
        )

    def test_limits_to_five_words(self) -> None:
        """Only the first 5 words should appear in the slug."""
        slug = _slugify_question("one two three four five six seven")
        assert slug == "one-two-three-four-five"

    def test_strips_special_characters(self) -> None:
        """Punctuation and special characters should be removed."""
        slug = _slugify_question("What's the cost (in USD)?")
        assert "(" not in slug
        assert ")" not in slug
        assert "'" not in slug

    def test_empty_question_returns_untitled(self) -> None:
        """An empty question should return 'untitled'."""
        assert _slugify_question("") == "untitled"


class TestAddWikiFrontMatter:
    """_add_wiki_front_matter should add origin and pinned fields."""

    def test_adds_to_existing_front_matter(self) -> None:
        """Fields should be added to existing YAML front matter."""
        content = "---\nquestion: test\nmode: deep\n---\n\n# Test\n"
        result = _add_wiki_front_matter(content)

        fm_end = result.index("---", 4)
        fm = yaml.safe_load(result[4:fm_end])

        assert fm["origin"] == "query"
        assert fm["pinned"] is True
        assert fm["question"] == "test"

    def test_creates_front_matter_when_missing(self) -> None:
        """Should create front matter if none exists."""
        content = "# Just a heading\n\nSome content.\n"
        result = _add_wiki_front_matter(content)

        assert result.startswith("---\n")
        fm_end = result.index("---", 4)
        fm = yaml.safe_load(result[4:fm_end])

        assert fm["origin"] == "query"
        assert fm["pinned"] is True
        # Original content should be preserved.
        assert "# Just a heading" in result
