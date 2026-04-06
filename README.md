# Grove

You have 200 papers and you can't keep it all in your head.

Existing LLM tools offer ephemeral chat (RAG) or manual notes with AI assist. Neither produces durable, structured, compounding knowledge. The proven second-brain frameworks — PARA, Zettelkasten, Evergreen Notes — all work, but fail because humans can't maintain them at scale. The maintenance cost is the bottleneck.

Grove removes the bottleneck. Feed it your raw documents — PDFs, HTML pages, markdown notes, plain text — and it compiles them into a structured, interlinked wiki. Every article cites its sources. Every recompilation preserves your annotations. The whole thing lives in git so you can diff, rollback, and audit every change.

**Three paradigms for LLM knowledge work:**

1. **RAG** — search and retrieve. Ephemeral.
2. **Chat** (Claude Projects, NotebookLM) — persistent conversation. No structure.
3. **Compilation** (Grove) — the LLM writes and maintains a second brain.

This idea originates from [Andrej Karpathy's LLM Knowledge Bases](https://x.com/karpathy/status/2039805659525644595): use LLMs not to chat about documents but to *compile* them into structured, interlinked wikis that compound over time. Grove is an open-source implementation of that vision.

## Quick start

```bash
pip install grove-kb
export ANTHROPIC_API_KEY="sk-ant-..."

grove init my-research
grove ingest-dir ~/papers/
grove compile
grove query "What are the main findings?"
```

Your compiled wiki is now in `wiki/`. Open the folder in [Obsidian](https://obsidian.md) to browse articles and follow `[[wiki-links]]` between them.

## How it works

```
Raw sources (PDFs, HTML, markdown)
  → grove ingest        # convert, score quality, deduplicate, summarise
  → grove compile       # LLM compiles sources into wiki articles
  → grove health        # check provenance, contradictions, staleness
  → grove query         # ask questions, file answers back into the wiki
```

**Ingest** converts documents to markdown, scores conversion quality, detects duplicates, and generates summaries with key concepts using a fast LLM.

**Compile** loads all sources (full text for short ones, summaries for long ones) into a single LLM call. The model produces structured articles with YAML front matter, `[source: filename.md]` citations, and `[[wiki-links]]`. A quality ratchet blocks the commit if provenance drops below 50% or contradictions are introduced.

**Query** searches your wiki (keyword, semantic, or hybrid) and synthesises answers citing specific articles. Answers can be filed back into the wiki, where they compound with source-compiled knowledge.

Everything auto-commits to git. `grove rollback` is always one command away.

## Installation

### Core

```bash
pip install grove-kb
```

Requires Python 3.11+ and an [Anthropic API key](https://console.anthropic.com/).

### Full extras

```bash
pip install "grove-kb[full]"
```

Adds PDF conversion (`pymupdf4llm`, `pdfminer.six`), HTML extraction (`readability-lxml`, `markdownify`), semantic search (`sqlite-vec`, `ollama`), and the local web UI (`FastAPI`, `uvicorn`).

### Local Ollama (optional)

For fast-tier operations and semantic search without API costs:

```bash
ollama pull nomic-embed-text
```

Grove detects Ollama automatically on `localhost:11434`. When unavailable, it falls back to the Anthropic API.

## Configuration

Grove stores config in `.grove/config.yaml`. Key settings:

```yaml
llm:
  routing:
    fast:
      provider: ollama              # local model for summaries, embeddings
      model: llama3.2
      fallback:
        provider: anthropic
        model: claude-haiku-4-5-20251001
    standard:
      provider: anthropic           # compilation and deep queries
      model: claude-sonnet-4-6

budget:
  daily_limit_usd: 5.00            # hard stop — no API calls beyond this
  warn_at_usd: 3.00                # warning threshold

compile:
  quality_threshold: partial        # exclude only 'poor' sources
```

API keys are read from environment variables (`ANTHROPIC_API_KEY`), never stored in config files.

## CLI reference

### Initialisation

| Command | Description |
|---------|-------------|
| `grove init [NAME]` | Create a new grove with directory structure, config, and git repo |
| `grove init --dir PATH` | Initialise in a specific directory |

### Ingest

| Command | Description |
|---------|-------------|
| `grove ingest <path-or-url>` | Ingest a single file or URL |
| `grove ingest-dir <directory>` | Bulk ingest all supported files from a directory |

### Compile

| Command | Description |
|---------|-------------|
| `grove compile` | Compile sources into wiki articles |
| `grove compile --dry-run` | Estimate token count and cost without calling the LLM |
| `grove compile --json` | NDJSON progress events (for the Obsidian plugin) |

### Query

| Command | Description |
|---------|-------------|
| `grove query "<question>"` | Deep query — loads relevant articles, synthesises an answer with citations |
| `grove query "<question>" --quick` | Quick query — index only, fast model, under 5 seconds |
| `grove query "<question>" -o md` | Output as markdown |
| `grove query "<question>" -o slides` | Output as Marp presentation slides |
| `grove file [path]` | Promote a query answer into the wiki |

### Search

| Command | Description |
|---------|-------------|
| `grove search <query>` | Keyword search (BM25) |
| `grove search <query> --mode semantic` | Semantic search via Ollama embeddings |
| `grove search <query> --mode hybrid` | Combined BM25 + cosine similarity |

### Source management

| Command | Description |
|---------|-------------|
| `grove raw list` | All ingested sources with quality flags |
| `grove raw list --failed` | Failed or poor-quality sources only |
| `grove raw retry <path>` | Re-run ingest on a single source |
| `grove raw drop <path>` | Remove a source, flag affected articles as stale |

### Article management

| Command | Description |
|---------|-------------|
| `grove pin <path>` | Preserve an article across recompilations |
| `grove unpin <path>` | Allow an article to be updated by recompilation |

### History and costs

| Command | Description |
|---------|-------------|
| `grove log` | Compile history with dates and article counts |
| `grove diff` | Article-level changes in the most recent compile |
| `grove rollback` | Revert the last compile (`git revert`) |
| `grove rollback --to <sha>` | Restore wiki to a specific commit |
| `grove costs` | LLM spend by task type and model |
| `grove costs --today` | Today's spend against the daily budget |

### Health

| Command | Description |
|---------|-------------|
| `grove health` | Provenance, contradictions, staleness, gaps, orphans |
| `grove health --fix` | Auto-fix broken wiki-links and commit |

### Web UI

| Command | Description |
|---------|-------------|
| `grove serve` | Local search UI at `http://localhost:8765` |

## Obsidian plugin

The plugin adds compile, query, and health commands to Obsidian's command palette. It communicates with the Python CLI via subprocess — all compilation runs locally.

**Install from source:**

```bash
cd obsidian-plugin && npm install && npm run build
```

Copy `main.js`, `manifest.json`, and `styles.css` to your vault's `.obsidian/plugins/grove/` directory. Enable in Settings > Community Plugins.

**Features:**
- **Compile** — progress tracking, dry-run modal, cost reporting
- **Query sidebar** — quick/deep modes, markdown-rendered answers, file-to-wiki
- **Multi-grove detection** — auto-discovers groves in your vault
- **Settings** — API key (masked), CLI path auto-detection, configurable defaults

See [`obsidian-plugin/README.md`](obsidian-plugin/README.md) for the full plugin guide.

## Scale and cost

| Sources | Approach | Compile cost | Compile time |
|---------|----------|-------------|-------------|
| 10–100 | Brute force (1M context) | ~$0.50 | ~45–90s |
| 100–500 | Incremental (planned) | ~$0.15 | ~15s |
| 500+ | Beyond current scope | — | — |

Grove is designed for 50–500 sources. If you have thousands of documents, a RAG pipeline is the right tool.

## Key concepts

- **Raw sources** (`raw/`) — your original documents, converted to markdown on ingest
- **Wiki** (`wiki/`) — compiled output with YAML front matter, `[source:]` citations, and `[[wiki-links]]`
- **Pinned articles** — `pinned: true` articles survive recompilation unchanged
- **Human blocks** — wrap your notes in `<!-- grove:human -->` / `<!-- /grove:human -->` and they survive recompilation
- **Quality ratchet** — blocks compilation if provenance drops below 50%, contradictions are introduced, or invariants are violated
- **Filed queries** — query answers promoted to the wiki are excluded from compilation sources, preventing circular reasoning

## Demo vault

A pre-compiled demo grove ships in `.grove-example/`. Open it in Obsidian to browse the wiki without an API key. See [`.grove-example/DEMO.md`](.grove-example/DEMO.md).

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system design: module structure, design decisions, quality ratchet specification, plugin protocol, and data integrity guarantees.

## Contributing

```bash
pip install -e ".[dev,full]"
pytest tests/ -v
ruff check grove/ tests/
black --check grove/ tests/
```

Follow [conventional commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`.

## Credits

Grove implements the [LLM Knowledge Bases](https://x.com/karpathy/status/2039805659525644595) concept described by Andrej Karpathy — using LLMs not to chat about documents but to compile them into structured, interlinked wikis that compound over time.

## Licence

[MIT](LICENSE)
