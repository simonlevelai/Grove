# CLAUDE.md — Grove

**Project:** Grove  
**One-line description:** Open-source LLM knowledge compiler — raw documents in, structured interlinked wiki out.  
**Owner:** Simon Allen / Digital Wonderlab  
**Status:** Phase 1 — Compilation engine + Obsidian plugin  
**Last updated:** 2026-04-03

---

## What Grove Is

Grove is a knowledge compiler, not a chat tool or RAG pipeline. The LLM is the *author and maintainer* of a structured markdown wiki. Users ingest sources, the LLM compiles them into cross-linked articles, and knowledge compounds as queries are filed back in. Everything lives in plain files, viewable in Obsidian.

---

## Tech Stack

### Phase 1 (Current)
| Layer | Technology |
|-------|-----------|
| Compilation engine | Python (CLI) |
| Obsidian plugin | TypeScript |
| LLM providers | Anthropic Claude (standard/fast), Ollama (local fast tier) |
| Storage | Local filesystem + Git |
| Search | SQLite FTS5 + sqlite-vec |

### Phase 2 (Planned)
| Layer | Technology |
|-------|-----------|
| Backend | FastAPI on Azure Container Apps |
| Frontend | Next.js on Azure Static Web Apps |
| Database | Supabase (auth, storage, vector search) |
| Browser extension | TypeScript (Chrome/Firefox) |

---

## Repository Structure (Target)

```
grove/
  grove/                    # Python package (compilation engine)
    ingest/                 # PDF/HTML conversion pipeline
    compile/                # Compilation engine + prompt management
    query/                  # Query engine
    health/                 # Quality ratchet + linting
    search/                 # SQLite FTS5 + embeddings
    git/                    # Git commit automation
    cli.py                  # CLI entry point (Typer)
  obsidian-plugin/          # TypeScript Obsidian plugin
    src/
      main.ts               # Plugin entry point
      compile.ts            # Compile command
      query.ts              # Query sidebar
      file.ts               # File-back command
    manifest.json
    package.json
  prompts/                  # Default compilation prompts (shipped with engine)
    compile-wiki.md
    compile-article.md
    query.md
    summarise.md
    contradiction.md
  tests/
  pyproject.toml
  .grove-example/           # Demo vault for onboarding
```

---

## Knowledge Base Directory Structure (at runtime)

```
my-knowledge-base/
  .git/
  .grove/
    config.yaml             # LLM routing + budget
    state.json              # dependency graph, checksums
    prompts/                # editable — override shipped defaults
    search.db
    logs/
  raw/
    _manifest.md
    articles/
    papers/
    repos/
    images/
  wiki/
    _index.md
    _concepts.md
    _health.md
    topics/
    people/
    glossary/
    connections/
  queries/
  outputs/
```

---

## Key Architecture Decisions (see ARCH.md for full detail)

- **Plugin ↔ Python:** Subprocess, not HTTP server. Plugin spawns `grove <command> --json`, reads NDJSON events from stdout
- **PDF conversion:** `pymupdf4llm` (MIT), not marker (GPL-3.0)
- **Search:** sqlite-vec + Ollama `nomic-embed-text`, degrades to FTS5-only if Ollama absent
- **Packaging:** `grove-kb` on PyPI (`grove` is taken). `pip install grove-kb[full]` for PDF/search extras
- **Templates:** Python `string.Template` for prompts, not Jinja2
- **Token counting:** Anthropic SDK `count_tokens()` with word-count heuristic fallback
- **Article separation:** `<!-- grove:article wiki/path/to/file.md -->` markers in LLM output

## Coding Standards

- **UK English** in all code, comments, and docs
- Comments explain *why*, not *what*
- Python: type hints throughout, Ruff for linting, Black for formatting
- TypeScript: strict mode, ESLint + Prettier
- No hardcoded secrets — API keys via env vars or `.grove/config.yaml` (gitignored)
- Every compile auto-commits to git — never mutate wiki without a commit

## Key Invariants (Never Break These)

1. **A failed compile never makes the wiki worse.** Git ensures this — partial failures commit partial successes, rollback is always available
2. **Filed query answers are never used as compilation sources.** `origin: query` articles are output, not input
3. **Human annotations (`<!-- grove:human -->`) are preserved verbatim.** The compiler must never touch them
4. **Provenance is non-negotiable.** Every factual claim must be attributable to a source file

---

## DWL Agents

Available globally via slash commands:

- `/dwl-pm` — write or update PRD.md / FRD.md
- `/dwl-plan` — create ARCH.md + TASKS.md
- `/dwl-poc` — prototype a feature quickly
- `/dwl-dev` — implement tasks from TASKS.md to production quality
- `/dwl-audit` — codebase analysis and recommendations
- `/dwl-security` — OWASP security audit

Specs live in `.claude/specs/`. Read them before implementing anything.

---

## Environment Setup (Phase 1)

```bash
# Python engine (requires Python 3.11+)
python3.12 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e ".[dev]"

# Obsidian plugin
cd obsidian-plugin
npm install
npm run dev   # watch mode

# Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run CLI
grove --help
```
