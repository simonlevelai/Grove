"""ProvenanceChecker -- counts ``[source:...]`` citations vs factual sentences.

Reuses the same regex patterns as the compile-time quality ratchet
(``grove.compile.ratchet``) but operates on-demand against all wiki
articles on disk, rather than only newly compiled articles.

Per-article provenance coverage is reported so users can identify
which articles need more citations.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from grove.health.models import CheckResult

logger = logging.getLogger(__name__)

# Reuse the same patterns from the compile ratchet for consistency.
_SOURCE_CITATION_RE = re.compile(r"\[source:\s*[^\]]+\]")

_FACTUAL_SENTENCE_RE = re.compile(
    r"(?:"
    r"\d+"
    r"|(?:more|fewer|less|greater|higher|lower|larger|smaller)\s+than"
    r"|(?:because|therefore|consequently|as a result|due to|caused by)"
    r"|(?:according to|research shows|studies show|data indicates)"
    r"|(?:increased|decreased|improved|declined|grew|fell|rose|dropped)"
    r")",
    re.IGNORECASE,
)

# Files that are not regular articles.
_SKIP_FILES = {"_index.md", "_concepts.md", "_health.md"}


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences, stripping front matter and headings."""
    stripped = text.lstrip("\n")
    if stripped.startswith("---"):
        end_idx = stripped.find("\n---", 3)
        if end_idx != -1:
            stripped = stripped[end_idx + 4 :]

    lines = [
        line
        for line in stripped.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    body = " ".join(lines)
    sentences = re.split(r"(?<=[.!?])\s+", body)
    return [s.strip() for s in sentences if s.strip()]


class ProvenanceChecker:
    """Count ``[source:...]`` citations vs factual sentences per article."""

    def __init__(self, wiki_dir: Path) -> None:
        self._wiki_dir = wiki_dir

    def check(self) -> CheckResult:
        """Run the provenance check across all wiki articles."""
        if not self._wiki_dir.exists():
            return CheckResult(
                name="provenance",
                status="pass",
                message="No wiki directory found.",
            )

        articles = self._collect_articles()
        if not articles:
            return CheckResult(
                name="provenance",
                status="pass",
                message="No wiki articles found.",
            )

        total_factual = 0
        total_cited = 0
        poorly_cited: list[str] = []

        for rel_path, content in articles:
            sentences = _split_sentences(content)
            factual = [s for s in sentences if _FACTUAL_SENTENCE_RE.search(s)]
            cited = [s for s in factual if _SOURCE_CITATION_RE.search(s)]

            total_factual += len(factual)
            total_cited += len(cited)

            if factual:
                ratio = len(cited) / len(factual)
                if ratio < 0.50:
                    poorly_cited.append(
                        f"{rel_path} ({len(cited)}/{len(factual)} cited)"
                    )

        if total_factual == 0:
            return CheckResult(
                name="provenance",
                status="pass",
                message="No factual sentences detected.",
            )

        overall_ratio = total_cited / total_factual

        if overall_ratio >= 0.90:
            return CheckResult(
                name="provenance",
                status="pass",
                message=(
                    f"Provenance coverage: {overall_ratio:.0%} "
                    f"({total_cited}/{total_factual} factual sentences cited)."
                ),
            )

        if overall_ratio >= 0.50:
            return CheckResult(
                name="provenance",
                status="warn",
                message=(
                    f"Provenance coverage: {overall_ratio:.0%} "
                    f"({total_cited}/{total_factual} cited). "
                    f"{len(poorly_cited)} article(s) below 50%."
                ),
                details=poorly_cited,
            )

        return CheckResult(
            name="provenance",
            status="fail",
            message=(
                f"Provenance coverage: {overall_ratio:.0%} "
                f"({total_cited}/{total_factual} cited). "
                f"{len(poorly_cited)} article(s) below 50%."
            ),
            details=poorly_cited,
        )

    def _collect_articles(self) -> list[tuple[str, str]]:
        """Return ``(relative_path, content)`` for each wiki article."""
        results: list[tuple[str, str]] = []
        for md_file in sorted(self._wiki_dir.rglob("*.md")):
            if md_file.name in _SKIP_FILES:
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                rel_path = str(md_file.relative_to(self._wiki_dir))
                results.append((rel_path, content))
            except OSError:
                logger.warning("Could not read %s", md_file)
        return results
