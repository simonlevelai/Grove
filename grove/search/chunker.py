"""Chunker — splits wiki articles into overlapping token-estimated chunks.

Each article is split into chunks of approximately ``chunk_size`` tokens
with ``overlap`` tokens of overlap between consecutive chunks.  Token
count is estimated as ``word_count * 1.3`` (a reasonable heuristic for
English text and typical BPE tokenisers).

The chunks are used to build the FTS5 search index so that search results
can surface the most relevant *portion* of a long article rather than
returning the entire text.
"""

from __future__ import annotations

from pydantic import BaseModel


class Chunk(BaseModel):
    """A single text chunk extracted from a wiki article."""

    article_path: str  # relative path, e.g. "wiki/topics/foo.md"
    content: str  # chunk text
    position: int  # chunk index within article (0, 1, 2, ...)
    token_count: int  # estimated tokens


def _estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in *text* using a word-based heuristic."""
    word_count = len(text.split())
    return int(word_count * 1.3)


def _estimate_word_count_for_tokens(token_count: int) -> int:
    """Convert a token target back to an approximate word count."""
    return max(1, int(token_count / 1.3))


class Chunker:
    """Splits article text into overlapping chunks by estimated token count.

    Parameters
    ----------
    chunk_size:
        Target chunk size in estimated tokens.
    overlap:
        Number of overlapping tokens between consecutive chunks.
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 64) -> None:
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk_article(self, article_path: str, content: str) -> list[Chunk]:
        """Split *content* into overlapping chunks.

        Returns at least one chunk even for very short articles.
        """
        words = content.split()
        if not words:
            return []

        chunk_words = _estimate_word_count_for_tokens(self._chunk_size)
        overlap_words = _estimate_word_count_for_tokens(self._overlap)
        step = max(1, chunk_words - overlap_words)

        chunks: list[Chunk] = []
        position = 0
        start = 0

        while start < len(words):
            end = start + chunk_words
            chunk_text = " ".join(words[start:end])
            token_count = _estimate_tokens(chunk_text)

            chunks.append(
                Chunk(
                    article_path=article_path,
                    content=chunk_text,
                    position=position,
                    token_count=token_count,
                )
            )

            position += 1
            start += step

            # If we have already captured the last words, stop.
            if end >= len(words):
                break

        return chunks
