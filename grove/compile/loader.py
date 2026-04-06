"""SourceLoader -- loads raw sources with quality filtering and token budget.

Reads the manifest to discover all ingested sources, filters by quality
threshold, excludes ``origin: query`` files, and enforces the 800K token
budget.  Sources over 10K tokens use their ``grove_summary`` field instead
of full content.  Sources between 2K and 10K tokens fall back to summary
if the budget is exhausted.

The returned ``ContextPayload`` carries both the ordered source text and
per-source metadata (path, checksum, token count) for provenance tracking
in downstream compilation stages.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from grove.config.loader import GroveConfig
from grove.ingest.manifest import ManifestWriter

logger = logging.getLogger(__name__)

# Token budget defaults -- from ARCH.md section "Token budget management".
_DEFAULT_BUDGET = 800_000
_LARGE_SOURCE_THRESHOLD = 10_000
_MEDIUM_SOURCE_THRESHOLD = 2_000

# Quality grade ordering for threshold comparison.
_QUALITY_LEVELS: dict[str, int] = {
    "good": 2,
    "partial": 1,
    "poor": 0,
}


# ---------------------------------------------------------------------------
# Pydantic models for the loader output
# ---------------------------------------------------------------------------


class SourceEntry(BaseModel):
    """A single loaded source with content and provenance metadata."""

    path: str = Field(
        description="Relative path within the grove (e.g. 'raw/articles/foo.md')."
    )
    content: str = Field(description="Full text or summary, depending on token budget.")
    checksum: str = Field(description="SHA-256 of the original full content.")
    token_count: int = Field(
        description="Estimated tokens for this source's loaded content."
    )
    used_summary: bool = Field(
        description="True if the grove_summary was used instead of full text."
    )


class ContextPayload(BaseModel):
    """The complete set of sources loaded for a compilation pass."""

    sources: list[SourceEntry] = Field(default_factory=list)
    total_tokens: int = 0
    budget_limit: int = _DEFAULT_BUDGET
    sources_summarised: int = Field(
        default=0,
        description="Count of sources where summary was used instead of full text.",
    )
    sources_excluded: int = Field(
        default=0,
        description="Count of sources excluded by quality threshold or origin filter.",
    )


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Approximate token count using the word-count heuristic from ARCH.md.

    The multiplier of 1.3 accounts for sub-word tokenisation in modern
    LLMs.  This is intentionally simple -- precise counting would require
    a tokeniser dependency that is not justified at this stage.
    """
    return int(len(text.split()) * 1.3)


# ---------------------------------------------------------------------------
# Front matter parsing (reuses the same logic as the summariser)
# ---------------------------------------------------------------------------


