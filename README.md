# Grove

LLM-compiled knowledge bases. Feed Grove your raw documents -- PDFs, HTML pages, Markdown notes, plain text -- and it compiles them into a structured, interlinked wiki with full provenance tracking. Every article cites its sources, every recompilation preserves your annotations, and the whole thing lives in git so you can diff, rollback, and audit changes.

## Quick start

```bash
pip install grove-kb
grove init "my-research"
grove ingest-dir ~/papers/
grove compile
grove query "What are the main findings?"
```

Your compiled wiki is now in `wiki/`. Open the folder in [Obsidian](https://obsidian.md) to browse articles and follow `[[wiki-links]]` between them.

## Installation

### Core (required)

```bash
pip install grove-kb
```

Requires Python 3.11+ and an Anthropic API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Full (optional extras)

```bash
pip install "grove-kb[full]"
```

Adds PDF conversion (pymupdf4llm, pdfminer.six), HTML extraction (readability-lxml, markdownify), semantic search (sqlite-vec, ollama), and the local web UI (FastAPI, uvicorn).

### Local Ollama (optional)

For fast-tier operations and semantic search, install [Ollama](https://ollama.ai) and pull a model:

```bash
ollama pull nomic-embed-text
```

Grove detects Ollama automatically on `localhost:11434` and uses it for summarisation, embeddings, and quick queries. When Ollama is unavailable, it falls back to the Anthropic API.

## BYOK setup

Grove is bring-your-own-key. Set your Anthropic API key before using compile or query commands:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Alternatively, `grove init` will prompt you to enter it interactively. The key is stored in your shell environment only -- never written to config files.

To configure the daily spend limit, edit `.grove/config.yaml`:

```yaml
budget:
  daily_limit_usd: 5.00
  warn_at_usd: 3.00
```

## CLI command reference

### Initialisation

| Command | Description |
|---------|-------------|
| `grove init [NAME]` | Initialise a new grove. Creates directory structure, config, and git repo. |
| `grove init --dir PATH` | Initialise in a specific directory. |
| `grove version` | Print the Grove version. |

### Ingest

| Command | Description |
|---------|-------------|
| `grove ingest <path-or-url>` | Ingest a single file or URL. Runs: convert, score, dedup, summarise, register. |
| `grove ingest-dir <directory>` | Ingest all supported files (`.pdf`, `.html`, `.htm`, `.md`, `.txt`) from a directory. |

### Compile

| Command | Description |
|---------|-------------|
| `grove compile` | Compile raw sources into structured wiki articles. |
| `grove compile --dry-run` | Estimate token count and cost without making LLM calls. |
| `grove compile --json` | Emit NDJSON progress events (for the Obsidian plugin). |

### Search

| Command | Description |
|---------|-------------|
| `grove search <query>` | Keyword search (BM25) over the wiki. |
| `grove search <query> --mode semantic` | Semantic search via Ollama embeddings (cosine similarity). |
| `grove search <query> --mode hybrid` | Combined BM25 + cosine similarity with score fusion. |
| `grove search <query> -n 20` | Limit results (default: 10). |

### Query

| Command | Description |
|---------|-------------|
| `grove query "<question>"` | Deep query: loads top-5 articles, synthesises an answer with citations. |
| `grove query "<question>" --quick` | Quick query: index-only, fast LLM tier, under 5 seconds. |
| `grove query "<question>" -o md` | Output as Markdown. |
| `grove query "<question>" -o slides` | Output as Marp slides. |
| `grove query "<question>" --json` | NDJSON output (for Obsidian plugin). |
| `grove file [path]` | Promote a query result to the wiki (pins it and sets `origin: query`). |

### Source management

| Command | Description |
|---------|-------------|
| `grove raw list` | List all ingested sources with quality flags. |
| `grove raw list --failed` | Show only failed or poor-quality sources. |
| `grove raw retry <path>` | Re-run the ingest pipeline on a single source. |
| `grove raw drop <path>` | Remove a source and flag affected wiki articles as stale. |

### Article management

| Command | Description |
|---------|-------------|
| `grove pin <path>` | Pin an article so it is preserved across recompilations. |
| `grove unpin <path>` | Unpin an article so it can be updated by recompilations. |

### History and costs

| Command | Description |
|---------|-------------|
| `grove log` | List grove auto-commit history with dates and article counts. |
| `grove diff` | Show article-level changes in the most recent grove commit. |
| `grove rollback` | Revert the last grove commit (uses `git revert`). |
| `grove rollback --to <sha>` | Restore wiki to its state at a specific commit. |
| `grove costs` | Display LLM cost summary by task type and model. |
| `grove costs --today` | Show today's spend against the daily budget. |

### Health and maintenance

| Command | Description |
|---------|-------------|
| `grove health` | Run health checks: provenance, contradictions, staleness, gaps, orphans. |
| `grove health --json` | NDJSON output (for Obsidian plugin). |
| `grove health --fix` | Auto-create stub articles for broken wiki-links and commit. |

### Web UI

| Command | Description |
|---------|-------------|
| `grove serve` | Start the local search UI at `http://localhost:8765`. |
| `grove serve --port 9000` | Use a custom port. |

## Obsidian plugin

The Grove plugin adds compile, query, and health commands directly to Obsidian's command palette. Install it from the `obsidian-plugin/` directory:

1. Build the plugin:
   ```bash
   cd obsidian-plugin
   npm install
   npm run build
   ```

2. Copy `main.js` and `manifest.json` to your vault's `.obsidian/plugins/grove/` directory.

3. Enable "Grove" in Obsidian Settings > Community Plugins.

4. Configure the plugin: set your CLI path (auto-detected if `grove` is on PATH) and API key.

The plugin spawns the `grove` CLI as a subprocess and communicates via NDJSON events. All compilation logic runs in Python -- the plugin is a thin UI layer.

See [`obsidian-plugin/README.md`](obsidian-plugin/README.md) for details.

## Demo vault

A pre-compiled demo grove ships in `.grove-example/`. Open it in Obsidian to browse the wiki without needing an API key or running any commands. See [`.grove-example/DEMO.md`](.grove-example/DEMO.md) for details.

## Key concepts

- **Raw sources** (`raw/`) -- your original documents, converted to Markdown on ingest.
- **Wiki** (`wiki/`) -- the compiled output. Structured articles with YAML front matter, `[source: path.md]` citations, and `[[wiki-links]]`.
- **Pinned articles** -- articles with `pinned: true` in front matter are preserved across recompilations.
- **Human blocks** -- wrap your own notes in `<!-- grove:human -->` / `<!-- /grove:human -->` and they will survive recompilation.
- **Quality ratchet** -- blocks compilation if provenance coverage drops below 50%, contradictions are introduced, or invariants are violated.

## Directory structure

```
my-grove/
  .grove/
    config.yaml       # Configuration
    state.json        # Checksums, compile history
    search.db         # FTS5 + sqlite-vec index
    logs/             # Cost tracking, ratchet reports
    prompts/          # User prompt overrides
  raw/                # Ingested source documents
    _manifest.md      # Source metadata table
  wiki/               # Compiled wiki articles
    _index.md         # Article index
    _concepts.md      # Concept map
  queries/            # Saved query results
  outputs/            # Exported artefacts
```

## Contributing

1. Clone the repository and install in development mode:
   ```bash
   pip install -e ".[dev,full]"
   ```

2. Run the test suite:
   ```bash
   pytest tests/ -v
   ```

3. Check linting and formatting:
   ```bash
   ruff check grove/ tests/
   black --check grove/ tests/
   ```

4. Follow [conventional commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`.

## Licence

MIT
