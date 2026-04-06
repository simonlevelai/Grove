"""ManifestWriter -- reads and writes ``raw/_manifest.md``.

The manifest is a YAML-fronted markdown file that tracks every ingested
source: its quality grade, word count, key concepts, and ingest timestamp.
It serves as the human-readable index of the raw/ directory and is kept
in sync with ``state.json`` checksums.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from grove.config.state import StateManager
from grove.ingest.dedup import Deduplicator
from grove.ingest.models import ConversionResult
from grove.ingest.summariser import SummaryResult


class ManifestEntry(BaseModel):
    """A single row in the ``_manifest.md`` table."""

    source_path: str
    original_path: str = Field(
        description="Where the file came from (URL or original disk path)."
    )
    quality: str = Field(description="good | partial | poor")
    word_count: int
    concepts: list[str] = Field(default_factory=list)
    ingested_at: str = Field(description="ISO-8601 timestamp.")
    checksum: str


class ManifestWriter:
    """Reads and writes the ``raw/_manifest.md`` manifest file.

    The manifest is the single source of truth for what has been ingested
    into a grove.  Each call to :meth:`register` appends a row and
    persists the checksum to ``state.json`` via the deduplicator.
    """

    def __init__(self, grove_root: Path) -> None:
        """Initialise with *grove_root* -- the knowledge base root that
        contains ``raw/``.
        """
        self._grove_root = grove_root
        self._manifest_path = grove_root / "raw" / "_manifest.md"
        self._state = StateManager(grove_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        source_path: Path,
        original_path: str,
        conversion: ConversionResult,
        quality: str,
        summary: SummaryResult,
        checksum: str,
    ) -> None:
        """Add an entry to ``raw/_manifest.md`` and store the checksum.

        The checksum is persisted via the deduplicator's storage mechanism
        in ``state.json`` so future ingests detect duplicates.
        """
        relative = _relative_source_path(source_path, self._grove_root)

        entry = ManifestEntry(
            source_path=relative,
            original_path=original_path,
            quality=quality,
            word_count=conversion.word_count,
            concepts=summary.concepts,
            ingested_at=datetime.now(tz=UTC).isoformat(),
            checksum=checksum,
        )

        entries = self.read()
        entries.append(entry)
        self._write(entries)

        # Persist checksum to state.json for dedup
        dedup = Deduplicator(self._state)
        dedup.store(checksum, relative)

    def read(self) -> list[ManifestEntry]:
        """Read all entries from the manifest, returning an empty list if
        the file does not exist or contains no entries.
        """
        if not self._manifest_path.exists():
            return []

        text = self._manifest_path.read_text(encoding="utf-8")
        _, body = _split_manifest_front_matter(text)

        entries: list[ManifestEntry] = []
        for line in body.strip().splitlines():
            entry = _parse_table_row(line)
            if entry is not None:
                entries.append(entry)

        return entries

    def remove(self, source_path: Path) -> None:
        """Remove the entry matching *source_path* from the manifest.

        Also removes the checksum from ``state.json``.
        """
        relative = _relative_source_path(source_path, self._grove_root)
        entries = self.read()

        found = False
        remaining = []
        for entry in entries:
            if entry.source_path == relative:
                found = True
            else:
                remaining.append(entry)

        self._write(remaining)

        # Remove checksum from state.json by reverse-lookup
        # (checksum -> source_path mapping)
        if found:
            self._state.invalidate_cache()
            checksums: dict[str, str] = self._state.get("checksums", {})
            keys_to_remove = [k for k, v in checksums.items() if v == relative]
            for key in keys_to_remove:
                del checksums[key]
            self._state.set("checksums", checksums)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, entries: list[ManifestEntry]) -> None:
        """Write the full manifest file from a list of entries."""
        now = datetime.now(tz=UTC).isoformat()

        front_matter = yaml.dump(
            {
                "total_sources": len(entries),
                "last_updated": now,
            },
            default_flow_style=False,
            sort_keys=False,
        ).rstrip("\n")

        lines = [
            f"---\n{front_matter}\n---\n",
            "",
            "| Source | Quality | Words | Concepts | Ingested |",
            "|--------|---------|-------|----------|----------|",
        ]

        for entry in entries:
            concepts_str = ", ".join(entry.concepts) if entry.concepts else ""
            ingested_date = entry.ingested_at[:10]  # YYYY-MM-DD
            lines.append(
                f"| {entry.source_path} "
                f"| {entry.quality} "
                f"| {entry.word_count} "
                f"| {concepts_str} "
                f"| {ingested_date} |"
            )

        lines.append("")  # trailing newline
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _relative_source_path(source_path: Path, grove_root: Path) -> str:
    """Return *source_path* relative to *grove_root* if possible.

    Falls back to the absolute path as a string if the source is not
    inside the grove.
    """
    try:
        return str(source_path.resolve().relative_to(grove_root.resolve()))
    except ValueError:
        return str(source_path)


def _split_manifest_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Split the manifest file into YAML front matter and body."""
    if not text.startswith("---"):
        return {}, text

    end_idx = text.find("\n---", 3)
    if end_idx == -1:
        return {}, text

    yaml_block = text[4:end_idx]
    body = text[end_idx + 4 :]

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        meta = None

    if not isinstance(meta, dict):
        meta = {}

    return meta, body


def _parse_table_row(line: str) -> ManifestEntry | None:
    """Parse a single markdown table row into a ManifestEntry.

    Returns ``None`` for header rows, separator rows, or malformed lines.
    """
    line = line.strip()
    if not line.startswith("|"):
        return None

    # Skip the header row and separator row
    if "Source" in line and "Quality" in line:
        return None
    if re.match(r"^\|[-|\s]+\|$", line):
        return None

    # Split on | and strip the empty strings from leading/trailing pipes
    cells = [c.strip() for c in line.split("|")[1:-1]]

    if len(cells) < 5:
        return None

    source_path = cells[0]
    quality = cells[1]
    try:
        word_count = int(cells[2])
    except (ValueError, IndexError):
        return None

    concepts_str = cells[3]
    concepts = [c.strip() for c in concepts_str.split(",") if c.strip()]

    ingested_at = cells[4] if len(cells) > 4 else ""

    # Original path and checksum are not stored in the table --
    # use source_path as fallback; checksum can be read from state.json.
    return ManifestEntry(
        source_path=source_path,
        original_path=source_path,
        quality=quality,
        word_count=word_count,
        concepts=concepts,
        ingested_at=ingested_at,
        checksum="",
    )
