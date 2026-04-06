"""Tests for ArticleParser — the LLM compilation response parser.

Covers clean responses, malformed YAML, missing fields, truncated output,
fallback parsing, garbage input, and the guarantee that the parser never
raises an unhandled exception.
"""

from __future__ import annotations

from grove.compile.parser import (
    ArticleParser,
    ParsedArticle,
    _extract_title_from_body,
    _looks_truncated,
    _split_yaml_front_matter,
)

# ------------------------------------------------------------------
# Fixtures — reusable LLM response fragments
# ------------------------------------------------------------------

_CLEAN_ARTICLE_1 = """\
<!-- grove:article wiki/topics/transformers/overview.md -->
---
title: "Transformer Architecture"
compiled_from:
  - raw/papers/attention-is-all-you-need.md
  - raw/articles/transformers-explained.md
concepts: [transformer, self-attention, encoder-decoder]
summary: "Overview of the transformer architecture."
status: published
generation: 3
last_compiled: "2026-04-03T14:22:00Z"
---

# Transformer Architecture

The transformer architecture uses self-attention mechanisms.

[source: attention-is-all-you-need.md]
"""

_CLEAN_ARTICLE_2 = """\
<!-- grove:article wiki/topics/embeddings/word2vec.md -->
---
title: "Word2Vec Embeddings"
compiled_from:
  - raw/papers/word2vec.md
concepts: [word2vec, embeddings, skip-gram]
summary: "Introduction to word2vec embedding models."
last_compiled: "2026-04-03T14:22:00Z"
---

# Word2Vec Embeddings

Word2Vec produces dense vector representations of words.

[source: word2vec.md]
"""

_CLEAN_TWO_ARTICLES = _CLEAN_ARTICLE_1 + _CLEAN_ARTICLE_2


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


def _parser() -> ArticleParser:
    """Create a fresh parser instance."""
    return ArticleParser()


# ------------------------------------------------------------------
# _split_yaml_front_matter
# ------------------------------------------------------------------


class TestSplitYamlFrontMatter:
    """The YAML front matter splitter handles edge cases."""

    def test_no_front_matter(self) -> None:
        """Plain text returns None yaml_str and full text as body."""
        yaml_str, body = _split_yaml_front_matter("Just some text.")
        assert yaml_str is None
        assert body == "Just some text."

    def test_standard_front_matter(self) -> None:
        """Standard --- delimited block is extracted."""
        text = "---\ntitle: Test\n---\nBody text."
        yaml_str, body = _split_yaml_front_matter(text)
        assert yaml_str is not None
        assert "title: Test" in yaml_str
        assert "Body text." in body

    def test_unclosed_front_matter(self) -> None:
        """Unclosed --- block returns None yaml_str."""
        text = "---\ntitle: Test\nNo closing delimiter"
        yaml_str, body = _split_yaml_front_matter(text)
        assert yaml_str is None

    def test_leading_newlines(self) -> None:
        """Leading newlines before --- are stripped."""
        text = "\n\n---\ntitle: Test\n---\nBody."
        yaml_str, body = _split_yaml_front_matter(text)
        assert yaml_str is not None
        assert "title: Test" in yaml_str


# ------------------------------------------------------------------
# _looks_truncated
# ------------------------------------------------------------------


class TestLooksTruncated:
    """Truncation heuristic catches incomplete articles."""

    def test_very_short_text(self) -> None:
        """Text shorter than 20 non-whitespace chars is truncated."""
        assert _looks_truncated("---\ntitle") is True

    def test_unclosed_yaml(self) -> None:
        """Unclosed front matter is truncated."""
        assert (
            _looks_truncated("---\ntitle: Test\nsome content here that is long") is True
        )

    def test_mid_sentence_ending(self) -> None:
        """Text ending mid-sentence (letter) is truncated."""
        text = "---\ntitle: Test\n---\n\nSome content that ends abruptl"
        assert _looks_truncated(text) is True

    def test_complete_article(self) -> None:
        """A properly terminated article is not truncated."""
        text = "---\ntitle: Test\n---\n\n# Test\n\nComplete article.\n"
        assert _looks_truncated(text) is False


# ------------------------------------------------------------------
# _extract_title_from_body
# ------------------------------------------------------------------


