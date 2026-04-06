"""VecIndex -- sqlite-vec semantic search index for wiki articles.

Generates embeddings via Ollama ``nomic-embed-text`` and stores them in a
sqlite-vec virtual table alongside the FTS5 index in ``.grove/search.db``.
Queries compute cosine similarity against the stored embeddings.

If Ollama is unreachable or the model is not pulled, callers receive a
clear ``OllamaUnavailableError`` so the CLI can fall back gracefully.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path

from grove.search.chunker import Chunker
from grove.search.fts import (
    SearchResult,
    _strip_front_matter,
)

logger = logging.getLogger(__name__)

# nomic-embed-text produces 768-dimensional embeddings.
EMBEDDING_DIM = 768
DEFAULT_MODEL = "nomic-embed-text"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OllamaUnavailableError(Exception):
    """Raised when Ollama cannot be reached or the embedding model is absent."""


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _serialize_f32(vector: Sequence[float]) -> bytes:
    """Pack a float vector into little-endian bytes for sqlite-vec."""
    return struct.pack(f"<{len(vector)}f", *vector)


def _embed_texts(texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
    """Generate embeddings for *texts* via Ollama.

    Raises ``OllamaUnavailableError`` if Ollama is not running or the
    model is not available.
    """
    try:
        import ollama
    except ImportError as exc:
        raise OllamaUnavailableError(
            "The 'ollama' package is not installed. "
            "Install it with: pip install grove-kb[full]"
        ) from exc

    try:
        response = ollama.embed(model=model, input=texts)
    except Exception as exc:
        msg = str(exc).lower()
        if "connection" in msg or "refused" in msg or "connect" in msg:
            raise OllamaUnavailableError(
                "Cannot connect to Ollama. Is it running? Start it with: ollama serve"
            ) from exc
        if "not found" in msg or "does not exist" in msg:
            raise OllamaUnavailableError(
                f"Embedding model '{model}' is not available. "
                f"Pull it with: ollama pull {model}"
            ) from exc
        raise OllamaUnavailableError(f"Ollama embedding failed: {exc}") from exc

    return response.embeddings


# ---------------------------------------------------------------------------
# VecIndex
# ---------------------------------------------------------------------------


class VecIndex:
    """sqlite-vec semantic search index for the grove wiki.

    Stores chunk-level embeddings alongside the FTS5 index in the same
    SQLite database.  Queries return article-level results ranked by
    cosine similarity.

    Parameters
    ----------
    db_path:
        Path to the SQLite database (typically ``.grove/search.db``).
    model:
        Ollama embedding model name.
    """

    def __init__(
        self,
        db_path: Path,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._db_path = db_path
        self._model = model

    def build(self, wiki_root: Path) -> int:
        """(Re)build the vector index from all wiki ``.md`` files.

        Drops existing vec tables and rebuilds from scratch.
        Returns the number of chunks indexed.

        Raises ``OllamaUnavailableError`` if embeddings cannot be generated.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self._db_path))
        conn.enable_load_extension(True)
        try:
            import sqlite_vec

            sqlite_vec.load(conn)
        except ImportError as exc:
            conn.close()
            raise OllamaUnavailableError(
                "The 'sqlite-vec' package is not installed. "
                "Install it with: pip install grove-kb[full]"
            ) from exc

        try:
            return self._build_index(conn, wiki_root)
        finally:
            conn.close()

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search the vector index by cosine similarity.

        Embeds *query* via Ollama and finds the nearest chunks.
        Returns article-level results deduplicated by path.

        Raises ``OllamaUnavailableError`` if embeddings cannot be generated.
        """
        if not self._db_path.exists():
            return []

        # Generate query embedding.
        embeddings = _embed_texts([query], model=self._model)
        if not embeddings or not embeddings[0]:
            return []

        query_vec = _serialize_f32(embeddings[0])

        conn = sqlite3.connect(str(self._db_path))
        conn.enable_load_extension(True)
        try:
            import sqlite_vec

            sqlite_vec.load(conn)
            return self._run_search(conn, query_vec, limit)
        except ImportError:
            return []
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_index(self, conn: sqlite3.Connection, wiki_root: Path) -> int:
        """Build the vec index inside an open connection."""
        cur = conn.cursor()

        # Drop existing vec tables.
        cur.execute("DROP TABLE IF EXISTS vec_chunks")

        # Create the vec0 virtual table with cosine distance.
        cur.execute(
            f"CREATE VIRTUAL TABLE vec_chunks USING vec0("
            f"  embedding float[{EMBEDDING_DIM}] distance_metric=cosine"
            f")"
        )

        # Metadata table mapping vec rowids to article/chunk info.
        cur.execute("DROP TABLE IF EXISTS vec_chunk_meta")
        cur.execute("""
            CREATE TABLE vec_chunk_meta (
                rowid INTEGER PRIMARY KEY,
                article_path TEXT NOT NULL,
                chunk_position INTEGER NOT NULL,
                content TEXT NOT NULL
            )
        """)

        if not wiki_root.exists():
            conn.commit()
            return 0

        chunker = Chunker()
        grove_root = wiki_root.parent

        # Collect all chunks first, then batch-embed.
        all_chunks: list[tuple[str, int, str]] = []  # (article_path, position, content)

        for md_file in sorted(wiki_root.rglob("*.md")):
            try:
                file_content = md_file.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not read %s: %s", md_file, exc)
                continue

            rel_path = str(md_file.relative_to(grove_root))
            body = _strip_front_matter(file_content)
            chunks = chunker.chunk_article(rel_path, body)

            for chunk in chunks:
                all_chunks.append((chunk.article_path, chunk.position, chunk.content))

        if not all_chunks:
            conn.commit()
            return 0

        # Batch-embed all chunk texts (Ollama handles batching internally).
        texts = [content for _, _, content in all_chunks]
        embeddings = _embed_texts(texts, model=self._model)

        if len(embeddings) != len(all_chunks):
            logger.error(
                "Embedding count mismatch: expected %d, got %d",
                len(all_chunks),
                len(embeddings),
            )
            conn.commit()
            return 0

        # Insert into vec table and metadata table.
        for i, (article_path, position, content) in enumerate(all_chunks):
            vec_bytes = _serialize_f32(embeddings[i])
            rowid = i + 1  # 1-based rowids
            cur.execute(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                (rowid, vec_bytes),
            )
            cur.execute(
                "INSERT INTO vec_chunk_meta"
                "(rowid, article_path, chunk_position, content) "
                "VALUES (?, ?, ?, ?)",
                (rowid, article_path, position, content),
            )

        conn.commit()
        logger.info("Built vec index: %d chunks from %s", len(all_chunks), wiki_root)
        return len(all_chunks)

    def _run_search(
        self,
        conn: sqlite3.Connection,
        query_vec: bytes,
        limit: int,
    ) -> list[SearchResult]:
        """Execute cosine similarity search and deduplicate by article."""
        cur = conn.cursor()

        # Retrieve more chunks than the limit so we can deduplicate.
        fetch_limit = limit * 5

        try:
            rows = cur.execute(
                """
                SELECT
                    v.rowid,
                    v.distance,
                    m.article_path,
                    m.chunk_position,
                    m.content
                FROM vec_chunks v
                JOIN vec_chunk_meta m ON m.rowid = v.rowid
                WHERE v.embedding MATCH ?
                AND k = ?
                ORDER BY v.distance ASC
                """,
                (query_vec, fetch_limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("Vec search failed: %s", exc)
            return []

        # Load article metadata from the articles table (created by FTSIndex).
        article_meta: dict[str, tuple[str, str]] = {}
        try:
            meta_rows = cur.execute(
                "SELECT path, title, summary FROM articles"
            ).fetchall()
            for path, title, summary in meta_rows:
                article_meta[path] = (title or path, summary or "")
        except sqlite3.OperationalError:
            pass  # articles table might not exist yet

        # Deduplicate by article path, keeping best (lowest distance) chunk.
        seen: dict[str, SearchResult] = {}
        for _rowid, distance, article_path, _pos, content in rows:
            if article_path not in seen:
                title, summary = article_meta.get(article_path, (article_path, ""))
                # Convert cosine distance to a similarity score.
                # cosine_distance in [0, 2]; similarity = 1 - distance.
                similarity = 1.0 - distance
                seen[article_path] = SearchResult(
                    article_path=article_path,
                    title=title,
                    summary=summary,
                    best_chunk=content,
                    score=similarity,
                )

        # Sort by similarity descending (higher = better match).
        results = sorted(seen.values(), key=lambda r: r.score, reverse=True)
        return results[:limit]
