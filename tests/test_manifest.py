"""Tests for the manifest writer and grove ingest CLI commands.

Covers ManifestWriter read/write/remove, the full ingest pipeline via
CLI, directory ingestion with summary reports, duplicate detection,
non-grove error handling, and URL ingestion (with mocked httpx).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from grove.cli import app
from grove.ingest.manifest import ManifestWriter
from grove.ingest.models import ConversionResult
from grove.ingest.summariser import SummaryResult

runner = CliRunner()

# Fixtures directory for sample files
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _init_grove(root: Path) -> None:
    """Set up a minimal grove structure at *root*."""
    (root / ".grove").mkdir(parents=True, exist_ok=True)
    (root / ".grove" / "logs").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "articles").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "papers").mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)

    # Write a minimal config.yaml
    config = {
        "llm": {
            "providers": {
                "anthropic": {"api_key": "test-key"},
                "ollama": {"base_url": "http://localhost:11434"},
            },
            "routing": {
                "fast": {
                    "provider": "anthropic",
                    "model": "test-model",
                },
                "standard": {
                    "provider": "anthropic",
                    "model": "test-model",
                },
                "powerful": {
                    "provider": "anthropic",
                    "model": "test-model",
                },
            },
        },
    }
    (root / ".grove" / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )

    # Write empty state.json
    (root / ".grove" / "state.json").write_text(
        json.dumps({}, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_conversion(
    content: str = "Test content with enough words to pass quality checks.",
    source_path: str = "raw/articles/test.md",
    word_count: int = 600,
    has_headings: bool = True,
) -> ConversionResult:
    """Build a ConversionResult for testing."""
    return ConversionResult(
        content=content,
        source_path=source_path,
        mime_type="text/markdown",
        converter_used="text",
        word_count=word_count,
        has_headings=has_headings,
    )


def _make_summary(
    summary: str = "A test summary of the document.",
    concepts: list[str] | None = None,
) -> SummaryResult:
    """Build a SummaryResult for testing."""
    return SummaryResult(
        summary=summary,
        concepts=concepts or ["testing", "grove"],
    )


# ------------------------------------------------------------------
# ManifestWriter.register
# ------------------------------------------------------------------


class TestManifestWriterRegister:
    """ManifestWriter.register adds entries to _manifest.md."""

    def test_register_creates_manifest(self, tmp_path: Path) -> None:
        """First register call creates the _manifest.md file."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        source = tmp_path / "raw" / "articles" / "test.md"
        source.write_text("# Test\n\nContent here.\n", encoding="utf-8")

        conversion = _make_conversion()
        summary = _make_summary()

        writer.register(
            source_path=source,
            original_path="/original/path/test.md",
            conversion=conversion,
            quality="good",
            summary=summary,
            checksum="abc123def456",
        )

        manifest_path = tmp_path / "raw" / "_manifest.md"
        assert manifest_path.exists()

        text = manifest_path.read_text(encoding="utf-8")
        assert "test.md" in text
        assert "good" in text
        assert "600" in text

    def test_register_adds_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple register calls append rows to the table."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        for i in range(3):
            source = tmp_path / "raw" / "articles" / f"doc{i}.md"
            source.write_text(f"# Doc {i}\n", encoding="utf-8")

            writer.register(
                source_path=source,
                original_path=f"/original/doc{i}.md",
                conversion=_make_conversion(
                    source_path=f"raw/articles/doc{i}.md",
                    word_count=500 + i * 100,
                ),
                quality="good",
                summary=_make_summary(concepts=[f"concept-{i}"]),
                checksum=f"checksum-{i}",
            )

        entries = writer.read()
        assert len(entries) == 3

    def test_register_persists_checksum_to_state(self, tmp_path: Path) -> None:
        """Register stores the checksum in state.json for dedup."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        source = tmp_path / "raw" / "articles" / "test.md"
        source.write_text("Content.\n", encoding="utf-8")

        writer.register(
            source_path=source,
            original_path="/original/test.md",
            conversion=_make_conversion(),
            quality="good",
            summary=_make_summary(),
            checksum="unique-checksum-value",
        )

        state = json.loads(
            (tmp_path / ".grove" / "state.json").read_text(encoding="utf-8")
        )
        checksums = state.get("checksums", {})
        assert "unique-checksum-value" in checksums

    def test_register_writes_yaml_front_matter(self, tmp_path: Path) -> None:
        """The manifest file includes YAML front matter with metadata."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        source = tmp_path / "raw" / "articles" / "test.md"
        source.write_text("Content.\n", encoding="utf-8")

        writer.register(
            source_path=source,
            original_path="/original/test.md",
            conversion=_make_conversion(),
            quality="good",
            summary=_make_summary(),
            checksum="abc",
        )

        text = (tmp_path / "raw" / "_manifest.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "total_sources: 1" in text
        assert "last_updated:" in text


# ------------------------------------------------------------------
# ManifestWriter.read
# ------------------------------------------------------------------


class TestManifestWriterRead:
    """ManifestWriter.read returns all entries from the manifest."""

    def test_read_returns_empty_when_no_manifest(self, tmp_path: Path) -> None:
        """Reading a non-existent manifest returns an empty list."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        assert writer.read() == []

    def test_read_returns_registered_entries(self, tmp_path: Path) -> None:
        """Read returns entries that were previously registered."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        source = tmp_path / "raw" / "articles" / "test.md"
        source.write_text("Content.\n", encoding="utf-8")

        writer.register(
            source_path=source,
            original_path="/original/test.md",
            conversion=_make_conversion(word_count=1234),
            quality="partial",
            summary=_make_summary(concepts=["AI", "ML"]),
            checksum="read-test-checksum",
        )

        entries = writer.read()
        assert len(entries) == 1

        entry = entries[0]
        assert "test.md" in entry.source_path
        assert entry.quality == "partial"
        assert entry.word_count == 1234
        assert entry.concepts == ["AI", "ML"]

    def test_read_preserves_order(self, tmp_path: Path) -> None:
        """Entries are returned in the order they were registered."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        for name in ["alpha.md", "beta.md", "gamma.md"]:
            source = tmp_path / "raw" / "articles" / name
            source.write_text(f"# {name}\n", encoding="utf-8")
            writer.register(
                source_path=source,
                original_path=f"/original/{name}",
                conversion=_make_conversion(source_path=f"raw/articles/{name}"),
                quality="good",
                summary=_make_summary(),
                checksum=f"checksum-{name}",
            )

        entries = writer.read()
        names = [e.source_path for e in entries]
        assert "alpha.md" in names[0]
        assert "beta.md" in names[1]
        assert "gamma.md" in names[2]


# ------------------------------------------------------------------
# ManifestWriter.remove
# ------------------------------------------------------------------


class TestManifestWriterRemove:
    """ManifestWriter.remove removes an entry from the manifest."""

    def test_remove_deletes_entry(self, tmp_path: Path) -> None:
        """Removing an entry reduces the manifest count by one."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        source = tmp_path / "raw" / "articles" / "removeme.md"
        source.write_text("Content.\n", encoding="utf-8")

        writer.register(
            source_path=source,
            original_path="/original/removeme.md",
            conversion=_make_conversion(),
            quality="good",
            summary=_make_summary(),
            checksum="remove-checksum",
        )

        assert len(writer.read()) == 1

        writer.remove(source)

        assert len(writer.read()) == 0

    def test_remove_preserves_other_entries(self, tmp_path: Path) -> None:
        """Removing one entry leaves the rest intact."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        for name in ["keep.md", "remove.md"]:
            source = tmp_path / "raw" / "articles" / name
            source.write_text(f"# {name}\n", encoding="utf-8")
            writer.register(
                source_path=source,
                original_path=f"/original/{name}",
                conversion=_make_conversion(source_path=f"raw/articles/{name}"),
                quality="good",
                summary=_make_summary(),
                checksum=f"checksum-{name}",
            )

        target = tmp_path / "raw" / "articles" / "remove.md"
        writer.remove(target)

        entries = writer.read()
        assert len(entries) == 1
        assert "keep.md" in entries[0].source_path

    def test_remove_cleans_up_state_checksum(self, tmp_path: Path) -> None:
        """Removing an entry also removes the checksum from state.json."""
        _init_grove(tmp_path)
        writer = ManifestWriter(tmp_path)

        source = tmp_path / "raw" / "articles" / "test.md"
        source.write_text("Content.\n", encoding="utf-8")

        writer.register(
            source_path=source,
            original_path="/original/test.md",
            conversion=_make_conversion(),
            quality="good",
            summary=_make_summary(),
            checksum="state-checksum",
        )

        # Verify checksum is in state
        state = json.loads(
            (tmp_path / ".grove" / "state.json").read_text(encoding="utf-8")
        )
        assert "state-checksum" in state.get("checksums", {})

        writer.remove(source)

        # Verify checksum is removed from state
        state = json.loads(
            (tmp_path / ".grove" / "state.json").read_text(encoding="utf-8")
        )
        assert "state-checksum" not in state.get("checksums", {})


# ------------------------------------------------------------------
# grove ingest CLI — file ingestion
# ------------------------------------------------------------------


class TestIngestCommand:
    """The ``grove ingest`` command copies files and runs the pipeline."""

    def test_ingest_copies_file_and_runs_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingesting a file copies it to raw/ and creates a manifest entry."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Create a source file outside raw/
        source = tmp_path / "external" / "document.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "# Test Document\n\n" + "This is test content. " * 100 + "\n",
            encoding="utf-8",
        )

        # Mock the LLM-dependent parts of the pipeline
        mock_summary = SummaryResult(
            summary="A test summary.",
            concepts=["testing"],
        )

        with (
            patch("grove.ingest.summariser.Summariser") as mock_summariser_cls,
            patch("grove.llm.router.LLMRouter"),
            patch("grove.config.loader.ConfigLoader") as mock_config_cls,
        ):
            mock_summariser = MagicMock()
            mock_summariser.summarise.return_value = mock_summary
            mock_summariser_cls.return_value = mock_summariser
            mock_config_cls.return_value.load.return_value = MagicMock()

            result = runner.invoke(app, ["ingest", str(source)])

        assert result.exit_code == 0, result.output
        assert "Ingested" in result.output or "ingested" in result.output.lower()

        # Verify the file was copied into raw/
        raw_files = list((tmp_path / "raw" / "articles").glob("*.md"))
        assert len(raw_files) >= 1

    def test_ingest_nonexistent_file_shows_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingesting a file that does not exist prints an error."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            ["ingest", "/nonexistent/file.md"],
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "File not found" in result.output

    def test_ingest_on_non_grove_directory_shows_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running ingest outside a grove prints 'Not a grove' error."""
        monkeypatch.chdir(tmp_path)

        source = tmp_path / "file.txt"
        source.write_text("content", encoding="utf-8")

        result = runner.invoke(app, ["ingest", str(source)])

        assert result.exit_code != 0
        assert "Not a grove" in result.output


# ------------------------------------------------------------------
# grove ingest — duplicate detection
# ------------------------------------------------------------------


class TestIngestDedup:
    """The ingest command detects and reports duplicates."""

    def test_ingest_detects_duplicates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingesting the same content twice is detected as a duplicate."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        content = (
            "# Unique Document\n\n"
            + "Distinctive content for dedup testing. " * 100
            + "\n"
        )

        # Create the source file
        source = tmp_path / "external" / "doc.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content, encoding="utf-8")

        mock_summary = SummaryResult(summary="Summary.", concepts=["testing"])

        with (
            patch("grove.ingest.summariser.Summariser") as mock_summariser_cls,
            patch("grove.llm.router.LLMRouter"),
            patch("grove.config.loader.ConfigLoader") as mock_config_cls,
        ):
            mock_summariser = MagicMock()
            mock_summariser.summarise.return_value = mock_summary
            mock_summariser_cls.return_value = mock_summariser
            mock_config_cls.return_value.load.return_value = MagicMock()

            # First ingest -- should succeed
            result1 = runner.invoke(app, ["ingest", str(source)])
            assert result1.exit_code == 0

            # Second ingest -- same content, should report duplicate
            source2 = tmp_path / "external" / "doc-copy.md"
            source2.write_text(content, encoding="utf-8")

            result2 = runner.invoke(app, ["ingest", str(source2)])

        assert result2.exit_code == 0
        output_lower = result2.output.lower()
        assert "duplicate" in output_lower


