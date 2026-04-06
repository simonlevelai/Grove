# Grove for Obsidian

Knowledge compiler plugin -- turns raw sources into a structured, interlinked wiki directly from Obsidian.

## What it does

Grove compiles your research documents (PDFs, HTML, Markdown, plain text) into a structured wiki with full provenance tracking. This plugin provides an Obsidian-native interface to the Grove CLI:

- **Compile** your sources into wiki articles from the command palette
- **Query** your knowledge base in a sidebar panel with quick and deep modes
- **File** query answers into the wiki as pinned articles
- **Health check** your wiki for broken links, stale sources, and gaps

## Requirements

- [Obsidian](https://obsidian.md) 1.5.0 or later (desktop only)
- [Grove CLI](https://github.com/digitalwonderlab/grove) (`pip install grove-kb`)
- Python 3.11+
- An Anthropic API key

## Installation

### From source

1. Clone the Grove repository
2. Build the plugin:
   ```bash
   cd obsidian-plugin
   npm install
   npm run build
   ```
3. Copy `main.js` and `manifest.json` to your vault at `.obsidian/plugins/grove/`
4. Restart Obsidian and enable "Grove" in Settings > Community Plugins

## Configuration

Open Settings > Grove to configure:

| Setting | Description |
|---------|-------------|
| **CLI path** | Path to the `grove` executable. Click "Auto-detect" to find it on PATH. |
| **API key** | Your Anthropic API key (stored locally, never transmitted to third parties). |
| **Dry run** | When enabled, compile estimates cost without making LLM calls. |
| **Default query mode** | Choose between quick (fast, index-only) and deep (full article loading). |

If your vault contains multiple groves (directories with `.grove/config.yaml`), a grove selector dropdown appears in settings.

## Usage

### Compile

Open the command palette (`Cmd/Ctrl+P`) and run **Grove: Compile**. A progress notice updates as compilation proceeds. On completion you will see article counts and cost.

### Query

Click the Grove icon in the left ribbon to open the query panel. Type a question, choose quick or deep mode, and submit. The answer renders as Markdown with wiki-link citations. Click "File this answer" to promote it to the wiki.

### Health

Run **Grove: Health** from the command palette to check wiki quality. Issues are displayed as Obsidian notices.

## How it works

The plugin spawns the `grove` CLI as a child process and reads NDJSON events from stdout. All compilation, search, and query logic runs in the Python engine. The plugin handles only UI, settings, and subprocess lifecycle.

Communication protocol:
- **Progress events:** `{"type": "progress", "step": "...", "pct": 50}`
- **Result events:** `{"type": "result", ...}`
- **Error events:** `{"type": "error", "message": "...", "code": "..."}`

## Licence

MIT
