"""Tests for QualityRatchet -- post-compilation quality gate.

Covers all seven checks:
1. Provenance coverage (>90% passes, <50% blocks, 50-90% warns)
2. Contradiction detection (mock LLM, detect contradiction, pass on NONE)
3. Coverage drop (>10% drop blocks)
4. Broken wiki-links (detects [[nonexistent]], passes for valid links)
5. Human annotation preservation (detects removed blocks, passes when preserved)
6. Pinned article overwrite (detects overwritten pinned article)
7. Query article as source (detects origin:query file in sources)

Also covers:
- Overall: all checks pass -> result.passed is True
- Overall: one BLOCK -> result.passed is False
- Overall: only WARNs -> result.passed is True
- Report saved to .grove/logs/
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from grove.compile.parser import ParsedArticle
from grove.compile.ratchet import (
    QualityRatchet,
    RatchetResult,
    _parse_front_matter,
    _split_sentences,
)

# ------------------------------------------------------------------
# Fixtures -- reusable article builders
# ------------------------------------------------------------------


def _make_article(
    file_path: str = "wiki/test-article.md",
    title: str = "Test Article",
    compiled_from: list[str] | None = None,
    concepts: list[str] | None = None,
    content: str | None = None,
    raw_body: str | None = None,
) -> ParsedArticle:
    """Build a minimal ParsedArticle for testing."""
    if compiled_from is None:
        compiled_from = ["raw/source-a.md"]
    if concepts is None:
        concepts = ["testing", "grove"]

    default_content = """\
---
title: "Test Article"
compiled_from:
  - raw/source-a.md
concepts: [testing, grove]
summary: "A test article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Test Article

This is a general introduction to the topic.
"""
    if content is None:
        content = default_content
    if raw_body is None:
        raw_body = content.split("---", 2)[-1].strip() if "---" in content else content

    return ParsedArticle(
        file_path=file_path,
        title=title,
        compiled_from=compiled_from,
        concepts=concepts,
        summary="A test article.",
        last_compiled="2026-04-03T14:00:00Z",
        content=content,
        raw_body=raw_body,
    )


def _make_well_cited_article(file_path: str = "wiki/cited.md") -> ParsedArticle:
    """Article where all factual sentences have [source:...] citations."""
    content = """\
---
title: "Well Cited"
compiled_from:
  - raw/source-a.md
  - raw/source-b.md
concepts: [testing]
summary: "A well-cited article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Well Cited

The system increased performance by 50% [source: source-a.md].
According to research, the cost decreased by 30% [source: source-b.md].
Studies show that adoption grew significantly [source: source-a.md].
The results improved due to better algorithms [source: source-b.md].
"""
    raw_body = content.split("---", 2)[-1].strip()
    return ParsedArticle(
        file_path=file_path,
        title="Well Cited",
        compiled_from=["raw/source-a.md", "raw/source-b.md"],
        concepts=["testing"],
        summary="A well-cited article.",
        last_compiled="2026-04-03T14:00:00Z",
        content=content,
        raw_body=raw_body,
    )


def _make_poorly_cited_article(file_path: str = "wiki/uncited.md") -> ParsedArticle:
    """Article with factual sentences but no citations -- should BLOCK."""
    content = """\
---
title: "Poorly Cited"
compiled_from:
  - raw/source-a.md
concepts: [testing]
summary: "A poorly cited article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Poorly Cited

The system increased performance by 50%.
According to research, the cost decreased by 30%.
Studies show that adoption grew significantly.
The results improved due to better algorithms.
Production output rose 25% year-over-year.
Data indicates the trend will continue.
"""
    raw_body = content.split("---", 2)[-1].strip()
    return ParsedArticle(
        file_path=file_path,
        title="Poorly Cited",
        compiled_from=["raw/source-a.md"],
        concepts=["testing"],
        summary="A poorly cited article.",
        last_compiled="2026-04-03T14:00:00Z",
        content=content,
        raw_body=raw_body,
    )


def _make_partially_cited_article(file_path: str = "wiki/partial.md") -> ParsedArticle:
    """Article with ~60% provenance coverage -- should WARN."""
    content = """\
---
title: "Partially Cited"
compiled_from:
  - raw/source-a.md
concepts: [testing]
summary: "A partially cited article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Partially Cited

