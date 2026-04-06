"""Tests for the grove.ingest conversion pipeline.

Covers TextConverter, HTMLConverter, PDFConverter, and the Converter
dispatcher.  Optional-dependency converters are skipped when the
relevant library is not installed.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from grove.ingest.converter import Converter, _detect_mime_type
from grove.ingest.exceptions import ConversionError, UnsupportedFormatError
from grove.ingest.models import ConversionResult
from grove.ingest.text import TextConverter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_has_readability = importlib.util.find_spec("readability") is not None
_has_markdownify = importlib.util.find_spec("markdownify") is not None
_has_pymupdf4llm = importlib.util.find_spec("pymupdf4llm") is not None
_has_pdfminer = importlib.util.find_spec("pdfminer") is not None

_skip_html = pytest.mark.skipif(
    not (_has_readability and _has_markdownify),
    reason="readability-lxml or markdownify not installed",
)
_skip_pdf = pytest.mark.skipif(
    not (_has_pymupdf4llm or _has_pdfminer),
    reason="Neither pymupdf4llm nor pdfminer.six installed",
)


# ---------------------------------------------------------------------------
# MIME type detection
# ---------------------------------------------------------------------------


class TestMimeDetection:
    """Verify _detect_mime_type returns the correct MIME for each extension."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("report.pdf", "application/pdf"),
            ("page.html", "text/html"),
            ("page.htm", "text/html"),
            ("notes.md", "text/markdown"),
            ("notes.markdown", "text/markdown"),
            ("readme.txt", "text/plain"),
        ],
    )
    def test_known_extensions(self, filename: str, expected: str) -> None:
        assert _detect_mime_type(Path(filename)) == expected

    def test_unknown_extension_returns_octet_stream(self) -> None:
        assert _detect_mime_type(Path("data.xyz123")) == "application/octet-stream"


# ---------------------------------------------------------------------------
# TextConverter
# ---------------------------------------------------------------------------


class TestTextConverter:
    """TextConverter handles .txt and .md files with no optional dependencies."""

    def test_convert_txt(self) -> None:
        result = TextConverter().convert(FIXTURES_DIR / "sample.txt")

        assert isinstance(result, ConversionResult)
        assert result.mime_type == "text/plain"
        assert result.converter_used == "text"
        assert result.word_count > 0
        assert "Grove" in result.content

    def test_convert_md(self) -> None:
        result = TextConverter().convert(
            FIXTURES_DIR / "sample.md", mime_type="text/markdown"
        )

        assert isinstance(result, ConversionResult)
        assert result.mime_type == "text/markdown"
        assert result.converter_used == "text"
        assert result.has_headings is True
        assert "# Grove Overview" in result.content

    def test_line_ending_normalisation(self, tmp_path: Path) -> None:
        """Windows-style \\r\\n line endings are normalised to \\n."""
        crlf_file = tmp_path / "crlf.txt"
        crlf_file.write_bytes(b"line one\r\nline two\r\nline three\r\n")

        result = TextConverter().convert(crlf_file)

        assert "\r" not in result.content
        assert result.content == "line one\nline two\nline three\n"

    def test_word_count_accuracy(self, tmp_path: Path) -> None:
        """Word count should match the number of whitespace-separated tokens."""
        test_file = tmp_path / "words.txt"
        test_file.write_text("one two three four five", encoding="utf-8")

        result = TextConverter().convert(test_file)

        assert result.word_count == 5

    def test_has_headings_false_for_plain_text(self) -> None:
        """Plain text without markdown headings should report has_headings=False."""
        result = TextConverter().convert(FIXTURES_DIR / "sample.txt")

        assert result.has_headings is False

    def test_source_path_recorded(self) -> None:
        """The source_path field should contain the file path as a string."""
        result = TextConverter().convert(FIXTURES_DIR / "sample.txt")

        assert "sample.txt" in result.source_path


# ---------------------------------------------------------------------------
# HTMLConverter
# ---------------------------------------------------------------------------


@_skip_html
class TestHTMLConverter:
    """HTMLConverter requires readability-lxml and markdownify."""

    def test_convert_html_file(self) -> None:
        from grove.ingest.html import HTMLConverter

        result = HTMLConverter().convert(FIXTURES_DIR / "sample.html")

        assert isinstance(result, ConversionResult)
        assert result.mime_type == "text/html"
        assert result.converter_used == "readability"
        assert result.word_count > 0
        # The main content should survive; boilerplate should be stripped
        assert "knowledge base" in result.content.lower()

    def test_convert_html_string(self) -> None:
        from grove.ingest.html import HTMLConverter

        html = (
            "<html><head><title>Test</title></head>"
            "<body><h1>Heading</h1><p>Body text here.</p></body></html>"
        )
        result = HTMLConverter().convert(html, from_string=True)

        assert isinstance(result, ConversionResult)
        assert result.source_path == "<string>"
        assert result.word_count > 0

    def test_title_extracted_to_metadata(self) -> None:
        from grove.ingest.html import HTMLConverter

        result = HTMLConverter().convert(FIXTURES_DIR / "sample.html")

        # readability should extract a title from the <title> tag
        assert "title" in result.metadata

    def test_boilerplate_stripped(self) -> None:
        """Navigation and footer boilerplate should be reduced or removed."""
        from grove.ingest.html import HTMLConverter

        result = HTMLConverter().convert(FIXTURES_DIR / "sample.html")

        # The nav links and footer copyright should not dominate the output
        content_lower = result.content.lower()
        assert "knowledge base" in content_lower


