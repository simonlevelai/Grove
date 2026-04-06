"""grove serve — local web UI for searching the wiki.

Serves a FastAPI + HTMX application on ``http://localhost:8765``.
Provides a search box with hybrid search results, article previews,
and Obsidian deep links.  No JavaScript framework — HTMX only,
Tailwind CSS via CDN.

WCAG 2.1 AA: semantic HTML, keyboard navigable, sufficient contrast.
"""

from __future__ import annotations

import html
import logging
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _grove_root_from_cwd() -> Path:
    """Walk up from CWD to find the grove root."""
    candidate = Path.cwd()
    while True:
        if (candidate / ".grove" / "config.yaml").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    raise FileNotFoundError("Not a grove. Run `grove init` first.")


def _escape(text: str) -> str:
    """HTML-escape text for safe embedding in templates."""
    return html.escape(text, quote=True)


def _render_search_results(results: list, warnings: list[str], query: str) -> str:
    """Render search results as an HTML fragment for HTMX swap."""
    parts: list[str] = []

    for warning in warnings:
        parts.append(
            f'<div class="bg-amber-50 border-l-4 border-amber-400 p-3 mb-4'
            f' text-amber-800 text-sm" role="alert">{_escape(warning)}</div>'
        )

    if not results:
        parts.append('<p class="text-gray-500 text-centre py-8">No results found.</p>')
        return "\n".join(parts)

    parts.append('<ol class="space-y-4" role="list">')
    for result in results:
        title = _escape(result.title)
        path = _escape(result.article_path)
        score = f"{result.score:.4f}"

        # Article preview — first 300 chars of best chunk.
        preview = result.best_chunk[:300]
        if len(result.best_chunk) > 300:
            preview += "..."
        preview = _escape(preview)

        # Obsidian deep link.
        obsidian_path = urllib.parse.quote(result.article_path, safe="")
        obsidian_link = f"obsidian://open?path={obsidian_path}"

        parts.append(f"""\
<li class="bg-white rounded-lg shadow-sm border border-gray-200 p-4
    hover:shadow-md transition-shadow">
  <div class="flex items-start justify-between gap-2">
    <h3 class="text-lg font-semibold text-gray-900">{title}</h3>
    <span class="text-xs text-gray-400 font-mono shrink-0"
      >score: {score}</span>
  </div>
  <p class="text-sm text-gray-500 font-mono mt-1">{path}</p>
  <p class="text-sm text-gray-700 mt-2 leading-relaxed">{preview}</p>
  <div class="mt-3">
    <a href="{obsidian_link}"
       class="inline-flex items-center text-sm text-violet-600
              hover:text-violet-800 font-medium"
       aria-label="Open {title} in Obsidian">
      Open in Obsidian &rarr;
    </a>
  </div>
</li>""")

    parts.append("</ol>")
    parts.append(
        f'<p class="text-sm text-gray-400 mt-4">'
        f"{len(results)} result(s) for "
        f"&ldquo;{_escape(query)}&rdquo;</p>"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# HTML page template
# ---------------------------------------------------------------------------

_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Grove Search</title>
  <link rel="stylesheet"
    href="https://cdn.jsdelivr.net/npm/@tailwindcss/cdn@4" />
  <script src="https://unpkg.com/htmx.org@2.0.4"
    integrity="sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+"
    crossorigin="anonymous"></script>
  <style>
    /* Spinner for HTMX indicator */
    .htmx-indicator { display: none; }
    .htmx-request .htmx-indicator,
    .htmx-request.htmx-indicator { display: inline-block; }
  </style>
</head>
<body class="bg-gray-50 min-h-screen">
  <a href="#search-input"
     class="sr-only focus:not-sr-only focus:absolute focus:top-2
            focus:left-2 focus:z-50 focus:bg-white focus:p-2
            focus:rounded focus:shadow-lg">
    Skip to search
  </a>

  <header class="bg-white border-b border-gray-200">
    <div class="max-w-3xl mx-auto px-4 py-6">
      <h1 class="text-2xl font-bold text-gray-900">Grove Search</h1>
      <p class="text-sm text-gray-500 mt-1">
        Search your compiled knowledge base
      </p>
    </div>
  </header>

  <main class="max-w-3xl mx-auto px-4 py-8">
    <form hx-get="/search" hx-target="#results" hx-indicator="#spinner"
          hx-push-url="true" role="search" aria-label="Search the wiki">
      <div class="flex gap-2">
        <label for="search-input" class="sr-only">Search query</label>
        <input
          id="search-input"
          type="search"
          name="q"
          placeholder="Search articles..."
          autocomplete="off"
          autofocus
          required
          class="flex-1 rounded-lg border border-gray-300 px-4 py-2.5
                 text-gray-900 placeholder-gray-400
                 focus:outline-none focus:ring-2 focus:ring-violet-500
                 focus:border-violet-500"
        >
        <select name="mode" aria-label="Search mode"
          class="rounded-lg border border-gray-300 px-3 py-2.5
                 text-gray-700 bg-white
                 focus:outline-none focus:ring-2 focus:ring-violet-500">
          <option value="hybrid">Hybrid</option>
          <option value="keyword">Keyword</option>
          <option value="semantic">Semantic</option>
        </select>
        <button type="submit"
          class="rounded-lg bg-violet-600 px-5 py-2.5 text-white
                 font-medium hover:bg-violet-700
                 focus:outline-none focus:ring-2 focus:ring-violet-500
                 focus:ring-offset-2">
          Search
        </button>
      </div>
      <span id="spinner" class="htmx-indicator text-sm text-gray-400 mt-2">
        Searching...
      </span>
    </form>

    <section id="results" class="mt-8" aria-live="polite">
    </section>
  </main>

  <footer class="max-w-3xl mx-auto px-4 py-6 text-centre text-xs text-gray-400">
    Grove &mdash; LLM-compiled knowledge bases
  </footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def create_app(grove_root: Path | None = None) -> FastAPI:
    """Create the FastAPI application for grove serve.

    Parameters
    ----------
    grove_root:
        Explicit grove root path.  If ``None``, auto-detected from CWD.
    """
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "FastAPI is required for grove serve. "
            "Install it with: pip install grove-kb[full]"
        ) from exc

    if grove_root is None:
        grove_root = _grove_root_from_cwd()

    db_path = grove_root / ".grove" / "search.db"

    app = FastAPI(title="Grove Search", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve the main search page."""
        return _PAGE_HTML

    @app.get("/search", response_class=HTMLResponse)
    async def search_endpoint(q: str = "", mode: str = "hybrid") -> str:
        """Run a search and return an HTML fragment for HTMX."""
        if not q.strip():
            return '<p class="text-gray-500 py-4">Enter a search query.</p>'

        if not db_path.exists():
            return (
                '<p class="text-red-600 py-4">'
                "Search index not found. Run <code>grove compile</code> first."
                "</p>"
            )

        valid_modes = {"keyword", "semantic", "hybrid"}
        if mode not in valid_modes:
            mode = "hybrid"

        warnings: list[str] = []

        if mode == "keyword":
            from grove.search.fts import FTSIndex

            fts = FTSIndex(db_path)
            results = fts.search(q, limit=20)
        elif mode == "semantic":
            from grove.search.vec import OllamaUnavailableError, VecIndex

            vec = VecIndex(db_path)
            try:
                results = vec.search(q, limit=20)
            except OllamaUnavailableError as exc:
                return (
                    '<div class="bg-red-50 border-l-4 border-red-400'
                    ' p-3 text-red-800 text-sm" role="alert">'
                    f"Semantic search unavailable: {_escape(str(exc))}"
                    "</div>"
                )
        else:
            from grove.search.hybrid import HybridSearch

            hybrid = HybridSearch(db_path)
            results, warnings = hybrid.search(q, limit=20)

        return _render_search_results(results, warnings, q)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    return app