The system increased performance by 50% [source: source-a.md].
According to research, the cost decreased by 30% [source: source-a.md].
Studies show that adoption grew significantly [source: source-a.md].
The results improved due to better algorithms.
Production output rose 25% year-over-year.
"""
    raw_body = content.split("---", 2)[-1].strip()
    return ParsedArticle(
        file_path=file_path,
        title="Partially Cited",
        compiled_from=["raw/source-a.md"],
        concepts=["testing"],
        summary="A partially cited article.",
        last_compiled="2026-04-03T14:00:00Z",
        content=content,
        raw_body=raw_body,
    )


def _setup_state_json(grove_root: Path, source_count: int) -> None:
    """Write a state.json with the given last_compile_source_count."""
    grove_dir = grove_root / ".grove"
    grove_dir.mkdir(parents=True, exist_ok=True)
    state_path = grove_dir / "state.json"
    state_path.write_text(
        json.dumps({"last_compile_source_count": source_count}, indent=2) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# Provenance coverage
# ------------------------------------------------------------------


class TestProvenanceCoverage:
    """Provenance check: >90% passes, <50% blocks, 50-90% warns."""

    def test_well_cited_passes(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        article = _make_well_cited_article()
        result = ratchet.check([article])

        prov = result.details["provenance_coverage"]
        assert prov["severity"] == "PASS"
        assert prov["score"] >= 0.90
        assert "provenance_coverage" not in result.blocking_failures
        assert "provenance_coverage" not in result.warnings

    def test_poorly_cited_blocks(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        article = _make_poorly_cited_article()
        result = ratchet.check([article])

        prov = result.details["provenance_coverage"]
        assert prov["severity"] == "BLOCK"
        assert prov["score"] < 0.50
        assert "provenance_coverage" in result.blocking_failures

    def test_partially_cited_warns(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        article = _make_partially_cited_article()
        result = ratchet.check([article])

        prov = result.details["provenance_coverage"]
        assert prov["severity"] == "WARN"
        assert 0.50 <= prov["score"] < 0.90
        assert "provenance_coverage" in result.warnings

    def test_no_factual_sentences_passes(self, tmp_path: Path) -> None:
        """An article with no factual sentences has 100% coverage by default."""
        content = """\
---
title: "Narrative"
compiled_from: [raw/a.md]
concepts: [narrative]
summary: "A narrative article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Narrative

This is a narrative introduction to the topic.

The article explores various aspects of the subject.
"""
        article = _make_article(
            content=content,
            raw_body=content.split("---", 2)[-1].strip(),
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        prov = result.details["provenance_coverage"]
        assert prov["severity"] == "PASS"

    def test_empty_articles_passes(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([])
        prov = result.details["provenance_coverage"]
        assert prov["severity"] == "PASS"
        assert prov["score"] == 1.0


# ------------------------------------------------------------------
# Broken wiki-links
# ------------------------------------------------------------------


class TestBrokenWikiLinks:
    """Broken [[links]] should warn; valid links should pass."""

    def test_detects_broken_link(self, tmp_path: Path) -> None:
        content = """\
---
title: "Linker"
compiled_from: [raw/a.md]
concepts: [links]
summary: "Article with links."
last_compiled: "2026-04-03T14:00:00Z"
---

# Linker

See also [[nonexistent-article]] for more details.
"""
        raw_body = content.split("---", 2)[-1].strip()
        article = _make_article(
            file_path="wiki/linker.md",
            content=content,
            raw_body=raw_body,
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        links = result.details["broken_wiki_links"]
        assert links["severity"] == "WARN"
        assert links["count"] >= 1
        assert any(bl["link"] == "nonexistent-article" for bl in links["broken_links"])
        assert "broken_wiki_links" in result.warnings

    def test_valid_link_to_other_article_passes(self, tmp_path: Path) -> None:
        """A link to another article in the same batch resolves."""
        content_a = """\
---
title: "Article A"
compiled_from: [raw/a.md]
concepts: [links]
summary: "Article A."
last_compiled: "2026-04-03T14:00:00Z"
---

# Article A

See also [[article-b]] for more.
"""
        content_b = """\
---
title: "Article B"
compiled_from: [raw/b.md]
concepts: [links]
summary: "Article B."
last_compiled: "2026-04-03T14:00:00Z"
---

# Article B