class TestExtractTitleFromBody:
    """Title extraction from markdown headings."""

    def test_extracts_h1(self) -> None:
        """First H1 heading is used as title."""
        assert _extract_title_from_body("# My Title\n\nBody.") == "My Title"

    def test_no_heading(self) -> None:
        """Returns 'Untitled' when no heading is found."""
        assert _extract_title_from_body("Just body text.") == "Untitled"

    def test_skips_h2(self) -> None:
        """H2 headings are not treated as titles."""
        assert _extract_title_from_body("## Subtitle\n\nBody.") == "Untitled"


# ------------------------------------------------------------------
# ArticleParser — clean responses
# ------------------------------------------------------------------


class TestParserCleanResponse:
    """Parser handles well-formed LLM responses correctly."""

    def test_parses_two_articles(self) -> None:
        """A response with two clean articles returns two ParsedArticle objects."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert len(articles) == 2

    def test_extracts_file_paths(self) -> None:
        """File paths are extracted from the marker comments."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].file_path == "wiki/topics/transformers/overview.md"
        assert articles[1].file_path == "wiki/topics/embeddings/word2vec.md"

    def test_parses_title(self) -> None:
        """Title is extracted from YAML front matter."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].title == "Transformer Architecture"
        assert articles[1].title == "Word2Vec Embeddings"

    def test_parses_compiled_from(self) -> None:
        """compiled_from list is extracted correctly."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].compiled_from == [
            "raw/papers/attention-is-all-you-need.md",
            "raw/articles/transformers-explained.md",
        ]

    def test_parses_concepts(self) -> None:
        """Concepts list is extracted correctly."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].concepts == [
            "transformer",
            "self-attention",
            "encoder-decoder",
        ]

    def test_parses_summary(self) -> None:
        """Summary string is extracted correctly."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].summary == "Overview of the transformer architecture."

    def test_parses_last_compiled(self) -> None:
        """last_compiled timestamp is extracted correctly."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].last_compiled == "2026-04-03T14:22:00Z"

    def test_parses_status(self) -> None:
        """Status is extracted from front matter."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].status == "published"

    def test_parses_generation(self) -> None:
        """Generation number is extracted correctly."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].generation == 3

    def test_no_warnings_on_clean_input(self) -> None:
        """Clean articles produce no warnings."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].warnings == []
        assert articles[1].warnings == []

    def test_content_includes_front_matter(self) -> None:
        """The content field includes the full markdown with front matter."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert articles[0].content.startswith("---\n")
        assert "title:" in articles[0].content
        assert "# Transformer Architecture" in articles[0].content

    def test_raw_body_excludes_front_matter(self) -> None:
        """The raw_body field contains only the markdown body."""
        articles = _parser().parse(_CLEAN_TWO_ARTICLES)
        assert "---" not in articles[0].raw_body
        assert "title:" not in articles[0].raw_body
        assert "# Transformer Architecture" in articles[0].raw_body


# ------------------------------------------------------------------
# ArticleParser — single article
# ------------------------------------------------------------------


class TestParserSingleArticle:
    """Parser handles a response with only one article."""

    def test_single_article(self) -> None:
        """A response with one complete article returns one ParsedArticle."""
        articles = _parser().parse(_CLEAN_ARTICLE_1)
        assert len(articles) == 1
        assert articles[0].file_path == "wiki/topics/transformers/overview.md"
        assert articles[0].title == "Transformer Architecture"


# ------------------------------------------------------------------
# ArticleParser — missing optional fields
# ------------------------------------------------------------------


class TestParserMissingOptionalFields:
    """Parser fills defaults for missing optional fields."""

    def test_missing_status_defaults_to_published(self) -> None:
        """Missing status field defaults to 'published'."""
        response = """\
<!-- grove:article wiki/test.md -->
---
title: "Test"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "A test."
last_compiled: "2026-04-03T14:00:00Z"
---

# Test

Body content here.
"""
        articles = _parser().parse(response)
        assert len(articles) == 1
        assert articles[0].status == "published"
        # No warning for missing optional field.
        assert not any("status" in w for w in articles[0].warnings)

    def test_missing_generation_defaults_to_one(self) -> None:
        """Missing generation field defaults to 1."""
        response = """\
<!-- grove:article wiki/test.md -->
---
title: "Test"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "A test."
last_compiled: "2026-04-03T14:00:00Z"
---

