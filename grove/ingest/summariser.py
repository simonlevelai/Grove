"""Summariser -- calls the fast LLM tier to produce summaries and key concepts.

Each ingested source gets a ~150-word summary and up to 10 key concepts.
Results are written as YAML front matter fields (``grove_summary``,
``grove_concepts``) into the source's raw markdown file.  If the LLM
call fails after the router's built-in retry/fallback, the source is
marked ``unsummarised: true`` and the pipeline continues without blocking.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from grove.compile.prompt import PromptBuilder
from grove.llm.models import LLMRequest
from grove.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# Maximum number of key concepts to keep from the LLM response.
_MAX_CONCEPTS = 10


class SummaryResult(BaseModel):
    """Output of a summarisation attempt on a single source."""

    summary: str = ""
    concepts: list[str] = Field(default_factory=list)
    unsummarised: bool = False
    error: str | None = None


class Summariser:
    """Produces summaries and key concepts for ingested sources.

    Uses the fast LLM tier via *router* and the ``summarise.md`` prompt
    via *prompt_builder*.  Failures are absorbed gracefully so the ingest
    pipeline is never blocked by a single source.
    """

    def __init__(self, router: LLMRouter, prompt_builder: PromptBuilder) -> None:
        self._router = router
        self._prompt_builder = prompt_builder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarise(self, source_path: Path, content: str) -> SummaryResult:
        """Call the fast LLM to summarise *content*.

        The prompt is built from ``summarise.md`` via PromptBuilder.
        The LLM response is expected to be YAML with ``summary`` and
        ``concepts`` fields.  On any failure the result is returned
        with ``unsummarised=True`` rather than raising.
        """
        try:
            prompt_text = self._prompt_builder.build("summarise.md", source=content)

            request = LLMRequest(
                prompt=prompt_text,
                tier="fast",
                task_type="ingest_summary",
                temperature=0.0,
                max_tokens=1024,
            )

            response = self._router.complete_sync(request)
            return self._parse_response(response.content)

        except Exception as exc:  # noqa: BLE001  — intentionally broad
            logger.warning(
                "Summarisation failed for %s: %s",
                source_path,
                exc,
            )
            return SummaryResult(
                unsummarised=True,
                error=str(exc),
            )

    def write_front_matter(self, source_path: Path, result: SummaryResult) -> None:
        """Write grove fields into the source file's YAML front matter.

        If the file already has ``---`` delimited front matter, the grove
        fields are merged into it (preserving any existing non-grove
        fields).  If there is no front matter, a new block is prepended.

        When the result is unsummarised, writes ``unsummarised: true``
        instead of summary/concepts so downstream stages know the source
        was not summarised.
        """
        text = source_path.read_text(encoding="utf-8")
        existing_meta, body = _split_front_matter(text)

        # Build the fields to inject
        if result.unsummarised:
            existing_meta["unsummarised"] = True
        else:
            existing_meta["grove_summary"] = result.summary
            existing_meta["grove_concepts"] = result.concepts
            # Remove stale unsummarised flag if present
            existing_meta.pop("unsummarised", None)

        front_matter_str = yaml.dump(
            existing_meta,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).rstrip("\n")

        output = f"---\n{front_matter_str}\n---\n{body}"
        source_path.write_text(output, encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> SummaryResult:
        """Parse the YAML response from the LLM into a SummaryResult.

        Handles common LLM quirks: markdown code fences around the YAML,
        extra whitespace, and missing fields.
        """
        cleaned = _strip_code_fences(raw).strip()

        try:
            data = yaml.safe_load(cleaned)
        except yaml.YAMLError as exc:
            logger.warning("Failed to parse LLM YAML response: %s", exc)
            return SummaryResult(
                unsummarised=True,
                error=f"YAML parse error: {exc}",
            )

        if not isinstance(data, dict):
            return SummaryResult(
                unsummarised=True,
                error=f"Expected YAML mapping, got {type(data).__name__}",
            )

        summary = data.get("summary", "")
        if not isinstance(summary, str):
            summary = str(summary)

        raw_concepts = data.get("concepts", [])
        if not isinstance(raw_concepts, list):
            raw_concepts = []

        concepts = [str(c) for c in raw_concepts[:_MAX_CONCEPTS]]

        return SummaryResult(summary=summary, concepts=concepts)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap around YAML."""
    lines = text.strip().splitlines()

    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    return "\n".join(lines)


def _split_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Split a markdown file into its YAML front matter dict and body.

    Returns ``({}, body)`` if no front matter is found.  The body
    always starts with ``\\n`` or is empty so that reassembly is clean.
    """
    if not text.startswith("---"):
        # No front matter -- body is the entire file
        body = f"\n{text}" if text else ""
        return {}, body

    # Find the closing --- (must be on its own line after the opening one)
    end_idx = text.find("\n---", 3)
    if end_idx == -1:
        # Opening --- but no closing --- -- treat as no front matter
        body = f"\n{text}" if text else ""
        return {}, body

    yaml_block = text[4:end_idx]  # skip the opening ---\n
    # Body starts after the closing ---\n
    body = text[end_idx + 4 :]  # skip \n---
    if not body.startswith("\n"):
        body = f"\n{body}"

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        meta = None

    if not isinstance(meta, dict):
        meta = {}

    return meta, body
