"""Converter — dispatches file conversion by MIME type.

Detects the MIME type using Python's ``mimetypes`` module (with file
extension as fallback) and routes to the appropriate converter:

- ``application/pdf`` -> PDFConverter
- ``text/html``       -> HTMLConverter
- ``text/plain``, ``text/markdown`` -> TextConverter
- anything else       -> raises UnsupportedFormatError
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from grove.ingest.exceptions import UnsupportedFormatError
from grove.ingest.html import HTMLConverter
from grove.ingest.models import ConversionResult
from grove.ingest.pdf import PDFConverter
from grove.ingest.text import TextConverter

# Ensure markdown is registered — not all platforms include it by default
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/markdown", ".markdown")

# MIME types that the text converter handles
_TEXT_TYPES = frozenset({"text/plain", "text/markdown"})


def _detect_mime_type(path: Path) -> str:
    """Detect the MIME type of a file, falling back to extension heuristics."""
    mime_type, _ = mimetypes.guess_type(str(path))

    if mime_type is not None:
        return mime_type

    # Extension-based fallback for types mimetypes may not know about
    suffix = path.suffix.lower()
    extension_map: dict[str, str] = {
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
    }

    return extension_map.get(suffix, "application/octet-stream")


class Converter:
    """Dispatch file conversion to the correct converter by MIME type.

    Acts as the single entry point for the ingest pipeline's conversion
    step.  Callers pass a file path and receive a ConversionResult
    regardless of which underlying converter handled the work.
    """

    def __init__(self) -> None:
        self._pdf = PDFConverter()
        self._html = HTMLConverter()
        self._text = TextConverter()

    def convert(self, path: str | Path) -> ConversionResult:
        """Convert a file at *path* to markdown.

        Raises UnsupportedFormatError if the MIME type has no registered
        converter.
        """
        file_path = Path(path)
        mime_type = _detect_mime_type(file_path)

        if mime_type == "application/pdf":
            return self._pdf.convert(file_path)

        if mime_type == "text/html":
            return self._html.convert(file_path)

        if mime_type in _TEXT_TYPES:
            return self._text.convert(file_path, mime_type=mime_type)

        raise UnsupportedFormatError(
            f"Unsupported file format: {mime_type} ({file_path.name}). "
            "Grove supports PDF, HTML, Markdown, and plain-text files."
        )

    def detect_mime_type(self, path: str | Path) -> str:
        """Expose MIME detection for callers that need it before conversion."""
        return _detect_mime_type(Path(path))
