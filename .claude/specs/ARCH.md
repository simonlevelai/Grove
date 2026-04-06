# ARCH.md — Grove Architecture
**Version:** 0.2
**Date:** 2026-04-03
**Status:** Phase 1 — Resolved and ready for implementation

---

## System Overview

Grove is a knowledge compiler with three layers:

1. **Core engine (Python)** — ingest, compile, query, health, search, git
2. **CLI** — Typer-based commands wrapping the engine; the primary interface for Phase 1
3. **Obsidian plugin (TypeScript)** — thin client; spawns the Python CLI as a subprocess

```
+-----------------------------------------------------------+
|                        USER LAYER                          |
|  Obsidian plugin  |  CLI terminal  |  grove serve (HTMX)  |
+-----------------------------------------------------------+
           |                |                  |
           v subprocess     v direct           v HTTP localhost
+-----------------------------------------------------------+
|                    GROVE CLI (Typer)                       |
|  grove init | ingest | compile | query | health | search   |
|  raw | file | log | diff | rollback | costs | serve        |
+-----------------------------------------------------------+
           |
+-----------------------------------------------------------+
|                  GROVE CORE (Python package)               |
|                                                            |
|  ingest/          compile/          query/                 |
|  - Converter      - Loader           - IndexSearch         |
|  - QualityScorer  - PromptBuilder    - ArticleSearch       |
|  - Deduplicator   - ArticleParser    - AnswerFormatter     |
|  - Manifest       - QualityRatchet   - FileBacker           |
|                   - GitCommitter                           |
|                                                            |
|  health/          search/           llm/                   |
|  - ProvenanceCheck  - FTS5Index     - Router               |
|  - ContradictionDet - VecIndex      - AnthropicProvider    |
|  - HealthReporter   - HybridSearch  - OllamaProvider       |
|                                     - CostTracker           |
|                                                            |
|  git/             config/                                  |
|  - AutoCommit     - ConfigLoader                          |
|  - LogReader      - StateManager                          |
|  - Rollback                                               |
+-----------------------------------------------------------+
           |
+-----------------------------------------------------------+
|                  FILESYSTEM (grove structure)              |
|  raw/   wiki/   queries/  outputs/  .grove/  .git/        |
+-----------------------------------------------------------+
```

---

## Resolved Architecture Decisions

### Decision 1: Plugin ↔ Python Communication — Subprocess

**Chosen:** Subprocess (shell out to `grove` CLI, read JSON from stdout/stderr).

**Rationale:** A local HTTP server requires daemon lifecycle management (start, stop, crash recovery), port conflict handling, and a way for the plugin to know the server is ready. Subprocess has none of these problems. The plugin spawns `grove compile --json` (or equivalent), captures stdout, and parses structured JSON. Latency from subprocess spawning is 200–400ms — acceptable for user-initiated actions (compile, query). If we later need streaming output for progress updates, we can read stdout line-by-line during the subprocess run.

**Protocol:** Every CLI command accepts `--json` flag. In JSON mode, output is newline-delimited JSON events:
```json
{"type": "progress", "step": "loading_sources", "pct": 10}
{"type": "progress", "step": "llm_call", "pct": 40}
{"type": "result", "articles_created": 12, "articles_updated": 3, "cost_usd": 0.43}
{"type": "error", "message": "Rate limit exceeded", "code": "rate_limit"}
```

**Plugin reads** stdout line-by-line, updates the Obsidian progress indicator per event, and parses the final `result` or `error` object.

---

### Decision 2: BYOK API Key Storage in Obsidian Plugin

**Chosen:** Obsidian `Plugin.loadData()` / `Plugin.saveData()` — persists to `<vault>/.obsidian/plugins/grove/data.json`.

**Security considerations:**
- `data.json` is inside the Obsidian vault. The vault should be git-tracked but `.obsidian/` should be in `.gitignore` — standard Obsidian practice.
- The plugin settings page shows the key masked (asterisked) after entry.
- The key is passed to the subprocess via environment variable (`ANTHROPIC_API_KEY=...`), never written to any grove-tracked file.
- On macOS/Linux the plugin folder inherits vault directory permissions. We document: do not sync `data.json` via cloud storage.
- No encryption at rest in Phase 1 (Obsidian itself does not offer this). Document this limitation explicitly in the README.

