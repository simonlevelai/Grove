# TASKS.md — Grove Phase 1
**Version:** 0.1
**Date:** 2026-04-03
**Horizon:** Phase 1 — 6–8 weeks for 1 developer

---

## Status Key
- [ ] Not started
- [~] In progress
- [x] Complete
- [!] Blocked

## SP Scale
| SP | Time |
|----|------|
| 0.5 | 1 hour |
| 1 | Half a day |
| 2 | 1 day |
| 3 | 1.5 days |
| 5 | 3 days |
| 8 | 4–5 days (full week) |

## Tags
- [FE] frontend only (plugin, HTMX UI)
- [BE] backend only (Python engine, CLI)
- [FE+BE] both

---

## Epic 1 — Project Scaffold and Tooling

### TASK-001 — Python package scaffold
**Description:** Initialise the Python package with `pyproject.toml` (Hatch build backend, `grove-kb` package name, entry point `grove = "grove.cli:app"`), the `grove/` source directory with an empty Typer app in `cli.py`, a `tests/` directory with pytest config, and Ruff + Black config. `pip install -e ".[dev]"` must succeed and `grove --help` must print a usage line without errors.
**Depends on:** none
**SP:** 0.5
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-002 — Obsidian plugin scaffold
**Description:** Initialise the TypeScript Obsidian plugin in `obsidian-plugin/` with `manifest.json`, `package.json` (esbuild bundler, Obsidian API dev dependencies), `tsconfig.json` (strict mode), `esbuild.config.mjs`, and a minimal `src/main.ts` that registers as an Obsidian plugin. Include `npm run dev` (watch) and `npm run build` scripts. The built `main.js` must load in Obsidian without console errors.
**Depends on:** none
**SP:** 0.5
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [FE]

---

### TASK-003 — Config module and grove init command
**Description:** Implement `grove/config/` — ConfigLoader (reads/validates `.grove/config.yaml` via Pydantic), StateManager (reads/writes `.grove/state.json` for checksums and compile history), and Defaults (default config values). Implement the `grove init` CLI command: creates the full directory structure (`.grove/`, `raw/`, `wiki/`, `queries/`, `outputs/`), writes `config.yaml` from defaults, initialises a git repo, detects local Ollama, and prompts for an Anthropic API key if not in environment.
**Depends on:** TASK-001
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Epic 2 — Ingest Pipeline

### TASK-004 — LLM router and provider abstraction
**Description:** Implement `grove/llm/` — LLMRouter (selects provider + model by tier: fast/standard/powerful), AnthropicProvider (calls Anthropic API with retry + exponential backoff), OllamaProvider (calls local Ollama, detects availability at startup), CostTracker (records token usage per task type to `.grove/logs/costs.jsonl`), and Pydantic LLMRequest/LLMResponse models. All providers implement the same interface. Router handles fallback gracefully when Ollama is unreachable.
**Depends on:** TASK-003
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-005 — PDF and HTML conversion
**Description:** Implement `grove/ingest/converter.py` (Converter: dispatches by MIME type), `pdf.py` (PDFConverter: pymupdf4llm primary, pdfminer.six fallback), `html.py` (HTMLConverter: readability-lxml then markdownify), and `text.py` (TextConverter: pass-through with line ending normalisation). Each converter returns a ConversionResult (markdown string + metadata including conversion method used). Add test fixtures (`tests/fixtures/sample.pdf`, `sample.html`, `sample.md`) and passing unit tests for all three converters.
**Depends on:** TASK-001
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-006 — Quality scoring and deduplication
**Description:** Implement `grove/ingest/quality.py` (QualityScorer: scores conversion output as `good | partial | poor` based on word count, heading structure, and conversion method) and `grove/ingest/dedup.py` (Deduplicator: SHA-256 checksum comparison against `state.json` to detect exact duplicates). Unit tests cover all three quality grades, the pdfminer fallback triggering `partial`, and duplicate detection.
**Depends on:** TASK-005
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-007 — Ingest summariser
**Description:** Implement `grove/ingest/summariser.py` (Summariser). Calls the fast LLM tier to produce a 150-word summary and up to 10 key concepts for each ingested source. Output is stored as YAML front matter fields (`grove_summary`, `grove_concepts`) in the source's raw markdown file. Summariser handles API errors gracefully (retry × 2, then mark as `unsummarised: true` and continue). Unit tests mock the LLM call.
**Depends on:** TASK-004, TASK-005
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-008 — Manifest writer and grove ingest command
**Description:** Implement `grove/ingest/manifest.py` (ManifestWriter: reads/writes `raw/_manifest.md` as a YAML-fronted markdown table) and the full `grove ingest <path>` and `grove ingest-dir <path>` CLI commands. Ingest wires the full pipeline: convert → score → dedup → summarise → manifest update → state.json update. `grove ingest-dir` continues on individual file failures and prints a summary report. URL ingestion downloads HTML; file ingestion detects MIME type.
**Depends on:** TASK-006, TASK-007
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Epic 3 — Compilation Engine (Phase 0)

