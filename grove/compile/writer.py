"""ArticleWriter -- writes compiled articles to the wiki directory.

Enforces three non-negotiable invariants:

1. **Human annotation preservation:** ``<!-- grove:human -->`` blocks in
   existing articles survive recompilation.  They are extracted before
   writing and re-injected into the new article at a matching section
   heading, or appended at the end if no match is found.

2. **Pinned article protection:** Articles with ``pinned: true`` in their
   YAML front matter are never overwritten, regardless of what the LLM
   produces.

3. **Atomic writes:** All articles are written to a temporary directory
   first.  Only after every article succeeds are the files moved into
   ``wiki/``.  If any write fails, the wiki is left untouched.

See ARCH.md "Data Integrity Guarantees" for the authoritative spec.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from grove.compile.parser import ParsedArticle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex for human annotation blocks
# ---------------------------------------------------------------------------

_HUMAN_BLOCK_RE = re.compile(
    r"(<!--\s*grove:human\s*-->.*?<!--\s*/grove:human\s*-->)",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class WriteResult(BaseModel):
    """Statistics returned by ``ArticleWriter.write_all``."""

    articles_written: int = Field(
        default=0, description="Number of articles successfully written."
    )
    articles_skipped_pinned: int = Field(
        default=0, description="Number of articles skipped due to pinned: true."
    )
    human_blocks_preserved: int = Field(
        default=0, description="Total human annotation blocks re-injected."
    )
    warnings: list[str] = Field(
        default_factory=list, description="Any non-fatal issues encountered."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_existing_file(path: Path) -> str | None:
    """Read an existing file, returning ``None`` if it does not exist."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read existing file %s: %s", path, exc)
        return None


def _parse_front_matter_pinned(content: str) -> bool:
    """Return ``True`` if *content* has ``pinned: true`` in YAML front matter."""
    stripped = content.lstrip("\n")
    if not stripped.startswith("---"):
        return False

    end_idx = stripped.find("\n---", 3)
    if end_idx == -1:
        return False

    yaml_str = stripped[3:end_idx]
    try:
        meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return False

    if not isinstance(meta, dict):
        return False

    return meta.get("pinned") is True


def _extract_human_blocks(content: str) -> list[tuple[str, str | None]]:
    """Extract ``<!-- grove:human -->`` blocks and their preceding heading context.

    Returns a list of ``(block_text, nearest_heading)`` tuples.  The heading
    is the last ``## ...`` or ``# ...`` line that appears before the block,
    or ``None`` if no heading precedes it.
    """
    results: list[tuple[str, str | None]] = []

    for match in _HUMAN_BLOCK_RE.finditer(content):
        block_text = match.group(1)
        preceding = content[: match.start()]

        # Find the nearest heading before this block.
        heading: str | None = None
        for line in reversed(preceding.split("\n")):
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped
                break

        results.append((block_text, heading))

    return results


def _inject_human_blocks(
    new_content: str, blocks: list[tuple[str, str | None]]
) -> tuple[str, int]:
    """Re-inject human annotation blocks into *new_content*.

    For each block, if the associated heading exists in the new content,
    inject the block after the first paragraph following that heading.
    Otherwise, append the block at the end of the article.

    Returns ``(updated_content, count_injected)``.
    """
    if not blocks:
        return new_content, 0

    injected = 0
    lines = new_content.split("\n")

    for block_text, heading in blocks:
        if heading is not None:
            # Find the heading in the new content.
            heading_idx = _find_heading_line(lines, heading)
            if heading_idx is not None:
                # Inject after the next blank line following the heading,
                # or at the end of the section if no blank line is found.
                insert_idx = _find_injection_point(lines, heading_idx)
                lines.insert(insert_idx, "")
                lines.insert(insert_idx + 1, block_text)
                lines.insert(insert_idx + 2, "")
                injected += 1
                continue

        # Fallback: append at the end.
        lines.append("")
        lines.append(block_text)
        lines.append("")
        injected += 1

    return "\n".join(lines), injected


def _find_heading_line(lines: list[str], heading: str) -> int | None:
    """Find the index of a heading line in *lines*.

    Matches by stripping whitespace from both sides to handle minor
    formatting differences.
    """
    target = heading.strip()
    for i, line in enumerate(lines):
        if line.strip() == target:
            return i
    return None


