"""Tests for the SourceLoader and token budget management.

Covers quality threshold filtering, origin:query exclusion, token budget
enforcement with summary fallback, empty raw directories, sources without
front matter, and correct checksum computation from full content.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from grove.compile.loader import (
    ContextPayload,
    SourceEntry,
    SourceLoader,
    _split_front_matter,
    estimate_tokens,
)
from grove.config.loader import GroveConfig
from grove.ingest.manifest import ManifestWriter
from grove.ingest.models import ConversionResult
from grove.ingest.summariser import SummaryResult

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _init_grove(root: Path) -> None:
    """Set up a minimal grove structure at *root*."""
    (root / ".grove").mkdir(parents=True, exist_ok=True)
    (root / ".grove" / "logs").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "articles").mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)

    config = {
        "llm": {
            "providers": {
                "anthropic": {"api_key": "test-key"},
                "ollama": {"base_url": "http://localhost:11434"},
            },
            "routing": {
                "fast": {"provider": "anthropic", "model": "test-model"},
                "standard": {"provider": "anthropic", "model": "test-model"},
                "powerful": {"provider": "anthropic", "model": "test-model"},
            },
        },
    }
    (root / ".grove" / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )
    (root / ".grove" / "state.json").write_text(
        json.dumps({}, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_config(root: Path, quality_threshold: str = "partial") -> GroveConfig:
    """Load config from *root* and override the quality threshold."""
    from grove.config.loader import ConfigLoader

    config = ConfigLoader(root).load()
    config.compile.quality_threshold = quality_threshold  # type: ignore[assignment]
    return config


def _register_source(
    root: Path,
    name: str,
    content: str,
    quality: str = "good",
    word_count: int | None = None,
    front_matter: dict[str, object] | None = None,
) -> Path:
    """Create a source file, optionally prepend front matter, and register it.

    Returns the path to the created source file.
    """
    source_path = root / "raw" / "articles" / name
    source_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the file content
    if front_matter:
        fm_str = yaml.dump(
            front_matter,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).rstrip("\n")
        file_text = f"---\n{fm_str}\n---\n{content}"
    else:
        file_text = content

    source_path.write_text(file_text, encoding="utf-8")

    # Compute word count from the body content
    if word_count is None:
        word_count = len(content.split())

    # Register in manifest
    writer = ManifestWriter(root)
    conversion = ConversionResult(
        content=content,
        source_path=f"raw/articles/{name}",
        mime_type="text/markdown",
        converter_used="text",
        word_count=word_count,
        has_headings=True,
    )
    summary = SummaryResult(
        summary=(
            front_matter.get("grove_summary", "Test summary.")
            if front_matter
            else "Test summary."
        ),
        concepts=(
            front_matter.get("grove_concepts", ["testing"])
            if front_matter
            else ["testing"]
        ),
    )
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    writer.register(
        source_path=source_path,
        original_path=f"/original/{name}",
        conversion=conversion,
        quality=quality,
        summary=summary,
        checksum=checksum,
    )
    return source_path


def _make_long_content(word_count: int) -> str:
    """Generate content with approximately *word_count* words."""
    word = "knowledge "
    return (word * word_count).strip()


# ------------------------------------------------------------------
# estimate_tokens
# ------------------------------------------------------------------


class TestEstimateTokens:
    """The estimate_tokens function uses words x 1.3."""

    def test_empty_string(self) -> None:
        """Empty text returns zero tokens."""
        assert estimate_tokens("") == 0

    def test_single_word(self) -> None:
        """A single word returns 1 (int(1 * 1.3) = 1)."""
        assert estimate_tokens("hello") == 1

    def test_ten_words(self) -> None:
        """Ten words returns 13 (int(10 * 1.3) = 13)."""
        text = " ".join(["word"] * 10)
        assert estimate_tokens(text) == 13

    def test_hundred_words(self) -> None:
        """Hundred words returns 130."""
        text = " ".join(["word"] * 100)
        assert estimate_tokens(text) == 130


# ------------------------------------------------------------------
# _split_front_matter
# ------------------------------------------------------------------


class TestSplitFrontMatter:
    """Front matter parsing extracts YAML and body correctly."""

    def test_no_front_matter(self) -> None:
        """Plain text returns empty dict and full text as body."""
        meta, body = _split_front_matter("Just some text.")
        assert meta == {}
        assert body == "Just some text."

    def test_with_front_matter(self) -> None:
        """Standard front matter is parsed into a dict."""
        text = "---\ntitle: Test\n---\nBody text."
        meta, body = _split_front_matter(text)
        assert meta["title"] == "Test"
        assert "Body text." in body

    def test_invalid_yaml(self) -> None:
        """Invalid YAML returns empty dict."""
        text = "---\n: invalid: [yaml\n---\nBody."
        meta, body = _split_front_matter(text)
        assert meta == {}


# ------------------------------------------------------------------
# SourceLoader — basic loading
# ------------------------------------------------------------------


class TestSourceLoaderBasic:
    """SourceLoader loads sources from the manifest."""

    def test_loads_sources_from_raw(self, tmp_path: Path) -> None:
        """Loads all sources registered in the manifest."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        _register_source(tmp_path, "doc1.md", "# Document One\n\nContent here.\n")
        _register_source(tmp_path, "doc2.md", "# Document Two\n\nMore content.\n")

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 2
        assert payload.total_tokens > 0
        assert payload.budget_limit == 800_000

    def test_returns_correct_metadata(self, tmp_path: Path) -> None:
        """Each SourceEntry has path, checksum, token_count, and used_summary."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        content = "# Test\n\nSome body content for testing.\n"
        _register_source(tmp_path, "meta.md", content)

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        entry = payload.sources[0]
        assert "meta.md" in entry.path
        assert len(entry.checksum) == 64  # SHA-256 hex length
        assert entry.token_count > 0
        assert entry.used_summary is False

    def test_empty_raw_directory(self, tmp_path: Path) -> None:
        """An empty manifest returns an empty ContextPayload."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 0
        assert payload.total_tokens == 0
        assert payload.sources_excluded == 0
        assert payload.sources_summarised == 0