### TASK-009 — Source loader with token budget management
**Description:** Implement `grove/compile/loader.py` (SourceLoader). Loads all raw sources respecting the configured quality threshold (skip `poor` by default). Enforces an 800K token budget: sources over 10K tokens use their `grove_summary` field instead of full content. Excludes any file with `origin: query` in YAML front matter. Returns a ContextPayload with ordered source text and per-source metadata (path, checksum, token count) for provenance tracking downstream.
**Depends on:** TASK-008
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-010 — Prompt management system
**Description:** Implement `grove/compile/prompt.py` (PromptBuilder). Ships four default prompt files in `prompts/` (`compile-wiki.md`, `query.md`, `summarise.md`, `contradiction.md`). PromptBuilder loads the shipped default and merges with a user override from `.grove/prompts/` if present (user file wins). Prompts use Python `string.Template` variables (`$sources`, `$existing_wiki`, `$question`). Note: the actual prompt content is written in TASK-030.
**Depends on:** TASK-003
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-011 — Article parser
**Description:** Implement `grove/compile/parser.py` (ArticleParser). Splits the LLM response by `<!-- grove:article wiki/path/to/file.md -->` markers (see ARCH.md "Article Output Format Contract"). Extracts YAML front matter per article. Validates required front matter fields: `title`, `compiled_from`, `concepts`, `summary`, `last_compiled`. Returns a list of ParsedArticle objects (file path, content, metadata). Handles: missing separators (fall back to `---` boundary detection), missing fields (fill defaults + flag), truncated output (discard incomplete last article). Parser must never raise an unhandled exception. Unit tests cover clean, malformed, and truncated responses.
**Depends on:** TASK-010
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-012 — Article writer with human annotation preservation
**Description:** Implement `grove/compile/writer.py` (ArticleWriter). Writes articles to `wiki/`, creating subdirectories as needed. Must enforce three invariants: (1) extract `<!-- grove:human -->` blocks from the existing article before write and re-inject at same position after write, (2) skip any article with `pinned: true` in front matter without modification, (3) write to a temp directory first then move atomically so the wiki is never in a partial state. Unit tests verify all three protection rules with adversarial inputs.
**Depends on:** TASK-011
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-013 — Quality ratchet
**Description:** Implement `grove/compile/ratchet.py` (QualityRatchet). Runs seven checks in sequence — provenance coverage (BLOCK <50%), new contradictions (BLOCK), coverage drop >10% (BLOCK), broken wiki-links (WARN), human annotation preservation (BLOCK), pinned article overwrite (BLOCK), query article used as source (BLOCK). Contradiction detection calls the fast LLM tier on article pairs sharing concepts. Outputs a structured JSON report to `.grove/logs/ratchet-<timestamp>.json`. Returns a RatchetResult with pass/fail and per-check details.
**Depends on:** TASK-012, TASK-004
**SP:** 3
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-014 — Git automation module
**Description:** Implement `grove/git/` — AutoCommitter (stages `wiki/`, commits with `grove: compile — N articles` message), CompileLog (reads git log, filters grove: commits, returns structured GroveCommit list), RollbackManager (`grove rollback` uses `git revert`; `grove rollback --to <sha>` uses `git checkout <sha> -- wiki/` then new commit), and CompileDiff (article-level additions/removals between two grove commits). All operations use `gitpython`. Unit tests use a temporary git repo created in a temp directory.
**Depends on:** TASK-012
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-015 — Compile engine orchestration and grove compile command
**Description:** Implement `grove/compile/engine.py` (CompileEngine, Phase 0 only) and the `grove compile` CLI command. `--dry-run` estimates token count and cost without making an LLM call. The engine orchestrates: SourceLoader → PromptBuilder → LLMRouter (standard tier) → ArticleParser → ArticleWriter → QualityRatchet → GitCommitter. With `--json` flag, emits NDJSON progress events to stdout per the protocol defined in ARCH.md. Compile command respects `pinned: true` articles and `<!-- grove:human -->` blocks via the writer.
**Depends on:** TASK-009, TASK-013, TASK-014
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Epic 4 — Query Engine

