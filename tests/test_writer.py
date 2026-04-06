"""Tests for ArticleWriter -- the wiki article writer with data-integrity guarantees.

Covers all three invariants:
1. Human annotation preservation (``<!-- grove:human -->`` blocks)
2. Pinned article protection (``pinned: true`` in front matter)
3. Atomic writes (temp directory then move; failure leaves wiki untouched)

Also covers: correct path creation, WriteResult statistics, adversarial
inputs with special characters, and multiple human blocks.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from grove.compile.parser import ParsedArticle
from grove.compile.writer import (
    ArticleWriter,
    WriteResult,
    _extract_human_blocks,
    _inject_human_blocks,
    _parse_front_matter_pinned,
)

# ------------------------------------------------------------------
# Fixtures — reusable article content
# ------------------------------------------------------------------

_FRONT_MATTER = """\
---
title: "Test Article"
compiled_from:
  - raw/source-a.md
concepts: [testing, grove]
summary: "A test article."
last_compiled: "2026-04-03T14:00:00Z"
---"""

_BODY = """
# Test Article

This is the body of a test article.

## Section One

Some content in section one.

## Section Two

Some content in section two.
"""

_ARTICLE_CONTENT = _FRONT_MATTER + _BODY


def _make_article(
    file_path: str = "wiki/test-article.md",
    content: str | None = None,
    title: str = "Test Article",
) -> ParsedArticle:
    """Build a minimal ParsedArticle for testing."""
    if content is None:
        content = _ARTICLE_CONTENT
    return ParsedArticle(
        file_path=file_path,
        title=title,
        compiled_from=["raw/source-a.md"],
        concepts=["testing", "grove"],
        summary="A test article.",
        last_compiled="2026-04-03T14:00:00Z",
        content=content,
        raw_body=_BODY,
    )


def _make_pinned_content(title: str = "Pinned Article") -> str:
    """Build article content with ``pinned: true`` in front matter."""
    return f"""\
---
title: "{title}"
pinned: true
compiled_from:
  - raw/source-pinned.md
concepts: [pinned]
summary: "This article is pinned."
last_compiled: "2026-04-03T14:00:00Z"
---

# {title}

This article must never be overwritten.
"""


def _make_content_with_human_block(
    heading: str = "## Section One",
    annotation: str = "This is a human annotation.",
) -> str:
    """Build article content with a human annotation block after a heading."""
    return f"""\
---
title: "Existing Article"
compiled_from:
  - raw/source-a.md
concepts: [testing]
summary: "An existing article."
last_compiled: "2026-04-03T12:00:00Z"
---

# Existing Article

Introduction paragraph.

{heading}

Some existing content.

<!-- grove:human -->
{annotation}
<!-- /grove:human -->

## Section Two

More content here.
"""


# ------------------------------------------------------------------
# _parse_front_matter_pinned
# ------------------------------------------------------------------


class TestParseFrontMatterPinned:
    """Detection of ``pinned: true`` in YAML front matter."""

    def test_pinned_true_detected(self) -> None:
        content = _make_pinned_content()
        assert _parse_front_matter_pinned(content) is True

    def test_pinned_false_not_detected(self) -> None:
        content = """\
---
title: "Not Pinned"
pinned: false
---

