# FRD — Grove Functional Requirements
**Version:** 0.1  
**Date:** 2026-04-03  
**Status:** Draft — to be detailed by /dwl-pm

---

## Phase 1 Functional Requirements

### FR-01: grove init
- Creates grove directory structure (`.grove/`, `raw/`, `wiki/`, `queries/`, `outputs/`)
- Initialises git repository
- Creates `.grove/config.yaml` with sensible defaults
- Detects local Ollama and configures fast tier automatically
- Prompts for Anthropic API key if not in environment
- Creates `.grove/prompts/` with shipped default prompts

### FR-02: grove ingest
- Accepts file path or URL
- Converts PDF → markdown (via pymupdf4llm, pdfminer.six fallback)
- Converts HTML → markdown (via readability-lxml + markdownify)
- Quality scoring: `good | partial | poor`
  - Poor: excluded from compilation by default
  - Partial: included with warning
- Duplicate detection: checksum comparison against existing sources
- Generates summary + key concepts (fast model)
- Updates `raw/_manifest.md`
- Bulk ingest via `grove ingest-dir <path>`: continues on failure, reports failed files

### FR-03: grove compile
- Phase 0: brute-force single LLM call
  - Loads all source content (full or summary based on length)
  - Loads existing wiki articles if recompiling
  - Parses response into individual `.md` files with YAML front matter
- Quality ratchet runs post-compile
- Auto git commit on success
- `--dry-run` flag: estimates token count and cost, no LLM call
- Respects `pinned: true` front matter — never overwrites pinned articles
- Preserves `<!-- grove:human -->` blocks verbatim

### FR-03a: grove pin / grove unpin
- `grove pin <path>` sets `pinned: true` in article YAML front matter
- `grove unpin <path>` removes `pinned: true`
- Validates path is within wiki/, has YAML front matter
- Auto-commits the change

### FR-04: grove query
- Modes: `--quick` (index only), `--deep` (full articles). `--research` (+ web search) deferred to post-Phase 1
- Output formats: terminal, `--output md`, `--output slides` (Marp)
- Cites wiki articles as `[wiki: article-path.md]`
- Suggests 2–3 follow-up questions
- Saves query result to `queries/` with timestamp filename

### FR-05: grove file
- Promotes a query result file into the wiki
- Adds `origin: query` and `pinned: true` to front matter
- Excluded from compilation source loading
- Health check flags if it contradicts source-compiled articles

### FR-06: grove health
- Checks: provenance coverage, contradictions, staleness, gaps, orphans, coverage
- `--fix` flag: auto-fixes where possible (e.g. stub articles for broken wiki-links), commits
- Human-readable report + machine-readable JSON output

### FR-07: grove rollback
- `grove rollback` → reverts last compile (git revert)
- `grove rollback --to <commit>` → reverts to specific compile
- `grove log` → lists compile history with dates and stats
- `grove diff` → shows what changed in last compile

### FR-08: grove search
- Keyword: SQLite FTS5 across all wiki articles
- Semantic: sqlite-vec embeddings (local model)
- Hybrid: combines both, ranked results
- `grove serve` → FastAPI + HTMX local web UI on localhost

### FR-09: grove raw
- `grove raw list` → all sources with quality flag
- `grove raw list --failed` → failed/poor sources
- `grove raw retry <path>` → re-run ingest on a source
- `grove raw drop <path>` → remove source, flag affected articles as stale

### FR-10: Cost tracking
- `grove costs` → spend by task type (ingest, compile, query)
- `grove costs --today`
- Daily warning and limit configurable in `.grove/config.yaml`

---

## Obsidian Plugin Functional Requirements

### OB-01: Plugin installation
- Available via Obsidian community plugin directory
- Settings page: API key, Python backend path, grove folder detection

### OB-02: Compile command
- Command palette: "Grove: Compile"
- Progress indicator during compilation
- Success/failure notification with stats (articles created/updated, cost)
- Dry-run option in settings

### OB-03: Query sidebar
- Ribbon icon opens query panel
- Text input for question, mode selector (quick/deep)
- Answer rendered as markdown in panel
- "File this answer" button triggers FR-05

### OB-04: Multiple groves
- Detect multiple `raw/` + `wiki/` folder pairs in vault
- Or support pointing at a folder outside the vault
- Grove selector in plugin settings

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| First compile (25 sources) | <60 seconds |
| Incremental compile (1 new source) | <15 seconds |
| Query (deep mode) | <10 seconds |
| Provenance coverage (compiled articles) | >90% |
| Hallucination rate | <2% |
| Article usefulness (human eval) | >80% rated "useful" |
| Data integrity | Zero incidents — git ensures this |
| CLI startup time | <500ms |

---

## Out of Scope (Phase 1)

- Image compilation (Phase 2)
- Obsidian Mobile support
- Local model for article compilation (only fast-tier tasks)
- Grove-to-grove queries (Phase 2)
- Web platform / browser extension (Phase 2)
- MCP server (Phase 2)
- Incremental compilation with dependency graph (Phase 1 if needed, else Phase 2)
