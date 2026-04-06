# Architecture

Grove has three layers: a Python engine, a CLI, and an Obsidian plugin.

```
┌───────────────────────────────────────────────────┐
│                    USER LAYER                      │
│  Obsidian plugin  │  CLI terminal  │  grove serve  │
└───────┬───────────┴───────┬────────┴──────┬───────┘
        │ subprocess        │ direct        │ HTTP
┌───────┴───────────────────┴───────────────┴───────┐
│                 GROVE CLI (Typer)                   │
│  init │ ingest │ compile │ query │ health │ search │
└───────────────────────┬───────────────────────────┘
                        │
┌───────────────────────┴───────────────────────────┐
│              GROVE CORE (Python package)            │
│                                                     │
│  ingest/        compile/        query/              │
│  ├ Converter    ├ SourceLoader  ├ QuickQuery        │
│  ├ QualityScorer├ PromptBuilder ├ DeepQuery         │
│  ├ Deduplicator ├ ArticleParser ├ AnswerFormatter   │
│  ├ Summariser   ├ ArticleWriter ├ QueryFiler        │
│  └ ManifestWriter├ QualityRatchet                   │
│                 └ GitCommitter                      │
│                                                     │
│  health/        search/        llm/                 │
│  ├ Provenance   ├ FTSIndex     ├ Router             │
│  ├ Contradictions├ VecIndex    ├ AnthropicProvider   │
│  ├ Staleness    ├ HybridSearch ├ OllamaProvider     │
│  ├ Gaps         ├ Chunker     └ CostTracker         │
│  ├ Orphans      └ LocalServer                       │
│  └ Reporter                                         │
│                                                     │
│  git/           config/                             │
│  ├ AutoCommit   ├ ConfigLoader                      │
│  ├ LogReader    ├ StateManager                      │
│  ├ Rollback     └ Defaults                          │
│  └ CompileDiff                                      │
└─────────────────────────┬─────────────────────────┘
                          │
┌─────────────────────────┴─────────────────────────┐
│                   FILESYSTEM                        │
│  raw/   wiki/   queries/   .grove/   .git/          │
└───────────────────────────────────────────────────┘
```

## Pipelines

### Ingest

```
Input (file path or URL)
  → Converter          # PDF/HTML/text → markdown
  → QualityScorer      # good / partial / poor
  → Deduplicator       # SHA-256 checksum vs existing sources
  → Summariser         # fast LLM: 150-word summary + key concepts
  → ManifestWriter     # register in raw/_manifest.md + state.json
```

Converters: `pymupdf4llm` for PDF (MIT licence; `pdfminer.six` as fallback), `readability-lxml` + `markdownify` for HTML, pass-through for text and markdown.

### Compile

```
SourceLoader           # load sources, enforce 800K token budget
  → PromptBuilder      # merge shipped prompt with user overrides
  → LLMRouter          # standard tier (Claude Sonnet)
  → ArticleParser      # split by <!-- grove:article --> markers
  → ArticleWriter      # atomic write, preserve human blocks + pinned
  → QualityRatchet     # 7 checks — block or warn
  → GitCommitter       # auto-commit on pass, rollback on fail
```

**Token budget management:** Sources under 10K tokens are included in full. Longer sources use their ingest-time summary. Total budget is 800K tokens, leaving headroom for the prompt and response within a 1M context window.

### Query

**Quick mode** searches `_index.md` and `_concepts.md` only, calls the fast LLM tier. Under 5 seconds.

**Deep mode** uses FTS5/hybrid search to find the top 5 relevant articles, loads their full content, and calls the standard LLM tier. Includes citations and follow-up suggestions.

Answers can be filed to the wiki with `grove file`. Filed answers get `origin: query` and `pinned: true` in their front matter, and are excluded from compilation source loading to prevent circular reasoning.

## Quality ratchet

Runs after every compile, before the git commit. Failing checks abort the commit — the wiki is unchanged.

| Check | Threshold | Severity |
|-------|-----------|----------|
| Provenance coverage | <50% of factual sentences cite a source | BLOCK |
| New contradictions | Any unresolved contradiction between articles | BLOCK |
| Coverage drop | >10% fewer sources referenced than previous compile | BLOCK |
| Broken wiki-links | `[[links]]` that don't resolve to a file | WARN |
| Human annotation preservation | Any `<!-- grove:human -->` block removed | BLOCK |
| Pinned article overwrite | Any `pinned: true` article modified | BLOCK |
| Query article used as source | Any `origin: query` article in source loader | BLOCK |