# Not Pinned
"""
        assert _parse_front_matter_pinned(content) is False

    def test_no_pinned_field(self) -> None:
        assert _parse_front_matter_pinned(_ARTICLE_CONTENT) is False

    def test_no_front_matter(self) -> None:
        assert _parse_front_matter_pinned("# Just a heading\n\nNo YAML.") is False

    def test_malformed_yaml(self) -> None:
        content = "---\n: [invalid yaml\n---\n\n# Body"
        assert _parse_front_matter_pinned(content) is False

    def test_pinned_string_not_bool(self) -> None:
        """``pinned: "true"`` (string) should NOT count as pinned."""
        content = '---\ntitle: "Test"\npinned: "true"\n---\n\n# Test'
        assert _parse_front_matter_pinned(content) is False


# ------------------------------------------------------------------
# _extract_human_blocks
# ------------------------------------------------------------------


class TestExtractHumanBlocks:
    """Extraction of ``<!-- grove:human -->`` blocks from article content."""

    def test_single_block_with_heading(self) -> None:
        content = _make_content_with_human_block()
        blocks = _extract_human_blocks(content)
        assert len(blocks) == 1
        block_text, heading = blocks[0]
        assert "This is a human annotation." in block_text
        assert heading == "## Section One"

    def test_no_blocks(self) -> None:
        blocks = _extract_human_blocks(_ARTICLE_CONTENT)
        assert blocks == []

    def test_multiple_blocks(self) -> None:
        content = """\
# Title

## Section A

<!-- grove:human -->
Annotation A.
<!-- /grove:human -->

## Section B

<!-- grove:human -->
Annotation B.
<!-- /grove:human -->
"""
        blocks = _extract_human_blocks(content)
        assert len(blocks) == 2
        assert "Annotation A." in blocks[0][0]
        assert blocks[0][1] == "## Section A"
        assert "Annotation B." in blocks[1][0]
        assert blocks[1][1] == "## Section B"

    def test_block_before_any_heading(self) -> None:
        """A human block before any heading has ``None`` as its heading."""
        content = """\
<!-- grove:human -->
Early annotation.
<!-- /grove:human -->

# Title
"""
        blocks = _extract_human_blocks(content)
        assert len(blocks) == 1
        assert blocks[0][1] is None

    def test_block_with_special_characters(self) -> None:
        """Special characters inside a human block are preserved exactly."""
        annotation = (
            "Special chars: <>&\"'\\n\\t\n"
            "```python\nprint('hello')\n```\n"
            "Unicode: \u00e9\u00e0\u00fc\u00f1 \U0001f600"
        )
        content = f"""\
## Notes

<!-- grove:human -->
{annotation}
<!-- /grove:human -->
"""
        blocks = _extract_human_blocks(content)
        assert len(blocks) == 1
        assert annotation in blocks[0][0]


# ------------------------------------------------------------------
# _inject_human_blocks
# ------------------------------------------------------------------


class TestInjectHumanBlocks:
    """Re-injection of human blocks into new article content."""

    def test_inject_at_matching_heading(self) -> None:
        new_content = """\
# Test

## Section One

New content for section one.

## Section Two

New content for section two.
"""
        block_text = "<!-- grove:human -->\nAnnotation.\n<!-- /grove:human -->"
        blocks = [(block_text, "## Section One")]
        result, count = _inject_human_blocks(new_content, blocks)
        assert count == 1
        assert block_text in result
        # The block should appear after "Section One" content.
        section_one_idx = result.index("## Section One")
        block_idx = result.index(block_text)
        section_two_idx = result.index("## Section Two")
        assert section_one_idx < block_idx < section_two_idx

    def test_inject_appended_when_heading_missing(self) -> None:
        new_content = """\
# Test

## Different Section

