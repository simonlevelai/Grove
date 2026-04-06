"""TextConverter — pass-through for plain text and markdown files.

No optional dependencies required.  Reads the file, normalises line
endings to ``\\n``, and returns a ConversionResult.
"""

from __future__ import annotations

import re
from pathlib import Path

from grove.ingest.models import ConversionResult


def _has_headings(text: str) -> bool:
    """Return True if the text contains at least one markdown heading."""
    return bool(re.search(r"^#{1,6}\s", text, re.MULTILINE))


def _word_count(text: str) -> int:
    """Count words by splitting on whitespace."""
    return len(text.split())


class TextConverter:
    """Convert plain-text and markdown files to a ConversionResult.

    For both ``.txt`` and ``.md`` the content is passed through as-is
    after normalising line endings (``\\r\\n`` and ``\\r`` become ``\\n``).
    """

    def convert(
        self, path: str | Path, mime_type: str = "text/plain"
    ) -> ConversionResult:
        """Read *path*, normalise line endings, and return a ConversionResult."""
        file_path = Path(path)
        raw = file_path.read_text(encoding="utf-8")

        # Normalise line endings: \r\n -> \n, then lone \r -> \n
        content = raw.replace("\r\n", "\n").replace("\r", "\n")

        return ConversionResult(
            content=content,
            source_path=str(file_path),
            mime_type=mime_type,
            converter_used="text",
            word_count=_word_count(content),
            has_headings=_has_headings(content),
        )
