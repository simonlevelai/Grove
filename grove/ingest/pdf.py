"""PDFConverter — extract markdown from PDF files.

Primary: ``pymupdf4llm`` (MIT licence).
Fallback: ``pdfminer.six`` (MIT licence) when pymupdf4llm is unavailable
or fails on a particular file.

Both libraries are in the ``[full]`` optional dependency group.  If neither
is installed the converter raises a clear installation hint.
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


def _convert_with_pymupdf4llm(path: Path) -> tuple[str, dict[str, object]]:
    """Attempt conversion via pymupdf4llm.  Returns (markdown, metadata)."""
    try:
        import pymupdf4llm
    except ImportError as err:
        raise ImportError(
            "pymupdf4llm is not installed. Install with: pip install grove-kb[full]"
        ) from err

    md_text: str = pymupdf4llm.to_markdown(str(path))
    metadata: dict[str, object] = {}

    # Extract basic PDF metadata via pymupdf if available
    try:
        import pymupdf

        doc = pymupdf.open(str(path))
        metadata["page_count"] = doc.page_count
        pdf_meta = doc.metadata
        if pdf_meta and pdf_meta.get("title"):
            metadata["title"] = pdf_meta["title"]
        doc.close()
    except Exception:
        pass

    return md_text, metadata


def _convert_with_pdfminer(path: Path) -> tuple[str, dict[str, object]]:
    """Attempt conversion via pdfminer.six.  Returns (text, metadata)."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError as err:
        raise ImportError(
            "pdfminer.six is not installed. Install with: pip install grove-kb[full]"
        ) from err

    text: str = extract_text(str(path))
    metadata: dict[str, object] = {
        "fallback_reason": "pymupdf4llm unavailable or failed"
    }
    return text, metadata


class PDFConverter:
    """Convert PDF files to markdown via pymupdf4llm with pdfminer fallback.

    Tries pymupdf4llm first for structured markdown output.  If that
    fails (import error or conversion error), falls back to pdfminer.six
    for plain-text extraction.  If both fail, raises ConversionError.
    """

    def convert(self, path: str | Path) -> ConversionResult:
        """Convert a PDF at *path* to markdown."""
        file_path = Path(path)
        converter_used = "pymupdf4llm"
        metadata: dict[str, object] = {}

        # --- Try pymupdf4llm first ---
        try:
            content, metadata = _convert_with_pymupdf4llm(file_path)
        except ImportError:
            # pymupdf4llm not installed — try pdfminer
            try:
                content, metadata = _convert_with_pdfminer(file_path)
                converter_used = "pdfminer"
            except ImportError as err:
                raise ConversionError(
                    "PDF conversion requires grove-kb[full]. "
                    "Install with: pip install grove-kb[full]"
                ) from err
        except Exception as exc:
            # pymupdf4llm installed but failed on this file — try pdfminer
            try:
                content, fallback_meta = _convert_with_pdfminer(file_path)
                fallback_meta["pymupdf4llm_error"] = str(exc)
                metadata = fallback_meta
                converter_used = "pdfminer"
            except ImportError:
                raise ConversionError(
                    f"pymupdf4llm failed ({exc}) and pdfminer.six is not installed. "
                    "Install with: pip install grove-kb[full]"
                ) from exc
            except Exception as pdfminer_exc:
                raise ConversionError(
                    f"Both PDF converters failed. "
                    f"pymupdf4llm: {exc}; pdfminer: {pdfminer_exc}"
                ) from pdfminer_exc

        return ConversionResult(
            content=content,
            source_path=str(file_path),
            mime_type="application/pdf",
            converter_used=converter_used,
            word_count=_word_count(content),
            has_headings=_has_headings(content),
            metadata=metadata,
        )
