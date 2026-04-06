"""QualityRatchet -- post-compilation quality gate.

Runs seven checks in sequence after the ArticleWriter has written articles
to ``wiki/``, but before the git commit.  Any check marked BLOCK that fails
aborts the commit; checks marked WARN are logged but do not block.

Outputs a structured JSON report to ``.grove/logs/ratchet-<timestamp>.json``.

Checks (in order):
1. Provenance coverage — factual sentences must have ``[source:...]`` citations
2. New contradictions — LLM-powered comparison of overlapping article pairs
3. Coverage drop — ``compiled_from`` count must not drop >10% vs. previous
4. Broken wiki-links — ``[[article]]`` links must resolve to files on disk
5. Human annotation preservation — ``<!-- grove:human -->`` blocks must survive
6. Pinned article overwrite — ``pinned: true`` articles must not change
7. Query article used as source — ``origin: query`` files must not appear in sources

See ARCH.md "Quality Ratchet -- Full Specification" for the authoritative spec.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from grove.compile.parser import ParsedArticle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches [source: ...] citations in article bodies.
_SOURCE_CITATION_RE = re.compile(r"\[source:\s*[^\]]+\]")

# Heuristic: a "factual sentence" contains numbers, comparisons, or causal
# language.  This is deliberately broad -- false positives are acceptable
# because the ratchet thresholds account for imprecision.
_FACTUAL_SENTENCE_RE = re.compile(
    r"(?:"
    r"\d+"  # contains a number
    r"|(?:more|fewer|less|greater|higher|lower|larger|smaller)\s+than"  # comparison
    r"|(?:because|therefore|consequently|as a result|due to|caused by)"  # causal
    r"|(?:according to|research shows|studies show|data indicates)"  # attribution
    r"|(?:increased|decreased|improved|declined|grew|fell|rose|dropped)"  # trend
    r")",
    re.IGNORECASE,
)

# Matches [[wiki-link]] patterns.
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Matches <!-- grove:human --> ... <!-- /grove:human --> blocks.
_HUMAN_BLOCK_RE = re.compile(
    r"(<!--\s*grove:human\s*-->.*?<!--\s*/grove:human\s*-->)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class RatchetResult(BaseModel):
    """Outcome of running the quality ratchet on a set of compiled articles."""

    timestamp: str = Field(description="ISO-8601 timestamp of the ratchet run.")
    passed: bool = Field(description="True if no BLOCK-level checks failed.")
    blocking_failures: list[str] = Field(
        default_factory=list,
        description="Names of checks that failed at BLOCK severity.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Names of checks that triggered WARN severity.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-check detail objects.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences using a simple regex heuristic.

    Splits on sentence-ending punctuation followed by whitespace or end of
    string.  This is intentionally rough -- the ratchet thresholds account
    for imprecision.
    """
    # Remove front matter if present.
    stripped = text.lstrip("\n")
    if stripped.startswith("---"):
        end_idx = stripped.find("\n---", 3)
        if end_idx != -1:
            stripped = stripped[end_idx + 4 :]

    # Remove headings and blank lines.
    lines = [
        line
        for line in stripped.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    body = " ".join(lines)

    # Split on sentence boundaries.
    sentences = re.split(r"(?<=[.!?])\s+", body)
    return [s.strip() for s in sentences if s.strip()]


def _read_file_safe(path: Path) -> str | None:
    """Read a file, returning ``None`` on any error."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


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


# ---------------------------------------------------------------------------
# QualityRatchet
# ---------------------------------------------------------------------------


class QualityRatchet:
    """Post-compilation quality gate.

    Runs seven checks in sequence.  Checks marked BLOCK abort the compile
    if they fail; checks marked WARN are logged but do not block.

    The contradiction check requires an ``LLMRouter`` and ``PromptBuilder``
    and is skipped if neither is provided.
    """

    def __init__(
        self,
        grove_root: Path,
        router: Any | None = None,
        prompt_builder: Any | None = None,
    ) -> None:
        """Initialise the ratchet.

        Parameters
        ----------
        grove_root:
            The root directory of the grove project.
        router:
            An ``LLMRouter`` instance for contradiction detection.  If
            ``None``, the contradiction check is skipped.
        prompt_builder:
            A ``PromptBuilder`` instance for rendering the contradiction
            prompt.  If ``None``, the contradiction check is skipped.
        """
        self._grove_root = grove_root
        self._wiki_dir = grove_root / "wiki"
        self._router = router
        self._prompt_builder = prompt_builder

    def check(
        self,
        new_articles: list[ParsedArticle],
        source_paths: list[str] | None = None,
    ) -> RatchetResult:
        """Run all seven checks and return a ``RatchetResult``.

        Parameters
        ----------
        new_articles:
            The articles produced by this compilation.
        source_paths:
            Paths of source files used in this compilation.  Used by the
            query-article-as-source check.  If ``None``, that check is
            skipped.
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        blocking_failures: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}

        # 1. Provenance coverage
        prov = self._check_provenance_coverage(new_articles)
        details["provenance_coverage"] = prov
        if prov.get("severity") == "BLOCK":
            blocking_failures.append("provenance_coverage")
        elif prov.get("severity") == "WARN":
            warnings.append("provenance_coverage")

        # 2. New contradictions (optional -- requires router + prompt_builder)
        contra = self._check_contradictions(new_articles)
        details["contradictions"] = contra
        if contra.get("severity") == "BLOCK":
            blocking_failures.append("contradictions")

        # 3. Coverage drop
        cov = self._check_coverage_drop(new_articles)
        details["coverage_drop"] = cov
        if cov.get("severity") == "BLOCK":
            blocking_failures.append("coverage_drop")

        # 4. Broken wiki-links
        links = self._check_broken_wiki_links(new_articles)
        details["broken_wiki_links"] = links
        if links.get("severity") == "WARN":
            warnings.append("broken_wiki_links")

        # 5. Human annotation preservation
        human = self._check_human_annotation_preservation(new_articles)
        details["human_annotation_preservation"] = human
        if human.get("severity") == "BLOCK":
            blocking_failures.append("human_annotation_preservation")

        # 6. Pinned article overwrite
        pinned = self._check_pinned_article_overwrite(new_articles)
        details["pinned_article_overwrite"] = pinned
        if pinned.get("severity") == "BLOCK":
            blocking_failures.append("pinned_article_overwrite")

        # 7. Query article used as source
        query = self._check_query_article_as_source(source_paths)
        details["query_article_as_source"] = query
        if query.get("severity") == "BLOCK":
            blocking_failures.append("query_article_as_source")

        passed = len(blocking_failures) == 0

        result = RatchetResult(
            timestamp=timestamp,
            passed=passed,
            blocking_failures=blocking_failures,
            warnings=warnings,
            details=details,
        )

        if passed:
            logger.info("Quality ratchet passed (%d warnings).", len(warnings))
        else:
            logger.warning("Quality ratchet FAILED: %s", ", ".join(blocking_failures))

        return result

    def save_report(self, result: RatchetResult) -> Path:
        """Write a JSON report to ``.grove/logs/ratchet-<timestamp>.json``.

        Returns the path to the written file.
        """
        logs_dir = self._grove_root / ".grove" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Use the timestamp from the result, sanitised for filenames.
        safe_ts = result.timestamp.replace(":", "-")
        report_path = logs_dir / f"ratchet-{safe_ts}.json"

        report_data = result.model_dump()
        report_path.write_text(
            json.dumps(report_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        logger.info("Ratchet report written to %s", report_path)
        return report_path

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_provenance_coverage(
        self, articles: list[ParsedArticle]
    ) -> dict[str, Any]:
        """Check that factual sentences have ``[source:...]`` citations.

        Thresholds:
        - <50% → BLOCK
        - 50-89% → WARN
        - >=90% → PASS
        """
        if not articles:
            return {
                "score": 1.0,
                "threshold": 0.50,
                "articles_below": [],
                "severity": "PASS",
            }

        articles_below_block: list[str] = []
        articles_below_warn: list[str] = []
        total_factual = 0
        total_cited = 0

        for article in articles:
            sentences = _split_sentences(article.content)
            factual = [s for s in sentences if _FACTUAL_SENTENCE_RE.search(s)]
            cited = [s for s in factual if _SOURCE_CITATION_RE.search(s)]

            total_factual += len(factual)
            total_cited += len(cited)

            if factual:
                ratio = len(cited) / len(factual)
                if ratio < 0.50:
                    articles_below_block.append(article.file_path)
                elif ratio < 0.90:
                    articles_below_warn.append(article.file_path)

        overall_score = total_cited / total_factual if total_factual > 0 else 1.0

        if overall_score < 0.50:
            severity = "BLOCK"
        elif overall_score < 0.90:
            severity = "WARN"
        else:
            severity = "PASS"

        return {
            "score": round(overall_score, 4),
            "threshold": 0.50,
            "articles_below": articles_below_block + articles_below_warn,
            "severity": severity,
        }

    def _check_contradictions(self, articles: list[ParsedArticle]) -> dict[str, Any]:
        """Detect contradictions between article pairs sharing 2+ concepts.

        Uses the fast LLM tier via the router.  Skipped entirely if no
        router or prompt_builder is configured.
        """
        if self._router is None or self._prompt_builder is None:
            return {
                "skipped": True,
                "reason": "No LLM router or prompt builder provided.",
                "severity": "PASS",
            }

        if len(articles) < 2:
            return {"pairs_checked": 0, "contradictions": [], "severity": "PASS"}

        # Build concept index: concept -> list of article indices.
        concept_index: dict[str, list[int]] = {}
        for i, article in enumerate(articles):
            for concept in article.concepts:
                concept_index.setdefault(concept, []).append(i)

        # Find pairs sharing 2+ concepts.
        pair_set: set[tuple[int, int]] = set()
        for indices in concept_index.values():
            for a_idx in indices:
                for b_idx in indices:
                    if a_idx < b_idx:
                        pair_set.add((a_idx, b_idx))

        # Filter to pairs with 2+ shared concepts.
        pairs_to_check: list[tuple[int, int]] = []
        for a_idx, b_idx in pair_set:
            shared = set(articles[a_idx].concepts) & set(articles[b_idx].concepts)
            if len(shared) >= 2:
                pairs_to_check.append((a_idx, b_idx))

        if not pairs_to_check:
            return {"pairs_checked": 0, "contradictions": [], "severity": "PASS"}

        contradictions: list[dict[str, str]] = []

        for a_idx, b_idx in pairs_to_check:
            article_a = articles[a_idx]
            article_b = articles[b_idx]

            try:
                prompt_text = self._prompt_builder.build(
                    "contradiction.md",
                    article_a=article_a.content,
                    article_b=article_b.content,
                )
            except (FileNotFoundError, KeyError) as exc:
                logger.warning("Could not build contradiction prompt: %s", exc)
                continue

            from grove.llm.models import LLMRequest

            request = LLMRequest(
                prompt=prompt_text,
                tier="fast",
                task_type="contradiction_check",
                max_tokens=2048,
                temperature=0.0,
            )

            try:
                response = self._router.complete_sync(request)
            except Exception as exc:
                logger.warning(
                    "Contradiction check failed for %s vs %s: %s",
                    article_a.file_path,
                    article_b.file_path,
                    exc,
                )
                continue

            content = response.content.strip()
            if content.upper() != "NONE":
                contradictions.append(
                    {
                        "article_a": article_a.file_path,
                        "article_b": article_b.file_path,
                        "details": content,
                    }
                )

        severity = "BLOCK" if contradictions else "PASS"

        return {
            "pairs_checked": len(pairs_to_check),
            "contradictions": contradictions,
            "severity": severity,
        }

    def _check_coverage_drop(self, articles: list[ParsedArticle]) -> dict[str, Any]:
        """Compare ``compiled_from`` count against previous compilation.

        A drop of >10% triggers BLOCK.
        """
        # Count total compiled_from entries in the new articles.
        new_count = sum(len(a.compiled_from) for a in articles)

        # Read previous count from state.json.
        state_path = self._grove_root / ".grove" / "state.json"
        previous_count: int | None = None

        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                previous_count = state_data.get("last_compile_source_count")
            except (json.JSONDecodeError, OSError):
                pass

        if previous_count is None or previous_count == 0:
            return {
                "new_count": new_count,
                "previous_count": previous_count,
                "drop_pct": 0.0,
                "severity": "PASS",
            }

        drop_pct = (previous_count - new_count) / previous_count
        severity = "BLOCK" if drop_pct > 0.10 else "PASS"

        return {
            "new_count": new_count,
            "previous_count": previous_count,
            "drop_pct": round(drop_pct, 4),
            "severity": severity,
        }

    def _check_broken_wiki_links(self, articles: list[ParsedArticle]) -> dict[str, Any]:
        """Scan for ``[[article]]`` links that do not resolve to files.

        Broken links trigger WARN (auto-fixable).
        """
        broken_links: list[dict[str, str]] = []

        # Build a set of all known article paths (new + existing on disk).
        known_paths: set[str] = set()

        # Include new articles.
        for article in articles:
            known_paths.add(article.file_path)
            # Also include the filename without the wiki/ prefix and .md suffix,
            # as wiki-links often use just the slug.
            basename = Path(article.file_path).stem
            known_paths.add(basename)

        # Include existing wiki files on disk.
        if self._wiki_dir.exists():
            for md_file in self._wiki_dir.rglob("*.md"):
                rel_path = str(md_file.relative_to(self._grove_root))
                known_paths.add(rel_path)
                known_paths.add(md_file.stem)

        for article in articles:
            links = _WIKI_LINK_RE.findall(article.raw_body)
            for link_target in links:
                target = link_target.strip()
                # Normalise: remove .md suffix for comparison if present.
                target_normalised = target.removesuffix(".md")

                # Check various resolution strategies.
                resolved = (
                    target in known_paths
                    or target_normalised in known_paths
                    or f"wiki/{target}" in known_paths
                    or f"wiki/{target}.md" in known_paths
                )

                if not resolved:
                    broken_links.append(
                        {
                            "article": article.file_path,
                            "link": target,
                        }
                    )

        severity = "WARN" if broken_links else "PASS"

        return {
            "broken_links": broken_links,
            "count": len(broken_links),
            "severity": severity,
        }

    def _check_human_annotation_preservation(
        self, articles: list[ParsedArticle]
    ) -> dict[str, Any]:
        """Ensure ``<!-- grove:human -->`` blocks were not removed.

        Compares human blocks in the new article content against the
        existing file on disk.  If any block present in the existing file
        is missing from the new content, that is a BLOCK failure.
        """
        removed_blocks: list[dict[str, str]] = []

        for article in articles:
            existing_path = self._grove_root / article.file_path
            existing_content = _read_file_safe(existing_path)

            if existing_content is None:
                # New article -- nothing to compare against.
                continue

            existing_blocks = _HUMAN_BLOCK_RE.findall(existing_content)
            if not existing_blocks:
                continue

            new_content = article.content
            for block in existing_blocks:
                if block not in new_content:
                    removed_blocks.append(
                        {
                            "article": article.file_path,
                            "block_preview": block[:100],
                        }
                    )

        severity = "BLOCK" if removed_blocks else "PASS"

        return {
            "removed_blocks": removed_blocks,
            "count": len(removed_blocks),
            "severity": severity,
        }

    def _check_pinned_article_overwrite(
        self, articles: list[ParsedArticle]
    ) -> dict[str, Any]:
        """Check that ``pinned: true`` articles were not modified.

        This is a safety net -- the ArticleWriter should already prevent
        overwrites, but the ratchet catches any bypass.
        """
        overwritten: list[str] = []

        for article in articles:
            existing_path = self._grove_root / article.file_path
            existing_content = _read_file_safe(existing_path)

            if existing_content is None:
                continue

            meta = _parse_front_matter(existing_content)
            if meta is None:
                continue

            if meta.get("pinned") is not True:
                continue

            # The existing article is pinned.  If the new content differs,
            # it means the writer was bypassed.
            if article.content.strip() != existing_content.strip():
                overwritten.append(article.file_path)

        severity = "BLOCK" if overwritten else "PASS"

        return {
            "overwritten_pinned": overwritten,
            "count": len(overwritten),
            "severity": severity,
        }

    def _check_query_article_as_source(
        self, source_paths: list[str] | None
    ) -> dict[str, Any]:
        """Check that ``origin: query`` files were not used as sources.

        The SourceLoader should already filter these out, but the ratchet
        catches any bypass.
        """
        if source_paths is None:
            return {
                "skipped": True,
                "reason": "No source paths provided.",
                "severity": "PASS",
            }

        query_sources: list[str] = []

        for source_path in source_paths:
            full_path = self._grove_root / source_path
            content = _read_file_safe(full_path)

            if content is None:
                continue

            meta = _parse_front_matter(content)
            if meta is None:
                continue

            if meta.get("origin") == "query":
                query_sources.append(source_path)

        severity = "BLOCK" if query_sources else "PASS"

        return {
            "query_sources_found": query_sources,
            "count": len(query_sources),
            "severity": severity,
        }
