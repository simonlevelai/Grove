"""Tests for the ingest summariser.

Covers LLM call routing, YAML response parsing, error handling, and
front matter read/write operations on source markdown files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from grove.ingest.summariser import (
    Summariser,
    SummaryResult,
    _split_front_matter,
    _strip_code_fences,
)
from grove.llm.models import LLMRequest, LLMResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_router() -> MagicMock:
    """Return a mocked LLMRouter."""
    return MagicMock()


@pytest.fixture()
def mock_prompt_builder() -> MagicMock:
    """Return a mocked PromptBuilder that echoes the source content."""
    builder = MagicMock()
    builder.build.return_value = "rendered prompt text"
    return builder


@pytest.fixture()
def summariser(mock_router: MagicMock, mock_prompt_builder: MagicMock) -> Summariser:
    """Return a Summariser wired to mocked dependencies."""
    return Summariser(router=mock_router, prompt_builder=mock_prompt_builder)


def _make_llm_response(content: str) -> LLMResponse:
    """Build a minimal LLMResponse with the given content."""
    return LLMResponse(
        content=content,
        model="test-model",
        provider="test",
        input_tokens=100,
        output_tokens=50,
    )


# ---------------------------------------------------------------------------
# Summariser.summarise — LLM routing
# ---------------------------------------------------------------------------


class TestSummariseRouting:
    """Verifies that summarise() calls the LLM with the correct parameters."""

    def test_calls_router_with_fast_tier(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
        mock_prompt_builder: MagicMock,
    ) -> None:
        """The request must use tier='fast' and task_type='ingest_summary'."""
        mock_router.complete_sync.return_value = _make_llm_response(
            'summary: "A test summary."\nconcepts:\n  - testing'
        )

        summariser.summarise(Path("/raw/test.md"), "Some content")

        mock_router.complete_sync.assert_called_once()
        request: LLMRequest = mock_router.complete_sync.call_args[0][0]
        assert request.tier == "fast"
        assert request.task_type == "ingest_summary"

    def test_calls_prompt_builder_with_summarise_template(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
        mock_prompt_builder: MagicMock,
    ) -> None:
        """The prompt must be built from 'summarise.md' with the source content."""
        mock_router.complete_sync.return_value = _make_llm_response(
            'summary: "Test."\nconcepts:\n  - one'
        )

        summariser.summarise(Path("/raw/test.md"), "Document body here")

        mock_prompt_builder.build.assert_called_once_with(
            "summarise.md", source="Document body here"
        )

    def test_prompt_text_is_passed_to_request(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
        mock_prompt_builder: MagicMock,
    ) -> None:
        """The rendered prompt text should appear in the LLMRequest."""
        mock_prompt_builder.build.return_value = "custom rendered prompt"
        mock_router.complete_sync.return_value = _make_llm_response(
            'summary: "X."\nconcepts: []'
        )

        summariser.summarise(Path("/raw/test.md"), "content")

        request: LLMRequest = mock_router.complete_sync.call_args[0][0]
        assert request.prompt == "custom rendered prompt"


# ---------------------------------------------------------------------------
# Summariser.summarise — response parsing
# ---------------------------------------------------------------------------


class TestSummariseParsing:
    """Verifies correct parsing of YAML responses from the LLM."""

    def test_parses_valid_yaml_response(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """A well-formed YAML response produces a correct SummaryResult."""
        yaml_response = (
            'summary: "This is a summary of the document content."\n'
            "concepts:\n"
            "  - machine learning\n"
            "  - neural networks\n"
            "  - deep learning"
        )
        mock_router.complete_sync.return_value = _make_llm_response(yaml_response)

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is False
        assert result.summary == "This is a summary of the document content."
        assert result.concepts == [
            "machine learning",
            "neural networks",
            "deep learning",
        ]
        assert result.error is None

    def test_parses_response_with_code_fences(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """LLMs sometimes wrap YAML in markdown code fences -- these are stripped."""
        yaml_response = (
            '```yaml\nsummary: "Fenced summary."\nconcepts:\n  - fencing\n```'
        )
        mock_router.complete_sync.return_value = _make_llm_response(yaml_response)

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is False
        assert result.summary == "Fenced summary."
        assert result.concepts == ["fencing"]

    def test_truncates_concepts_to_max_ten(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """At most 10 concepts should be kept from the LLM response."""
        concepts = "\n".join(f"  - concept{i}" for i in range(15))
        yaml_response = f'summary: "Many concepts."\nconcepts:\n{concepts}'
        mock_router.complete_sync.return_value = _make_llm_response(yaml_response)

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert len(result.concepts) == 10

    def test_handles_malformed_yaml_gracefully(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """Malformed YAML returns unsummarised=True with an error."""
        mock_router.complete_sync.return_value = _make_llm_response(
            "this is not: valid: yaml: [["
        )

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is True
        assert result.error is not None
        assert "YAML parse error" in result.error

    def test_handles_non_mapping_yaml(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """A YAML response that is not a mapping returns unsummarised=True."""
        mock_router.complete_sync.return_value = _make_llm_response(
            "- just\n- a\n- list"
        )

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is True
        assert result.error is not None
        assert "mapping" in result.error.lower()

    def test_handles_missing_summary_field(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """Missing summary field defaults to empty string."""
        mock_router.complete_sync.return_value = _make_llm_response(
            "concepts:\n  - only concepts"
        )

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is False
        assert result.summary == ""
        assert result.concepts == ["only concepts"]

    def test_handles_missing_concepts_field(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """Missing concepts field defaults to an empty list."""
        mock_router.complete_sync.return_value = _make_llm_response(
            'summary: "Only a summary."'
        )

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is False
        assert result.summary == "Only a summary."
        assert result.concepts == []


# ---------------------------------------------------------------------------
# Summariser.summarise — error handling
# ---------------------------------------------------------------------------


class TestSummariseErrors:
    """Verifies graceful error handling when the LLM call fails."""

    def test_handles_llm_exception(
        self,
        summariser: Summariser,
        mock_router: MagicMock,
    ) -> None:
        """An exception from the router returns unsummarised=True with the error."""
        mock_router.complete_sync.side_effect = RuntimeError("API timeout")

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is True
        assert result.error == "API timeout"
        assert result.summary == ""
        assert result.concepts == []

    def test_handles_prompt_builder_exception(
        self,
        summariser: Summariser,
        mock_prompt_builder: MagicMock,
    ) -> None:
        """An exception from the prompt builder is caught the same way."""
        mock_prompt_builder.build.side_effect = FileNotFoundError(
            "summarise.md not found"
        )

        result = summariser.summarise(Path("/raw/test.md"), "content")

        assert result.unsummarised is True
        assert "not found" in result.error


# ---------------------------------------------------------------------------
# Summariser.write_front_matter — no existing front matter
# ---------------------------------------------------------------------------


class TestWriteFrontMatterNew:
    """Verifies front matter creation when the file has none."""

    def test_adds_front_matter_to_plain_file(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """A file with no front matter gets grove fields prepended."""
        source = tmp_path / "test.md"
        source.write_text("# Original heading\n\nSome body text.\n", encoding="utf-8")

        result = SummaryResult(
            summary="A brief summary.",
            concepts=["concept-a", "concept-b"],
        )
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "grove_summary: A brief summary." in text
        assert "grove_concepts:" in text
        assert "- concept-a" in text
        assert "- concept-b" in text
        # Original content is preserved
        assert "# Original heading" in text
        assert "Some body text." in text

    def test_front_matter_is_valid_yaml(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """The written front matter must be parseable YAML."""
        source = tmp_path / "test.md"
        source.write_text("Body only.\n", encoding="utf-8")

        result = SummaryResult(
            summary="Valid YAML test.",
            concepts=["alpha"],
        )
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        # Extract front matter between --- delimiters
        parts = text.split("---")
        assert len(parts) >= 3, "Expected front matter delimiters"
        meta = yaml.safe_load(parts[1])
        assert meta["grove_summary"] == "Valid YAML test."
        assert meta["grove_concepts"] == ["alpha"]


# ---------------------------------------------------------------------------
# Summariser.write_front_matter — existing front matter
# ---------------------------------------------------------------------------


class TestWriteFrontMatterMerge:
    """Verifies merging grove fields into existing front matter."""

    def test_merges_into_existing_front_matter(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """Grove fields are added alongside existing YAML fields."""
        source = tmp_path / "test.md"
        source.write_text(
            "---\ntitle: My Document\nauthor: Test Author\n---\n\n# Content\n",
            encoding="utf-8",
        )

        result = SummaryResult(
            summary="Merged summary.",
            concepts=["merging"],
        )
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        parts = text.split("---")
        meta = yaml.safe_load(parts[1])

        assert meta["title"] == "My Document"
        assert meta["author"] == "Test Author"
        assert meta["grove_summary"] == "Merged summary."
        assert meta["grove_concepts"] == ["merging"]

    def test_preserves_existing_non_grove_fields(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """Fields unrelated to grove are untouched during merge."""
        source = tmp_path / "test.md"
        source.write_text(
            "---\ncustom_field: preserved\ntags:\n  - one\n  - two\n---\n\nBody.\n",
            encoding="utf-8",
        )

        result = SummaryResult(
            summary="Summary here.",
            concepts=["preservation"],
        )
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        parts = text.split("---")
        meta = yaml.safe_load(parts[1])

        assert meta["custom_field"] == "preserved"
        assert meta["tags"] == ["one", "two"]
        assert meta["grove_summary"] == "Summary here."

    def test_overwrites_existing_grove_fields(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """Re-summarisation overwrites previous grove_summary and grove_concepts."""
        source = tmp_path / "test.md"
        source.write_text(
            "---\ngrove_summary: Old summary.\ngrove_concepts:\n"
            "  - old\n---\n\nBody.\n",
            encoding="utf-8",
        )

        result = SummaryResult(
            summary="New summary.",
            concepts=["new-concept"],
        )
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        parts = text.split("---")
        meta = yaml.safe_load(parts[1])

        assert meta["grove_summary"] == "New summary."
        assert meta["grove_concepts"] == ["new-concept"]

    def test_removes_unsummarised_flag_on_success(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """A successful summarisation removes a previous unsummarised flag."""
        source = tmp_path / "test.md"
        source.write_text(
            "---\nunsummarised: true\n---\n\nBody.\n",
            encoding="utf-8",
        )

        result = SummaryResult(
            summary="Now summarised.",
            concepts=["success"],
        )
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        parts = text.split("---")
        meta = yaml.safe_load(parts[1])

        assert "unsummarised" not in meta
        assert meta["grove_summary"] == "Now summarised."

    def test_writes_unsummarised_flag_on_failure(
        self,
        summariser: Summariser,
        tmp_path: Path,
    ) -> None:
        """An unsummarised result writes the unsummarised flag instead of summary."""
        source = tmp_path / "test.md"
        source.write_text("# Just a heading\n", encoding="utf-8")

        result = SummaryResult(unsummarised=True, error="LLM failed")
        summariser.write_front_matter(source, result)

        text = source.read_text(encoding="utf-8")
        parts = text.split("---")
        meta = yaml.safe_load(parts[1])

        assert meta["unsummarised"] is True
        assert "grove_summary" not in meta


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestStripCodeFences:
    """Verifies code fence stripping for LLM response cleanup."""

    def test_strips_yaml_fences(self) -> None:
        assert _strip_code_fences("```yaml\nfoo: bar\n```") == "foo: bar"

    def test_strips_plain_fences(self) -> None:
        assert _strip_code_fences("```\nfoo: bar\n```") == "foo: bar"

    def test_passes_through_unfenced_text(self) -> None:
        assert _strip_code_fences("foo: bar") == "foo: bar"

    def test_handles_empty_string(self) -> None:
        assert _strip_code_fences("") == ""


class TestSplitFrontMatter:
    """Verifies front matter splitting logic."""

    def test_splits_valid_front_matter(self) -> None:
        text = "---\ntitle: Test\n---\n\nBody here."
        meta, body = _split_front_matter(text)
        assert meta == {"title": "Test"}
        assert "Body here." in body

    def test_returns_empty_dict_for_no_front_matter(self) -> None:
        text = "# Just a heading\n\nBody text."
        meta, body = _split_front_matter(text)
        assert meta == {}
        assert "# Just a heading" in body

    def test_handles_unclosed_front_matter(self) -> None:
        text = "---\ntitle: Unclosed\nNo closing delimiter."
        meta, body = _split_front_matter(text)
        assert meta == {}
        assert "---" in body

    def test_handles_empty_string(self) -> None:
        meta, body = _split_front_matter("")
        assert meta == {}
        assert body == ""
