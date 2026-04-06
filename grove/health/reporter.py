"""HealthReporter -- orchestrates all health checkers and aggregates results.

Runs each checker in sequence, collects ``CheckResult`` objects, and
produces a ``HealthReport`` with an overall status.  Also handles the
``--fix`` mode: creates stub articles for broken wiki-links and writes
``_health.md`` to the wiki directory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grove.config.state import StateManager
from grove.health.contradictions import ContradictionDetector
from grove.health.gaps import GapDetector
from grove.health.models import CheckResult, HealthReport
from grove.health.orphans import OrphanDetector
from grove.health.provenance import ProvenanceChecker
from grove.health.staleness import StalenessChecker

logger = logging.getLogger(__name__)

# Template for stub articles created by --fix.
_STUB_TEMPLATE = """\
---
title: "{title}"
status: stub
compiled_from: []
concepts: [{concept}]
summary: "Stub article — not yet compiled."
last_compiled: "{timestamp}"
---

# {title}

This article has not yet been compiled.
"""


class HealthReporter:
    """Aggregate all health checks into a single report.

    Parameters
    ----------
    grove_root:
        Root directory of the grove project.
    router:
        Optional ``LLMRouter`` for contradiction detection.
    prompt_builder:
        Optional ``PromptBuilder`` for contradiction detection.
    """

    def __init__(
        self,
        grove_root: Path,
        router: Any | None = None,
        prompt_builder: Any | None = None,
    ) -> None:
        self._grove_root = grove_root
        self._wiki_dir = grove_root / "wiki"
        self._router = router
        self._prompt_builder = prompt_builder

    def run(self) -> HealthReport:
        """Execute all checkers and return an aggregated report."""
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        total_articles = self._count_articles()

        state = StateManager(self._grove_root)

        checks: dict[str, CheckResult] = {}

        # 1. Provenance
        provenance = ProvenanceChecker(self._wiki_dir)
        checks["provenance"] = provenance.check()

        # 2. Contradictions (optional)
        contradictions = ContradictionDetector(
            self._wiki_dir, self._router, self._prompt_builder
        )
        checks["contradictions"] = contradictions.check()

        # 3. Staleness
        staleness = StalenessChecker(self._grove_root, state)
        checks["staleness"] = staleness.check()

        # 4. Gaps
        gaps = GapDetector(self._grove_root)
        checks["gaps"] = gaps.check()

        # 5. Orphans
        orphans = OrphanDetector(self._wiki_dir)
        checks["orphans"] = orphans.check()

        # Determine overall status.
        statuses = [c.status for c in checks.values()]
        if any(s == "fail" for s in statuses):
            overall = "issues"
        elif any(s == "warn" for s in statuses):
            overall = "warnings"
        else:
            overall = "healthy"

        return HealthReport(
            timestamp=timestamp,
            overall_status=overall,
            total_articles=total_articles,
            checks=checks,
        )

    def fix(self, report: HealthReport) -> list[str]:
        """Auto-fix issues identified in *report*.

        Currently fixes:
        - Creates stub articles for broken wiki-links (gap check).

        Returns a list of human-readable descriptions of fixes applied.
        """
        fixes: list[str] = []

        # Fix broken wiki-links by creating stub articles.
        gap_check = report.checks.get("gaps")
        if gap_check and gap_check.auto_fixable and gap_check.details:
            gap_detector = GapDetector(self._grove_root)
            broken_links = gap_detector.get_broken_links()

            for slug in broken_links:
                stub_path = self._wiki_dir / f"{slug}.md"
                if stub_path.exists():
                    continue

                title = slug.replace("-", " ").title()
                timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                content = _STUB_TEMPLATE.format(
                    title=title,
                    concept=slug,
                    timestamp=timestamp,
                )
                stub_path.parent.mkdir(parents=True, exist_ok=True)
                stub_path.write_text(content, encoding="utf-8")
                fixes.append(f"Created stub: wiki/{slug}.md")
                logger.info("Created stub article: %s", stub_path)

        return fixes

    def write_health_report(self, report: HealthReport) -> Path:
        """Write ``_health.md`` to the wiki directory.

        Returns the path to the written file.
        """
        self._wiki_dir.mkdir(parents=True, exist_ok=True)
        health_path = self._wiki_dir / "_health.md"

        lines: list[str] = [
            "---",
            "title: Health Report",
            f'generated: "{report.timestamp}"',
            f'status: "{report.overall_status}"',
            "---",
            "",
            "# Wiki Health Report",
            "",
            f"**Status:** {report.overall_status}",
            f"**Articles:** {report.total_articles}",
            f"**Timestamp:** {report.timestamp}",
            "",
        ]

        for name, check in report.checks.items():
            icon = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[check.status]
            lines.append(f"## {icon} {name}")
            lines.append("")
            lines.append(check.message)
            lines.append("")

            if check.details:
                for detail in check.details:
                    lines.append(f"- {detail}")
                lines.append("")

        health_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote health report to %s", health_path)
        return health_path

    def _count_articles(self) -> int:
        """Count wiki articles, excluding index and meta files."""
        skip = {"_index.md", "_concepts.md", "_health.md"}
        if not self._wiki_dir.exists():
            return 0
        return sum(1 for f in self._wiki_dir.rglob("*.md") if f.name not in skip)