### TASK-016 — Quick query mode
**Description:** Implement `grove/query/quick.py` (QuickQuery) and `grove query --quick` mode. QuickQuery searches `_index.md` and `_concepts.md` only (no article loading), synthesises a brief answer using the fast LLM tier, and cites relevant wiki links as `[wiki: path.md]`. Returns a QueryResult Pydantic model. This mode must complete in under 5 seconds.
**Depends on:** TASK-015
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-017 — Deep query mode
**Description:** Implement `grove/query/deep.py` (DeepQuery) and `grove query --deep` mode (default). DeepQuery uses FTS5 keyword search to identify the top-5 most relevant wiki articles, loads their full content, and calls the standard LLM tier to synthesise an answer. Answer includes `[wiki: article-path.md]` citations and 2–3 follow-up question suggestions. Must complete in under 10 seconds.
**Depends on:** TASK-016, TASK-020
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-018 — Answer formatter, grove query command, grove file command
**Description:** Implement `grove/query/formatter.py` (AnswerFormatter: terminal/markdown/Marp slides output controlled by `--output` flag), `grove/query/filer.py` (QueryFiler: saves query to `queries/<timestamp>-<slug>.md`; promotes to `wiki/` with `origin: query` and `pinned: true` on `grove file`), and the full `grove query` and `grove file` CLI commands. `grove query` saves the result automatically. `grove file` promotes the last query result unless a path is provided. Both commands support `--json` flag for NDJSON output (required by the Obsidian plugin — see ARCH.md protocol).
**Depends on:** TASK-017
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Epic 5 — Health, Search, Costs, and Utility Commands

### TASK-019 — Health check module and grove health command
**Description:** Implement `grove/health/` — ProvenanceChecker (counts `[source:...]` citations vs. factual sentences), ContradictionDetector (LLM on shared-concept article pairs), StalenessChecker (compares article `compiled_from` checksums vs. state.json), GapDetector (concepts in sources missing from wiki), OrphanDetector (wiki articles with no incoming links), HealthReporter (aggregates all checks). Implement `grove health` (human-readable), `grove health --json` (NDJSON output for Obsidian plugin — see ARCH.md protocol), and `grove health --fix` (auto-fixes broken links and orphan stubs, then commits). Full health suite must run under 30 seconds on a 100-article wiki.
**Depends on:** TASK-015, TASK-004
**SP:** 3
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-020 — SQLite FTS5 search index
**Description:** Implement `grove/search/fts.py` (FTSIndex: build/rebuild/query SQLite FTS5 in `.grove/search.db`), `grove/search/chunker.py` (Chunker: 512-token chunks, 64-token overlap), and `grove search <query>` CLI command (keyword mode). Index rebuilds automatically after each successful compile. Search returns ranked results with the best-matching chunk shown as context. Unit tests verify index build and BM25 query against fixture articles.
**Depends on:** TASK-015
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-021 — sqlite-vec semantic search and hybrid mode
**Description:** Implement `grove/search/vec.py` (VecIndex: generates embeddings via Ollama `nomic-embed-text`, stores in sqlite-vec alongside FTS5) and `grove/search/hybrid.py` (HybridSearch: BM25 × 0.5 + cosine similarity × 0.5, reranked). Extend `grove search` with `--mode keyword|semantic|hybrid` flag. If Ollama is unreachable, semantic mode returns a clear user-facing error; hybrid mode falls back to keyword-only with a warning. Unit tests mock Ollama embedding calls.
**Depends on:** TASK-020
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-022 — grove serve (local web UI)
**Description:** Implement `grove/search/serve.py` using FastAPI + HTMX. `grove serve` starts a local server on `http://localhost:8765`. The UI provides a search box, hybrid search results with article previews, and links to open articles in Obsidian (`obsidian://open?path=...`). No JavaScript framework — HTMX only. Tailwind CSS via CDN. Server must start in under 2 seconds. Accessible (WCAG 2.1 AA): semantic HTML, keyboard navigable.
**Depends on:** TASK-021
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [FE+BE]

---