# ------------------------------------------------------------------
# grove ingest-dir CLI
# ------------------------------------------------------------------


class TestIngestDirCommand:
    """The ``grove ingest-dir`` command processes multiple files."""

    def test_ingest_dir_processes_multiple_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ingest-dir processes all supported files and prints a summary."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Create source files in a separate directory
        source_dir = tmp_path / "sources"
        source_dir.mkdir()

        for name in ["a.md", "b.txt", "c.html"]:
            path = source_dir / name
            if name.endswith(".html"):
                path.write_text(
                    "<html><body><h1>Test</h1>"
                    "<p>" + "HTML content. " * 100 + "</p>"
                    "</body></html>",
                    encoding="utf-8",
                )
            else:
                path.write_text(
                    "# Test\n\n" + "Content for testing. " * 100 + "\n",
                    encoding="utf-8",
                )

        # Also create an unsupported file that should be skipped
        (source_dir / "skip.xyz").write_text("ignored", encoding="utf-8")

        mock_summary = SummaryResult(summary="Summary.", concepts=["test"])

        with (
            patch("grove.ingest.summariser.Summariser") as mock_summariser_cls,
            patch("grove.llm.router.LLMRouter"),
            patch("grove.config.loader.ConfigLoader") as mock_config_cls,
        ):
            mock_summariser = MagicMock()
            mock_summariser.summarise.return_value = mock_summary
            mock_summariser_cls.return_value = mock_summariser
            mock_config_cls.return_value.load.return_value = MagicMock()

            result = runner.invoke(app, ["ingest-dir", str(source_dir)])

        assert result.exit_code == 0, result.output
        # Should show summary table
        assert "Succeeded" in result.output
        assert "Failed" in result.output

    def test_ingest_dir_reports_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ingest-dir reports counts of succeeded, failed, duplicates."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        source_dir = tmp_path / "sources"
        source_dir.mkdir()

        (source_dir / "good.md").write_text(
            "# Good\n\n" + "Words here. " * 100 + "\n",
            encoding="utf-8",
        )

        mock_summary = SummaryResult(summary="Summary.", concepts=["test"])

        with (
            patch("grove.ingest.summariser.Summariser") as mock_summariser_cls,
            patch("grove.llm.router.LLMRouter"),
            patch("grove.config.loader.ConfigLoader") as mock_config_cls,
        ):
            mock_summariser = MagicMock()
            mock_summariser.summarise.return_value = mock_summary
            mock_summariser_cls.return_value = mock_summariser
            mock_config_cls.return_value.load.return_value = MagicMock()

            result = runner.invoke(app, ["ingest-dir", str(source_dir)])

        assert result.exit_code == 0
        assert "Succeeded" in result.output

    def test_ingest_dir_no_supported_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ingest-dir with no supported files prints a warning."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        # Add only unsupported files
        (empty_dir / "data.xyz").write_text("nope", encoding="utf-8")

        result = runner.invoke(app, ["ingest-dir", str(empty_dir)])

        assert "No supported files" in result.output