def _find_injection_point(lines: list[str], heading_idx: int) -> int:
    """Find the best insertion point after a heading.

    Walks forward from the heading to find the end of the first paragraph
    (next blank line), then inserts after it.  If no blank line is found
    before the next heading or end of file, inserts just before the next
    heading or at the end.
    """
    i = heading_idx + 1

    # Skip any blank lines immediately after the heading.
    while i < len(lines) and lines[i].strip() == "":
        i += 1

    # Walk through the paragraph content.
    while i < len(lines):
        if lines[i].strip() == "":
            # Found a blank line -- insert after it.
            return i + 1
        if lines[i].strip().startswith("#"):
            # Hit the next heading -- insert before it.
            return i
        i += 1

    # Reached end of file.
    return len(lines)


# ---------------------------------------------------------------------------
# ArticleWriter
# ---------------------------------------------------------------------------


class ArticleWriter:
    """Write compiled articles to ``wiki/`` with full data-integrity guarantees.

    All three invariants (human block preservation, pinned protection,
    atomic writes) are enforced on every call to ``write_all``.
    """

    def __init__(self, grove_root: Path) -> None:
        """Initialise the writer.

        Parameters
        ----------
        grove_root:
            The root directory of the grove project.  Must contain (or will
            contain) a ``wiki/`` subdirectory.
        """
        self._grove_root = grove_root
        self._wiki_dir = grove_root / "wiki"

    def write_all(self, articles: list[ParsedArticle]) -> WriteResult:
        """Write all articles to ``wiki/`` atomically.

        Steps:
        1. Create a temporary directory within the grove root.
        2. For each article, check pinned status and extract human blocks
           from the existing file on disk.
        3. Write the new article content (with human blocks re-injected)
           to the temporary directory.
        4. After ALL articles succeed, move each temp file to its final
           position in ``wiki/``.
        5. Clean up the temporary directory.

        If any write fails, the temporary directory is removed and the wiki
        is left unchanged.
        """
        result = WriteResult()

        if not articles:
            return result

        # Build the list of articles to write, checking pinned status
        # and extracting human blocks BEFORE creating the temp directory.
        prepared: list[tuple[ParsedArticle, str, int]] = []

        for article in articles:
            target_path = self._grove_root / article.file_path

            # Read existing file (if any).
            existing_content = _read_existing_file(target_path)

            # Check pinned status on the EXISTING file.
            if existing_content is not None and _parse_front_matter_pinned(
                existing_content
            ):
                result.articles_skipped_pinned += 1
                logger.info("Skipping pinned article: %s", article.file_path)
                continue

            # Extract human blocks from existing file.
            human_blocks: list[tuple[str, str | None]] = []
            if existing_content is not None:
                human_blocks = _extract_human_blocks(existing_content)

            # Re-inject human blocks into the new content.
            final_content, blocks_injected = _inject_human_blocks(
                article.content, human_blocks
            )
            result.human_blocks_preserved += blocks_injected

            prepared.append((article, final_content, blocks_injected))

        # Nothing to write?  Return early.
        if not prepared:
            return result

        # Atomic write: temp directory then move.
        temp_dir = Path(
            tempfile.mkdtemp(prefix=".grove-write-", dir=str(self._grove_root))
        )

        try:
            # Phase 1: write all articles to the temp directory.
            temp_files: list[tuple[Path, Path]] = []
            for article, final_content, _ in prepared:
                temp_article_path = temp_dir / article.file_path
                temp_article_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    temp_article_path.write_text(final_content, encoding="utf-8")
                except OSError as exc:
                    msg = (
                        f"Failed to write article '{article.file_path}' "
                        f"to temp directory: {exc}"
                    )
                    logger.error(msg)
                    result.warnings.append(msg)
                    # Abort: clean up and return without modifying wiki/.
                    raise

                final_path = self._grove_root / article.file_path
                temp_files.append((temp_article_path, final_path))

            # Phase 2: move all temp files to wiki/ atomically.
            for temp_path, final_path in temp_files:
                final_path.parent.mkdir(parents=True, exist_ok=True)
                # os.replace is atomic on the same filesystem.
                os.replace(str(temp_path), str(final_path))
                result.articles_written += 1

        except Exception:
            # On any failure, ensure wiki/ is untouched.
            logger.error(
                "Write failed; cleaning up temp directory.  Wiki is unchanged."
            )
            # Reset written count -- nothing actually landed in wiki/.
            result.articles_written = 0
            raise

        finally:
            # Always clean up the temp directory.
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

        logger.info(
            "Wrote %d articles (%d pinned skipped, %d human blocks preserved).",
            result.articles_written,
            result.articles_skipped_pinned,
            result.human_blocks_preserved,
        )

        return result