Some content.
"""
        block_text = "<!-- grove:human -->\nOrphan.\n<!-- /grove:human -->"
        blocks = [(block_text, "## Missing Section")]
        result, count = _inject_human_blocks(new_content, blocks)
        assert count == 1
        assert block_text in result
        # Block should be at the end since heading was not found.
        assert result.rstrip().endswith("<!-- /grove:human -->")

    def test_inject_appended_when_no_heading(self) -> None:
        """Block with ``None`` heading is always appended."""
        new_content = "# Title\n\nSome content.\n"
        block_text = "<!-- grove:human -->\nNote.\n<!-- /grove:human -->"
        blocks = [(block_text, None)]
        result, count = _inject_human_blocks(new_content, blocks)
        assert count == 1
        assert block_text in result

    def test_no_blocks_returns_unchanged(self) -> None:
        result, count = _inject_human_blocks("unchanged", [])
        assert result == "unchanged"
        assert count == 0


# ------------------------------------------------------------------
# ArticleWriter.write_all — basic writes
# ------------------------------------------------------------------


class TestWriteAll:
    """Core write functionality: correct paths, subdirectories, stats."""

    def test_writes_article_to_correct_path(self, tmp_path: Path) -> None:
        writer = ArticleWriter(tmp_path)
        article = _make_article(file_path="wiki/test-article.md")

        result = writer.write_all([article])

        target = tmp_path / "wiki" / "test-article.md"
        assert target.exists()
        assert "Test Article" in target.read_text(encoding="utf-8")
        assert result.articles_written == 1

    def test_creates_subdirectories(self, tmp_path: Path) -> None:
        writer = ArticleWriter(tmp_path)
        article = _make_article(file_path="wiki/topics/deep/nested/article.md")

        result = writer.write_all([article])

        target = tmp_path / "wiki" / "topics" / "deep" / "nested" / "article.md"
        assert target.exists()
        assert result.articles_written == 1

    def test_writes_multiple_articles(self, tmp_path: Path) -> None:
        writer = ArticleWriter(tmp_path)
        articles = [
            _make_article(file_path="wiki/alpha.md", title="Alpha"),
            _make_article(file_path="wiki/beta.md", title="Beta"),
            _make_article(file_path="wiki/gamma.md", title="Gamma"),
        ]

        result = writer.write_all(articles)

        assert result.articles_written == 3
        assert (tmp_path / "wiki" / "alpha.md").exists()
        assert (tmp_path / "wiki" / "beta.md").exists()
        assert (tmp_path / "wiki" / "gamma.md").exists()

    def test_empty_list_returns_zero_stats(self, tmp_path: Path) -> None:
        writer = ArticleWriter(tmp_path)
        result = writer.write_all([])
        assert result.articles_written == 0
        assert result.articles_skipped_pinned == 0
        assert result.human_blocks_preserved == 0

    def test_new_article_no_existing_file(self, tmp_path: Path) -> None:
        """Writing to a path that does not yet exist works cleanly."""
        writer = ArticleWriter(tmp_path)
        article = _make_article(file_path="wiki/brand-new.md")

        result = writer.write_all([article])

        assert result.articles_written == 1
        assert result.human_blocks_preserved == 0

    def test_returns_write_result_model(self, tmp_path: Path) -> None:
        writer = ArticleWriter(tmp_path)
        result = writer.write_all([_make_article()])
        assert isinstance(result, WriteResult)


# ------------------------------------------------------------------
# Pinned article protection
# ------------------------------------------------------------------


class TestPinnedProtection:
    """Articles with ``pinned: true`` must never be overwritten."""

    def test_skips_pinned_article(self, tmp_path: Path) -> None:
        # Pre-populate wiki/ with a pinned article.
        target = tmp_path / "wiki" / "pinned.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        original_content = _make_pinned_content()
        target.write_text(original_content, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        new_article = _make_article(
            file_path="wiki/pinned.md",
            content="---\ntitle: Overwrite Attempt\n---\n\n# Overwrite\n",
        )

        result = writer.write_all([new_article])

        # File must be unchanged.
        assert target.read_text(encoding="utf-8") == original_content
        assert result.articles_skipped_pinned == 1
        assert result.articles_written == 0

    def test_pinned_with_different_content_still_not_overwritten(
        self, tmp_path: Path
    ) -> None:
        """Even if the new article has completely different content,
        a pinned existing file is never touched."""
        target = tmp_path / "wiki" / "sacred.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = _make_pinned_content(title="Sacred Knowledge")
        target.write_text(original, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        replacement = _make_article(
            file_path="wiki/sacred.md",
            content=(
                "---\ntitle: Totally Different\n---\n\n# Replacement\n\nNew body.\n"
            ),
            title="Totally Different",
        )

        result = writer.write_all([replacement])

        assert target.read_text(encoding="utf-8") == original
        assert result.articles_skipped_pinned == 1
        assert result.articles_written == 0

    def test_mixed_pinned_and_unpinned(self, tmp_path: Path) -> None:
        """Pinned articles are skipped; unpinned articles are written."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # Pinned existing article.
        pinned_path = wiki_dir / "pinned.md"
        pinned_content = _make_pinned_content()
        pinned_path.write_text(pinned_content, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        articles = [
            _make_article(file_path="wiki/pinned.md", title="Pinned Overwrite"),
            _make_article(file_path="wiki/new-article.md", title="New Article"),
        ]

        result = writer.write_all(articles)

        assert result.articles_skipped_pinned == 1
        assert result.articles_written == 1
        assert pinned_path.read_text(encoding="utf-8") == pinned_content
        assert (wiki_dir / "new-article.md").exists()


# ------------------------------------------------------------------
# Human annotation preservation
# ------------------------------------------------------------------


class TestHumanAnnotationPreservation:
    """``<!-- grove:human -->`` blocks survive recompilation."""

    def test_single_human_block_preserved(self, tmp_path: Path) -> None:
        # Existing article with a human annotation.
        target = tmp_path / "wiki" / "annotated.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = _make_content_with_human_block(
            heading="## Section One",
            annotation="My important note.",
        )
        target.write_text(existing, encoding="utf-8")

        # New article from LLM (no human blocks).
        writer = ArticleWriter(tmp_path)
        new_article = _make_article(file_path="wiki/annotated.md")

        result = writer.write_all([new_article])

        written = target.read_text(encoding="utf-8")
        assert "<!-- grove:human -->" in written
        assert "My important note." in written
        assert "<!-- /grove:human -->" in written
        assert result.human_blocks_preserved == 1

    def test_multiple_human_blocks_preserved(self, tmp_path: Path) -> None:
        target = tmp_path / "wiki" / "multi.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = """\