def _split_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Split a markdown file into its YAML front matter dict and body.

    Returns ``({}, body)`` if no front matter is found.  The body is the
    content after the closing ``---`` delimiter.
    """
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


# ---------------------------------------------------------------------------
# SourceLoader
# ---------------------------------------------------------------------------


class SourceLoader:
    """Loads raw sources for compilation, respecting quality and token budget.

    The loader reads the manifest to get the list of ingested sources,
    filters out those below the configured quality threshold and those
    with ``origin: query`` in their front matter, and then loads content
    within the 800K token budget.

    Sources over 10K tokens use their ``grove_summary`` field.  Sources
    between 2K and 10K tokens use full text unless the budget would be
    exceeded, in which case they fall back to summary.

    Parameters
    ----------
    grove_root:
        Path to the grove knowledge base (the directory containing
        ``raw/`` and ``.grove/``).
    config:
        Validated ``GroveConfig`` from the config loader.
    """

    def __init__(self, grove_root: Path, config: GroveConfig) -> None:
        self._grove_root = grove_root
        self._config = config
        self._manifest = ManifestWriter(grove_root)
        self._quality_threshold = config.compile.quality_threshold
        self._budget = _DEFAULT_BUDGET

    def load_all(self) -> ContextPayload:
        """Load all sources from ``raw/`` respecting quality and budget.

        Returns a ``ContextPayload`` with ordered source text and
        per-source metadata.  Sources are loaded in manifest order
        (i.e. ingest order).

        Loading logic:
        1. Read manifest entries and filter by quality threshold.
        2. For each remaining source, read the file and check front matter.
        3. Skip files with ``origin: query`` (filed query answers).
        4. Apply token budget rules to decide full text vs. summary.
        5. If total exceeds budget even with summaries, stop loading.
        """
        entries = self._manifest.read()

        sources: list[SourceEntry] = []
        total_tokens = 0
        sources_summarised = 0
        sources_excluded = 0

        for entry in entries:
            # Step 1: Quality threshold filtering
            if not self._meets_quality_threshold(entry.quality):
                sources_excluded += 1
                logger.debug(
                    "Skipping %s: quality '%s' below threshold '%s'",
                    entry.source_path,
                    entry.quality,
                    self._quality_threshold,
                )
                continue

            # Step 2: Read the source file
            source_path = self._grove_root / entry.source_path
            if not source_path.exists():
                logger.warning(
                    "Source file not found, skipping: %s",
                    source_path,
                )
                sources_excluded += 1
                continue

            raw_text = source_path.read_text(encoding="utf-8")
            front_matter, body = _split_front_matter(raw_text)

            # Step 3: Exclude origin: query files
            if front_matter.get("origin") == "query":
                sources_excluded += 1
                logger.debug(
                    "Skipping %s: origin is 'query'",
                    entry.source_path,
                )
                continue

            # Compute checksum from the full original content (body after front matter)
            body_stripped = body.lstrip("\n")
            checksum = hashlib.sha256(body_stripped.encode("utf-8")).hexdigest()

            # Get the summary from front matter (may not exist)
            grove_summary = front_matter.get("grove_summary")
            summary_available = (
                isinstance(grove_summary, str) and len(grove_summary.strip()) > 0
            )

            # Step 4: Token budget management
            full_tokens = estimate_tokens(body_stripped)
            used_summary = False
            content: str

            if full_tokens > _LARGE_SOURCE_THRESHOLD:
                # Over 10K tokens: always use summary if available
                if summary_available:
                    content = grove_summary  # type: ignore[assignment]
                    used_summary = True
                else:
                    # No summary available -- load full text regardless
                    content = body_stripped
                    logger.warning(
                        "Source %s exceeds 10K tokens (%d) but has no summary; "
                        "loading full text",
                        entry.source_path,
                        full_tokens,
                    )
            elif full_tokens > _MEDIUM_SOURCE_THRESHOLD:
                # 2K-10K tokens: use full text unless budget is tight
                content_tokens = estimate_tokens(body_stripped)
                if total_tokens + content_tokens > self._budget and summary_available:
                    content = grove_summary  # type: ignore[assignment]
                    used_summary = True
                else:
                    content = body_stripped
            else:
                # Under 2K tokens: always load full text
                content = body_stripped

            content_tokens = estimate_tokens(content)

            # Step 5: Check budget before adding
            if total_tokens + content_tokens > self._budget:
                if used_summary or not summary_available:
                    # Already using summary or no summary available --
                    # cannot reduce further, stop loading
                    logger.warning(
                        "Token budget exhausted at %d tokens (budget: %d). "
                        "Stopping source loading at %s.",
                        total_tokens,
                        self._budget,
                        entry.source_path,
                    )
                    break
                else:
                    # Try falling back to summary
                    if summary_available:
                        content = grove_summary  # type: ignore[assignment]
                        used_summary = True
                        content_tokens = estimate_tokens(content)

                        if total_tokens + content_tokens > self._budget:
                            logger.warning(
                                "Token budget exhausted at %d tokens (budget: %d). "
                                "Stopping source loading at %s.",
                                total_tokens,
                                self._budget,
                                entry.source_path,
                            )
                            break

            total_tokens += content_tokens
            if used_summary:
                sources_summarised += 1

            sources.append(
                SourceEntry(
                    path=entry.source_path,
                    content=content,
                    checksum=checksum,
                    token_count=content_tokens,
                    used_summary=used_summary,
                )
            )

        return ContextPayload(
            sources=sources,
            total_tokens=total_tokens,
            budget_limit=self._budget,
            sources_summarised=sources_summarised,
            sources_excluded=sources_excluded,
        )

    def _meets_quality_threshold(self, quality: str) -> bool:
        """Check whether *quality* meets or exceeds the configured threshold.

        The threshold works as a minimum: if threshold is ``"partial"``,
        then ``"good"`` and ``"partial"`` pass but ``"poor"`` does not.
        If threshold is ``"poor"``, everything passes.  If threshold is
        ``"good"``, only ``"good"`` passes.
        """
        threshold_level = _QUALITY_LEVELS.get(self._quality_threshold, 0)
        quality_level = _QUALITY_LEVELS.get(quality, -1)
        return quality_level >= threshold_level
