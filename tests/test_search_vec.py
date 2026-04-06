"""Tests for sqlite-vec semantic search and hybrid mode.

Covers:
- VecIndex.build indexes wiki articles using mocked Ollama embeddings
- VecIndex.search returns cosine similarity results
- VecIndex deduplicates results by article
- VecIndex handles missing database gracefully
- VecIndex raises OllamaUnavailableError when Ollama unavailable
- HybridSearch combines BM25 and cosine scores
- HybridSearch falls back to keyword-only when Ollama unavailable
- HybridSearch normalises and merges scores correctly
- CLI --mode keyword|semantic|hybrid flag works
- CLI --mode semantic shows error when Ollama unavailable
- CLI --mode invalid prints error
- Serialisation round-trip for float vectors
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grove.search.fts import FTSIndex, SearchResult
from grove.search.hybrid import (
    HybridSearch,
    _merge_results,
    _normalise_bm25_scores,
    _normalise_vec_scores,
)
from grove.search.vec import (
    EMBEDDING_DIM,
    OllamaUnavailableError,
    VecIndex,
    _embed_texts,
    _serialize_f32,
)

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


def _fake_embedding(seed: float = 0.1) -> list[float]:
    """Generate a deterministic fake embedding vector."""
    return [seed + i * 0.001 for i in range(EMBEDDING_DIM)]


def _fake_embed_texts(
    texts: list[str], model: str = "nomic-embed-text"
) -> list[list[float]]:
    """Mock replacement for _embed_texts that returns deterministic embeddings."""
    embeddings = []
    for _i, text in enumerate(texts):
        # Use hash of text content to create slightly different embeddings
        seed = (hash(text) % 1000) / 1000.0
        embeddings.append(_fake_embedding(seed))
    return embeddings


# ---------------------------------------------------------------------------
# _serialize_f32 tests
# ---------------------------------------------------------------------------


class TestSerializeF32:
    """Tests for float vector serialisation."""

    def test_round_trip(self) -> None:
        """Serialised vector can be unpacked back to original values."""
        original = [1.0, 2.0, 3.0, 4.5]
        packed = _serialize_f32(original)
        unpacked = struct.unpack(f"<{len(original)}f", packed)
        assert list(unpacked) == pytest.approx(original)

    def test_correct_byte_length(self) -> None:
        """Each float should take 4 bytes."""
        vec = [0.0] * 768
        packed = _serialize_f32(vec)
        assert len(packed) == 768 * 4

    def test_empty_vector(self) -> None:
        """Empty vector produces empty bytes."""
        assert _serialize_f32([]) == b""


# ---------------------------------------------------------------------------
# _embed_texts tests (with mocked Ollama)
# ---------------------------------------------------------------------------


class TestEmbedTexts:
    """Tests for the Ollama embedding wrapper."""

    def test_missing_ollama_package_raises(self) -> None:
        """Raises OllamaUnavailableError when the ollama package is absent."""
        with (
            patch.dict("sys.modules", {"ollama": None}),
            pytest.raises(OllamaUnavailableError, match="not installed"),
        ):
            _embed_texts(["hello"])

    def test_connection_error_raises(self) -> None:
        """Raises OllamaUnavailableError on connection failure."""
        mock_ollama = MagicMock()
        mock_ollama.embed.side_effect = ConnectionError("Connection refused")
        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            pytest.raises(OllamaUnavailableError, match="Cannot connect"),
        ):
            _embed_texts(["hello"])

    def test_model_not_found_raises(self) -> None:
        """Raises OllamaUnavailableError when model is not pulled."""
        mock_ollama = MagicMock()
        mock_ollama.embed.side_effect = Exception("model not found")
        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            pytest.raises(OllamaUnavailableError, match="not available"),
        ):
            _embed_texts(["hello"])

    def test_successful_embedding(self) -> None:
        """Returns embeddings on success."""
        expected = [[0.1] * 768]
        mock_ollama = MagicMock()
        mock_ollama.embed.return_value = MagicMock(embeddings=expected)
        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            result = _embed_texts(["hello"])
        assert result == expected


# ---------------------------------------------------------------------------
# VecIndex tests
# ---------------------------------------------------------------------------


class TestVecIndex:
    """Tests for the VecIndex build and search operations."""

    @patch("grove.search.vec._embed_texts", side_effect=_fake_embed_texts)
    def test_build_creates_index(self, _mock_embed: MagicMock, tmp_path: Path) -> None:
        """Build creates the vec tables and indexes chunks."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is the first letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        # Build FTS first (needed for article metadata).
        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        vec = VecIndex(db_path)
        count = vec.build(grove_root / "wiki")
        assert count >= 1

    @patch("grove.search.vec._embed_texts", side_effect=_fake_embed_texts)
    def test_build_empty_wiki(self, _mock_embed: MagicMock, tmp_path: Path) -> None:
        """Build on an empty wiki returns 0 chunks."""
        grove_root = _build_wiki(tmp_path, {})
        db_path = grove_root / ".grove" / "search.db"

        vec = VecIndex(db_path)
        count = vec.build(grove_root / "wiki")
        assert count == 0

    @patch("grove.search.vec._embed_texts", side_effect=_fake_embed_texts)
    def test_search_returns_results(
        self, _mock_embed: MagicMock, tmp_path: Path
    ) -> None:
        """Search returns results after building an index."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is the first letter of the alphabet."
            ),
            "wiki/topics/beta.md": _make_article(
                "Beta", "About beta.", "Beta is the second letter of the alphabet."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        vec = VecIndex(db_path)
        vec.build(grove_root / "wiki")

        results = vec.search("alphabet", limit=5)
        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)

    @patch("grove.search.vec._embed_texts", side_effect=_fake_embed_texts)
    def test_search_deduplicates_by_article(
        self, _mock_embed: MagicMock, tmp_path: Path
    ) -> None:
        """Search returns at most one result per article."""
        # Create an article with enough content to produce multiple chunks.
        long_body = " ".join(["This is a test sentence about algorithms."] * 200)
        articles = {
            "wiki/topics/long.md": _make_article("Long", "A long article.", long_body),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        vec = VecIndex(db_path)
        vec.build(grove_root / "wiki")

        results = vec.search("algorithms", limit=10)
        paths = [r.article_path for r in results]
        assert len(paths) == len(
            set(paths)
        ), "Results should be deduplicated by article"

    def test_search_missing_database(self, tmp_path: Path) -> None:
        """Search returns empty list when the database does not exist."""
        db_path = tmp_path / "nonexistent.db"
        vec = VecIndex(db_path)
        results = vec.search("anything")
        assert results == []

    def test_build_raises_when_ollama_unavailable(self, tmp_path: Path) -> None:
        """Build raises OllamaUnavailableError when embeddings fail."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is the first letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        vec = VecIndex(db_path)
        with (
            patch(
                "grove.search.vec._embed_texts",
                side_effect=OllamaUnavailableError("Ollama not running"),
            ),
            pytest.raises(OllamaUnavailableError),
        ):
            vec.build(grove_root / "wiki")