# ------------------------------------------------------------------
# SourceLoader — quality filtering
# ------------------------------------------------------------------


class TestSourceLoaderQualityFiltering:
    """SourceLoader filters sources by quality threshold."""

    def test_excludes_poor_with_partial_threshold(self, tmp_path: Path) -> None:
        """With threshold 'partial', poor sources are excluded."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path, quality_threshold="partial")

        _register_source(tmp_path, "good.md", "# Good\n\nContent.\n", quality="good")
        _register_source(
            tmp_path, "partial.md", "# Partial\n\nContent.\n", quality="partial"
        )
        _register_source(tmp_path, "poor.md", "# Poor\n\nContent.\n", quality="poor")

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        paths = [s.path for s in payload.sources]
        assert any("good.md" in p for p in paths)
        assert any("partial.md" in p for p in paths)
        assert not any("poor.md" in p for p in paths)
        assert payload.sources_excluded == 1

    def test_excludes_partial_with_good_threshold(self, tmp_path: Path) -> None:
        """With threshold 'good', only good sources are included."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path, quality_threshold="good")

        _register_source(tmp_path, "good.md", "# Good\n\nContent.\n", quality="good")
        _register_source(
            tmp_path, "partial.md", "# Partial\n\nContent.\n", quality="partial"
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 1
        assert "good.md" in payload.sources[0].path
        assert payload.sources_excluded == 1

    def test_includes_everything_with_poor_threshold(self, tmp_path: Path) -> None:
        """With threshold 'poor', all sources pass."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path, quality_threshold="poor")

        _register_source(tmp_path, "good.md", "# Good\n\n", quality="good")
        _register_source(tmp_path, "poor.md", "# Poor\n\n", quality="poor")

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 2
        assert payload.sources_excluded == 0


# ------------------------------------------------------------------
# SourceLoader — origin: query exclusion
# ------------------------------------------------------------------


class TestSourceLoaderOriginQuery:
    """SourceLoader excludes files with origin: query in front matter."""

    def test_excludes_origin_query_files(self, tmp_path: Path) -> None:
        """Files with origin: query are skipped."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        # Normal source
        _register_source(tmp_path, "normal.md", "# Normal\n\nContent.\n")

        # Query-origin source
        _register_source(
            tmp_path,
            "query-answer.md",
            "# Query Answer\n\nFiled answer.\n",
            front_matter={"origin": "query", "pinned": True},
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        paths = [s.path for s in payload.sources]
        assert any("normal.md" in p for p in paths)
        assert not any("query-answer.md" in p for p in paths)
        assert payload.sources_excluded == 1

    def test_includes_non_query_origin(self, tmp_path: Path) -> None:
        """Files with origin set to something other than 'query' are included."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        _register_source(
            tmp_path,
            "imported.md",
            "# Imported\n\nContent.\n",
            front_matter={"origin": "import"},
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 1


# ------------------------------------------------------------------
# SourceLoader — token budget and summary fallback
# ------------------------------------------------------------------


class TestSourceLoaderTokenBudget:
    """SourceLoader enforces the 800K token budget."""

    def test_uses_summary_for_large_sources(self, tmp_path: Path) -> None:
        """Sources over 10K tokens use grove_summary from front matter."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        # Create a source with ~12K tokens (about 9,230 words at 1.3x)
        long_content = _make_long_content(9_300)
        summary = "A concise summary of the large document."

        _register_source(
            tmp_path,
            "large.md",
            long_content,
            word_count=9_300,
            front_matter={
                "grove_summary": summary,
                "grove_concepts": ["testing"],
            },
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 1
        entry = payload.sources[0]
        assert entry.used_summary is True
        assert entry.content == summary
        assert payload.sources_summarised == 1

    def test_loads_full_text_for_small_sources(self, tmp_path: Path) -> None:
        """Sources under 2K tokens always load full text."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        content = "# Small Document\n\nA short piece of content.\n"
        _register_source(
            tmp_path,
            "small.md",
            content,
            front_matter={
                "grove_summary": "Summary that should not be used.",
                "grove_concepts": ["testing"],
            },
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        entry = payload.sources[0]
        assert entry.used_summary is False
        assert content in entry.content

    def test_large_source_without_summary_loads_full_text(self, tmp_path: Path) -> None:
        """Large sources without a summary load full text regardless of size."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        long_content = _make_long_content(9_300)

        _register_source(
            tmp_path,
            "no-summary.md",
            long_content,
            word_count=9_300,
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        entry = payload.sources[0]
        assert entry.used_summary is False
        assert payload.sources_summarised == 0

    def test_medium_source_uses_summary_when_budget_tight(self, tmp_path: Path) -> None:
        """Sources 2K-10K tokens fall back to summary when budget is exhausted."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        # Fill up most of the budget with a medium source
        # (we'll manipulate the budget to make this testable)
        medium_content = _make_long_content(3_000)
        summary = "Short summary of medium document."

        _register_source(
            tmp_path,
            "medium.md",
            medium_content,
            word_count=3_000,
            front_matter={
                "grove_summary": summary,
                "grove_concepts": ["testing"],
            },
        )

        loader = SourceLoader(tmp_path, config)
        # Set a very small budget so the medium source exceeds it
        loader._budget = 100

        payload = loader.load_all()

        assert len(payload.sources) == 1
        entry = payload.sources[0]
        assert entry.used_summary is True
        assert entry.content == summary

    def test_budget_stops_loading_when_exhausted(self, tmp_path: Path) -> None:
        """Loading stops when budget is fully exhausted."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        # Create multiple sources that together exceed a small budget
        for i in range(5):
            content = _make_long_content(200)
            _register_source(
                tmp_path,
                f"doc{i}.md",
                content,
                word_count=200,
            )

        loader = SourceLoader(tmp_path, config)
        # Set budget to fit roughly 2 sources (200 words * 1.3 = 260 tokens each)
        loader._budget = 550

        payload = loader.load_all()

        # Should load only what fits within the budget
        assert len(payload.sources) < 5
        assert payload.total_tokens <= 550

    def test_total_tokens_is_accurate(self, tmp_path: Path) -> None:
        """Total tokens in payload matches sum of individual token counts."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        _register_source(tmp_path, "a.md", "Word " * 50)
        _register_source(tmp_path, "b.md", "Word " * 100)

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        computed_total = sum(s.token_count for s in payload.sources)
        assert payload.total_tokens == computed_total


# ------------------------------------------------------------------
# SourceLoader — checksum computation
# ------------------------------------------------------------------


class TestSourceLoaderChecksum:
    """Checksum is computed from full content, not summary."""

    def test_checksum_from_full_content(self, tmp_path: Path) -> None:
        """Checksum is SHA-256 of the body text, even when summary is used."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        long_content = _make_long_content(9_300)
        summary = "A summary of the content."

        _register_source(
            tmp_path,
            "checksummed.md",
            long_content,
            word_count=9_300,
            front_matter={
                "grove_summary": summary,
                "grove_concepts": ["testing"],
            },
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        entry = payload.sources[0]
        # The checksum should be of the full body, not the summary
        expected_checksum = hashlib.sha256(long_content.encode("utf-8")).hexdigest()
        assert entry.checksum == expected_checksum
        # But the content should be the summary
        assert entry.used_summary is True
        assert entry.content == summary

    def test_checksum_excludes_front_matter(self, tmp_path: Path) -> None:
        """Checksum is computed from body text only, excluding front matter."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        body_content = "# Document\n\nThe actual content.\n"
        _register_source(
            tmp_path,
            "with-fm.md",
            body_content,
            front_matter={
                "grove_summary": "A summary.",
                "grove_concepts": ["testing"],
            },
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        entry = payload.sources[0]
        expected_checksum = hashlib.sha256(body_content.encode("utf-8")).hexdigest()
        assert entry.checksum == expected_checksum


# ------------------------------------------------------------------
# SourceLoader — sources without front matter
# ------------------------------------------------------------------


class TestSourceLoaderNoFrontMatter:
    """Sources without front matter are loaded as full text."""

    def test_no_front_matter_loads_full_text(self, tmp_path: Path) -> None:
        """A source with no front matter loads the entire file as content."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        content = "# Plain Document\n\nNo front matter here.\n"
        _register_source(tmp_path, "plain.md", content)

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 1
        entry = payload.sources[0]
        assert content in entry.content
        assert entry.used_summary is False

    def test_large_source_without_front_matter(self, tmp_path: Path) -> None:
        """Large source without front matter loads full text (no summary)."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        long_content = _make_long_content(9_300)
        _register_source(
            tmp_path,
            "large-plain.md",
            long_content,
            word_count=9_300,
        )

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        entry = payload.sources[0]
        assert entry.used_summary is False
        assert payload.sources_summarised == 0


# ------------------------------------------------------------------
# SourceLoader — missing source file
# ------------------------------------------------------------------


class TestSourceLoaderMissingFile:
    """SourceLoader handles missing source files gracefully."""

    def test_missing_file_is_skipped(self, tmp_path: Path) -> None:
        """A manifest entry for a deleted file is skipped without error."""
        _init_grove(tmp_path)
        config = _load_config(tmp_path)

        # Register a source
        _register_source(tmp_path, "exists.md", "# Exists\n\nContent.\n")

        # Also register one and then delete the file
        deleted = _register_source(tmp_path, "deleted.md", "# Deleted\n\nGone.\n")
        deleted.unlink()

        loader = SourceLoader(tmp_path, config)
        payload = loader.load_all()

        assert len(payload.sources) == 1
        assert "exists.md" in payload.sources[0].path
        # The deleted file should count as excluded
        assert payload.sources_excluded == 1


# ------------------------------------------------------------------
# ContextPayload and SourceEntry models
# ------------------------------------------------------------------


class TestModels:
    """ContextPayload and SourceEntry Pydantic models validate correctly."""

    def test_source_entry_creation(self) -> None:
        """SourceEntry can be created with all required fields."""
        entry = SourceEntry(
            path="raw/articles/test.md",
            content="Some content.",
            checksum="abc123",
            token_count=42,
            used_summary=False,
        )
        assert entry.path == "raw/articles/test.md"
        assert entry.token_count == 42

    def test_context_payload_defaults(self) -> None:
        """ContextPayload has sensible defaults."""
        payload = ContextPayload()
        assert payload.sources == []
        assert payload.total_tokens == 0
        assert payload.budget_limit == 800_000
        assert payload.sources_summarised == 0
        assert payload.sources_excluded == 0

    def test_context_payload_with_sources(self) -> None:
        """ContextPayload correctly holds a list of SourceEntry objects."""
        entries = [
            SourceEntry(
                path=f"raw/articles/doc{i}.md",
                content=f"Content {i}.",
                checksum=f"hash{i}",
                token_count=100,
                used_summary=False,
            )
            for i in range(3)
        ]
        payload = ContextPayload(
            sources=entries,
            total_tokens=300,
            sources_summarised=0,
            sources_excluded=1,
        )
        assert len(payload.sources) == 3
        assert payload.total_tokens == 300
        assert payload.sources_excluded == 1