# Test

Body content here.
"""
        articles = _parser().parse(response)
        assert articles[0].generation == 1


# ------------------------------------------------------------------
# ArticleParser — missing required fields
# ------------------------------------------------------------------


class TestParserMissingRequiredFields:
    """Parser fills defaults and adds warnings for missing required fields."""

    def test_missing_title_uses_heading(self) -> None:
        """Missing title falls back to first H1 heading."""
        response = """\
<!-- grove:article wiki/test.md -->
---
compiled_from: [raw/a.md]
concepts: [testing]
summary: "A test."
last_compiled: "2026-04-03T14:00:00Z"
---

# Fallback Title

Body here.
"""
        articles = _parser().parse(response)
        assert articles[0].title == "Fallback Title"
        assert any("title" in w for w in articles[0].warnings)

    def test_missing_compiled_from(self) -> None:
        """Missing compiled_from defaults to empty list with warning."""
        response = """\
<!-- grove:article wiki/test.md -->
---
title: "Test"
concepts: [testing]
summary: "A test."
last_compiled: "2026-04-03T14:00:00Z"
---

# Test

Body.
"""
        articles = _parser().parse(response)
        assert articles[0].compiled_from == []
        assert any("compiled_from" in w for w in articles[0].warnings)

    def test_missing_concepts(self) -> None:
        """Missing concepts defaults to empty list with warning."""
        response = """\
<!-- grove:article wiki/test.md -->
---
title: "Test"
compiled_from: [raw/a.md]
summary: "A test."
last_compiled: "2026-04-03T14:00:00Z"
---

# Test

Body.
"""
        articles = _parser().parse(response)
        assert articles[0].concepts == []
        assert any("concepts" in w for w in articles[0].warnings)

    def test_missing_summary(self) -> None:
        """Missing summary defaults to empty string with warning."""
        response = """\
<!-- grove:article wiki/test.md -->
---
title: "Test"
compiled_from: [raw/a.md]
concepts: [testing]
last_compiled: "2026-04-03T14:00:00Z"
---

# Test

Body.
"""
        articles = _parser().parse(response)
        assert articles[0].summary == ""
        assert any("summary" in w for w in articles[0].warnings)

    def test_missing_last_compiled(self) -> None:
        """Missing last_compiled gets a generated timestamp with warning."""
        response = """\
<!-- grove:article wiki/test.md -->
---
title: "Test"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "A test."
---

# Test

Body.
"""
        articles = _parser().parse(response)
        assert articles[0].last_compiled != ""
        assert any("last_compiled" in w for w in articles[0].warnings)

    def test_all_required_fields_missing(self) -> None:
        """All required fields missing still produces an article with warnings."""
        response = """\
<!-- grove:article wiki/test.md -->
---
status: published
---

# Untitled Article

Some body.
"""
        articles = _parser().parse(response)
        assert len(articles) == 1
        assert len(articles[0].warnings) >= 5  # All 5 required fields flagged


# ------------------------------------------------------------------
# ArticleParser — malformed YAML
# ------------------------------------------------------------------


class TestParserMalformedYaml:
    """Parser handles malformed YAML gracefully."""

    def test_malformed_yaml_skips_article(self) -> None:
        """An article with unparseable YAML is skipped entirely."""
        response = """\
<!-- grove:article wiki/good.md -->
---
title: "Good Article"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Good."
last_compiled: "2026-04-03T14:00:00Z"
---

# Good Article

Content.

