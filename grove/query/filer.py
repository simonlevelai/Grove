"""QueryFiler -- saves query results and promotes them to wiki/.

The filer handles two operations:
1. **Auto-save:** every ``grove query`` result is saved to
   ``queries/<timestamp>-<slug>.md`` automatically.
2. **File to wiki:** ``grove file`` promotes a query result to
   ``wiki/queries/<filename>`` with ``origin: query`` and
   ``pinned: true`` in front matter, then commits via AutoCommitter.

See ARCH.md ``grove/query/`` table for the authoritative spec.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from grove.query.formatter import AnswerFormatter
from grove.query.models import QueryResult

logger = logging.getLogger(__name__)

# Maximum number of words from the question to include in the slug.
_SLUG_WORD_LIMIT = 5


class QueryFiler:
    """Persist and promote query results within a grove.

    Parameters
    ----------
    grove_root:
        Path to the grove project root (contains ``queries/``,
        ``wiki/``, and ``.git/``).
    """

    def __init__(self, grove_root: Path) -> None:
        self._grove_root = grove_root
        self._queries_dir = grove_root / "queries"
        self._wiki_queries_dir = grove_root / "wiki" / "queries"
        self._formatter = AnswerFormatter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_query(self, result: QueryResult) -> Path:
        """Save a query result to ``queries/<timestamp>-<slug>.md``.

        The slug is derived from the first 5 words of the question,
        lowercased and hyphen-separated.  Returns the path to the
        saved file.
        """
        self._queries_dir.mkdir(parents=True, exist_ok=True)

        # Build filename: timestamp prefix + slug.
        ts_prefix = _timestamp_prefix(result.timestamp)
        slug = _slugify_question(result.question)
        filename = f"{ts_prefix}-{slug}.md"

        file_path = self._queries_dir / filename
        content = self._formatter.format_markdown(result)
        file_path.write_text(content, encoding="utf-8")

        logger.info("Saved query to %s", file_path)
        return file_path

    def file_to_wiki(self, query_path: Path) -> Path:
        """Promote a query result file to ``wiki/queries/``.

        Reads the query file, adds ``origin: query`` and
        ``pinned: true`` to its YAML front matter, copies it to
        ``wiki/queries/<filename>``, and commits via AutoCommitter.

        Returns the path to the filed wiki article.
        """
        if not query_path.is_file():
            raise FileNotFoundError(f"Query file not found: {query_path}")

        content = query_path.read_text(encoding="utf-8")
        updated_content = _add_wiki_front_matter(content)

        # Ensure the target directory exists.
        self._wiki_queries_dir.mkdir(parents=True, exist_ok=True)

        wiki_path = self._wiki_queries_dir / query_path.name
        wiki_path.write_text(updated_content, encoding="utf-8")

        logger.info("Filed query to %s", wiki_path)

        # Commit via AutoCommitter.
        self._commit_filed_query(wiki_path)

        return wiki_path

    def get_latest_query(self) -> Path | None:
        """Return the most recent file in ``queries/`` by name.

        Files are named ``<timestamp>-<slug>.md``, so lexicographic
        sorting by name gives chronological order (newest last).
        Returns ``None`` if the queries directory is empty or absent.
        """
        if not self._queries_dir.is_dir():
            return None

        query_files = sorted(self._queries_dir.glob("*.md"))
        if not query_files:
            return None

        return query_files[-1]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _commit_filed_query(self, wiki_path: Path) -> None:
        """Stage and commit the filed query via AutoCommitter."""
        try:
            from grove.git.auto_commit import AutoCommitter

            committer = AutoCommitter(self._grove_root)
            rel_path = str(wiki_path.relative_to(self._grove_root))
            committer.commit_file_query(rel_path)
        except Exception as exc:  # noqa: BLE001
            # Filing should not fail the user operation -- log and continue.
            logger.warning(
                "Could not auto-commit filed query %s: %s",
                wiki_path,
                exc,
            )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _timestamp_prefix(timestamp: str) -> str:
    """Extract a filesystem-safe date-time prefix from an ISO timestamp.

    ``2026-04-03T14:22:00Z`` becomes ``2026-04-03T142200``.
    Falls back to the raw timestamp (cleaned) if parsing fails.
    """
    # Strip the timezone suffix and colons for filesystem safety.
    prefix = timestamp.replace("Z", "").replace(":", "")
    # Remove any trailing fractional seconds.
    prefix = re.sub(r"\.\d+$", "", prefix)
    return prefix


def _slugify_question(question: str) -> str:
    """Convert the first 5 words of *question* into a filesystem-safe slug.

    ``What is the transformer architecture?`` becomes
    ``what-is-the-transformer-architecture``.
    """
    # Take the first N words.
    words = question.split()[:_SLUG_WORD_LIMIT]
    joined = "-".join(words).lower()

    # Strip non-alphanumeric characters (except hyphens).
    slug = re.sub(r"[^\w-]", "", joined)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")

    return slug or "untitled"


def _add_wiki_front_matter(content: str) -> str:
    """Add ``origin: query`` and ``pinned: true`` to YAML front matter.

    If the file already has front matter (delimited by ``---``), the
    fields are added/updated.  If it does not, front matter is created.
    """
    if content.startswith("---\n"):
        # Find the closing --- delimiter.
        end_idx = content.index("---", 4)
        fm_raw = content[4:end_idx]
        body = content[end_idx + 3 :]

        fm_dict = yaml.safe_load(fm_raw) or {}
        fm_dict["origin"] = "query"
        fm_dict["pinned"] = True

        fm_str = yaml.dump(
            fm_dict,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).rstrip("\n")

        return f"---\n{fm_str}\n---{body}"

    # No existing front matter -- create one.
    fm_dict = {"origin": "query", "pinned": True}
    fm_str = yaml.dump(
        fm_dict,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip("\n")

    return f"---\n{fm_str}\n---\n\n{content}"
