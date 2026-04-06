"""ArticleParser -- splits LLM compilation output into individual articles.

Parses the ``<!-- grove:article wiki/path/to/file.md -->`` markers that
separate articles in the LLM response.  Extracts YAML front matter and
markdown body from each article, validates required fields, and returns
a list of ``ParsedArticle`` objects.

Recovery strategies ensure the parser never raises an unhandled exception:

- Missing markers: fall back to ``---`` YAML boundary detection.
- Missing required fields: fill defaults and attach a warning.
- Truncated output: discard the last incomplete article with a warning.
- Malformed YAML: skip that article with a warning.

See ARCH.md "Article Output Format Contract" for the definitive spec.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# The marker that separates articles in LLM output.
# Capture group extracts the file path.
_ARTICLE_MARKER_RE = re.compile(r"<!--\s*grove:article\s+(.+?)\s*-->")

# Required front matter fields.  If any are missing the parser fills
# defaults and attaches a warning.
_REQUIRED_FIELDS: list[str] = [
    "title",
    "compiled_from",
    "concepts",
    "summary",
    "last_compiled",
]


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class ParsedArticle(BaseModel):
    """A single article extracted from the LLM compilation response."""

    file_path: str = Field(
        description="Target path within the wiki, e.g. 'wiki/topics/foo.md'."
    )
    title: str = Field(description="Article title from front matter.")
    compiled_from: list[str] = Field(
        default_factory=list,
        description="Source paths this article was compiled from.",
    )
    concepts: list[str] = Field(
        default_factory=list,
        description="Key concepts covered by this article.",
    )
    summary: str = Field(
        default="",
        description="One-line summary from front matter.",
    )
    last_compiled: str = Field(
        default="",
        description="ISO-8601 timestamp of last compilation.",
    )
    status: str = Field(
        default="published",
        description="Article status (default: published).",
    )
    generation: int = Field(
        default=1,
        description="Compilation generation number.",
    )
    content: str = Field(
        description="Full markdown content INCLUDING front matter.",
    )
    raw_body: str = Field(
        description="Markdown body WITHOUT front matter.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Any issues found during parsing.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_yaml_front_matter(text: str) -> tuple[str | None, str]:
    """Split *text* into raw YAML string and body.

    Returns ``(yaml_string, body)``.  If no front matter is detected,
    returns ``(None, text)``.  The ``yaml_string`` does **not** include
    the ``---`` delimiters.
    """
    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return None, text

    # Find the closing --- delimiter.  We search from position 3 onwards
    # so the opening --- is not matched again.
    end_idx = stripped.find("\n---", 3)
    if end_idx == -1:
        # No closing delimiter -- treat the whole thing as body.
        return None, text

    yaml_str = stripped[3:end_idx].strip("\n")
    # Body starts after the closing --- plus the newline.
    body = stripped[end_idx + 4 :]
    return yaml_str, body


def _parse_yaml_block(yaml_str: str) -> dict[str, object] | None:
    """Safely parse a YAML string into a dict.

    Returns ``None`` if parsing fails -- callers should treat this as
    malformed YAML and skip or warn accordingly.
    """
    # Import here to keep the module-level namespace clean.  yaml is
    # already a project dependency (pyyaml).
    import yaml

    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return None

    if not isinstance(data, dict):
        return None

    return data


def _reconstruct_front_matter(yaml_str: str) -> str:
    """Wrap a raw YAML block back into markdown front matter delimiters."""
    return f"---\n{yaml_str}\n---"


def _now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _looks_truncated(text: str) -> bool:
    """Heuristic: does *text* look like a truncated article?

    An article is considered truncated if:
    - it is very short (fewer than 20 non-whitespace characters), or
    - it has an opening YAML ``---`` but no closing ``---``, or
    - it ends mid-sentence (last non-whitespace char is a letter or comma)
    """
    stripped = text.strip()
    if len(stripped) < 20:
        return True

    # Check for unclosed front matter.
    if stripped.startswith("---"):
        after_open = stripped[3:]
        if "\n---" not in after_open:
            return True

    # Check for mid-sentence ending.
    return bool(stripped and stripped[-1] in "abcdefghijklmnopqrstuvwxyz,")


def _extract_title_from_body(body: str) -> str:
    """Try to extract a title from the first markdown heading in *body*.

    Returns ``"Untitled"`` if no heading is found.
    """
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return "Untitled"


def _build_parsed_article(
    file_path: str,
    raw_content: str,
    yaml_str: str | None,
    meta: dict[str, object] | None,
    body: str,
) -> ParsedArticle:
    """Construct a ``ParsedArticle`` from parsed components.

    Fills defaults for missing required fields and records warnings.
    """
    warnings: list[str] = []

    if meta is None:
        meta = {}

    # --- Extract and validate required fields ---

    title = meta.get("title")
    if not title or not isinstance(title, str):
        fallback_title = _extract_title_from_body(body)
        warnings.append(
            f"Missing required field 'title'; defaulting to '{fallback_title}'."
        )
        title = fallback_title

    compiled_from = meta.get("compiled_from")
    if not isinstance(compiled_from, list):
        compiled_from = []
        warnings.append("Missing required field 'compiled_from'; defaulting to [].")

    concepts = meta.get("concepts")
    if not isinstance(concepts, list):
        concepts = []
        warnings.append("Missing required field 'concepts'; defaulting to [].")

    summary = meta.get("summary")
    if not summary or not isinstance(summary, str):
        summary = ""
        warnings.append("Missing required field 'summary'; defaulting to ''.")

    last_compiled = meta.get("last_compiled")
    if not last_compiled or not isinstance(last_compiled, str):
        now = _now_iso()
        warnings.append(
            f"Missing required field 'last_compiled'; defaulting to '{now}'."
        )
        last_compiled = now

    # --- Optional fields with defaults ---

    status = meta.get("status")
    if not status or not isinstance(status, str):
        status = "published"

    generation = meta.get("generation")
    if not isinstance(generation, int):
        generation = 1

    # --- Build content (full markdown with front matter) ---

    # The 'content' field includes front matter; 'raw_body' does not.
    if yaml_str is not None:
        content = _reconstruct_front_matter(yaml_str) + "\n" + body.lstrip("\n")
    else:
        content = raw_content.strip()

    raw_body = body.lstrip("\n")

    return ParsedArticle(
        file_path=file_path,
        title=title,
        compiled_from=[str(c) for c in compiled_from],
        concepts=[str(c) for c in concepts],
        summary=summary,
        last_compiled=str(last_compiled),
        status=status,
        generation=generation,
        content=content,
        raw_body=raw_body,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# ArticleParser
# ---------------------------------------------------------------------------


class ArticleParser:
    """Parse an LLM compilation response into individual articles.

    The parser is intentionally defensive -- it never raises an unhandled
    exception and returns whatever it can successfully parse.  Warnings
    are attached to individual articles and also logged.
    """

    def parse(self, llm_response: str) -> list[ParsedArticle]:
        """Parse the LLM's compilation output into individual articles.

        1. Split by ``<!-- grove:article ... -->`` markers.
        2. Extract file path from each marker.
        3. Parse YAML front matter from each article.
        4. Validate required fields, fill defaults for missing optional fields.
        5. Add warnings for any issues (missing fields, malformed YAML).

        Recovery strategies:
        - Missing markers: try to detect articles by ``---`` YAML boundaries.
        - Missing required fields: fill defaults, add warning.
        - Truncated output: discard the last incomplete article, add warning.
        - Malformed YAML: skip that article, add warning.

        NEVER raises an exception.  Returns whatever it can parse.
        """
        try:
            return self._parse_inner(llm_response)
        except Exception:
            logger.exception("Unexpected error during article parsing")
            return []

    def _parse_inner(self, llm_response: str) -> list[ParsedArticle]:
        """Core parsing logic, separated for clarity."""
        if not llm_response or not llm_response.strip():
            return []

        # Attempt marker-based splitting first.
        segments = self._split_by_markers(llm_response)

        if segments:
            return self._parse_segments(segments)

        # Fallback: no markers found -- attempt detection by YAML boundaries.
        logger.warning(
            "No <!-- grove:article --> markers found; "
            "attempting fallback YAML boundary detection."
        )
        return self._fallback_parse(llm_response)

    def _split_by_markers(self, text: str) -> list[tuple[str, str]]:
        """Split *text* into ``(file_path, article_content)`` pairs.

        Returns an empty list if no markers are found.
        """
        matches = list(_ARTICLE_MARKER_RE.finditer(text))
        if not matches:
            return []

        segments: list[tuple[str, str]] = []
        for i, match in enumerate(matches):
            file_path = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end]
            segments.append((file_path, content))

        return segments

    def _parse_segments(self, segments: list[tuple[str, str]]) -> list[ParsedArticle]:
        """Parse a list of ``(file_path, raw_content)`` segments into articles.

        Discards the last segment if it looks truncated.
        """
        articles: list[ParsedArticle] = []

        for i, (file_path, raw_content) in enumerate(segments):
            is_last = i == len(segments) - 1

            # Truncation check on the last segment.
            if is_last and _looks_truncated(raw_content):
                logger.warning(
                    "Discarding truncated last article for '%s'.",
                    file_path,
                )
                # Attach the warning to previously parsed articles if any exist.
                if articles:
                    articles[-1].warnings.append(
                        f"Subsequent article '{file_path}' was discarded as truncated."
                    )
                continue

            article = self._parse_single_article(file_path, raw_content)
            if article is not None:
                articles.append(article)

        return articles

    def _parse_single_article(
        self, file_path: str, raw_content: str
    ) -> ParsedArticle | None:
        """Parse a single article's content into a ``ParsedArticle``.

        Returns ``None`` if the article cannot be parsed (e.g. malformed
        YAML with no recoverable content).
        """
        yaml_str, body = _split_yaml_front_matter(raw_content)

        if yaml_str is not None:
            meta = _parse_yaml_block(yaml_str)
            if meta is None:
                # Malformed YAML -- skip the article.
                logger.warning(
                    "Malformed YAML in article '%s'; skipping.",
                    file_path,
                )
                return None
        else:
            meta = None

        return _build_parsed_article(file_path, raw_content, yaml_str, meta, body)

    def _fallback_parse(self, text: str) -> list[ParsedArticle]:
        """Attempt to parse articles without markers, using ``---`` boundaries.

        This is a best-effort fallback for responses where the LLM did not
        include the expected ``<!-- grove:article -->`` markers.  It looks
        for ``---`` YAML front matter blocks and treats each as a separate
        article.

        Each article must have a ``title`` field in its front matter so the
        parser can generate a file path.
        """
        articles: list[ParsedArticle] = []

        # Find all positions where a YAML front matter block opens.
        # A front matter opener is ``---\n`` at the start of the string or
        # preceded by a blank line (``\n\n---\n`` or ``\n---\n`` at a
        # logical article boundary).  We use finditer to locate every
        # ``---\n`` and then check that a matching close exists.
        opener_re = re.compile(r"(?:^|\n)---\n", re.MULTILINE)
        openers = list(opener_re.finditer(text))

        # Pair each opener with its closing ``\n---\n`` to form complete
        # front matter blocks, then grab the body that follows until the
        # next opener (or end of string).
        block_starts: list[int] = []
        for m in openers:
            # The actual ``---`` may be preceded by a captured ``\n``.
            pos = m.start()
            if text[pos] == "\n":
                pos += 1
            # Check there is a closing delimiter after this opener.
            close_search_start = pos + 4  # skip past ``---\n``
            close_idx = text.find("\n---", close_search_start)
            if close_idx != -1:
                block_starts.append(pos)

        # Deduplicate: only keep openers that are not themselves the
        # closing ``---`` of a previous block.
        filtered_starts: list[int] = []
        skip_until = -1
        for pos in block_starts:
            if pos <= skip_until:
                continue
            filtered_starts.append(pos)
            # Find the closing --- for this block to know what to skip.
            close_idx = text.find("\n---", pos + 4)
            if close_idx != -1:
                skip_until = close_idx + 4

        # Extract each article: from its opener to just before the next opener.
        for i, start in enumerate(filtered_starts):
            end = filtered_starts[i + 1] if i + 1 < len(filtered_starts) else len(text)
            part = text[start:end].strip()
            if not part:
                continue

            yaml_str, body = _split_yaml_front_matter(part)
            if yaml_str is None:
                continue

            meta = _parse_yaml_block(yaml_str)
            if meta is None:
                continue

            # Generate a file path from the title if available.
            title = meta.get("title", "")
            if isinstance(title, str) and title.strip():
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                file_path = f"wiki/{slug}.md"
            else:
                file_path = f"wiki/untitled-{len(articles) + 1}.md"

            article = _build_parsed_article(file_path, part, yaml_str, meta, body)
            articles.append(article)

        if not articles:
            logger.warning("Fallback parsing found no articles in the LLM response.")

        return articles