---
title: "Multi-Block Article"
compiled_from:
  - raw/source.md
concepts: [test]
summary: "Article with multiple human blocks."
last_compiled: "2026-04-03T12:00:00Z"
---

# Multi-Block Article

## Section One

Content one.

<!-- grove:human -->
First annotation.
<!-- /grove:human -->

## Section Two

Content two.

<!-- grove:human -->
Second annotation.
<!-- /grove:human -->
"""
        target.write_text(existing, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        new_article = _make_article(file_path="wiki/multi.md")

        result = writer.write_all([new_article])

        written = target.read_text(encoding="utf-8")
        assert written.count("<!-- grove:human -->") == 2
        assert "First annotation." in written
        assert "Second annotation." in written
        assert result.human_blocks_preserved == 2

    def test_human_block_appended_when_heading_removed(self, tmp_path: Path) -> None:
        """If the heading a block was under no longer exists, the block
        is appended at the end rather than lost."""
        target = tmp_path / "wiki" / "orphan-block.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = _make_content_with_human_block(
            heading="## Deleted Section",
            annotation="Orphaned note.",
        )
        target.write_text(existing, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        # New article does NOT have "## Deleted Section".
        new_article = _make_article(file_path="wiki/orphan-block.md")

        result = writer.write_all([new_article])

        written = target.read_text(encoding="utf-8")
        assert "Orphaned note." in written
        assert result.human_blocks_preserved == 1

    def test_new_article_has_no_human_blocks_to_preserve(self, tmp_path: Path) -> None:
        """When writing a brand-new article (no existing file), there are
        no human blocks to extract or inject."""
        writer = ArticleWriter(tmp_path)
        article = _make_article(file_path="wiki/fresh.md")

        result = writer.write_all([article])

        assert result.human_blocks_preserved == 0
        written = (tmp_path / "wiki" / "fresh.md").read_text(encoding="utf-8")
        assert "<!-- grove:human -->" not in written

    def test_adversarial_human_block_special_characters(self, tmp_path: Path) -> None:
        """Human blocks with special/adversarial characters are preserved
        exactly, byte-for-byte."""
        target = tmp_path / "wiki" / "adversarial.md"
        target.parent.mkdir(parents=True, exist_ok=True)

        adversarial_annotation = (
            'Special: <script>alert("xss")</script>\n'
            "Backticks: ```python\nprint('evil')\n```\n"
            "Unicode: \u00e9\u00e0\u00fc\u00f1 \U0001f4a5 \U0001f600\n"
            "Escapes: \\n \\t \\r \\0\n"
            "YAML-like: key: value\n"
            "Regex: ^(.*?)$\n"
            "HTML comment: <!-- not a grove marker -->"
        )

        existing = f"""\