# ---------------------------------------------------------------------------
# Score normalisation tests
# ---------------------------------------------------------------------------


class TestScoreNormalisation:
    """Tests for BM25 and cosine similarity score normalisation."""

    def test_normalise_bm25_single_result(self) -> None:
        """Single result normalises to 1.0."""
        results = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="chunk",
                score=-5.0,
            ),
        ]
        normed = _normalise_bm25_scores(results)
        assert normed["wiki/a.md"] == pytest.approx(1.0)

    def test_normalise_bm25_multiple_results(self) -> None:
        """Best BM25 score (most negative) normalises to 1.0."""
        results = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="",
                score=-10.0,
            ),
            SearchResult(
                article_path="wiki/b.md",
                title="B",
                summary="",
                best_chunk="",
                score=-2.0,
            ),
        ]
        normed = _normalise_bm25_scores(results)
        assert normed["wiki/a.md"] == pytest.approx(1.0)
        assert normed["wiki/b.md"] == pytest.approx(0.0)

    def test_normalise_bm25_empty(self) -> None:
        """Empty input returns empty dict."""
        assert _normalise_bm25_scores([]) == {}

    def test_normalise_vec_single_result(self) -> None:
        """Single vec result normalises to 1.0."""
        results = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="",
                score=0.8,
            ),
        ]
        normed = _normalise_vec_scores(results)
        assert normed["wiki/a.md"] == pytest.approx(1.0)

    def test_normalise_vec_multiple_results(self) -> None:
        """Highest vec score normalises to 1.0, lowest to 0.0."""
        results = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="",
                score=0.9,
            ),
            SearchResult(
                article_path="wiki/b.md",
                title="B",
                summary="",
                best_chunk="",
                score=0.3,
            ),
        ]
        normed = _normalise_vec_scores(results)
        assert normed["wiki/a.md"] == pytest.approx(1.0)
        assert normed["wiki/b.md"] == pytest.approx(0.0)

    def test_normalise_vec_empty(self) -> None:
        """Empty input returns empty dict."""
        assert _normalise_vec_scores([]) == {}


# ---------------------------------------------------------------------------
# _merge_results tests
# ---------------------------------------------------------------------------


class TestMergeResults:
    """Tests for the hybrid score merging logic."""

    def test_merge_with_equal_alpha(self) -> None:
        """Alpha=0.5 gives equal weight to both scores."""
        keyword = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="k",
                score=-10.0,
            ),
            SearchResult(
                article_path="wiki/b.md",
                title="B",
                summary="",
                best_chunk="k",
                score=-2.0,
            ),
        ]
        vec = [
            SearchResult(
                article_path="wiki/b.md",
                title="B",
                summary="",
                best_chunk="v",
                score=0.9,
            ),
            SearchResult(
                article_path="wiki/c.md",
                title="C",
                summary="",
                best_chunk="v",
                score=0.3,
            ),
        ]
        merged = _merge_results(keyword, vec, alpha=0.5)
        assert len(merged) == 3  # a, b, c
        # All paths present.
        paths = {r.article_path for r in merged}
        assert paths == {"wiki/a.md", "wiki/b.md", "wiki/c.md"}

    def test_merge_sorted_descending(self) -> None:
        """Merged results are sorted by score descending."""
        keyword = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="",
                score=-10.0,
            ),
        ]
        vec = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="",
                score=0.9,
            ),
        ]
        merged = _merge_results(keyword, vec, alpha=0.5)
        scores = [r.score for r in merged]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# HybridSearch tests
