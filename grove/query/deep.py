"""DeepQuery -- FTS5-powered query mode that loads full article content.

Searches the FTS5 index for the top-5 most relevant wiki articles,
loads their full content, and calls the standard LLM tier to synthesise
a comprehensive answer with citations and follow-up suggestions.

Falls back to loading all wiki articles (within a token budget) when
the search index does not exist.

See ARCH.md ``grove/query/`` table for the authoritative spec.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from grove.compile.prompt import PromptBuilder
from grove.llm.models import LLMRequest
from grove.llm.router import LLMRouter
from grove.query.models import QueryResult
from grove.query.quick import _parse_citations, _parse_follow_up_questions
from grove.search.fts import FTSIndex

logger = logging.getLogger(__name__)

# Maximum number of articles to retrieve from FTS5 search.
_TOP_K = 5

# Approximate token budget for the fallback path (no search index).
# Used to cap the total article content loaded when FTS5 is unavailable.
_FALLBACK_TOKEN_BUDGET = 100_000

# Rough tokens-per-word multiplier for budget estimation.
_TOKENS_PER_WORD = 1.3


class DeepQuery:
    """Deep query mode that loads full article content for synthesis.

    Uses FTS5 keyword search to identify the top-5 most relevant wiki
    articles, loads their full content alongside the wiki index, and
    calls the standard LLM tier for a thorough answer.

    When the search index does not exist, falls back to loading all
    wiki articles up to a ~100K token budget.

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
        """Answer *question* using full article content from the wiki.

        1. Search FTS5 index for top-5 articles matching the question.
        2. Load ``wiki/_index.md`` for overview context.
        3. Load full content of the top-5 matching articles.
        4. Build prompt using the ``query.md`` template.
        5. Call the standard LLM tier (``task_type="query_deep"``).
        6. Parse ``[wiki: ...]`` citations and follow-up questions.
        7. Return a ``QueryResult``.

        If the FTS5 index does not exist, falls back to loading all
        wiki articles within a ~100K token budget.  If the wiki is
        empty, returns a helpful error message.
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # -- Step 1: Load wiki index for overview context ------------------
        wiki_index = self._load_file("wiki/_index.md")

        # -- Step 2: Find and load relevant articles -----------------------
        articles_text = self._load_articles(question)

        if not wiki_index and not articles_text:
            return QueryResult(
                question=question,
                answer=(
                    "No wiki compiled yet. Run `grove compile` first "
                    "to generate the wiki and search index."
                ),
                mode="deep",
                citations=[],
                follow_up_questions=[],
                model_used="",
                tokens_used=0,
                cost_usd=0.0,
                timestamp=timestamp,
            )

        # -- Step 3: Build prompt ------------------------------------------
        prompt_text = self._prompt_builder.build(
            "query.md",
            question=question,
            wiki_index=wiki_index or "(no index available)",
            articles=articles_text or "(no articles found)",
        )

        # -- Step 4: Call standard LLM tier --------------------------------
        request = LLMRequest(
            prompt=prompt_text,
            tier="standard",
            task_type="query_deep",
            max_tokens=4096,
            temperature=0.0,
        )

        response = self._router.complete_sync(request)

        # -- Step 5: Parse citations and follow-ups ------------------------
        citations = _parse_citations(response.content)
        follow_ups = _parse_follow_up_questions(response.content)

        # -- Step 6: Return QueryResult ------------------------------------
        return QueryResult(
            question=question,
            answer=response.content,
            mode="deep",
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

    def _load_articles(self, question: str) -> str:
        """Load article content, using FTS5 search or fallback.

        Returns the concatenated article content as a single string,
        with each article prefixed by its path for citation purposes.
        """
        db_path = self._grove_root / ".grove" / "search.db"

        if db_path.exists():
            return self._load_articles_via_fts(question, db_path)

        logger.info(
            "Search index not found at %s; falling back to loading "
            "all wiki articles within token budget.",
            db_path,
        )
        return self._load_articles_fallback()

    def _load_articles_via_fts(self, question: str, db_path: Path) -> str:
        """Use FTS5 to find top-K articles and load their full content."""
        fts = FTSIndex(db_path)
        results = fts.search(question, limit=_TOP_K)

        if not results:
            # FTS returned nothing; fall back to loading all articles.
            logger.info(
                "FTS5 search returned no results; falling back to all articles."
            )
            return self._load_articles_fallback()

        sections: list[str] = []
        for result in results:
            # The article_path from FTS is relative to the grove root
            # (e.g. "wiki/topics/overview.md").
            full_path = self._grove_root / result.article_path
            content = self._read_file(full_path)
            if content:
                sections.append(f"### [{result.article_path}]\n\n{content}")

        return "\n\n---\n\n".join(sections)

    def _load_articles_fallback(self) -> str:
        """Load all wiki articles up to the token budget.

        Used when the FTS5 search index does not exist.  Walks the
        ``wiki/`` directory, skipping index files (``_index.md``,
        ``_concepts.md``), and accumulates content until the budget
        is exhausted.
        """
        wiki_root = self._grove_root / "wiki"
        if not wiki_root.is_dir():
            return ""

        # Skip internal index files -- they are loaded separately.
        skip_names = {"_index.md", "_concepts.md"}
        budget_remaining = _FALLBACK_TOKEN_BUDGET

        sections: list[str] = []
        for md_file in sorted(wiki_root.rglob("*.md")):
            if md_file.name in skip_names:
                continue

            content = self._read_file(md_file)
            if not content:
                continue

            # Estimate tokens for this file.
            estimated_tokens = int(len(content.split()) * _TOKENS_PER_WORD)
            if estimated_tokens > budget_remaining:
                logger.info(
                    "Token budget exhausted; loaded %d articles.",
                    len(sections),
                )
                break

            rel_path = str(md_file.relative_to(self._grove_root))
            sections.append(f"### [{rel_path}]\n\n{content}")
            budget_remaining -= estimated_tokens

        return "\n\n---\n\n".join(sections)

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

    def _read_file(self, path: Path) -> str:
        """Read a file by absolute path, returning empty string on failure."""
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return ""