# ------------------------------------------------------------------
# grove ingest — URL ingestion
# ------------------------------------------------------------------


class TestIngestURL:
    """URL ingestion downloads HTML and processes it."""

    def test_url_ingestion_downloads_and_saves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ingesting a URL downloads HTML and saves to raw/articles/."""
        _init_grove(tmp_path)
        monkeypatch.chdir(tmp_path)

        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Test Page</title></head>"
            "<body><h1>Downloaded</h1>"
            "<p>" + "Web content here. " * 100 + "</p>"
            "</body></html>"
        )
        mock_response.status_code = 200

        mock_summary = SummaryResult(summary="Web summary.", concepts=["web"])

        with (
            patch("grove.cli.httpx.get") as mock_get,
            patch("grove.ingest.summariser.Summariser") as mock_summariser_cls,
            patch("grove.llm.router.LLMRouter"),
            patch("grove.config.loader.ConfigLoader") as mock_config_cls,
        ):
            mock_get.return_value = mock_response
            mock_summariser = MagicMock()
            mock_summariser.summarise.return_value = mock_summary
            mock_summariser_cls.return_value = mock_summariser
            mock_config_cls.return_value.load.return_value = MagicMock()

            result = runner.invoke(
                app,
                ["ingest", "https://example.com/article"],
            )

        assert result.exit_code == 0, result.output
        mock_get.assert_called_once()

        # Verify HTML was saved to raw/articles/
        html_files = list((tmp_path / "raw" / "articles").glob("*.html"))
        assert len(html_files) >= 1