<!-- grove:article wiki/bad.md -->
---
: invalid: [yaml: {{
  broken: {{{{
---

# Bad Article

This should not appear.
"""
        articles = _parser().parse(response)
        # Only the good article should be parsed.
        assert len(articles) == 1
        assert articles[0].file_path == "wiki/good.md"

    def test_only_malformed_yaml_returns_empty(self) -> None:
        """A response with only one malformed article returns empty list."""
        response = """\
<!-- grove:article wiki/bad.md -->
---
: invalid: [yaml
---

# Bad

Content.
"""
        articles = _parser().parse(response)
        assert len(articles) == 0


# ------------------------------------------------------------------
# ArticleParser — truncated output
# ------------------------------------------------------------------


class TestParserTruncatedOutput:
    """Parser discards truncated last articles."""

    def test_truncated_last_article_discarded(self) -> None:
        """A truncated final article is discarded, previous articles kept."""
        response = """\
<!-- grove:article wiki/complete.md -->
---
title: "Complete Article"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Complete."
last_compiled: "2026-04-03T14:00:00Z"
---

# Complete Article

This article is fully formed.

<!-- grove:article wiki/truncated.md -->
---
title: "Truncated
"""
        articles = _parser().parse(response)
        assert len(articles) == 1
        assert articles[0].file_path == "wiki/complete.md"

    def test_truncated_warning_attached(self) -> None:
        """Truncation warning is attached to the last successful article."""
        response = """\
<!-- grove:article wiki/complete.md -->
---
title: "Complete"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Complete."
last_compiled: "2026-04-03T14:00:00Z"
---

# Complete

Full content here.

<!-- grove:article wiki/truncated.md -->
---
title: "Trun
"""
        articles = _parser().parse(response)
        assert len(articles) == 1
        assert any("truncated" in w.lower() for w in articles[0].warnings)

    def test_single_truncated_article_returns_empty(self) -> None:
        """A response with only a truncated article returns empty list."""
        response = """\
<!-- grove:article wiki/only.md -->
---
title: "Inc
"""
        articles = _parser().parse(response)
        assert len(articles) == 0


# ------------------------------------------------------------------
# ArticleParser — no markers (fallback)
# ------------------------------------------------------------------


class TestParserNoMarkersFallback:
    """Parser falls back to --- boundary detection when no markers exist."""

    def test_fallback_parses_yaml_boundaries(self) -> None:
        """Articles separated by --- boundaries are detected without markers."""
        response = """\
---
title: "First Article"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "First."
last_compiled: "2026-04-03T14:00:00Z"
---

# First Article

Content of first article.

---
title: "Second Article"
compiled_from: [raw/b.md]
concepts: [testing]
summary: "Second."
last_compiled: "2026-04-03T14:00:00Z"
---

# Second Article

Content of second article.
"""
        articles = _parser().parse(response)
        assert len(articles) == 2
        assert articles[0].title == "First Article"
        assert articles[1].title == "Second Article"

    def test_fallback_generates_file_paths(self) -> None:
        """File paths are generated from titles when markers are absent."""
        response = """\
---
title: "My Great Article"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "Great."
last_compiled: "2026-04-03T14:00:00Z"
---

# My Great Article

Body.
"""
        articles = _parser().parse(response)
        assert len(articles) == 1
        assert articles[0].file_path == "wiki/my-great-article.md"


# ------------------------------------------------------------------
# ArticleParser — empty response
# ------------------------------------------------------------------


class TestParserEmptyResponse:
    """Parser handles empty and whitespace-only responses."""

    def test_empty_string(self) -> None:
        """Empty string returns empty list."""
        assert _parser().parse("") == []

    def test_whitespace_only(self) -> None:
        """Whitespace-only string returns empty list."""
        assert _parser().parse("   \n\n  \t  ") == []

    def test_none_like_empty(self) -> None:
        """Falsy empty string returns empty list."""
        assert _parser().parse("") == []


# ------------------------------------------------------------------
# ArticleParser — never raises
# ------------------------------------------------------------------


class TestParserNeverRaises:
    """The parser must never raise an unhandled exception."""

    def test_garbage_binary_input(self) -> None:
        """Random byte-like strings do not crash the parser."""
        garbage = "\x00\x01\x02\xff\xfe garbage \x80\x81"
        result = _parser().parse(garbage)
        assert isinstance(result, list)

    def test_only_markers_no_content(self) -> None:
        """Markers with no article content do not crash."""
        response = "<!-- grove:article wiki/a.md --><!-- grove:article wiki/b.md -->"
        result = _parser().parse(response)
        assert isinstance(result, list)

    def test_deeply_nested_yaml(self) -> None:
        """Unusual YAML structures do not crash the parser."""
        response = """\
<!-- grove:article wiki/nested.md -->
---
title: "Nested"
compiled_from:
  - level1:
    - level2:
      - level3
concepts: 42
summary: [not, a, string]
last_compiled: 12345
---

# Nested

Body.
"""
        result = _parser().parse(response)
        assert isinstance(result, list)

    def test_massive_input(self) -> None:
        """Very large input does not crash the parser."""
        large = "<!-- grove:article wiki/big.md -->\n" + "x " * 100_000 + "\n"
        result = _parser().parse(large)
        assert isinstance(result, list)

    def test_unicode_input(self) -> None:
        """Unicode characters do not crash the parser."""
        response = """\
<!-- grove:article wiki/unicode.md -->
---
title: "Analyse des Donnees"
compiled_from: [raw/francais.md]
concepts: [analyse, donnees]
summary: "Analyse complete."
last_compiled: "2026-04-03T14:00:00Z"
---

# Analyse des Donnees

Contenu avec des caracteres speciaux: e, a, u, o, c.
"""
        result = _parser().parse(response)
        assert len(result) == 1
        assert result[0].title == "Analyse des Donnees"


# ------------------------------------------------------------------
# ArticleParser — content and raw_body preservation
# ------------------------------------------------------------------


class TestParserContentPreservation:
    """Content and raw_body fields are populated correctly."""

    def test_content_preserves_front_matter(self) -> None:
        """The content field includes the reconstructed front matter."""
        articles = _parser().parse(_CLEAN_ARTICLE_1)
        content = articles[0].content
        assert content.startswith("---\n")
        assert "title:" in content
        assert "# Transformer Architecture" in content

    def test_raw_body_strips_front_matter(self) -> None:
        """The raw_body field has no front matter delimiters or YAML."""
        articles = _parser().parse(_CLEAN_ARTICLE_1)
        raw_body = articles[0].raw_body
        assert not raw_body.startswith("---")
        assert "title:" not in raw_body
        assert "# Transformer Architecture" in raw_body
        assert "[source: attention-is-all-you-need.md]" in raw_body

    def test_no_front_matter_article(self) -> None:
        """An article without front matter has content == raw_body (stripped)."""
        response = """\
<!-- grove:article wiki/plain.md -->

# Plain Article

Just body content, no YAML.
"""
        articles = _parser().parse(response)
        assert len(articles) == 1
        # Both should contain the body.
        assert "# Plain Article" in articles[0].content
        assert "# Plain Article" in articles[0].raw_body


# ------------------------------------------------------------------
# ArticleParser — mixed valid and invalid articles
# ------------------------------------------------------------------


class TestParserMixedArticles:
    """Parser handles a mix of valid, invalid, and truncated articles."""

    def test_valid_then_invalid_then_valid(self) -> None:
        """Valid articles are returned; invalid one is skipped."""
        response = """\
<!-- grove:article wiki/first.md -->
---
title: "First"
compiled_from: [raw/a.md]
concepts: [testing]
summary: "First article."
last_compiled: "2026-04-03T14:00:00Z"
---

# First

Content.

<!-- grove:article wiki/bad.md -->
---
: [invalid yaml {{
---

# Bad

Skipped.

<!-- grove:article wiki/third.md -->
---
title: "Third"
compiled_from: [raw/c.md]
concepts: [testing]
summary: "Third article."
last_compiled: "2026-04-03T14:00:00Z"
---

# Third

Content.
"""
        articles = _parser().parse(response)
        assert len(articles) == 2
        assert articles[0].file_path == "wiki/first.md"
        assert articles[1].file_path == "wiki/third.md"


# ------------------------------------------------------------------
# ParsedArticle model
# ------------------------------------------------------------------


class TestParsedArticleModel:
    """The ParsedArticle Pydantic model validates correctly."""

    def test_creation_with_all_fields(self) -> None:
        """ParsedArticle can be created with all fields."""
        article = ParsedArticle(
            file_path="wiki/test.md",
            title="Test",
            compiled_from=["raw/a.md"],
            concepts=["testing"],
            summary="A test article.",
            last_compiled="2026-04-03T14:00:00Z",
            status="published",
            generation=2,
            content="---\ntitle: Test\n---\n# Test\n\nBody.",
            raw_body="# Test\n\nBody.",
            warnings=[],
        )
        assert article.file_path == "wiki/test.md"
        assert article.generation == 2

    def test_defaults(self) -> None:
        """ParsedArticle defaults are applied correctly."""
        article = ParsedArticle(
            file_path="wiki/test.md",
            title="Test",
            content="Content.",
            raw_body="Content.",
        )
        assert article.status == "published"
        assert article.generation == 1
        assert article.compiled_from == []
        assert article.concepts == []
        assert article.warnings == []