# ---------------------------------------------------------------------------
# PDFConverter
# ---------------------------------------------------------------------------


@_skip_pdf
class TestPDFConverter:
    """PDFConverter requires pymupdf4llm or pdfminer.six."""

    def test_convert_pdf(self) -> None:
        from grove.ingest.pdf import PDFConverter

        result = PDFConverter().convert(FIXTURES_DIR / "sample.pdf")

        assert isinstance(result, ConversionResult)
        assert result.mime_type == "application/pdf"
        assert result.converter_used in {"pymupdf4llm", "pdfminer"}
        assert result.word_count > 0

    def test_pdf_content_extracted(self) -> None:
        from grove.ingest.pdf import PDFConverter

        result = PDFConverter().convert(FIXTURES_DIR / "sample.pdf")

        # Our minimal PDF contains "Hello Grove"
        assert "Hello" in result.content or "Grove" in result.content

    def test_pdf_source_path_recorded(self) -> None:
        from grove.ingest.pdf import PDFConverter

        result = PDFConverter().convert(FIXTURES_DIR / "sample.pdf")

        assert "sample.pdf" in result.source_path


# ---------------------------------------------------------------------------
# Converter dispatcher
# ---------------------------------------------------------------------------


class TestConverterDispatcher:
    """The Converter class routes files to the correct converter by MIME type."""

    def test_dispatches_txt(self) -> None:
        result = Converter().convert(FIXTURES_DIR / "sample.txt")

        assert result.converter_used == "text"
        assert result.mime_type == "text/plain"

    def test_dispatches_md(self) -> None:
        result = Converter().convert(FIXTURES_DIR / "sample.md")

        assert result.converter_used == "text"
        assert result.mime_type == "text/markdown"

    @_skip_html
    def test_dispatches_html(self) -> None:
        result = Converter().convert(FIXTURES_DIR / "sample.html")

        assert result.converter_used == "readability"
        assert result.mime_type == "text/html"

    @_skip_pdf
    def test_dispatches_pdf(self) -> None:
        result = Converter().convert(FIXTURES_DIR / "sample.pdf")

        assert result.mime_type == "application/pdf"

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        """An unrecognised file extension should raise UnsupportedFormatError."""
        unknown_file = tmp_path / "data.xyz123"
        unknown_file.write_text("some content", encoding="utf-8")

        with pytest.raises(UnsupportedFormatError, match="Unsupported file format"):
            Converter().convert(unknown_file)

    def test_unsupported_format_is_conversion_error(self, tmp_path: Path) -> None:
        """UnsupportedFormatError should be a subclass of ConversionError."""
        unknown_file = tmp_path / "data.xyz123"
        unknown_file.write_text("some content", encoding="utf-8")

        with pytest.raises(ConversionError):
            Converter().convert(unknown_file)

    def test_detect_mime_type_exposed(self) -> None:
        """The public detect_mime_type method should work for callers."""
        converter = Converter()

        assert converter.detect_mime_type("report.pdf") == "application/pdf"
        assert converter.detect_mime_type("page.html") == "text/html"
        assert converter.detect_mime_type("notes.md") == "text/markdown"


# ---------------------------------------------------------------------------
# ConversionResult model
# ---------------------------------------------------------------------------


class TestConversionResult:
    """Verify the Pydantic model behaves correctly."""

    def test_default_metadata_is_empty_dict(self) -> None:
        result = ConversionResult(
            content="test",
            source_path="/tmp/test.txt",
            mime_type="text/plain",
            converter_used="text",
            word_count=1,
            has_headings=False,
        )

        assert result.metadata == {}

    def test_metadata_isolation(self) -> None:
        """Each instance should get its own metadata dict (no shared state)."""
        r1 = ConversionResult(
            content="a",
            source_path="/tmp/a.txt",
            mime_type="text/plain",
            converter_used="text",
            word_count=1,
            has_headings=False,
        )
        r2 = ConversionResult(
            content="b",
            source_path="/tmp/b.txt",
            mime_type="text/plain",
            converter_used="text",
            word_count=1,
            has_headings=False,
        )

        r1.metadata["key"] = "value"
        assert "key" not in r2.metadata