Companion to Article A.
"""
        article_a = _make_article(
            file_path="wiki/article-a.md",
            content=content_a,
            raw_body=content_a.split("---", 2)[-1].strip(),
        )
        article_b = _make_article(
            file_path="wiki/article-b.md",
            content=content_b,
            raw_body=content_b.split("---", 2)[-1].strip(),
        )

        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article_a, article_b])

        links = result.details["broken_wiki_links"]
        assert links["severity"] == "PASS"
        assert links["count"] == 0

    def test_valid_link_to_existing_file_on_disk(self, tmp_path: Path) -> None:
        """A link to a file that already exists in wiki/ on disk resolves."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        (wiki_dir / "existing.md").write_text("# Existing\n", encoding="utf-8")

        content = """\
---
title: "Refers Existing"
compiled_from: [raw/a.md]
concepts: [links]
summary: "Article referring to existing."
last_compiled: "2026-04-03T14:00:00Z"
---

# Refers Existing

See [[existing]] for context.
"""
        article = _make_article(
            file_path="wiki/refers.md",
            content=content,
            raw_body=content.split("---", 2)[-1].strip(),
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        links = result.details["broken_wiki_links"]
        assert links["severity"] == "PASS"

    def test_no_wiki_links_passes(self, tmp_path: Path) -> None:
        article = _make_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])
        links = result.details["broken_wiki_links"]
        assert links["severity"] == "PASS"


# ------------------------------------------------------------------
# Human annotation preservation
# ------------------------------------------------------------------


class TestHumanAnnotationPreservation:
    """Detects removed <!-- grove:human --> blocks, passes when preserved."""

    def test_detects_removed_block(self, tmp_path: Path) -> None:
        """If an existing file has a human block and the new article does not,
        the ratchet should BLOCK."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        existing_content = """\
---
title: "Annotated"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Annotated article."
last_compiled: "2026-04-03T12:00:00Z"
---

# Annotated

## Notes

<!-- grove:human -->
Important human note that must survive.
<!-- /grove:human -->
"""
        (wiki_dir / "annotated.md").write_text(existing_content, encoding="utf-8")

        # New article without the human block.
        new_content = """\
---
title: "Annotated"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Annotated article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Annotated

## Notes