---
title: "Adversarial"
compiled_from: [raw/adv.md]
concepts: [adversarial]
summary: "Test."
last_compiled: "2026-04-03T12:00:00Z"
---

# Adversarial

## Notes

<!-- grove:human -->
{adversarial_annotation}
<!-- /grove:human -->
"""
        target.write_text(existing, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        new_article = _make_article(file_path="wiki/adversarial.md")

        result = writer.write_all([new_article])

        written = target.read_text(encoding="utf-8")
        assert adversarial_annotation in written
        assert result.human_blocks_preserved == 1


# ------------------------------------------------------------------
# Atomic writes
# ------------------------------------------------------------------


class TestAtomicWrites:
    """Writes use a temp directory; on failure, wiki/ is unchanged."""

    def test_temp_directory_cleaned_up_on_success(self, tmp_path: Path) -> None:
        """After a successful write, no ``.grove-write-*`` temp dirs remain."""
        writer = ArticleWriter(tmp_path)
        writer.write_all([_make_article()])

        temp_dirs = list(tmp_path.glob(".grove-write-*"))
        assert temp_dirs == []

    def test_wiki_unchanged_on_write_failure(self, tmp_path: Path) -> None:
        """If writing an article to the temp dir fails, no files are
        written to wiki/ and the existing state is preserved."""
        # Pre-populate an existing article.
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        existing_path = wiki_dir / "existing.md"
        existing_content = "# Existing\n\nOriginal content.\n"
        existing_path.write_text(existing_content, encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        articles = [
            _make_article(file_path="wiki/good.md", title="Good"),
            _make_article(file_path="wiki/bad.md", title="Bad"),
        ]

        # Mock write_text on the second article to raise an error.
        original_write_text = Path.write_text
        call_count = 0

        def failing_write_text(self_path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            if self_path.name == "bad.md":
                raise OSError("Simulated disk failure")
            return original_write_text(self_path, *args, **kwargs)

        with (
            patch.object(Path, "write_text", failing_write_text),
            pytest.raises(OSError, match="Simulated disk failure"),
        ):
            writer.write_all(articles)

        # Wiki must be untouched: existing file preserved, new files absent.
        assert existing_path.read_text(encoding="utf-8") == existing_content
        assert not (wiki_dir / "good.md").exists()
        assert not (wiki_dir / "bad.md").exists()

    def test_temp_directory_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        """Even on failure, the temp directory is cleaned up."""
        writer = ArticleWriter(tmp_path)

        original_write_text = Path.write_text

        def always_fail(self_path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if ".grove-write-" in str(self_path):
                raise OSError("Disk full")
            return original_write_text(self_path, *args, **kwargs)

        with (
            patch.object(Path, "write_text", always_fail),
            pytest.raises(OSError),
        ):
            writer.write_all([_make_article()])

        temp_dirs = list(tmp_path.glob(".grove-write-*"))
        assert temp_dirs == []

    def test_all_or_nothing_multiple_articles(self, tmp_path: Path) -> None:
        """If the third of three articles fails, none appear in wiki/."""
        writer = ArticleWriter(tmp_path)
        articles = [
            _make_article(file_path="wiki/a.md", title="A"),
            _make_article(file_path="wiki/b.md", title="B"),
            _make_article(file_path="wiki/c.md", title="C"),
        ]

        original_write_text = Path.write_text

        def fail_on_c(self_path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self_path.name == "c.md":
                raise OSError("Simulated failure on article C")
            return original_write_text(self_path, *args, **kwargs)

        with (
            patch.object(Path, "write_text", fail_on_c),
            pytest.raises(OSError),
        ):
            writer.write_all(articles)

        wiki_dir = tmp_path / "wiki"
        assert not (wiki_dir / "a.md").exists()
        assert not (wiki_dir / "b.md").exists()
        assert not (wiki_dir / "c.md").exists()


# ------------------------------------------------------------------
# WriteResult statistics
# ------------------------------------------------------------------


class TestWriteResultStats:
    """WriteResult correctly reports all statistics."""

    def test_stats_for_mixed_operation(self, tmp_path: Path) -> None:
        """A write with pinned skips, human blocks, and normal writes
        returns correct aggregate stats."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # Pinned article on disk.
        pinned_path = wiki_dir / "pinned.md"
        pinned_path.write_text(_make_pinned_content(), encoding="utf-8")

        # Article with a human block on disk.
        annotated_path = wiki_dir / "annotated.md"
        annotated_path.write_text(
            _make_content_with_human_block(
                heading="## Section One",
                annotation="Keep this.",
            ),
            encoding="utf-8",
        )

        writer = ArticleWriter(tmp_path)
        articles = [
            _make_article(file_path="wiki/pinned.md", title="Pinned"),
            _make_article(file_path="wiki/annotated.md", title="Annotated"),
            _make_article(file_path="wiki/new.md", title="New"),
        ]

        result = writer.write_all(articles)

        assert result.articles_written == 2
        assert result.articles_skipped_pinned == 1
        assert result.human_blocks_preserved == 1
        assert result.warnings == []

    def test_all_pinned_returns_zero_written(self, tmp_path: Path) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        for name in ("a.md", "b.md"):
            path = wiki_dir / name
            path.write_text(_make_pinned_content(), encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        articles = [
            _make_article(file_path="wiki/a.md"),
            _make_article(file_path="wiki/b.md"),
        ]

        result = writer.write_all(articles)

        assert result.articles_written == 0
        assert result.articles_skipped_pinned == 2


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_overwrites_existing_unpinned_article(self, tmp_path: Path) -> None:
        """An existing article without ``pinned: true`` is replaced."""
        target = tmp_path / "wiki" / "old.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Old content\n", encoding="utf-8")

        writer = ArticleWriter(tmp_path)
        new_content = "---\ntitle: New\n---\n\n# New content\n"
        article = _make_article(
            file_path="wiki/old.md", content=new_content, title="New"
        )

        writer.write_all([article])

        assert "New content" in target.read_text(encoding="utf-8")

    def test_article_path_with_spaces(self, tmp_path: Path) -> None:
        writer = ArticleWriter(tmp_path)
        article = _make_article(
            file_path="wiki/my topic/article name.md",
            title="Spaced Path",
        )

        result = writer.write_all([article])

        target = tmp_path / "wiki" / "my topic" / "article name.md"
        assert target.exists()
        assert result.articles_written == 1

    def test_pinned_check_only_on_existing_file(self, tmp_path: Path) -> None:
        """If the new article's content has ``pinned: true`` but no file
        exists on disk, it should still be written (pinned check is on
        the existing file, not the incoming content)."""
        writer = ArticleWriter(tmp_path)
        pinned_content = _make_pinned_content(title="New Pinned")
        article = _make_article(
            file_path="wiki/new-pinned.md",
            content=pinned_content,
            title="New Pinned",
        )

        result = writer.write_all([article])

        assert result.articles_written == 1
        assert result.articles_skipped_pinned == 0
        target = tmp_path / "wiki" / "new-pinned.md"
        assert target.exists()
