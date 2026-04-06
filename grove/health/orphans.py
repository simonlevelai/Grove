"""OrphanDetector -- finds wiki articles with no incoming links.

An article is an orphan if no other article contains a ``[[slug]]``
link pointing to it.  Index files (``_index.md``, ``_concepts.md``,
``_health.md``) are excluded from both the target set and the
link-scanning set.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from grove.health.models import CheckResult

logger = logging.getLogger(__name__)

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Files that are not regular articles.
_SKIP_FILES = {"_index.md", "_concepts.md", "_health.md"}


class OrphanDetector:
    """Find wiki articles that receive zero incoming ``[[...]]`` links."""

    def __init__(self, wiki_dir: Path) -> None:
        self._wiki_dir = wiki_dir

    def check(self) -> CheckResult:
        """Run the orphan detection check."""
        if not self._wiki_dir.exists():
            return CheckResult(
                name="orphans",
                status="pass",
                message="No wiki directory found.",
            )

        # Collect all article slugs and a map of incoming links.
        article_slugs: set[str] = set()
        incoming_links: dict[str, set[str]] = {}

        for md_file in sorted(self._wiki_dir.rglob("*.md")):
            if md_file.name in _SKIP_FILES:
                continue
            article_slugs.add(md_file.stem)
            incoming_links[md_file.stem] = set()

        if not article_slugs:
            return CheckResult(
                name="orphans",
                status="pass",
                message="No wiki articles found.",
            )

        # Scan all articles for outgoing [[...]] links.
        for md_file in sorted(self._wiki_dir.rglob("*.md")):
            if md_file.name in _SKIP_FILES:
                continue

            source_slug = md_file.stem

            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            links = _WIKI_LINK_RE.findall(content)
            for link_target in links:
                target = link_target.strip().removesuffix(".md")
                if target in incoming_links and target != source_slug:
                    incoming_links[target].add(source_slug)

        # Find articles with zero incoming links.
        orphans = sorted(
            slug for slug, sources in incoming_links.items() if not sources
        )

        if not orphans:
            return CheckResult(
                name="orphans",
                status="pass",
                message="All articles have at least one incoming link.",
            )

        return CheckResult(
            name="orphans",
            status="warn",
            message=f"{len(orphans)} orphan article(s) with no incoming links.",
            details=orphans,
        )