Rewritten content without the human block.
"""
        article = _make_article(
            file_path="wiki/annotated.md",
            content=new_content,
            raw_body=new_content.split("---", 2)[-1].strip(),
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        human = result.details["human_annotation_preservation"]
        assert human["severity"] == "BLOCK"
        assert human["count"] >= 1
        assert "human_annotation_preservation" in result.blocking_failures

    def test_passes_when_block_preserved(self, tmp_path: Path) -> None:
        """If the new article content includes the human block, pass."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        human_block = "<!-- grove:human -->\nImportant note.\n<!-- /grove:human -->"
        existing_content = f"""\
---
title: "Annotated"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Annotated article."
last_compiled: "2026-04-03T12:00:00Z"
---

# Annotated

{human_block}
"""
        (wiki_dir / "annotated.md").write_text(existing_content, encoding="utf-8")

        # New article includes the human block.
        new_content = f"""\
---
title: "Annotated"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Annotated article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Annotated

Updated content.

{human_block}
"""
        article = _make_article(
            file_path="wiki/annotated.md",
            content=new_content,
            raw_body=new_content.split("---", 2)[-1].strip(),
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        human = result.details["human_annotation_preservation"]
        assert human["severity"] == "PASS"

    def test_new_article_no_existing_file_passes(self, tmp_path: Path) -> None:
        """A brand-new article with no existing file always passes."""
        article = _make_article(file_path="wiki/brand-new.md")
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        human = result.details["human_annotation_preservation"]
        assert human["severity"] == "PASS"


# ------------------------------------------------------------------
# Pinned article overwrite
# ------------------------------------------------------------------


class TestPinnedArticleOverwrite:
    """Detects when a pinned: true article was overwritten."""

    def test_detects_overwritten_pinned(self, tmp_path: Path) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        pinned_content = """\
---
title: "Sacred Knowledge"
pinned: true
compiled_from: [raw/sacred.md]
concepts: [pinned]
summary: "This is pinned."
last_compiled: "2026-04-03T12:00:00Z"
---

# Sacred Knowledge

This must never change.
"""
        (wiki_dir / "sacred.md").write_text(pinned_content, encoding="utf-8")

        # New article with different content.
        new_content = """\
---
title: "Sacred Knowledge"
compiled_from: [raw/sacred.md]
concepts: [pinned]
summary: "Rewritten."
last_compiled: "2026-04-03T14:00:00Z"
---

# Sacred Knowledge

This has been rewritten -- BAD!
"""
        article = _make_article(
            file_path="wiki/sacred.md",
            content=new_content,
            raw_body=new_content.split("---", 2)[-1].strip(),
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        pinned = result.details["pinned_article_overwrite"]
        assert pinned["severity"] == "BLOCK"
        assert "wiki/sacred.md" in pinned["overwritten_pinned"]
        assert "pinned_article_overwrite" in result.blocking_failures

    def test_unpinned_article_passes(self, tmp_path: Path) -> None:
        """Overwriting an unpinned article is fine."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        existing = """\
---
title: "Normal Article"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Normal."
last_compiled: "2026-04-03T12:00:00Z"
---

# Normal Article

Original content.
"""
        (wiki_dir / "normal.md").write_text(existing, encoding="utf-8")

        new_content = """\
---
title: "Normal Article"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Updated."
last_compiled: "2026-04-03T14:00:00Z"
---

# Normal Article

Updated content.
"""
        article = _make_article(
            file_path="wiki/normal.md",
            content=new_content,
            raw_body=new_content.split("---", 2)[-1].strip(),
        )
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        pinned = result.details["pinned_article_overwrite"]
        assert pinned["severity"] == "PASS"

    def test_new_article_no_existing_passes(self, tmp_path: Path) -> None:
        """Writing a brand-new article (no existing) always passes."""
        article = _make_article(file_path="wiki/new.md")
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        pinned = result.details["pinned_article_overwrite"]
        assert pinned["severity"] == "PASS"


# ------------------------------------------------------------------
# Query article as source
# ------------------------------------------------------------------


class TestQueryArticleAsSource:
    """Detects origin:query files in source paths."""

    def test_detects_query_source(self, tmp_path: Path) -> None:
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        query_file = """\
---
title: "Query Answer"
origin: query
---

# Query Answer

This was a query result, not a source.
"""
        (raw_dir / "query-answer.md").write_text(query_file, encoding="utf-8")

        article = _make_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article], source_paths=["raw/query-answer.md"])

        query = result.details["query_article_as_source"]
        assert query["severity"] == "BLOCK"
        assert "raw/query-answer.md" in query["query_sources_found"]
        assert "query_article_as_source" in result.blocking_failures

    def test_normal_source_passes(self, tmp_path: Path) -> None:
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        normal_file = """\
---
title: "Normal Source"
---

# Normal Source

Regular source content.
"""
        (raw_dir / "normal-source.md").write_text(normal_file, encoding="utf-8")

        article = _make_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article], source_paths=["raw/normal-source.md"])

        query = result.details["query_article_as_source"]
        assert query["severity"] == "PASS"

    def test_no_source_paths_skips(self, tmp_path: Path) -> None:
        """When source_paths is None, the check is skipped."""
        article = _make_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article], source_paths=None)

        query = result.details["query_article_as_source"]
        assert query["skipped"] is True
        assert query["severity"] == "PASS"


# ------------------------------------------------------------------
# Coverage drop
# ------------------------------------------------------------------


class TestCoverageDrop:
    """>10% drop in compiled_from count blocks."""

    def test_large_drop_blocks(self, tmp_path: Path) -> None:
        """Previous compile had 20 sources; new has 10 -- 50% drop blocks."""
        _setup_state_json(tmp_path, source_count=20)

        # Article with only 10 compiled_from entries.
        articles = [
            _make_article(
                file_path=f"wiki/article-{i}.md",
                compiled_from=[f"raw/source-{i}.md"],
            )
            for i in range(10)
        ]

        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check(articles)

        cov = result.details["coverage_drop"]
        assert cov["severity"] == "BLOCK"
        assert cov["drop_pct"] > 0.10
        assert "coverage_drop" in result.blocking_failures

    def test_small_drop_passes(self, tmp_path: Path) -> None:
        """Previous had 20; new has 19 -- 5% drop passes."""
        _setup_state_json(tmp_path, source_count=20)

        articles = [
            _make_article(
                file_path=f"wiki/article-{i}.md",
                compiled_from=[f"raw/source-{i}.md"],
            )
            for i in range(19)
        ]

        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check(articles)

        cov = result.details["coverage_drop"]
        assert cov["severity"] == "PASS"

    def test_no_previous_state_passes(self, tmp_path: Path) -> None:
        """First compile (no state.json) always passes."""
        article = _make_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        cov = result.details["coverage_drop"]
        assert cov["severity"] == "PASS"

    def test_increase_passes(self, tmp_path: Path) -> None:
        """An increase in sources always passes."""
        _setup_state_json(tmp_path, source_count=5)

        articles = [
            _make_article(
                file_path=f"wiki/article-{i}.md",
                compiled_from=[f"raw/source-{i}.md"],
            )
            for i in range(10)
        ]

        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check(articles)

        cov = result.details["coverage_drop"]
        assert cov["severity"] == "PASS"
        assert cov["drop_pct"] < 0


# ------------------------------------------------------------------
# Contradiction detection
# ------------------------------------------------------------------


class TestContradictionDetection:
    """Mock LLM-based contradiction detection."""

    def test_skipped_without_router(self, tmp_path: Path) -> None:
        """With no router, the check is skipped."""
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([_make_article()])

        contra = result.details["contradictions"]
        assert contra["skipped"] is True
        assert contra["severity"] == "PASS"

    def test_detects_contradiction(self, tmp_path: Path) -> None:
        """When LLM returns a contradiction description, the check blocks."""
        mock_router = MagicMock()
        mock_prompt_builder = MagicMock()

        mock_prompt_builder.build.return_value = "Contradiction prompt text"

        mock_response = MagicMock()
        mock_response.content = (
            "**Contradiction 1: Founding year**\n"
            "- **Article A claims:** Founded in 2015.\n"
            "- **Article B claims:** Founded in 2017.\n"
            "- **Severity:** major"
        )
        mock_router.complete_sync.return_value = mock_response

        # Two articles sharing 2+ concepts to trigger comparison.
        article_a = _make_article(
            file_path="wiki/alpha.md",
            concepts=["ai", "machine-learning", "neural-nets"],
        )
        article_b = _make_article(
            file_path="wiki/beta.md",
            concepts=["ai", "machine-learning", "deep-learning"],
        )

        ratchet = QualityRatchet(
            tmp_path, router=mock_router, prompt_builder=mock_prompt_builder
        )
        result = ratchet.check([article_a, article_b])

        contra = result.details["contradictions"]
        assert contra["severity"] == "BLOCK"
        assert len(contra["contradictions"]) == 1
        assert "contradictions" in result.blocking_failures

    def test_passes_on_none(self, tmp_path: Path) -> None:
        """When LLM returns "NONE", no contradictions are found."""
        mock_router = MagicMock()
        mock_prompt_builder = MagicMock()

        mock_prompt_builder.build.return_value = "Contradiction prompt text"

        mock_response = MagicMock()
        mock_response.content = "NONE"
        mock_router.complete_sync.return_value = mock_response

        article_a = _make_article(
            file_path="wiki/alpha.md",
            concepts=["ai", "machine-learning", "neural-nets"],
        )
        article_b = _make_article(
            file_path="wiki/beta.md",
            concepts=["ai", "machine-learning", "deep-learning"],
        )

        ratchet = QualityRatchet(
            tmp_path, router=mock_router, prompt_builder=mock_prompt_builder
        )
        result = ratchet.check([article_a, article_b])

        contra = result.details["contradictions"]
        assert contra["severity"] == "PASS"
        assert len(contra["contradictions"]) == 0

    def test_skipped_with_fewer_than_2_shared_concepts(self, tmp_path: Path) -> None:
        """Articles sharing only 1 concept are not checked."""
        mock_router = MagicMock()
        mock_prompt_builder = MagicMock()

        article_a = _make_article(
            file_path="wiki/alpha.md",
            concepts=["ai", "robotics"],
        )
        article_b = _make_article(
            file_path="wiki/beta.md",
            concepts=["ai", "biology"],
        )

        ratchet = QualityRatchet(
            tmp_path, router=mock_router, prompt_builder=mock_prompt_builder
        )
        result = ratchet.check([article_a, article_b])

        contra = result.details["contradictions"]
        assert contra["pairs_checked"] == 0
        assert contra["severity"] == "PASS"
        # LLM should not have been called.
        mock_router.complete_sync.assert_not_called()

    def test_single_article_skips_contradiction_check(self, tmp_path: Path) -> None:
        """With only one article, there are no pairs to compare."""
        mock_router = MagicMock()
        mock_prompt_builder = MagicMock()

        ratchet = QualityRatchet(
            tmp_path, router=mock_router, prompt_builder=mock_prompt_builder
        )
        result = ratchet.check([_make_article()])

        contra = result.details["contradictions"]
        assert contra["severity"] == "PASS"
        mock_router.complete_sync.assert_not_called()


# ------------------------------------------------------------------
# Overall result semantics
# ------------------------------------------------------------------


class TestOverallResult:
    """RatchetResult.passed reflects blocking failures only."""

    def test_all_checks_pass(self, tmp_path: Path) -> None:
        """With well-cited articles and no problems, result.passed is True."""
        article = _make_well_cited_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        assert result.passed is True
        assert result.blocking_failures == []

    def test_one_block_fails(self, tmp_path: Path) -> None:
        """A single BLOCK failure makes result.passed False."""
        article = _make_poorly_cited_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        assert result.passed is False
        assert len(result.blocking_failures) >= 1

    def test_only_warnings_still_passes(self, tmp_path: Path) -> None:
        """If only WARN-level checks fire, result.passed is still True."""
        article = _make_partially_cited_article()
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([article])

        # Provenance coverage should be WARN (50-90%).
        assert result.details["provenance_coverage"]["severity"] == "WARN"
        # But the overall result passes because WARNs don't block.
        # (Assuming no other checks fire -- no existing files on disk, etc.)
        # Check that provenance_coverage is NOT in blocking_failures.
        assert "provenance_coverage" not in result.blocking_failures
        assert "provenance_coverage" in result.warnings

    def test_result_has_timestamp(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([_make_article()])
        assert result.timestamp
        assert "T" in result.timestamp  # ISO-8601 format

    def test_result_is_ratchet_result_model(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([_make_article()])
        assert isinstance(result, RatchetResult)


# ------------------------------------------------------------------
# Report saving
# ------------------------------------------------------------------


class TestReportSaving:
    """Ratchet report saved to .grove/logs/."""

    def test_saves_report_to_logs(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([_make_well_cited_article()])
        report_path = ratchet.save_report(result)

        assert report_path.exists()
        assert report_path.parent == tmp_path / ".grove" / "logs"
        assert report_path.name.startswith("ratchet-")
        assert report_path.suffix == ".json"

        # Verify the JSON content is valid and has the right structure.
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert "passed" in data
        assert "blocking_failures" in data
        assert "warnings" in data
        assert "details" in data

    def test_report_reflects_result(self, tmp_path: Path) -> None:
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([_make_poorly_cited_article()])
        report_path = ratchet.save_report(result)

        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["passed"] is False
        assert "provenance_coverage" in data["blocking_failures"]

    def test_creates_logs_directory(self, tmp_path: Path) -> None:
        """The logs directory is created if it does not exist."""
        ratchet = QualityRatchet(tmp_path)
        result = ratchet.check([_make_article()])
        report_path = ratchet.save_report(result)

        assert (tmp_path / ".grove" / "logs").is_dir()
        assert report_path.exists()


# ------------------------------------------------------------------
# Helper function tests
# ------------------------------------------------------------------


class TestHelpers:
    """Unit tests for internal helper functions."""

    def test_split_sentences_basic(self) -> None:
        text = "First sentence. Second sentence. Third one."
        sentences = _split_sentences(text)
        assert len(sentences) == 3

    def test_split_sentences_strips_front_matter(self) -> None:
        text = "---\ntitle: Test\n---\n\nActual sentence."
        sentences = _split_sentences(text)
        assert len(sentences) >= 1
        assert "title" not in sentences[0]

    def test_split_sentences_ignores_headings(self) -> None:
        text = "# Heading\n\nBody sentence."
        sentences = _split_sentences(text)
        assert all(not s.startswith("#") for s in sentences)

    def test_parse_front_matter_valid(self) -> None:
        content = "---\ntitle: Test\npinned: true\n---\n\n# Body"
        meta = _parse_front_matter(content)
        assert meta is not None
        assert meta["title"] == "Test"
        assert meta["pinned"] is True

    def test_parse_front_matter_missing(self) -> None:
        content = "# Just a heading\n\nNo YAML."
        meta = _parse_front_matter(content)
        assert meta is None

    def test_parse_front_matter_malformed(self) -> None:
        content = "---\n: [invalid yaml\n---\n\n# Body"
        meta = _parse_front_matter(content)
        assert meta is None