**Settings stored in `data.json`:**
```json
{
  "anthropicApiKey": "sk-ant-...",
  "groveCliPath": "/usr/local/bin/grove",
  "defaultGrovePath": "",
  "dryRunByDefault": false,
  "defaultQueryMode": "deep"
}
```

---

### Decision 3: PDF Conversion — pymupdf4llm (MIT)

**Rejected: marker** — GPL-3.0 licence. Incompatible with Grove's MIT licence. Including marker in the dependency tree would contaminate the entire package.

**Chosen: `pymupdf4llm`** — MIT licence. PyMuPDF's LLM-optimised extraction layer. Produces structured markdown with heading hierarchy, table preservation, and clean paragraph separation. Actively maintained by the PyMuPDF team. Handles most academic PDFs, reports, and documents well.

**Fallback: `pdfminer.six`** — MIT licence. Used when `pymupdf4llm` fails (password-protected, corrupt, or scan-only PDFs). Produces plainer text output, quality-flagged as `partial`.

**Conversion quality scoring logic:**
- `good`: pymupdf4llm succeeds, heading structure detected, >500 words
- `partial`: pdfminer fallback, or <500 words, or no heading structure
- `poor`: extraction fails, <100 words, or detected as image-only scan

---

### Decision 4: Search — sqlite-vec + Ollama Embeddings

**Chosen:** `sqlite-vec` for vector storage, `nomic-embed-text` via Ollama for embeddings. FTS5 + vector hybrid search in a single SQLite DB (`.grove/search.db`).

**Rationale:**
- No separate vector store process to manage
- Search index lives next to FTS5 — one file, one query interface
- Fully offline — no embedding API calls, no cost
- `nomic-embed-text` is the best open-weight embedding model at its size (137M params, MIT licence)
- Hybrid search: BM25 score (FTS5) × 0.5 + cosine similarity (vec) × 0.5, reranked

**Graceful degradation:** If Ollama is not running or `nomic-embed-text` is not pulled, `grove search` falls back to FTS5-only with a warning. Semantic search and `--mode hybrid` return an informative error suggesting `ollama pull nomic-embed-text`.

**Index contents:** Each wiki article is chunked at 512 tokens, overlapping by 64 tokens. Each chunk has a rowid pointing back to the article file path and position. Search returns article-level results (deduplicated), with the best-matching chunk shown as context.

---

### Decision 5: Python Packaging — pyproject.toml with Hatch

