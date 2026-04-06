"""HybridSearch -- combines BM25 keyword scores with cosine similarity.

Merges results from :class:`~grove.search.fts.FTSIndex` (BM25) and
:class:`~grove.search.vec.VecIndex` (cosine similarity) using a weighted
linear combination:  ``score = alpha * vec_score + (1 - alpha) * bm25_score``

Both component scores are normalised to [0, 1] before combination.
BM25 scores from FTS5 are negative (more negative = better) so they are
min-max normalised then inverted.  Cosine similarity scores from VecIndex
are already in [-1, 1] and are shifted to [0, 1].

If Ollama is unreachable, hybrid mode falls back to keyword-only search
with a warning.
"""

from __future__ import annotations

import logging
from pathlib import Path

from grove.search.fts import FTSIndex, SearchResult
from grove.search.vec import OllamaUnavailableError, VecIndex

logger = logging.getLogger(__name__)


class HybridSearch:
    """Hybrid BM25 + cosine similarity search.

    Parameters
    ----------
    db_path:
        Path to the shared SQLite database (``.grove/search.db``).
    alpha:
        Weight for the vector (semantic) score.  ``1 - alpha`` is applied
        to the keyword score.  Default is 0.5 (equal weighting).
    model:
        Ollama embedding model name.
    """

    def __init__(
        self,
        db_path: Path,
        alpha: float = 0.5,
        model: str = "nomic-embed-text",
    ) -> None:
        self._db_path = db_path
        self._alpha = alpha
        self._fts = FTSIndex(db_path)
        self._vec = VecIndex(db_path, model=model)

    def search(
        self,
        query: str,
        limit: int = 10,
    ) -> tuple[list[SearchResult], list[str]]:
        """Run hybrid search combining keyword and semantic results.

        Returns a tuple of ``(results, warnings)``.  Warnings are
        human-readable strings for the CLI to display (e.g. when falling
        back to keyword-only mode).
        """
        warnings: list[str] = []

        # Keyword results (always available).
        keyword_results = self._fts.search(query, limit=limit * 3)

        # Semantic results (may fail if Ollama is down).
        vec_results: list[SearchResult] = []
        try:
            vec_results = self._vec.search(query, limit=limit * 3)
        except OllamaUnavailableError as exc:
            warnings.append(
                f"Semantic search unavailable, falling back to keyword only: {exc}"
            )

        if not vec_results:
            # Pure keyword fallback.
            return keyword_results[:limit], warnings

        if not keyword_results:
            # Pure semantic (unusual, but possible with empty FTS index).
            return vec_results[:limit], warnings

        # Normalise and merge.
        merged = _merge_results(keyword_results, vec_results, self._alpha)
        return merged[:limit], warnings

    def search_keyword(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Run keyword-only (BM25) search."""
        return self._fts.search(query, limit=limit)

    def search_semantic(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Run semantic-only (cosine similarity) search.

        Raises ``OllamaUnavailableError`` if Ollama is unreachable.
        """
        return self._vec.search(query, limit=limit)


# ---------------------------------------------------------------------------
# Score normalisation and merging
# ---------------------------------------------------------------------------


def _normalise_bm25_scores(results: list[SearchResult]) -> dict[str, float]:
    """Normalise BM25 scores to [0, 1] where 1 is the best match.

    FTS5 bm25() returns negative values; more negative = better match.
    We invert and min-max normalise.
    """
    if not results:
        return {}

    scores = [r.score for r in results]
    # bm25 scores are negative; the most negative is the best match.
    min_score = min(scores)
    max_score = max(scores)

    score_range = max_score - min_score
    normalised: dict[str, float] = {}

    for r in results:
        if score_range == 0:
            # All scores identical -- assign equal weight.
            normalised[r.article_path] = 1.0
        else:
            # Invert: best (most negative) becomes 1.0, worst becomes 0.0.
            normalised[r.article_path] = (max_score - r.score) / score_range

    return normalised


def _normalise_vec_scores(results: list[SearchResult]) -> dict[str, float]:
    """Normalise cosine similarity scores to [0, 1] where 1 is best.

    VecIndex already returns similarity (1 - distance) which is in [-1, 1].
    We shift to [0, 1] via min-max normalisation across the result set.
    """
    if not results:
        return {}

    scores = [r.score for r in results]
    min_score = min(scores)
    max_score = max(scores)

    score_range = max_score - min_score
    normalised: dict[str, float] = {}

    for r in results:
        if score_range == 0:
            normalised[r.article_path] = 1.0
        else:
            normalised[r.article_path] = (r.score - min_score) / score_range

    return normalised


def _merge_results(
    keyword_results: list[SearchResult],
    vec_results: list[SearchResult],
    alpha: float,
) -> list[SearchResult]:
    """Merge keyword and vector results using weighted linear combination.

    ``score = alpha * vec_normalised + (1 - alpha) * bm25_normalised``
    """
    bm25_scores = _normalise_bm25_scores(keyword_results)
    vec_scores = _normalise_vec_scores(vec_results)

    # Collect all article paths from both result sets.
    all_paths = set(bm25_scores.keys()) | set(vec_scores.keys())

    # Build a lookup from article_path to the best SearchResult metadata.
    result_lookup: dict[str, SearchResult] = {}
    for r in keyword_results:
        if r.article_path not in result_lookup:
            result_lookup[r.article_path] = r
    for r in vec_results:
        if r.article_path not in result_lookup:
            result_lookup[r.article_path] = r

    # Compute combined scores.
    merged: list[SearchResult] = []
    for path in all_paths:
        bm25_norm = bm25_scores.get(path, 0.0)
        vec_norm = vec_scores.get(path, 0.0)
        combined = alpha * vec_norm + (1 - alpha) * bm25_norm

        base = result_lookup[path]
        merged.append(
            SearchResult(
                article_path=base.article_path,
                title=base.title,
                summary=base.summary,
                best_chunk=base.best_chunk,
                score=combined,
            )
        )

    # Sort descending by combined score (higher = better).
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged
