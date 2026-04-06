"""StalenessChecker -- detects wiki articles compiled from changed sources.

For each wiki article, reads the ``compiled_from`` list from YAML front
matter.  For each source path, computes the current file's SHA-256
checksum and compares it against the checksum stored in ``state.json``
at ingest time.  If any source has changed, the article is stale and
should be recompiled.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml

from grove.config.state import StateManager
from grove.health.models import CheckResult

logger = logging.getLogger(__name__)

# Files that are not regular articles.
_SKIP_FILES = {"_index.md", "_concepts.md", "_health.md"}


def _parse_front_matter(content: str) -> dict[str, Any] | None:
    """Extract YAML front matter as a dict, or ``None`` on failure."""
    stripped = content.lstrip("\n")
    if not stripped.startswith("---"):
        return None

    end_idx = stripped.find("\n---", 3)
    if end_idx == -1:
        return None

    yaml_str = stripped[3:end_idx]
    try:
        meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return None

    if not isinstance(meta, dict):
        return None

    return meta


def _compute_checksum(path: Path) -> str | None:
    """Return SHA-256 hex digest of *path*, or ``None`` on read error."""
    try:
        content = path.read_text(encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    except (FileNotFoundError, OSError):
        return None


class StalenessChecker:
    """Compare article ``compiled_from`` checksums vs current source files."""

    def __init__(self, grove_root: Path, state: StateManager) -> None:
        self._grove_root = grove_root
        self._wiki_dir = grove_root / "wiki"
        self._state = state

    def check(self) -> CheckResult:
        """Run the staleness check across all wiki articles."""
        if not self._wiki_dir.exists():
            return CheckResult(
                name="staleness",
                status="pass",
                message="No wiki directory found.",
            )

        # Load the checksum map from state.json: {checksum: source_path}.
        # We need the reverse: {source_path: checksum}.
        checksums_map: dict[str, str] = self._state.get("checksums", {})
        path_to_checksum: dict[str, str] = {
            path: cksum for cksum, path in checksums_map.items()
        }

        stale_articles: list[str] = []

        for md_file in sorted(self._wiki_dir.rglob("*.md")):
            if md_file.name in _SKIP_FILES:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            meta = _parse_front_matter(content)
            if meta is None:
                continue

            compiled_from = meta.get("compiled_from", [])
            if not isinstance(compiled_from, list):
                continue

            rel_article = str(md_file.relative_to(self._grove_root))

            for source_path in compiled_from:
                stored_checksum = path_to_checksum.get(source_path)
                if stored_checksum is None:
                    # Source not tracked in state -- could be missing or
                    # pre-dates the checksum system.  Flag as stale.
                    stale_articles.append(
                        f"{rel_article} (source {source_path} not in state)"
                    )
                    break

                current_checksum = _compute_checksum(self._grove_root / source_path)
                if current_checksum is None:
                    stale_articles.append(
                        f"{rel_article} (source {source_path} missing)"
                    )
                    break

                if current_checksum != stored_checksum:
                    stale_articles.append(
                        f"{rel_article} (source {source_path} changed)"
                    )
                    break

        if stale_articles:
            return CheckResult(
                name="staleness",
                status="warn",
                message=(
                    f"{len(stale_articles)} article(s) have stale sources. "
                    "Re-run `grove compile` to update."
                ),
                details=stale_articles,
            )

        return CheckResult(
            name="staleness",
            status="pass",
            message="All articles are up to date with their sources.",
        )
