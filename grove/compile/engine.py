"""CompileEngine -- orchestrates Phase 0 brute-force compilation.

Wires together the full pipeline:

    SourceLoader.load_all()
      -> PromptBuilder.build_compile_prompt()
      -> LLMRouter.call(tier="standard", ...)
      -> ArticleParser.parse(response)
      -> ArticleWriter.write_all(articles)
      -> QualityRatchet.check()
      -> AutoCommitter.commit_compile()

In dry-run mode, loads sources and estimates token count and cost
without making an LLM call or modifying the filesystem.

The ``progress_callback`` parameter allows callers (including the CLI's
NDJSON emitter) to receive ``(step, pct, detail)`` tuples at each stage.

See ARCH.md "Phase 0 compilation" for the authoritative spec.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from grove.compile.loader import SourceLoader, estimate_tokens
from grove.compile.parser import ArticleParser
from grove.compile.prompt import PromptBuilder
from grove.compile.ratchet import QualityRatchet, RatchetResult
from grove.compile.writer import ArticleWriter
from grove.config.loader import GroveConfig
from grove.config.state import StateManager
from grove.git.auto_commit import AutoCommitter
from grove.llm.cost import CostTracker
from grove.llm.models import LLMRequest
from grove.llm.router import LLMRouter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class CompileResult(BaseModel):
    """Outcome of a compilation run (full or dry-run)."""

    articles_created: int = 0
    articles_updated: int = 0
    articles_skipped_pinned: int = 0
    human_blocks_preserved: int = 0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    cost_usd: float = 0.0
    ratchet_passed: bool = True
    ratchet_warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False
    estimated_tokens: int | None = None
    estimated_cost: float | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CompileError(Exception):
    """Raised when the compilation pipeline encounters an unrecoverable error."""


class NoSourcesError(CompileError):
    """Raised when there are no sources to compile."""


class RatchetFailedError(CompileError):
    """Raised when the quality ratchet blocks the commit."""

    def __init__(self, result: RatchetResult) -> None:
        self.result = result
        failures = ", ".join(result.blocking_failures)
        super().__init__(f"Quality ratchet blocked commit: {failures}")


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, int, str], None]


# ---------------------------------------------------------------------------
# CompileEngine
# ---------------------------------------------------------------------------


class CompileEngine:
    """Orchestrates Phase 0 brute-force compilation.

    Phase 0 loads all sources into a single prompt, makes one LLM call
    to produce the entire wiki, then validates via the quality ratchet
    before committing.

    Parameters
    ----------
    grove_root:
        Path to the grove project root.
    config:
        Validated ``GroveConfig``.
    router:
        ``LLMRouter`` for making LLM calls and recording costs.
    prompt_builder:
        ``PromptBuilder`` for rendering the compilation prompt.
    """

    def __init__(
        self,
        grove_root: Path,
        config: GroveConfig,
        router: LLMRouter,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._grove_root = grove_root
        self._config = config
        self._router = router
        self._prompt_builder = prompt_builder

    def compile(
        self,
        dry_run: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> CompileResult:
        """Run the full Phase 0 compilation pipeline.

        If *dry_run* is True, loads sources and estimates token count
        and cost without making an LLM call or writing to the filesystem.

        *progress_callback* receives ``(step, pct, detail)`` for each
        pipeline stage, enabling NDJSON progress output from the CLI.

        Raises
        ------
        NoSourcesError
            If no sources are available for compilation.
        RatchetFailedError
            If the quality ratchet blocks the commit.
        CompileError
            For other unrecoverable errors.
        """
        cb = progress_callback or (lambda step, pct, detail: None)

        # -- Step 1: Load sources ----------------------------------------
        cb("loading_sources", 10, "Loading raw sources")
        loader = SourceLoader(self._grove_root, self._config)
        payload = loader.load_all()

        if not payload.sources:
            raise NoSourcesError(
                "No sources found to compile. Ingest some documents first."
            )

        # Build the combined source text with path markers for provenance.
        source_lines: list[str] = []
        for entry in payload.sources:
            source_lines.append(f"<!-- source:{entry.path} -->\n{entry.content}\n")
        sources_text = "\n".join(source_lines)

        # -- Step 2: Load existing wiki for recompilation context --------
        cb("building_prompt", 20, "Building compilation prompt")
        existing_wiki = self._load_existing_wiki()
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build the full prompt using the compile-wiki template.
        prompt_text = self._prompt_builder.build(
            "compile-wiki.md",
            sources=sources_text,
            existing_wiki=existing_wiki,
            timestamp=timestamp,
        )

        # -- Dry run: estimate and return --------------------------------
        if dry_run:
            prompt_tokens = estimate_tokens(prompt_text)
            max_output = self._config.compile.max_output_tokens

            # Resolve model for cost estimation.
            tier_config = self._config.llm.routing.standard
            model_name = tier_config.model

            estimated_cost = CostTracker.estimate_cost(
                model_name, prompt_tokens, max_output
            )

            return CompileResult(
                dry_run=True,
                estimated_tokens=prompt_tokens,
                estimated_cost=estimated_cost,
                total_tokens_input=prompt_tokens,
            )

        # -- Step 3: LLM call -------------------------------------------
        cb("llm_call", 40, "Calling LLM (standard tier)")
        max_output_tokens = self._config.compile.max_output_tokens

        request = LLMRequest(
            prompt=prompt_text,
            tier="standard",
            task_type="compile",
            max_tokens=max_output_tokens,
            temperature=0.0,
        )

        response = self._router.complete_sync(request)

        # -- Step 4: Parse articles --------------------------------------
        cb("parsing_articles", 60, "Parsing LLM output into articles")
        parser = ArticleParser()
        articles = parser.parse(response.content)

        if not articles:
            raise CompileError(
                "LLM returned no parseable articles. "
                "The response may be malformed or truncated."
            )

        # Log parse warnings.
        for article in articles:
            for warning in article.warnings:
                logger.warning("Parse warning for %s: %s", article.file_path, warning)

        # -- Step 5: Write articles atomically ---------------------------
        cb("writing_articles", 70, "Writing articles to wiki/")

        # Snapshot existing wiki paths BEFORE writing so we can distinguish
        # newly created articles from updates.
        existing_wiki_paths = self._get_existing_wiki_paths()

        writer = ArticleWriter(self._grove_root)
        write_result = writer.write_all(articles)

        # Count created vs. updated.  The writer skips pinned articles, so
        # we count only the ones that were actually written
        # (write_result.articles_written).  A written article is an "update"
        # if its path existed before the write, otherwise it is "created".
        # We also build the list of written articles for the ratchet --
        # pinned-skipped articles must be excluded because the ratchet's
        # pinned-overwrite check compares parsed content against disk and
        # would false-positive on articles the writer deliberately skipped.
        articles_created = 0
        articles_updated = 0
        written_articles: list[object] = []
        for article in articles:
            target_path = self._grove_root / article.file_path
            # Pinned articles were skipped by the writer: the file still
            # exists on disk but with its original pinned content, not
            # the LLM's new content.  Detect skips by checking whether
            # the file path was in existing_wiki_paths AND is pinned.
            if article.file_path in existing_wiki_paths:
                # Read the file on disk to check if it matches the LLM output.
                # If it differs, the writer skipped it (pinned).
                disk_content = (self._grove_root / article.file_path).read_text(
                    encoding="utf-8"
                )
                if disk_content.strip() != article.content.strip():
                    # Writer skipped this article (pinned or write failure).
                    continue
                articles_updated += 1
            else:
                if not target_path.exists():
                    # Article was not written at all.
                    continue
                articles_created += 1
            written_articles.append(article)

        # -- Step 6: Quality ratchet -------------------------------------
        cb("quality_ratchet", 80, "Running quality checks")
        ratchet = QualityRatchet(
            self._grove_root,
            router=self._router,
            prompt_builder=self._prompt_builder,
        )
        source_paths = [entry.path for entry in payload.sources]
        ratchet_result = ratchet.check(written_articles, source_paths=source_paths)
        ratchet.save_report(ratchet_result)

        if not ratchet_result.passed:
            raise RatchetFailedError(ratchet_result)

        # -- Step 7: Git commit ------------------------------------------
        cb("git_commit", 90, "Committing to git")
        if self._config.git.auto_commit:
            try:
                committer = AutoCommitter(self._grove_root)
                if committer.has_changes():
                    committer.commit_compile(
                        articles_created=articles_created,
                        articles_updated=articles_updated,
                        cost_usd=response.cost_usd,
                    )
            except Exception as exc:
                logger.warning("Git commit failed (wiki is still updated): %s", exc)

        # -- Step 8: Rebuild search index ----------------------------------
        cb("search_index", 95, "Rebuilding search index")
        try:
            from grove.search.fts import FTSIndex

            fts = FTSIndex(self._grove_root / ".grove" / "search.db")
            chunks_indexed = fts.build(self._grove_root / "wiki")
            logger.info("Search index rebuilt: %d chunks", chunks_indexed)
        except Exception as exc:
            logger.warning("Search index rebuild failed: %s", exc)

        # -- Step 9: Update state.json -----------------------------------
        state = StateManager(self._grove_root)
        total_compiled_from = sum(len(a.compiled_from) for a in articles)
        state.set("last_compile_source_count", total_compiled_from)
        state.set("last_compile_timestamp", timestamp)

        return CompileResult(
            articles_created=articles_created,
            articles_updated=articles_updated,
            articles_skipped_pinned=write_result.articles_skipped_pinned,
            human_blocks_preserved=write_result.human_blocks_preserved,
            total_tokens_input=response.input_tokens,
            total_tokens_output=response.output_tokens,
            cost_usd=response.cost_usd,
            ratchet_passed=ratchet_result.passed,
            ratchet_warnings=ratchet_result.warnings,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_existing_wiki(self) -> str:
        """Load all existing wiki articles as a single string.

        Concatenates each ``.md`` file in ``wiki/`` with path markers
        so the LLM has context about what already exists.  Returns an
        empty string if the wiki directory is empty or does not exist.
        """
        wiki_dir = self._grove_root / "wiki"
        if not wiki_dir.exists():
            return ""

        parts: list[str] = []
        for md_file in sorted(wiki_dir.rglob("*.md")):
            rel_path = md_file.relative_to(self._grove_root)
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not read wiki file %s: %s", md_file, exc)
                continue
            parts.append(f"<!-- wiki:file {rel_path} -->\n{content}\n")

        return "\n".join(parts)

    def _get_existing_wiki_paths(self) -> set[str]:
        """Return the set of relative wiki article paths currently on disk.

        Used to distinguish newly created articles from updates.
        """
        wiki_dir = self._grove_root / "wiki"
        if not wiki_dir.exists():
            return set()

        paths: set[str] = set()
        for md_file in wiki_dir.rglob("*.md"):
            rel_path = str(md_file.relative_to(self._grove_root))
            paths.add(rel_path)

        return paths
