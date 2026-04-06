"""HTMLConverter — extract main content from HTML and convert to markdown.

Uses ``readability-lxml`` to strip navigation, ads, and boilerplate,
then ``markdownify`` to produce clean markdown.  Both libraries are in
the ``[full]`` optional dependency group.
"""

from __future__ import annotations

import re
from pathlib import Path

from grove.ingest.exceptions import ConversionError
from grove.ingest.models import ConversionResult


def _has_headings(text: str) -> bool:
    """Return True if the text contains at least one markdown heading."""
    return bool(re.search(r"^#{1,6}\s", text, re.MULTILINE))


def _word_count(text: str) -> int:
    """Count words by splitting on whitespace."""
    return len(text.split())


def _ensure_dependencies() -> None:
    """Raise a clear error if readability-lxml or markdownify are missing."""
    missing: list[str] = []
    try:
        import readability  # noqa: F401
    except ImportError:
        missing.append("readability-lxml")
    try:
        import markdownify  # noqa: F401
    except ImportError:
        missing.append("markdownify")

    if missing:
        raise ConversionError(
            f"HTML conversion requires {', '.join(missing)}. "
            "Install with: pip install grove-kb[full]"
        )


class HTMLConverter:
    """Convert HTML files (or raw HTML strings) to markdown.

    Uses readability-lxml to extract the main article content, then
    markdownify to produce markdown.  Handles both file paths and
    raw HTML strings via the *from_string* parameter.
    """

    def convert(
        self,
        path_or_html: str | Path,
        *,
        from_string: bool = False,
        source_path: str | None = None,
    ) -> ConversionResult:
        """Convert HTML to markdown.

        When *from_string* is True, *path_or_html* is treated as raw
        HTML content.  Otherwise it is read from disk as a file path.
        *source_path* overrides the path recorded in the result (useful
        when converting a downloaded URL).
        """
        _ensure_dependencies()

        from markdownify import markdownify
        from readability import Document

        if from_string:
            raw_html = str(path_or_html)
            resolved_path = source_path or "<string>"
        else:
            file_path = Path(path_or_html)
            raw_html = file_path.read_text(encoding="utf-8")
            resolved_path = source_path or str(file_path)

        # Extract the main content, stripping nav/ads/boilerplate
        try:
            doc = Document(raw_html)
            title = doc.title() or ""
            clean_html = doc.summary()
        except Exception as exc:
            raise ConversionError(
                f"readability-lxml failed to extract content: {exc}"
            ) from exc

        # Convert cleaned HTML to markdown
        try:
            content: str = markdownify(clean_html, heading_style="ATX", strip=["img"])
        except Exception as exc:
            raise ConversionError(f"markdownify failed to convert HTML: {exc}") from exc

        # Tidy up excessive blank lines
        content = re.sub(r"\n{3,}", "\n\n", content.strip())

        metadata: dict[str, object] = {}
        if title:
            metadata["title"] = title

        return ConversionResult(
            content=content,
            source_path=resolved_path,
            mime_type="text/html",
            converter_used="readability",
            word_count=_word_count(content),
            has_headings=_has_headings(content),
            metadata=metadata,
        )
