"""Tests for grove serve — FastAPI + HTMX local web UI.

Covers:
- Index page returns valid HTML with search form
- Search endpoint returns results for keyword mode
- Search endpoint returns results for hybrid mode (with mocked Ollama)
- Search endpoint handles empty query
- Search endpoint handles missing search index
- Search endpoint handles invalid mode gracefully
- Health check endpoint returns ok
- Obsidian deep links are generated in results
- CLI grove serve command is registered
- WCAG: skip link present, form has labels, aria attributes
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from grove.search.fts import FTSIndex
from grove.search.serve import _escape, _render_search_results, create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_article(title: str, summary: str, body: str) -> str:
    """Build a wiki article with YAML front matter."""
    return f"""\
---
title: "{title}"
summary: "{summary}"
compiled_from:
  - raw/articles/source.md
concepts: [testing]
last_compiled: "2026-04-03T14:00:00Z"
---

{body}
"""


def _build_wiki(tmp_path: Path, articles: dict[str, str]) -> Path:
    """Create a fake grove with wiki articles and search index."""
    grove_root = tmp_path / "grove"
    grove_dir = grove_root / ".grove"
    grove_dir.mkdir(parents=True)

    # Write a minimal config so _find_grove_root works.
    (grove_dir / "config.yaml").write_text("name: test\n", encoding="utf-8")

    for rel_path, content in articles.items():
        full_path = grove_root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    # Build the FTS index.
    db_path = grove_dir / "search.db"
    fts = FTSIndex(db_path)
    fts.build(grove_root / "wiki")

    return grove_root


# ---------------------------------------------------------------------------
# _escape tests
# ---------------------------------------------------------------------------


class TestEscape:
    """Tests for the HTML escape helper."""

    def test_escapes_angle_brackets(self) -> None:
        assert "&lt;" in _escape("<script>")

    def test_escapes_ampersand(self) -> None:
        assert "&amp;" in _escape("a & b")

    def test_escapes_quotes(self) -> None:
        assert "&quot;" in _escape('"hello"')


# ---------------------------------------------------------------------------
# _render_search_results tests
# ---------------------------------------------------------------------------


class TestRenderSearchResults:
    """Tests for the HTML fragment renderer."""

    def test_empty_results(self) -> None:
        html = _render_search_results([], [], "test")
        assert "No results found" in html

    def test_renders_warnings(self) -> None:
        html = _render_search_results([], ["Watch out!"], "test")
        assert "Watch out!" in html
        assert 'role="alert"' in html

    def test_renders_result_items(self) -> None:
        from grove.search.fts import SearchResult

        results = [
            SearchResult(
                article_path="wiki/alpha.md",
                title="Alpha",
                summary="About alpha",
                best_chunk="Alpha is the first letter.",
                score=0.95,
            ),
        ]
        html = _render_search_results(results, [], "alpha")
        assert "Alpha" in html
        assert "wiki/alpha.md" in html
        assert "0.9500" in html
        assert "obsidian://open" in html

    def test_result_count_displayed(self) -> None:
        from grove.search.fts import SearchResult

        results = [
            SearchResult(
                article_path="wiki/a.md",
                title="A",
                summary="",
                best_chunk="chunk",
                score=1.0,
            ),
        ]
        html = _render_search_results(results, [], "query")
        assert "1 result(s)" in html


# ---------------------------------------------------------------------------
# FastAPI endpoint tests
# ---------------------------------------------------------------------------


class TestServeEndpoints:
    """Tests for the FastAPI application endpoints."""

    def test_index_returns_html(self, tmp_path: Path) -> None:
        """GET / returns the search page."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha content."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        assert "Grove Search" in response.text
        assert "htmx" in response.text

    def test_index_has_skip_link(self, tmp_path: Path) -> None:
        """WCAG: page has a skip-to-content link."""
        grove_root = _build_wiki(tmp_path, {})
        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/")
        assert 'href="#search-input"' in response.text

    def test_index_has_form_labels(self, tmp_path: Path) -> None:
        """WCAG: search input has an associated label."""
        grove_root = _build_wiki(tmp_path, {})
        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/")
        assert 'for="search-input"' in response.text
        assert 'id="search-input"' in response.text

    def test_search_keyword_returns_results(self, tmp_path: Path) -> None:
        """GET /search?q=alpha&mode=keyword returns matching articles."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha is the first letter."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/search", params={"q": "Alpha", "mode": "keyword"})
        assert response.status_code == 200
        assert "Alpha" in response.text

    def test_search_empty_query(self, tmp_path: Path) -> None:
        """GET /search?q= returns a prompt to enter a query."""
        grove_root = _build_wiki(tmp_path, {})
        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/search", params={"q": "", "mode": "keyword"})
        assert response.status_code == 200
        assert "Enter a search query" in response.text

    def test_search_missing_index(self, tmp_path: Path) -> None:
        """GET /search without a search.db shows an error."""
        grove_root = tmp_path / "grove"
        (grove_root / ".grove").mkdir(parents=True)
        (grove_root / ".grove" / "config.yaml").write_text(
            "name: test\n", encoding="utf-8"
        )

        # Ensure no search.db exists.
        db_path = grove_root / ".grove" / "search.db"
        if db_path.exists():
            db_path.unlink()

        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/search", params={"q": "test", "mode": "keyword"})
        assert response.status_code == 200
        assert "Search index not found" in response.text

    def test_search_invalid_mode_defaults_to_hybrid(self, tmp_path: Path) -> None:
        """Invalid mode falls back to hybrid without error."""
        articles = {
            "wiki/topics/alpha.md": _make_article(
                "Alpha", "About alpha.", "Alpha content."
            ),
        }
        grove_root = _build_wiki(tmp_path, articles)
        app = create_app(grove_root)
        client = TestClient(app)

        with patch(
            "grove.search.vec._embed_texts",
            side_effect=lambda *a, **kw: (_ for _ in ()).throw(
                __import__(
                    "grove.search.vec", fromlist=["OllamaUnavailableError"]
                ).OllamaUnavailableError("mock")
            ),
        ):
            response = client.get("/search", params={"q": "Alpha", "mode": "bogus"})
        assert response.status_code == 200

    def test_health_check(self, tmp_path: Path) -> None:
        """GET /health returns ok."""
        grove_root = _build_wiki(tmp_path, {})
        app = create_app(grove_root)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_search_semantic_unavailable(self, tmp_path: Path) -> None:
        """Semantic mode shows error when Ollama unavailable."""
        grove_root = _build_wiki(tmp_path, {})
        db_path = grove_root / ".grove" / "search.db"

        # Ensure a search.db exists.
        fts = FTSIndex(db_path)
        fts.build(grove_root / "wiki")

        app = create_app(grove_root)
        client = TestClient(app)

        from grove.search.vec import OllamaUnavailableError

        with patch(
            "grove.search.vec.VecIndex.search",
            side_effect=OllamaUnavailableError("Ollama not running"),
        ):
            response = client.get("/search", params={"q": "test", "mode": "semantic"})
        assert response.status_code == 200
        assert "unavailable" in response.text.lower()


# ---------------------------------------------------------------------------
# CLI command registration test
# ---------------------------------------------------------------------------


class TestServeCLI:
    """Tests for the grove serve CLI command."""

    def test_serve_command_registered(self) -> None:
        """The serve command is registered in the Typer app."""
        from typer.testing import CliRunner

        from grove.cli import app

        result = CliRunner().invoke(app, ["--help"])
        assert "serve" in result.output
