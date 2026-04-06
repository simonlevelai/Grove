"""FTSIndex — SQLite FTS5 full-text search index for wiki articles.

Builds a search index from all Markdown files under ``wiki/``, chunked
via :class:`grove.search.chunker.Chunker`.  Queries use the FTS5 ``bm25``
ranking function and return article-level results (deduplicated by path,
keeping only the best-scoring chunk per article as context).

The database lives at ``.grove/search.db`` and is rebuilt from scratch
after each successful compile.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from grove.search.chunker import Chunker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML front matter helpers
# ---------------------------------------------------------------------------

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_front_matter_field(content: str, field: str) -> str:
    """Extract a single scalar YAML front matter field by simple regex.

    Avoids pulling in a full YAML parser for two fields.  Returns an
    empty string if the field is not found.
    """
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return ""
    fm_block = match.group(1)
    # Match `field: "value"` or `field: value`
    pattern = re.compile(rf'^{re.escape(field)}:\s*"?([^"\n]*)"?\s*$', re.MULTILINE)
    field_match = pattern.search(fm_block)
    return field_match.group(1).strip() if field_match else ""


def _strip_front_matter(content: str) -> str:
    """Remove YAML front matter from article content for indexing."""
    match = _FRONT_MATTER_RE.match(content)
    if match:
        return content[match.end() :]
    return content


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """A single search result at article level."""

    article_path: str
    title: str  # from YAML front matter
    summary: str  # from YAML front matter
    best_chunk: str  # the chunk that matched best
    score: float  # BM25 rank score (lower magnitude = better match)


# ---------------------------------------------------------------------------
# FTSIndex
# ---------------------------------------------------------------------------


class FTSIndex:
    """SQLite FTS5 search index for the grove wiki.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file (typically ``.grove/search.db``).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def build(self, wiki_root: Path) -> int:
        """(Re)build the FTS5 index from all wiki ``.md`` files.

        Drops existing tables and rebuilds from scratch.
        Returns the number of chunks indexed.
        """
        # Ensure parent directory exists.
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self._db_path))
        try:
            return self._build_index(conn, wiki_root)
        finally:
            conn.close()

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search the FTS5 index with a keyword query.

        Returns article-level results deduplicated by path.  When
        multiple chunks from the same article match, only the
        best-scoring chunk is returned as context.
        """
        if not self._db_path.exists():
            return []

        conn = sqlite3.connect(str(self._db_path))
        try:
            return self._run_search(conn, query, limit)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_index(self, conn: sqlite3.Connection, wiki_root: Path) -> int:
        """Build the index inside an open connection."""
        cur = conn.cursor()

        # Drop existing tables and rebuild.
        cur.execute("DROP TABLE IF EXISTS chunks")
        cur.execute("DROP TABLE IF EXISTS articles")

        # Create the FTS5 virtual table.
        cur.execute("""
            CREATE VIRTUAL TABLE chunks USING fts5(
                article_path,
                chunk_position,
                content,
                tokenize='porter unicode61'
            )
            """)

        # Metadata table for article info.
        cur.execute("""
            CREATE TABLE articles (
                path TEXT PRIMARY KEY,
                title TEXT,
                summary TEXT
            )
            """)

        chunker = Chunker()
        total_chunks = 0

        # Walk all .md files under the wiki root.
        if not wiki_root.exists():
            conn.commit()
            return 0

        grove_root = wiki_root.parent  # wiki_root is e.g. /path/to/grove/wiki
        for md_file in sorted(wiki_root.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not read %s: %s", md_file, exc)
                continue

            rel_path = str(md_file.relative_to(grove_root))

            # Extract metadata from front matter.
            title = _extract_front_matter_field(content, "title")
            summary = _extract_front_matter_field(content, "summary")

            if not title:
                # Fall back to filename stem.
                title = md_file.stem.replace("-", " ").title()

            # Register the article.
            cur.execute(
                "INSERT OR REPLACE INTO articles"
                " (path, title, summary) VALUES (?, ?, ?)",
                (rel_path, title, summary),
            )

            # Chunk the body text (front matter stripped).
            body = _strip_front_matter(content)
            chunks = chunker.chunk_article(rel_path, body)

            for chunk in chunks:
                cur.execute(
                    "INSERT INTO chunks (article_path, chunk_position, content) "
                    "VALUES (?, ?, ?)",
                    (chunk.article_path, str(chunk.position), chunk.content),
                )
                total_chunks += 1

        conn.commit()
        logger.info("Built FTS5 index: %d chunks from %s", total_chunks, wiki_root)
        return total_chunks

    def _run_search(
        self, conn: sqlite3.Connection, query: str, limit: int
    ) -> list[SearchResult]:
        """Execute the FTS5 search and deduplicate by article."""
        cur = conn.cursor()

        # Escape special FTS5 characters in the user query to prevent
        # syntax errors.  We wrap each token in double quotes so that
        # special chars (colons, hyphens, etc.) are treated as literals.
        safe_tokens = []
        for token in query.split():
            # Double-quote each token; escape any embedded quotes.
            escaped = token.replace('"', '""')
            safe_tokens.append(f'"{escaped}"')
        safe_query = " ".join(safe_tokens)

        if not safe_query.strip():
            return []

        try:
            # FTS5 match with BM25 ranking.
            # bm25() returns negative scores; more negative = better match.
            # We only match on the content column (index 2, 0-based).
            # Weight: article_path=0, chunk_position=0, content=1.0
            rows = cur.execute(
                """
                SELECT
                    c.article_path,
                    c.chunk_position,
                    c.content,
                    bm25(chunks, 0.0, 0.0, 1.0) AS score,
                    a.title,
                    a.summary
                FROM chunks c
                JOIN articles a ON a.path = c.article_path
                WHERE chunks MATCH ?
                ORDER BY score ASC
                """,
                (safe_query,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 query failed: %s", exc)
            return []

        # Deduplicate by article — keep the best-scoring chunk per article.
        seen: dict[str, SearchResult] = {}
        for article_path, _chunk_pos, content, score, title, summary in rows:
            if article_path not in seen:
                seen[article_path] = SearchResult(
                    article_path=article_path,
                    title=title or article_path,
                    summary=summary or "",
                    best_chunk=content,
                    score=score,
                )

        # Sort by score (ascending — more negative is better for bm25).
        results = sorted(seen.values(), key=lambda r: r.score)
        return results[:limit]