**Package name:** `grove-kb` (the `grove` name on PyPI is taken).
**Install command:** `pip install grove-kb`
**CLI entry point:** `grove = "grove.cli:app"`
**Build backend:** `hatchling` (Hatch's build backend — lightweight, PEP 517 compliant, no legacy setup.py).

**Dependency groups:**
```toml
[project]
dependencies = [
    "typer>=0.12",
    "pydantic>=2.0",
    "anthropic>=0.25",
    "httpx>=0.27",
    "gitpython>=3.1",
    "pyyaml>=6.0",
    "rich>=13.0",
    "sqlite-utils>=3.36",
]

[project.optional-dependencies]
full = [
    "pymupdf4llm>=0.0.17",
    "pdfminer.six>=20221105",
    "readability-lxml>=0.8",
    "markdownify>=0.13",
    "sqlite-vec>=0.1",
    "ollama>=0.2",
]
dev = [
    "ruff",
    "black",
    "pytest",
    "pytest-asyncio",
    "mypy",
    "httpx",
]
```

**Rationale for split:** Core package installs fast and works without system dependencies. `pip install grove-kb[full]` adds PDF/HTML conversion and semantic search. This matters for users who only want the CLI for text sources, and for CI where binary deps slow builds.

**Token counting:** SourceLoader needs to enforce the 800K token budget. Rather than adding a tokenizer dependency, use the Anthropic SDK's `client.count_tokens()` method when the Anthropic provider is configured. Fallback: a word-count heuristic (`words × 1.3 = approximate tokens`) which is good enough for budget enforcement — we're setting a hard cap with headroom, not a precise limit.

**Template engine:** Prompts use Python `string.Template` (`$sources`, `$existing_wiki`, `$question`) rather than Jinja2. The prompts are simple variable substitutions — Jinja2's control flow, filters, and inheritance are unnecessary complexity. If power users later need conditionals in prompts, Jinja2 can be added as an optional dependency without changing the prompt file format.

---

## Module-Level Design

### `grove/ingest/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `converter.py` | `Converter` | Dispatches to pdf/html/text converters by MIME type |
| `pdf.py` | `PDFConverter` | pymupdf4llm → markdown, falls back to pdfminer |
| `html.py` | `HTMLConverter` | readability-lxml → markdown via markdownify |
| `text.py` | `TextConverter` | Pass-through, normalise line endings |
| `quality.py` | `QualityScorer` | Scores conversion output: good / partial / poor |
| `dedup.py` | `Deduplicator` | SHA-256 checksum vs manifest, detects duplicates |
| `manifest.py` | `ManifestWriter` | Reads/writes `raw/_manifest.md` (YAML front matter + table) |
| `summariser.py` | `Summariser` | Calls fast LLM to produce 150-word summary + key concepts |

**Ingest pipeline flow:**
```
Input (path/URL)
  → Converter.convert()          # produce raw markdown
  → QualityScorer.score()        # good/partial/poor
  → Deduplicator.check()         # skip if duplicate
  → Summariser.summarise()       # fast model: summary + concepts
  → ManifestWriter.register()    # update _manifest.md + state.json
```

---

### `grove/compile/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `loader.py` | `SourceLoader` | Loads raw sources respecting quality thresholds; loads summaries if source exceeds token budget |
| `prompt.py` | `PromptBuilder` | Merges shipped default prompts with user overrides in `.grove/prompts/` |
| `engine.py` | `CompileEngine` | Orchestrates Phase 0 (brute-force) or Phase 1 (incremental) |
| `parser.py` | `ArticleParser` | Splits LLM response by `<!-- grove:article path/to/file.md -->` markers; validates YAML front matter |
| `writer.py` | `ArticleWriter` | Writes articles to `wiki/`; preserves `<!-- grove:human -->` blocks; respects `pinned: true` |
| `ratchet.py` | `QualityRatchet` | Runs all quality checks; returns pass/fail + detailed report |
| `committer.py` | `GitCommitter` | Auto-commits on success; rolls back partial writes on failure |

**Phase 0 compilation (implemented in Phase 1):**
```
SourceLoader.load_all()
  → builds combined context string (full text for short sources, summaries for long)
  → PromptBuilder.build_compile_prompt()
  → LLMRouter.call(tier="standard", prompt=..., max_tokens=65536)
  → ArticleParser.parse(response)
  → ArticleWriter.write_all(articles)
  → QualityRatchet.check()
  → GitCommitter.commit_or_rollback()
```

**Phase 1 incremental compilation (Phase 3 of TASKS.md, lower priority):**
```
StateManager.diff()                    # which sources are new/changed
Summariser.summarise_dirty(sources)    # fast model, parallel
ConceptResolver.merge()                # standard model
CompilePlanner.plan()                  # which articles to touch
ArticleCompiler.compile_articles()     # standard model, parallel
LinkResolver.update_backlinks()        # local, no LLM
IndexRebuilder.rebuild()               # fast model
GitCommitter.commit()
```

---

### `grove/query/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `engine.py` | `QueryEngine` | Dispatches to quick/deep/research modes |
| `quick.py` | `QuickQuery` | Searches index only (FTS5 + vec), no full article load |
| `deep.py` | `DeepQuery` | Loads full relevant articles, synthesises answer (standard model) |
| `formatter.py` | `AnswerFormatter` | Renders answer as terminal / markdown / Marp slides |
| `filer.py` | `QueryFiler` | Promotes query result to wiki with `origin: query`, `pinned: true` |

---

### `grove/health/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `provenance.py` | `ProvenanceChecker` | Counts `[source:...]` citations vs. factual sentences per article |
| `contradictions.py` | `ContradictionDetector` | Groups articles by shared concept; LLM compares pairs |
| `staleness.py` | `StalenessChecker` | Compares article `compiled_from` source checksums vs. current state.json |
| `gaps.py` | `GapDetector` | Identifies concepts mentioned in sources but missing from wiki |
| `orphans.py` | `OrphanDetector` | Finds wiki articles with no incoming links |
| `reporter.py` | `HealthReporter` | Aggregates all checks; outputs human text + JSON |

---

### `grove/search/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `fts.py` | `FTSIndex` | SQLite FTS5 index: build, rebuild, query |
| `vec.py` | `VecIndex` | sqlite-vec index: build (Ollama embeddings), query (cosine) |
| `hybrid.py` | `HybridSearch` | Combines BM25 + cosine scores; returns ranked article list |
| `chunker.py` | `Chunker` | Splits articles into 512-token chunks for indexing |
| `serve.py` | `LocalServer` | FastAPI + HTMX; `grove serve` command |

---

### `grove/llm/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `router.py` | `LLMRouter` | Selects provider + model by tier; handles fallback |
| `anthropic.py` | `AnthropicProvider` | Calls Anthropic API; streams where beneficial |
| `ollama.py` | `OllamaProvider` | Calls local Ollama; detects availability at startup |
| `cost.py` | `CostTracker` | Tracks token usage per task type; persists to `.grove/logs/costs.jsonl` |
| `models.py` | `LLMRequest`, `LLMResponse` | Pydantic models for LLM I/O |

**Model routing table:**

| Tier | Primary | Fallback | Used for |
|------|---------|----------|----------|
| fast | Ollama `llama3.2` | `claude-haiku-4-5-20251001` | Ingest summaries, index rebuild, contradiction detection, health pre-checks |
| standard | `claude-sonnet-4-6` | — | Compile, query (deep) |
| powerful | `claude-opus-4-6` | — | Deep query (spec says Opus for "complex synthesis"), `grove query --research` mode |

**Note on deep query tier:** The spec routes deep Q&A through the powerful tier (Opus). Phase 1 defaults deep queries to **standard** (Sonnet) to control costs — Opus is 5-10x more expensive. The `--research` flag or explicit `--tier powerful` escalates to Opus. Users can override the default in config. This is a deliberate cost-conscious divergence from the spec.

---

### `grove/config/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `loader.py` | `ConfigLoader` | Reads `.grove/config.yaml`; validates with Pydantic; merges env vars |
| `state.py` | `StateManager` | Reads/writes `.grove/state.json` (checksums, compile history, concept graph) |
| `defaults.py` | `Defaults` | Default config values; used by `grove init` |

**`.grove/config.yaml` schema (key fields):**
```yaml
llm:
  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}    # env var interpolation
    ollama:
      base_url: http://localhost:11434

  routing:
    fast:
      provider: ollama
      model: llama3.2
      fallback: { provider: anthropic, model: claude-haiku-4-5-20251001 }
    standard:
      provider: anthropic
      model: claude-sonnet-4-6
    powerful:
      provider: anthropic
      model: claude-opus-4-6

budget:
  daily_limit_usd: 5.00
  warn_at_usd: 3.00

compile:
  quality_threshold: partial   # good | partial | poor — excludes only poor by default
  phase: 0                     # 0 = brute-force, 1 = incremental (future)
  max_output_tokens: 65536     # max response tokens for compilation LLM call

search:
  embedding_model: nomic-embed-text
  hybrid_alpha: 0.5            # weight for vector score vs BM25

git:
  auto_commit: true
  commit_message_prefix: "grove:"
```

**Note:** Config structure matches the spec's nested format. Fallback is critical — when Ollama is unavailable, fast-tier operations must fall back to Haiku automatically without user intervention.

---

### `grove/git/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `auto_commit.py` | `AutoCommitter` | Stages wiki/ changes; commits with structured message. Low-level git ops only — called by compile's `GitCommitter` which handles commit-vs-rollback decisions |
| `log.py` | `CompileLog` | Reads git log; filters grove: commits; returns structured history |
| `rollback.py` | `RollbackManager` | `git revert` for last compile; `git checkout <sha> -- wiki/` for targeted |
| `diff.py` | `CompileDiff` | Shows article-level diff between two grove commits |

---

### `obsidian-plugin/src/`

| File | Class / Function | Responsibility |
|------|-----------------|----------------|
| `main.ts` | `GrovePlugin` | Registers commands, settings tab, ribbon icon; loads config |
| `settings.ts` | `GroveSettingTab` | Obsidian settings page; masked key input; CLI path auto-detect |
| `runner.ts` | `GroveRunner` | Spawns `grove` subprocess; reads NDJSON events from stdout; emits typed events |
| `compile.ts` | `CompileCommand` | Handles "Grove: Compile" command; progress notice; success/error notification |
| `query.ts` | `QueryPanel` | Right-panel leaf; question input; mode selector; renders markdown answer |
| `file.ts` | `FileCommand` | Handles "Grove: File this answer" from active query result |
| `grove-detector.ts` | `GroveDetector` | Detects grove directories (folders containing `.grove/config.yaml`) in vault |

**Plugin ↔ CLI interface contract:**

```typescript
// runner.ts — all communication passes through this class
interface GroveEvent {
  type: 'progress' | 'result' | 'error' | 'warning';
}

interface ProgressEvent extends GroveEvent {
  type: 'progress';
  step: string;       // e.g. "loading_sources", "llm_call", "quality_ratchet"
  pct: number;        // 0–100
  detail?: string;    // human-readable description
}

interface ResultEvent extends GroveEvent {
  type: 'result';
  // Varies per command — compile returns CompileResult, query returns QueryResult
  data: CompileResult | QueryResult | HealthResult;
}

interface ErrorEvent extends GroveEvent {
  type: 'error';
  message: string;
  code: string;       // machine-readable: "rate_limit", "no_sources", "ratchet_failed"
  recoverable: boolean;
}
```

---

## Quality Ratchet — Full Specification

Runs post-compile, before git commit. Failing checks marked [BLOCK] abort the commit.

| Check | Method | Threshold | Severity |
|-------|--------|-----------|----------|
| Provenance coverage | Count `[source:...]` citations vs. inferred factual sentences | <50% → BLOCK, <90% → WARN | BLOCK |
| New contradictions | LLM compares article pairs sharing concepts | Any unresolved → BLOCK | BLOCK |
| Coverage drop | Compare `compiled_from` source counts vs. previous compile | >10% drop → BLOCK | BLOCK |
| Broken wiki-links | Regex scan: `[[article]]` links that don't resolve to a file | Any → WARN (auto-fixable with `--fix`) | WARN |
| Human annotation preservation | Diff `<!-- grove:human -->` blocks vs. previous version | Any removed → BLOCK | BLOCK |
| Pinned article overwrite | Check `pinned: true` articles were not modified | Any modified → BLOCK | BLOCK |
| Query article used as source | Check `origin: query` articles are not in SourceLoader output | Any found → BLOCK | BLOCK |

**Ratchet output format (written to `.grove/logs/ratchet-<timestamp>.json`):**
```json
{
  "timestamp": "2026-04-03T14:22:00Z",
  "passed": false,
  "blocking_failures": ["provenance_coverage"],
  "warnings": ["broken_wiki_links"],
  "details": {
    "provenance_coverage": {"score": 0.42, "threshold": 0.50, "articles_below": ["ai-ethics.md"]}
  }
}
```

---

## Article Output Format Contract

The compilation prompt instructs the LLM to output articles separated by HTML comment markers. This is the contract between the prompt (TASK-030) and the parser (TASK-011):

**Separator:** `<!-- grove:article wiki/path/to/file.md -->`

Each article follows its separator and contains YAML front matter then markdown body:

```markdown
<!-- grove:article wiki/topics/transformers/overview.md -->
---
title: "Transformer Architecture"
compiled_from:
  - raw/papers/attention-is-all-you-need.md
  - raw/articles/transformers-explained.md
concepts: [transformer, self-attention, encoder-decoder]
summary: "One-line summary of this article."
status: published
generation: 3
last_compiled: "2026-04-03T14:22:00Z"
---

# Transformer Architecture

Article body with [[wiki-links]] and [source: filename.md] citations...
```

**Required front matter fields:** `title`, `compiled_from`, `concepts`, `summary`, `last_compiled`
**Optional front matter fields:** `status` (default: `published`), `generation` (auto-incremented), `pinned`, `origin`

The parser must handle:
- Missing/malformed separators → log warning, attempt recovery by detecting `---` YAML boundaries
- Missing required front matter fields → fill defaults, flag as WARN in ratchet
- Truncated output (LLM hit max tokens) → commit the complete articles, discard the truncated last one, log a warning

---

## Phase 0 vs Phase 1 Compilation — Delineation

### Phase 0: Brute-Force Single Call (Implemented in TASKS.md Phase 1)

**When:** All compiles in Grove Phase 1 product release.
**How:** Load everything into context, single LLM call.
**Limit:** ~500K tokens of source content (~75–100 typical sources).
**Cost:** ~$0.50 per full compile (Sonnet pricing).
**Speed:** ~45–90 seconds for 25 sources.

**Advantages:** No dependency graph, no concept resolution, no partial-compile edge cases. The LLM sees everything at once — best overall coherence. Correct choice for Phase 1's target user (50–200 sources).

**Token budget management:**
- Sources under 2,000 tokens: load full text
- Sources 2,000–10,000 tokens: load full text + flag for summary fallback if budget exceeded
- Sources over 10,000 tokens: load summary (generated at ingest time)
- Total context budget: 800K tokens (leaves headroom for prompt + response in 1M window)

### Phase 1 Incremental (Future — Phase 3 task group)

**When:** User has >200 sources and full recompile costs/time are prohibitive.
**How:** Diff sources by checksum → compile only affected articles → merge into existing wiki.
**Prerequisite:** Concept graph in `state.json` must be populated.
**Risk:** Coherence degrades vs. brute-force; contradiction rate may increase. Validated in Spike 2 before enabling.

---

## Data Integrity Guarantees

1. **Compile atomicity:** `ArticleWriter` writes to a temp directory first, then moves atomically. If the ratchet blocks the commit, temp files are cleaned up — the wiki is unchanged.
2. **Git as the undo layer:** Every successful compile auto-commits. `grove rollback` is `git revert`. No bespoke undo mechanism needed.
3. **Human annotations:** `ArticleWriter` extracts `<!-- grove:human -->` blocks before write, re-injects after write. Compiler never sees them in the output context — they are invisible to the LLM.
4. **Query isolation:** `SourceLoader` filters out any file with `origin: query` in YAML front matter. This is enforced at load time, not at write time.

---

## Phase 2 Architecture (Planned — Not In Scope)

- FastAPI backend on Azure Container Apps (UK South)
- Supabase: auth, grove metadata, vector search (pgvector)
- Next.js frontend on Azure Static Web Apps
- Browser extension: TypeScript (Chrome/Firefox)
- MCP server: exposes `grove_compile`, `grove_query`, `grove_health`, `grove_file` as tools
- Grove-to-grove federation: query across multiple groves via API

---

## Repository Layout (Final)

```
grove/                          # repo root
  grove/                        # Python package (pip install grove-kb)
    __init__.py
    cli.py                      # Typer CLI entry point
    ingest/
      __init__.py
      converter.py
      pdf.py
      html.py
      text.py
      quality.py
      dedup.py
      manifest.py
      summariser.py
    compile/
      __init__.py
      loader.py
      prompt.py
      engine.py
      parser.py
      writer.py
      ratchet.py
      committer.py
    query/
      __init__.py
      engine.py
      quick.py
      deep.py
      formatter.py
      filer.py
    health/
      __init__.py
      provenance.py
      contradictions.py
      staleness.py
      gaps.py
      orphans.py
      reporter.py
    search/
      __init__.py
      fts.py
      vec.py
      hybrid.py
      chunker.py
      serve.py
    llm/
      __init__.py
      router.py
      anthropic.py
      ollama.py
      cost.py
      models.py
    config/
      __init__.py
      loader.py
      state.py
      defaults.py
    git/
      __init__.py
      auto_commit.py
      log.py
      rollback.py
      diff.py
  obsidian-plugin/
    src/
      main.ts
      settings.ts
      runner.ts
      compile.ts
      query.ts
      file.ts
      grove-detector.ts
    manifest.json
    package.json
    tsconfig.json
    esbuild.config.mjs
  prompts/
    compile-wiki.md             # shipped default: brute-force compile
    compile-article.md          # shipped default: per-article (Phase 1 incremental)
    query.md                    # shipped default: query answering
    summarise.md                # shipped default: ingest summarisation
    contradiction.md            # shipped default: contradiction detection
  tests/
    test_ingest.py
    test_compile.py
    test_quality_ratchet.py
    test_query.py
    test_health.py
    test_llm_router.py
    fixtures/
      sample.pdf
      sample.html
      sample.md
  .grove-example/               # demo vault shipped for onboarding
    raw/
    wiki/
    .grove/
      config.yaml
  pyproject.toml
  CLAUDE.md
  README.md
  LICENSE                       # MIT
```

---

## Technical Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| pymupdf4llm produces poor output for complex PDFs | Medium | QualityScorer flags as `partial`; user can inspect and retry |
| sqlite-vec API stability (v0.x) | Low | Pin exact version; test on upgrade |
| Anthropic API rate limits during compile | Medium | Retry with exponential backoff in `AnthropicProvider`; expose as user-visible progress |
| Token budget overflow (very large sources) | Low | SourceLoader enforces budget hard cap; large sources always use summaries |
| Obsidian plugin API changes | Low | Pin Obsidian API version in package.json; test on Obsidian updates |
| `grove` PyPI name taken | Resolved | Use `grove-kb` as package name |
| Subprocess spawning fails (CLI not in PATH) | Medium | Plugin auto-detects CLI path on settings page; clear error message with install instructions |

---

## Intentional Divergences from Spec v0.5

Decisions where this architecture deliberately departs from the product spec, with rationale:

| Spec says | ARCH chose | Why |
|-----------|-----------|-----|
| Embedding model: `all-MiniLM` | `nomic-embed-text` | Better retrieval quality (768-dim vs 384-dim), MIT licence, actively maintained. Same Ollama deployment model |
| Deep Q&A tier: powerful (Opus) | standard (Sonnet) default, Opus via `--research` or `--tier powerful` | 5-10x cost difference. Sonnet is sufficient for most queries. Users can escalate explicitly |
| Config: nested `routing.fast.provider` | Same nested structure preserved | Aligned with spec — no divergence |
| Template engine: spec silent | `string.Template` (not Jinja2) | Prompts need variable substitution only. Jinja2 is unnecessary complexity |
| PDF conversion: marker | `pymupdf4llm` | Marker is GPL-3.0, incompatible with MIT licence |
| `grove watch` command | Deferred to post-Phase 1 | File watching adds daemon complexity. Users can run `grove ingest-dir` manually. Low-value vs implementation cost |
| `grove lint` command | Subsumed by `grove health` | Contradiction detection is one of health's seven checks. Separate `lint` command would duplicate code |
| `grove suggest` command | Deferred to post-Phase 1 | Requires gap analysis + LLM creativity. Lower priority than core compile/query loop |
| `grove pin/unpin` commands | Added to TASKS.md (see TASK-023a) | The writer respects `pinned: true` but the spec's explicit CLI commands were missing from tasks |
| Query `--research` mode (deep + web) | Deferred to post-Phase 1 | Requires web search integration. Deep mode covers 90% of use cases |
| matplotlib visualisation output | Deferred to post-Phase 1 | Terminal, markdown, and Marp slides cover core needs. matplotlib adds a heavy dep for a niche feature |
| `_health.md` in wiki/ | Generated by `grove health`, committed to wiki/ | Not called out explicitly in tasks but is output of TASK-019's HealthReporter |
| `overrides.yaml` | Deferred — spec says "Phase 1 if users need them" | Pinning + human annotations cover the two override mechanisms. Override directives are speculative |
| Budget defaults: warn $5, limit $20 | warn $3, limit $5 | Conservative defaults protect new users from cost surprises. A full compile is ~$0.50, so $5/day allows ~10 compiles. Users can raise limits in config |
