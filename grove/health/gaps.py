"""GapDetector -- identifies concepts mentioned but missing from the wiki.

Two gap sources:

1. **Broken wiki-links**: ``[[concept-name]]`` links inside wiki articles
   that do not resolve to an existing wiki file.
2. **Source concepts without articles**: concepts listed in
   ``grove_concepts`` front matter of raw sources that have no
   corresponding wiki article.

Broken wiki-links are auto-fixable via ``grove health --fix`` (stub
articles are created for each missing link target).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from grove.health.models import CheckResult

logger = logging.getLogger(__name__)

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

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


class GapDetector:
    """Find concepts referenced in sources or wiki-links with no article."""

    def __init__(self, grove_root: Path) -> None:
        self._grove_root = grove_root
        self._wiki_dir = grove_root / "wiki"
        self._raw_dir = grove_root / "raw"

    def check(self) -> CheckResult:
        """Run the gap detection check."""
        # Build the set of existing wiki article slugs.
        known_slugs = self._build_slug_set()

        broken_links = self._find_broken_wiki_links(known_slugs)
        source_gaps = self._find_source_concept_gaps(known_slugs)

        all_gaps = sorted(set(broken_links + source_gaps))

        if not all_gaps:
            return CheckResult(
                name="gaps",
                status="pass",
                message="All referenced concepts have wiki articles.",
            )

        return CheckResult(
            name="gaps",
            status="warn",
            message=(
                f"{len(all_gaps)} concept gap(s) found "
                f"({len(broken_links)} broken link(s), "
                f"{len(source_gaps)} source concept(s) without articles)."
            ),
            details=all_gaps,
            auto_fixable=True,
        )

    def get_broken_links(self) -> list[str]:
        """Return broken wiki-link targets (used by --fix)."""
        known_slugs = self._build_slug_set()
        return self._find_broken_wiki_links(known_slugs)

    def _build_slug_set(self) -> set[str]:
        """Build a set of known wiki article slugs (filename stems)."""
        slugs: set[str] = set()
        if not self._wiki_dir.exists():
            return slugs

        for md_file in self._wiki_dir.rglob("*.md"):
            slugs.add(md_file.stem)
        return slugs

    def _find_broken_wiki_links(self, known_slugs: set[str]) -> list[str]:
        """Find ``[[target]]`` links that do not resolve to wiki files."""
        if not self._wiki_dir.exists():
            return []

        broken: set[str] = set()

        for md_file in sorted(self._wiki_dir.rglob("*.md")):
            if md_file.name in _SKIP_FILES:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            links = _WIKI_LINK_RE.findall(content)
            for link_target in links:
                target = link_target.strip()
                target_normalised = target.removesuffix(".md")

                if target_normalised not in known_slugs:
                    broken.add(target_normalised)

        return sorted(broken)

    def _find_source_concept_gaps(self, known_slugs: set[str]) -> list[str]:
        """Find concepts from source front matter with no wiki article."""
        if not self._raw_dir.exists():
            return []

        gaps: set[str] = set()

        for md_file in sorted(self._raw_dir.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            meta = _parse_front_matter(content)
            if meta is None:
                continue

            concepts = meta.get("grove_concepts", [])
            if not isinstance(concepts, list):
                continue

            for concept in concepts:
                slug = str(concept).lower().replace(" ", "-")
                if slug not in known_slugs:
                    gaps.add(slug)

        return sorted(gaps)
