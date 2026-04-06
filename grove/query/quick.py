"""QuickQuery -- fast, lightweight query mode using index files only.

Searches ``wiki/_index.md`` and ``wiki/_concepts.md`` without loading
any full articles.  Uses the fast LLM tier for speed and cost
efficiency.  Designed to complete in under 5 seconds.

See ARCH.md ``grove/query/`` table for the authoritative spec.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from grove.compile.prompt import PromptBuilder
from grove.llm.models import LLMRequest
from grove.llm.router import LLMRouter
from grove.query.models import QueryResult

logger = logging.getLogger(__name__)

# Regex to extract [wiki: path.md] citations from LLM output.
_CITATION_PATTERN = re.compile(r"\[wiki:\s*([^\]]+)\]")

# Regex to extract numbered follow-up questions (e.g. "1. How does...")
_FOLLOWUP_PATTERN = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)


class QuickQuery:
    """Fast query mode that consults only the wiki index and concept graph.

    This avoids loading full articles, keeping the context window small
    and the response time under 5 seconds.  Uses the fast LLM tier.

    Parameters
    ----------
    grove_root:
        Path to the grove project root (contains ``wiki/``).
    router:
        ``LLMRouter`` for making LLM calls.
    prompt_builder:
        ``PromptBuilder`` for rendering the ``query.md`` template.
    """

    def __init__(
        self,
        grove_root: Path,
        router: LLMRouter,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._grove_root = grove_root
        self._router = router
        self._prompt_builder = prompt_builder

    def query(self, question: str) -> QueryResult:
        """Answer *question* using only the wiki index and concept graph.

        1. Load ``wiki/_index.md`` and ``wiki/_concepts.md`` (if they exist).
        2. Build prompt using the ``query.md`` template.
        3. Call the fast LLM tier (``task_type="query_quick"``).
        4. Parse ``[wiki: ...]`` citations from the response.
        5. Parse follow-up questions (numbered list at end).
        6. Return a ``QueryResult``.

        If neither index file exists, returns a helpful error message
        advising the user to run ``grove compile`` first.
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # -- Step 1: Load index files -----------------------------------
        wiki_index = self._load_file("wiki/_index.md")
        concepts = self._load_file("wiki/_concepts.md")

        if not wiki_index and not concepts:
            return QueryResult(
                question=question,
                answer=(
                    "No wiki compiled yet. Run `grove compile` first "
                    "to generate the wiki index and concept graph."
                ),
                mode="quick",
                citations=[],
                follow_up_questions=[],
                model_used="",
                tokens_used=0,
                cost_usd=0.0,
                timestamp=timestamp,
            )

        # -- Step 2: Build prompt ---------------------------------------
        prompt_text = self._prompt_builder.build(
            "query.md",
            question=question,
            wiki_index=wiki_index or "(no index available)",
            articles=concepts or "(no concept graph available)",
        )

        # -- Step 3: Call fast LLM tier ---------------------------------
        request = LLMRequest(
            prompt=prompt_text,
            tier="fast",
            task_type="query_quick",
            max_tokens=2048,
            temperature=0.0,
        )

        response = self._router.complete_sync(request)

        # -- Step 4: Parse citations ------------------------------------
        citations = _parse_citations(response.content)

        # -- Step 5: Parse follow-up questions --------------------------
        follow_ups = _parse_follow_up_questions(response.content)

        # -- Step 6: Return QueryResult ---------------------------------
        return QueryResult(
            question=question,
            answer=response.content,
            mode="quick",
            citations=citations,
            follow_up_questions=follow_ups,
            model_used=response.model,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=response.cost_usd,
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_file(self, relative_path: str) -> str:
        """Read a file relative to the grove root, returning empty string on failure."""
        path = self._grove_root / relative_path
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return ""


def _parse_citations(text: str) -> list[str]:
    """Extract unique ``[wiki: path.md]`` citations from LLM output.

    Returns a deduplicated list preserving first-occurrence order.
    """
    seen: set[str] = set()
    citations: list[str] = []

    for match in _CITATION_PATTERN.finditer(text):
        path = match.group(1).strip()
        if path not in seen:
            seen.add(path)
            citations.append(path)

    return citations


def _parse_follow_up_questions(text: str) -> list[str]:
    """Extract follow-up questions from the end of the LLM response.

    Looks for a numbered list (``1. ...``, ``2. ...``, ``3. ...``) in
    the last section of the response -- typically after a "Follow-up
    questions" heading.  Returns up to 3 questions.
    """
    # Focus on the last section of the response to avoid picking up
    # numbered items from the main answer body.
    sections = re.split(r"\n#{1,4}\s+", text)
    last_section = sections[-1] if sections else text

    questions: list[str] = []
    for match in _FOLLOWUP_PATTERN.finditer(last_section):
        question = match.group(1).strip()
        if question:
            questions.append(question)
        if len(questions) >= 3:
            break

    return questions