### TASK-023 — grove raw subcommands
**Description:** Implement the `grove raw` subcommand group: `grove raw list` (all sources with quality flag, tabular output via rich), `grove raw list --failed` (failed/poor sources only), `grove raw retry <path>` (re-runs the full ingest pipeline on a single source), `grove raw drop <path>` (removes the source file and its manifest entry, flags affected wiki articles as stale in state.json). Each command handles non-existent paths and non-grove directories with clear error messages.
**Depends on:** TASK-008
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-023a — grove pin and grove unpin commands
**Description:** Implement `grove pin <path>` (sets `pinned: true` in the article's YAML front matter) and `grove unpin <path>` (removes it). Both commands must handle: non-existent paths, files outside wiki/, files without YAML front matter. The writer already respects `pinned: true` — these commands are the user-facing interface to toggle it. Auto-commits the front matter change.
**Depends on:** TASK-012
**SP:** 0.5
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-024 — Cost tracking commands
**Description:** Implement `grove costs` and `grove costs --today` commands. CostTracker reads `.grove/logs/costs.jsonl` and aggregates spend by task type (ingest, compile, query) and by model, displayed as a rich table. Implement the daily budget limit check in LLMRouter: if `budget.daily_limit_usd` is exceeded, raise BudgetExceededError before making any API call; if spend exceeds `budget.warn_at_usd`, emit a rich warning but do not block.
**Depends on:** TASK-004
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-025 — grove log, grove diff, grove rollback commands
**Description:** Implement `grove log` (lists grove: git commits with date, article count, and cost from commit message metadata), `grove diff` (shows article-level additions/removals vs. previous grove commit using CompileDiff), `grove rollback` (reverts the last grove commit via `git revert`), and `grove rollback --to <sha>` (targeted rollback via `git checkout <sha> -- wiki/` and a new commit). All commands use the git module from TASK-014 and produce rich formatted output.
**Depends on:** TASK-014
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Epic 6 — Obsidian Plugin

### TASK-026 — Plugin runner and settings page
**Description:** Implement `obsidian-plugin/src/runner.ts` (GroveRunner: spawns the `grove` CLI as a child process, reads NDJSON events from stdout line-by-line, emits typed TypeScript events matching ProgressEvent/ResultEvent/ErrorEvent from ARCH.md) and `obsidian-plugin/src/settings.ts` (GroveSettingTab: masked API key input, CLI path field with auto-detect button, dry-run toggle, default query mode selector). Settings are stored via `Plugin.loadData()` to `data.json`. Runner handles subprocess crashes gracefully and surfaces errors as Obsidian notices.
**Depends on:** TASK-015, TASK-002
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [FE]

---

### TASK-027 — Plugin compile command
**Description:** Implement `obsidian-plugin/src/compile.ts`. Registers "Grove: Compile" in the Obsidian command palette. On activation: shows a progress notice updated per NDJSON progress events from GroveRunner, completes with a success notice ("12 articles created, 3 updated — cost: $0.43") or an error notice with the error code and a suggested remedy. If dry-run is enabled in settings, shows estimated tokens and cost as a modal rather than compiling.
**Depends on:** TASK-026
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [FE]

---

### TASK-028 — Plugin query sidebar
**Description:** Implement `obsidian-plugin/src/query.ts` (QueryPanel). Ribbon icon opens a right-panel leaf. Panel contains: text input for question, mode selector (quick/deep), submit button, and a markdown-rendered answer area using Obsidian's `MarkdownRenderer.renderMarkdown()`. Shows a spinner while the subprocess runs. "File this answer" button calls `grove file` via GroveRunner and confirms success with a notice. Panel state (last question, last answer) persists during the Obsidian session.
**Depends on:** TASK-026, TASK-018
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [FE]

---

### TASK-029 — Plugin grove detector and multi-grove support
**Description:** Implement `obsidian-plugin/src/grove-detector.ts` (GroveDetector). Scans the active vault for directories containing `.grove/config.yaml`. If multiple are found, adds a grove selector dropdown to the plugin settings page (OB-04). If the configured grove path points outside the vault, it is used as-is. The active grove path is passed to GroveRunner as the working directory (`cwd`) for all subprocess calls, so the CLI operates on the correct grove.
**Depends on:** TASK-026
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [FE]

---

## Epic 7 — Prompts, Demo Vault, and Documentation

### TASK-030 — Default compilation prompts
**Description:** Write the four default prompts shipped with the engine. `prompts/compile-wiki.md` instructs the LLM to produce a structured wiki from combined sources, specifying the article separation marker format, required YAML front matter fields, `[source: path.md]` citation format, and including an example output section. `prompts/query.md`, `prompts/summarise.md`, and `prompts/contradiction.md` follow the same structure. These prompts are load-bearing — quality depends on them. Each must include clear instructions, the exact output format, and a worked example.
**Depends on:** TASK-010
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-031 — Demo vault (.grove-example)
**Description:** Build a demo grove in `.grove-example/` using 10–15 public-domain or Creative Commons sources on a single topic (to be confirmed with Simon before starting). Run a real compilation against it to produce a populated `wiki/`. This demo vault validates the full pipeline on real content and serves as the onboarding example. Include `DEMO.md` explaining how to open it in Obsidian. The demo vault must be committed with its compiled wiki (no API key required for the reader).
**Depends on:** TASK-015, TASK-030
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-032 — README and plugin listing assets
**Description:** Write `README.md` covering: what Grove is (1 paragraph), quick-start (5 commands to get from zero to first compile), full CLI command reference, BYOK setup instructions, contributing guide. Write `obsidian-plugin/README.md` for the Obsidian community plugin directory. Update `manifest.json` with final plugin metadata. All content must accurately reflect the implemented commands. UK English throughout. No marketing fluff — respect the reader's time.
**Depends on:** TASK-031
**SP:** 1
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Epic 8 — Testing and Quality

### TASK-033 — Integration tests: ingest to compile pipeline
**Description:** Write end-to-end integration tests in `tests/test_compile.py` and `tests/test_ingest.py`. Tests run the full pipeline (grove init → ingest fixtures → compile → quality ratchet → git commit) in a temporary directory. LLM calls are mocked at the provider boundary using recorded responses (pytest-recording or equivalent). Cover: full compile succeeds and articles are written, ratchet blocks on low provenance and rolls back, rollback restores the wiki to its previous state. Tests must run without network access.
**Depends on:** TASK-015, TASK-013
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

### TASK-034 — Integration tests: query and health
**Description:** Write integration tests in `tests/test_query.py` and `tests/test_health.py`. Query tests: quick mode returns a QueryResult with citations; deep mode loads the top-5 articles and calls the standard model; `grove file` promotes correctly with `origin: query` front matter. Health tests: provenance check flags articles with no citations; staleness check detects changed source checksums; orphan detector identifies articles with no incoming links. All LLM calls mocked.
**Depends on:** TASK-019, TASK-018
**SP:** 2
**Status:** [x] Complete
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Backlog — Phase 3 (Not In Phase 1 Scope)

These tasks are defined for planning purposes. They are gated on Spike 2 (incremental compilation quality validation) passing.

### TASK-035 — Incremental compile engine
**Description:** Implement the Phase 1 incremental compilation algorithm alongside Phase 0 in `grove/compile/engine.py`. Includes StateManager.diff() for checksum-based change detection, parallel concept extraction on dirty sources (fast model), ConceptResolver (standard model), CompilePlanner, per-article ArticleCompiler (standard model, parallel), LinkResolver (local, no LLM), and IndexRebuilder (fast model). Activated via `compile.phase: 1` in config. Only schedule after Spike 2 confirms quality parity.
**Depends on:** TASK-015
**SP:** 8
**Status:** [ ] Todo
**Agent:** @dwl-dev
**Tag:** [BE]

---

## Task Summary

| Epic | Tasks | Total SP |
|------|-------|----------|
| 1 — Scaffold and Tooling | TASK-001 to 003 | 2 |
| 2 — Ingest Pipeline | TASK-004 to 008 | 8 |
| 3 — Compilation Engine | TASK-009 to 015 | 11 |
| 4 — Query Engine | TASK-016 to 018 | 3 |
| 5 — Health, Search, Costs, Utilities | TASK-019 to 025 + 023a | 12.5 |
| 6 — Obsidian Plugin | TASK-026 to 029 | 6 |
| 7 — Prompts and Documentation | TASK-030 to 032 | 5 |
| 8 — Testing | TASK-033 to 034 | 4 |
| **Total Phase 1** | **35 tasks** | **51.5 SP (~7 weeks)** |

---

## Recommended Execution Order by Week

**Week 1:** TASK-001 → TASK-002 → TASK-003 → TASK-004 → TASK-005
**Week 2:** TASK-006 → TASK-007 → TASK-008 → TASK-010 → TASK-009
**Week 3:** TASK-030 → TASK-011 → TASK-012 → TASK-013 → TASK-014
**Week 4:** TASK-015 → TASK-020 → TASK-031 → TASK-016 → TASK-017 → TASK-018
**Week 5:** TASK-019 → TASK-023 → TASK-023a → TASK-024 → TASK-025
**Week 6:** TASK-021 → TASK-022 → TASK-026 → TASK-027 → TASK-033
**Week 7:** TASK-028 → TASK-029 → TASK-034 → TASK-032

**Changes from v0.1:**
- TASK-030 (prompts) moved to start of Week 3 — parser (TASK-011) needs the article separation format the prompt defines
- TASK-020 (FTS5 search) moved to Week 4 — TASK-017 (deep query) depends on it
- TASK-023a (pin/unpin) added to Week 5