Reports are written to `.grove/logs/ratchet-<timestamp>.json`.

## Article format

The compilation prompt instructs the LLM to separate articles with HTML comment markers:

```markdown
<!-- grove:article wiki/topics/transformers/overview.md -->
---
title: "Transformer Architecture"
compiled_from:
  - raw/papers/attention-is-all-you-need.md
  - raw/articles/transformers-explained.md
concepts: [transformer, self-attention, encoder-decoder]
summary: "One-line summary."
last_compiled: "2026-04-03T14:22:00Z"
---

# Transformer Architecture

Article body with [[wiki-links]] and [source: filename.md] citations...
```

**Required front matter:** `title`, `compiled_from`, `concepts`, `summary`, `last_compiled`

The parser handles missing separators (falls back to `---` boundary detection), missing fields (fills defaults, flags a warning), and truncated output (commits complete articles, discards the incomplete last one).

## Plugin–CLI protocol

The Obsidian plugin spawns `grove <command> --json` as a child process and reads NDJSON events line-by-line from stdout:

```json
{"type": "progress", "step": "loading_sources", "pct": 10}
{"type": "progress", "step": "llm_call", "pct": 40}
{"type": "result", "data": {"articles_created": 12, "cost_usd": 0.43}}
{"type": "error", "code": "rate_limit", "message": "Rate limit exceeded"}
```

No HTTP server, no daemon — subprocess spawning adds ~300ms latency, which is acceptable for user-initiated actions. The plugin handles process crashes, ENOENT (CLI not found), and non-zero exit codes gracefully.

## LLM routing

| Tier | Primary | Fallback | Used for |
|------|---------|----------|----------|
| fast | Ollama (`llama3.2`) | Claude Haiku | Summaries, embeddings, health pre-checks |
| standard | Claude Sonnet | — | Compilation, deep queries |
| powerful | Claude Opus | — | Research-mode queries (explicit opt-in) |

When Ollama is unreachable, fast-tier operations fall back to Haiku automatically. Costs are tracked per task type in `.grove/logs/costs.jsonl`.

## Search

Single SQLite database (`.grove/search.db`) holds both FTS5 keyword index and `sqlite-vec` embeddings.

- **Keyword:** BM25 ranking via FTS5
- **Semantic:** Cosine similarity via `nomic-embed-text` embeddings (Ollama, local)
- **Hybrid:** BM25 × 0.5 + cosine × 0.5, reranked

Articles are chunked at 512 tokens with 64-token overlap. Search returns article-level results with the best-matching chunk as context. If Ollama is unavailable, semantic mode returns an error; hybrid mode falls back to keyword-only with a warning.

## Data integrity

1. **Atomic writes.** `ArticleWriter` writes to a temp directory first, then moves files. If the ratchet blocks, temp files are cleaned up — the wiki is unchanged.
2. **Git as undo.** Every successful compile auto-commits. `grove rollback` is `git revert`. No bespoke undo mechanism.
3. **Human annotations.** `<!-- grove:human -->` blocks are extracted before write and re-injected after. The LLM never sees them.
4. **Query isolation.** `SourceLoader` filters out `origin: query` files at load time. Filed answers cannot contaminate the compilation input.

## Design decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Plugin communication | Subprocess, not HTTP | No daemon lifecycle, no port conflicts, 300ms acceptable |
| PDF conversion | `pymupdf4llm` (MIT) | `marker` is GPL-3.0, incompatible with MIT licence |
| Search storage | sqlite-vec + FTS5 | Single file, no separate vector store process |
| Embedding model | `nomic-embed-text` | 768-dim, MIT licence, runs locally via Ollama |
| Package name | `grove-kb` | `grove` is taken on PyPI |
| Prompt templates | `string.Template` | Variable substitution only — Jinja2 is unnecessary |
| Deep query tier | Sonnet (not Opus) | 5–10x cost difference; Opus available via `--research` flag |
| Budget defaults | $5/day limit, $3 warn | Conservative — a full compile is ~$0.50 |

## Future (not in scope)

- **Incremental compilation** — checksum-based change detection, compile only affected articles
- **Web platform** — FastAPI backend, Next.js frontend, hosted groves
- **Browser extension** — clip web pages directly into a grove
- **MCP server** — expose grove operations as tools for other agents
- **Grove federation** — query across multiple groves via API