# ---------------------------------------------------------------------------


class TestHybridSearch:
    """Tests for the HybridSearch orchestrator."""

    @patch("grove.search.vec._embed_texts", side_effect=_fake_embed_texts)
    def test_hybrid_search_returns_results(
        self, _mock_embed: MagicMock, tmp_path: Path
    ) -> None:
        """Hybrid search returns combined results."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha",
                "About alpha.",
                "Alpha is the first letter of the alphabet used in mathematics.",
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        vec = VecIndex(db_path)
        vec.build(grove_root / "wiki")

        hybrid = HybridSearch(db_path)
        results, warnings = hybrid.search("alphabet")
        assert len(results) >= 1
        assert warnings == []

    def test_hybrid_falls_back_to_keyword(self, tmp_path: Path) -> None:
        """Hybrid mode falls back to keyword-only when Ollama unavailable."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha",
                "About alpha.",
                "Alpha is the first letter of the alphabet.",
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        # Build FTS index only (no vec index).
        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        hybrid = HybridSearch(db_path)
        with patch(
            "grove.search.vec._embed_texts",
            side_effect=OllamaUnavailableError("Ollama not running"),
        ):
            results, warnings = hybrid.search("alphabet")
        assert len(results) >= 1
        assert len(warnings) >= 1
        assert "falling back" in warnings[0].lower()

    def test_keyword_only_mode(self, tmp_path: Path) -> None:
        """search_keyword returns BM25-only results."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is a Greek letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        hybrid = HybridSearch(db_path)
        results = hybrid.search_keyword("Greek")
        assert len(results) >= 1

    @patch("grove.search.vec._embed_texts", side_effect=_fake_embed_texts)
    def test_semantic_only_mode(self, _mock_embed: MagicMock, tmp_path: Path) -> None:
        """search_semantic returns cosine-only results."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is the first letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        vec = VecIndex(db_path)
        vec.build(grove_root / "wiki")

        hybrid = HybridSearch(db_path)
        results = hybrid.search_semantic("alphabet")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# CLI --mode flag tests
# ---------------------------------------------------------------------------


class TestSearchCLIMode:
    """Tests for the grove search --mode CLI flag."""

    def test_invalid_mode_prints_error(self, tmp_path: Path) -> None:
        """Invalid mode value prints error and exits with code 1."""
        from grove.cli import app

        with patch("grove.cli._find_grove_root", return_value=tmp_path):
            result = runner.invoke(app, ["search", "query", "--mode", "invalid"])
        assert result.exit_code != 0
        assert "Invalid mode" in result.output

    def test_keyword_mode_works(self, tmp_path: Path) -> None:
        """--mode keyword uses FTSIndex."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is a Greek letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        from grove.cli import app

        with patch("grove.cli._find_grove_root", return_value=grove_root):
            result = runner.invoke(app, ["search", "Greek", "--mode", "keyword"])
        assert result.exit_code == 0
        assert "Alpha" in result.output

    def test_semantic_mode_error_when_ollama_unavailable(self, tmp_path: Path) -> None:
        """--mode semantic shows error when Ollama is unreachable."""
        grove_root = _build_wiki(tmp_path, {})
        db_path = grove_root / ".grove" / "search.db"

        # Create a minimal search.db so the file exists check passes.
        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        from grove.cli import app

        with (
            patch("grove.cli._find_grove_root", return_value=grove_root),
            patch(
                "grove.search.vec.VecIndex.search",
                side_effect=OllamaUnavailableError("Ollama not running"),
            ),
        ):
            result = runner.invoke(app, ["search", "test", "--mode", "semantic"])
        assert result.exit_code != 0
        assert "unavailable" in result.output.lower()

    def test_hybrid_mode_with_fallback(self, tmp_path: Path) -> None:
        """--mode hybrid falls back gracefully when Ollama unavailable."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is the first letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        db_path = grove_root / ".grove" / "search.db"

        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        from grove.cli import app

        with (
            patch("grove.cli._find_grove_root", return_value=grove_root),
            patch(
                "grove.search.vec._embed_texts",
                side_effect=OllamaUnavailableError("Ollama not running"),
            ),
        ):
            result = runner.invoke(app, ["search", "Alpha", "--mode", "hybrid"])
        # Should succeed with fallback to keyword.
        assert result.exit_code == 0
        assert "Alpha" in result.output
