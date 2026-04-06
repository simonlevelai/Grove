"""Grove CLI — the primary interface for the knowledge compiler."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import httpx
import typer
import yaml
from rich.console import Console

from grove.config.defaults import (
    DEFAULT_CONFIG,
    EMPTY_STATE,
    GITIGNORE_LINES,
    GROVE_DIRS,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="grove",
    help="LLM-compiled knowledge bases. Raw documents in, structured wiki out.",
)

console = Console()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    show_version: bool = typer.Option(
        False, "--version", "-V", help="Print the Grove version and exit."
    ),
) -> None:
    """Grove — LLM-compiled knowledge bases."""
    if show_version:
        from grove import __version__

        typer.echo(f"grove {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None and not show_version:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def version() -> None:
    """Print the Grove version."""
    from grove import __version__

    typer.echo(f"grove {__version__}")


@app.command()
def init(
    name: str | None = typer.Argument(
        None, help="Optional name for the knowledge base."
    ),
    directory: Path = typer.Option(  # noqa: B008
        ".",
        "--dir",
        "-d",
        help="Directory to initialise the grove in.",
        exists=False,
    ),
) -> None:
    """Initialise a new Grove knowledge base.

    Creates the directory structure, writes default configuration,
    initialises a git repository, and checks for local Ollama.
    """
    root = directory.resolve()

    # Guard: refuse to re-initialise an existing grove
    if (root / ".grove" / "config.yaml").exists():
        console.print(f"[yellow]Grove already initialised at {root}[/yellow]")
        raise typer.Exit(code=1)

    # 1. Create directory structure
    for dir_name in GROVE_DIRS:
        (root / dir_name).mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Created directory structure in {root}[/green]")

    # 2. Write config.yaml from defaults
    config_data = _prepare_config(name)
    config_path = root / ".grove" / "config.yaml"
    config_path.write_text(
        yaml.dump(config_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    console.print("[green]Wrote .grove/config.yaml[/green]")

    # 3. Write empty state.json
    state_path = root / ".grove" / "state.json"
    state_path.write_text(
        json.dumps(EMPTY_STATE, indent=2) + "\n",
        encoding="utf-8",
    )
    console.print("[green]Wrote .grove/state.json[/green]")

    # 4. Initialise git repo if not already in one
    _init_git(root)

    # 5. Write .gitignore
    _write_gitignore(root)

    # 6. Detect local Ollama
    _detect_ollama()

    # 7. Check for Anthropic API key
    _check_anthropic_key()

    # Summary
    display_name = name or root.name
    console.print(
        f'\n[bold green]Grove "{display_name}" initialised successfully.[/bold green]'
    )
    console.print("Next steps:")
    console.print("  1. Add sources to [cyan]raw/[/cyan]")
    console.print("  2. Run [cyan]grove ingest <path>[/cyan]")
    console.print("  3. Run [cyan]grove compile[/cyan]")


def _prepare_config(name: str | None) -> dict[str, object]:
    """Build the config dict, optionally injecting a name field."""
    import copy

    config = copy.deepcopy(DEFAULT_CONFIG)
    if name:
        # Store the name at the top level for identification
        config = {"name": name, **config}
    return config


def _init_git(root: Path) -> None:
    """Initialise a git repository if the directory is not already inside one."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            console.print("[dim]Git repository already exists.[/dim]")
            return
    except FileNotFoundError:
        console.print(
            "[yellow]git not found on PATH "
            "— skipping repository initialisation.[/yellow]"
        )
        return

    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    console.print("[green]Initialised git repository.[/green]")


def _write_gitignore(root: Path) -> None:
    """Write or append Grove-specific entries to .gitignore."""
    gitignore_path = root / ".gitignore"

    existing_lines: set[str] = set()
    if gitignore_path.exists():
        existing_lines = {
            line.strip()
            for line in gitignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    new_lines = [line for line in GITIGNORE_LINES if line not in existing_lines]

    if new_lines:
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing_lines:
                f.write("\n")
            f.write("# Grove\n")
            for line in new_lines:
                f.write(f"{line}\n")
        console.print("[green]Updated .gitignore[/green]")
    else:
        console.print("[dim].gitignore already contains Grove entries.[/dim]")


def _detect_ollama() -> None:
    """Check whether Ollama is running locally."""
    try:
        response = httpx.get(
            "http://localhost:11434/api/tags",
            timeout=3.0,
        )
        if response.status_code == 200:
            data = response.json()
            models = [m.get("name", "unknown") for m in data.get("models", [])]
            if models:
                console.print(
                    f"[green]Ollama detected with models: {', '.join(models)}[/green]"
                )
            else:
                console.print("[green]Ollama detected (no models pulled yet).[/green]")
            return
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    console.print(
        "[yellow]Ollama not detected at localhost:11434. "
        "Fast-tier operations will fall back to Anthropic API.[/yellow]"
    )


def _check_anthropic_key() -> None:
    """Check for ANTHROPIC_API_KEY in environment; prompt if missing."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[green]ANTHROPIC_API_KEY found in environment.[/green]")
        return

    console.print("[yellow]ANTHROPIC_API_KEY not found in environment.[/yellow]")

    # Only prompt if we are in an interactive terminal
    if not _is_interactive():
        console.print(
            "[dim]Run in an interactive terminal to set the key, "
            "or export ANTHROPIC_API_KEY before using Grove.[/dim]"
        )
        return

    try:
        key = typer.prompt(
            "Enter your Anthropic API key (or press Enter to skip)",
            default="",
            show_default=False,
        )
        if key.strip():
            os.environ["ANTHROPIC_API_KEY"] = key.strip()
            console.print(
                "[green]ANTHROPIC_API_KEY set for this session. "
                "Add it to your shell profile for persistence.[/green]"
            )
        else:
            console.print(
                "[dim]Skipped. Export ANTHROPIC_API_KEY before "
                "running grove compile or grove query.[/dim]"
            )
    except (EOFError, KeyboardInterrupt):
        console.print(
            "\n[dim]Skipped. Export ANTHROPIC_API_KEY before "
            "running grove compile or grove query.[/dim]"
        )


def _is_interactive() -> bool:
    """Return True if stdin appears to be an interactive terminal."""
    import sys

    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


# ------------------------------------------------------------------
# Grove detection helper
# ------------------------------------------------------------------


def _find_grove_root() -> Path:
    """Find the grove root by looking for ``.grove/config.yaml``.

    Searches the current directory and its parents.
    Raises ``typer.Exit(code=1)`` with an error message if not found.
    """
    candidate = Path.cwd()
    while True:
        if (candidate / ".grove" / "config.yaml").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    console.print("[red]Not a grove. Run `grove init` first.[/red]")
    raise typer.Exit(code=1)


# ------------------------------------------------------------------
# Ingest pipeline helpers
# ------------------------------------------------------------------

# File extensions accepted by grove ingest-dir
_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".html", ".htm", ".md", ".txt"}
)


def _is_url(path_str: str) -> bool:
    """Return True if *path_str* looks like an HTTP/HTTPS URL."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(path_str)
        return parsed.scheme in ("http", "https")
    except Exception:  # noqa: BLE001
        return False


def _slugify(text: str) -> str:
    """Convert *text* to a filesystem-safe slug."""
    import re

    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:80].strip("-") or "untitled"


def _subdirectory_for_mime(mime_type: str) -> str:
    """Return the raw/ subdirectory name for a given MIME type."""
    if mime_type == "application/pdf":
        return "papers"
    if mime_type == "text/html":
        return "articles"
    return "articles"


def _download_url(url: str, grove_root: Path) -> Path:
    """Download a URL and save it into ``raw/articles/``.

    Returns the path to the saved file.
    """
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()

    slug = _slugify(url.split("//")[-1].split("?")[0])
    filename = f"{slug}.html"

    target_dir = grove_root / "raw" / "articles"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    target_path.write_text(response.text, encoding="utf-8")
    return target_path


def _copy_source_to_raw(source: Path, grove_root: Path, mime_type: str) -> Path:
    """Copy a source file into the appropriate ``raw/`` subdirectory.

    Returns the path to the copy inside the grove.
    """
    import shutil

    subdir = _subdirectory_for_mime(mime_type)
    target_dir = grove_root / "raw" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source.name

    # Avoid overwriting an existing file with a different name
    if target_path.exists() and target_path.resolve() != source.resolve():
        stem = source.stem
        suffix = source.suffix
        counter = 1
        while target_path.exists():
            target_path = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1

    shutil.copy2(source, target_path)
    return target_path


def _run_ingest_pipeline(
    source_path: Path,
    original_path: str,
    grove_root: Path,
) -> dict[str, object]:
    """Run the full ingest pipeline on a single source file.

    Returns a dict with the result details for reporting.
    Raises on unrecoverable errors.
    """
    from grove.compile.prompt import PromptBuilder
    from grove.config.loader import ConfigLoader
    from grove.config.state import StateManager
    from grove.ingest.converter import Converter
    from grove.ingest.dedup import Deduplicator
    from grove.ingest.manifest import ManifestWriter
    from grove.ingest.quality import QualityScorer
    from grove.ingest.summariser import Summariser, SummaryResult
    from grove.llm.router import LLMRouter

    # 1. Convert
    converter = Converter()
    conversion = converter.convert(source_path)

    # 2. Score quality
    scorer = QualityScorer()
    quality = scorer.score(conversion)

    # 3. Dedup check (on the converted markdown content)
    state = StateManager(grove_root)
    dedup = Deduplicator(state)
    dedup_result = dedup.check(conversion.content)

    if dedup_result.is_duplicate:
        return {
            "status": "duplicate",
            "source": str(source_path),
            "duplicate_of": dedup_result.duplicate_of,
            "quality": quality,
            "word_count": conversion.word_count,
        }

    # 4. Summarise (skip if quality is poor)
    if quality == "poor":
        summary = SummaryResult(unsummarised=True, error="Poor quality")
    else:
        config = ConfigLoader(grove_root).load()
        router = LLMRouter(config, grove_root)
        prompt_builder = PromptBuilder(grove_root)
        summariser = Summariser(router, prompt_builder)
        summary = summariser.summarise(source_path, conversion.content)

        # Write summary into the source file's front matter
        summariser.write_front_matter(source_path, summary)

    # 5. Register in manifest and state.json
    manifest = ManifestWriter(grove_root)
    manifest.register(
        source_path=source_path,
        original_path=original_path,
        conversion=conversion,
        quality=quality,
        summary=summary,
        checksum=dedup_result.checksum,
    )

    return {
        "status": "ingested",
        "source": str(source_path),
        "quality": quality,
        "word_count": conversion.word_count,
        "concepts": summary.concepts,
    }


# ------------------------------------------------------------------
# grove ingest command
# ------------------------------------------------------------------


@app.command()
def ingest(
    path: str = typer.Argument(..., help="File path or URL to ingest."),  # noqa: B008
) -> None:
    """Ingest a single file or URL into the grove.

    Runs the full pipeline: convert, score, dedup, summarise,
    and register in the manifest.
    """
    grove_root = _find_grove_root()

    try:
        if _is_url(path):
            console.print(f"[dim]Downloading {path}...[/dim]")
            source_path = _download_url(path, grove_root)
            original_path = path
        else:
            file_path = Path(path).resolve()
            if not file_path.exists():
                console.print(f"[red]File not found: {path}[/red]")
                raise typer.Exit(code=1)

            # Detect MIME type before copying
            from grove.ingest.converter import Converter

            mime_type = Converter().detect_mime_type(file_path)
            source_path = _copy_source_to_raw(file_path, grove_root, mime_type)
            original_path = str(file_path)

        result = _run_ingest_pipeline(source_path, original_path, grove_root)

        _print_ingest_result(result)

    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Ingest failed: {exc}[/red]")
        logger.exception("Ingest pipeline error")
        raise typer.Exit(code=1) from None


def _print_ingest_result(result: dict[str, object]) -> None:
    """Print the result of an ingest operation to the console."""
    status = result["status"]

    if status == "duplicate":
        console.print(f"[yellow]Duplicate:[/yellow] {result['source']}")
        console.print(f"  Already ingested as: {result['duplicate_of']}")
        return

    quality = result["quality"]
    word_count = result["word_count"]
    concepts = result.get("concepts", [])

    quality_colour = {
        "good": "green",
        "partial": "yellow",
        "poor": "red",
    }.get(str(quality), "white")

    console.print(f"[green]Ingested:[/green] {result['source']}")
    console.print(
        f"  Quality: [{quality_colour}]{quality}[/{quality_colour}]"
        f"  |  Words: {word_count}"
    )
    if concepts:
        console.print(f"  Concepts: {', '.join(concepts)}")


# ------------------------------------------------------------------
# grove ingest-dir command
# ------------------------------------------------------------------


@app.command(name="ingest-dir")
def ingest_dir(
    directory: Path = typer.Argument(  # noqa: B008
        ...,
        help="Directory containing files to ingest.",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
) -> None:
    """Ingest all supported files from a directory.

    Processes *.pdf, *.html, *.htm, *.md, and *.txt files.
    Continues on individual failures and prints a summary report.
    """
    grove_root = _find_grove_root()
    resolved_dir = directory.resolve()

    # Collect all supported files
    files: list[Path] = sorted(
        f
        for f in resolved_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
    )

    if not files:
        console.print(f"[yellow]No supported files found in {resolved_dir}[/yellow]")
        raise typer.Exit()

    console.print(f"[dim]Found {len(files)} file(s) to ingest.[/dim]\n")

    succeeded = 0
    failed = 0
    duplicates = 0
    poor_quality = 0
    errors: list[tuple[str, str]] = []

    from grove.ingest.converter import Converter

    converter = Converter()

    for file_path in files:
        try:
            mime_type = converter.detect_mime_type(file_path)
            source_path = _copy_source_to_raw(file_path, grove_root, mime_type)
            result = _run_ingest_pipeline(source_path, str(file_path), grove_root)

            status = result["status"]
            if status == "duplicate":
                duplicates += 1
                console.print(f"  [yellow]skip[/yellow] {file_path.name} (duplicate)")
            elif result.get("quality") == "poor":
                poor_quality += 1
                succeeded += 1
                console.print(f"  [red]poor[/red] {file_path.name}")
            else:
                succeeded += 1
                quality = result.get("quality", "unknown")
                console.print(f"  [green]ok[/green]   {file_path.name} ({quality})")

        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append((file_path.name, str(exc)))
            console.print(f"  [red]fail[/red] {file_path.name}: {exc}")

    # Summary report
    console.print()
    from rich.table import Table

    table = Table(title="Ingest Summary", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Count")
    table.add_row("Succeeded", str(succeeded))
    table.add_row("Failed", str(failed))
    table.add_row("Duplicates", str(duplicates))
    table.add_row("Poor quality", str(poor_quality))
    console.print(table)


# ------------------------------------------------------------------
# grove compile command
# ------------------------------------------------------------------


def _ndjson_progress(step: str, pct: int, detail: str) -> None:
    """Emit a single NDJSON progress event to stdout."""
    import sys

    event = {"type": "progress", "step": step, "pct": pct}
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def _ndjson_result(result_data: dict[str, object]) -> None:
    """Emit a final NDJSON result event to stdout."""
    import sys

    event = {"type": "result", **result_data}
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def _ndjson_error(message: str, code: str) -> None:
    """Emit a NDJSON error event to stdout."""
    import sys

    event = {"type": "error", "message": message, "code": code}
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


@app.command()
def compile(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Estimate token count and cost without making an LLM call.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit NDJSON progress events to stdout (for Obsidian plugin).",
    ),
) -> None:
    """Compile raw sources into a structured wiki.

    Runs the full Phase 0 pipeline: load sources, build prompt, call LLM,
    parse articles, write to wiki/, run quality ratchet, and git commit.

    With --dry-run, estimates token count and cost without making an LLM call.
    With --json, emits NDJSON progress events for the Obsidian plugin.
    """
    grove_root = _find_grove_root()

    from grove.compile.engine import (
        CompileEngine,
        CompileError,
        NoSourcesError,
        RatchetFailedError,
    )
    from grove.compile.prompt import PromptBuilder
    from grove.config.loader import ConfigLoader
    from grove.llm.router import LLMRouter

    # Load config
    try:
        config = ConfigLoader(grove_root).load()
    except (FileNotFoundError, ValueError) as exc:
        if json_output:
            _ndjson_error(str(exc), "config_error")
        else:
            console.print(f"[red]Configuration error: {exc}[/red]")
        raise typer.Exit(code=1) from None

    router = LLMRouter(config, grove_root)
    prompt_builder = PromptBuilder(grove_root)
    engine = CompileEngine(grove_root, config, router, prompt_builder)

    # Set up progress reporting based on output mode.
    progress_callback = None
    if json_output:
        progress_callback = _ndjson_progress
    else:
        from rich.progress import Progress, SpinnerColumn, TextColumn

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        )

        task_id: object = None

        def _rich_progress(step: str, pct: int, detail: str) -> None:
            nonlocal task_id
            if task_id is None:
                task_id = progress.add_task(detail, total=100)
            progress.update(task_id, completed=pct, description=detail)

        progress_callback = _rich_progress

    # Run compilation.
    try:
        if not json_output:
            with progress:
                result = engine.compile(
                    dry_run=dry_run, progress_callback=progress_callback
                )
        else:
            result = engine.compile(
                dry_run=dry_run, progress_callback=progress_callback
            )

    except NoSourcesError as exc:
        if json_output:
            _ndjson_error(str(exc), "no_sources")
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    except RatchetFailedError as exc:
        if json_output:
            _ndjson_error(str(exc), "ratchet_failed")
        else:
            console.print("\n[red]Quality ratchet failed.[/red]")
            for failure in exc.result.blocking_failures:
                detail = exc.result.details.get(failure, {})
                console.print(f"  [red]BLOCK[/red] {failure}: {detail}")
            for warning in exc.result.warnings:
                detail = exc.result.details.get(warning, {})
                console.print(f"  [yellow]WARN[/yellow] {warning}: {detail}")
            console.print(
                "\n[dim]Wiki is unchanged. Fix the issues and recompile.[/dim]"
            )
        raise typer.Exit(code=1) from None

    except CompileError as exc:
        if json_output:
            _ndjson_error(str(exc), "compile_error")
        else:
            console.print(f"[red]Compilation failed: {exc}[/red]")
        raise typer.Exit(code=1) from None

    except Exception as exc:  # noqa: BLE001
        if json_output:
            _ndjson_error(str(exc), "unexpected_error")
        else:
            console.print(f"[red]Unexpected error: {exc}[/red]")
            logger.exception("Compile failed")
        raise typer.Exit(code=1) from None

    # Output results.
    _print_compile_result(result, json_output)


# ------------------------------------------------------------------
# grove search command
# ------------------------------------------------------------------


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),  # noqa: B008
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum results to return."),
    mode: str = typer.Option(
        "keyword",
        "--mode",
        "-m",
        help="Search mode: keyword (BM25), semantic (cosine), or hybrid.",
    ),
) -> None:
    """Search the wiki using full-text or semantic search.

    Modes: keyword (BM25, default), semantic (cosine similarity via
    Ollama embeddings), or hybrid (weighted combination of both).

    Requires a prior ``grove compile`` to build the search index.
    """
    grove_root = _find_grove_root()
    db_path = grove_root / ".grove" / "search.db"

    valid_modes = {"keyword", "semantic", "hybrid"}
    if mode not in valid_modes:
        choices = ", ".join(sorted(valid_modes))
        console.print(f"[red]Invalid mode '{mode}'. Choose from: {choices}[/red]")
        raise typer.Exit(code=1)

    if not db_path.exists():
        console.print(
            "[yellow]Search index not found.[/yellow]\n"
            "Run [cyan]grove compile[/cyan] to build the index."
        )
        raise typer.Exit(code=1)

    warnings: list[str] = []

    if mode == "keyword":
        from grove.search.fts import FTSIndex

        index = FTSIndex(db_path)
        results = index.search(query, limit=limit)
    elif mode == "semantic":
        from grove.search.vec import OllamaUnavailableError, VecIndex

        vec_index = VecIndex(db_path)
        try:
            results = vec_index.search(query, limit=limit)
        except OllamaUnavailableError as exc:
            console.print(f"[red]Semantic search unavailable: {exc}[/red]")
            raise typer.Exit(code=1) from None
    else:
        # hybrid mode
        from grove.search.hybrid import HybridSearch

        hybrid = HybridSearch(db_path)
        results, warnings = hybrid.search(query, limit=limit)

    for warning in warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    if not results:
        console.print("[dim]No results found.[/dim]")
        raise typer.Exit()

    from rich.panel import Panel
    from rich.text import Text

    for i, result in enumerate(results, 1):
        # Truncate the best chunk to 200 characters for display.
        chunk_preview = result.best_chunk[:200]
        if len(result.best_chunk) > 200:
            chunk_preview += "..."

        title_text = Text(f"{i}. {result.title}", style="bold")
        path_text = Text(f"   {result.article_path}", style="dim")
        score_text = Text(f"   Score: {result.score:.4f}", style="cyan")
        chunk_text = Text(f"   {chunk_preview}", style="white")

        panel_content = Text()
        panel_content.append_text(title_text)
        panel_content.append("\n")
        panel_content.append_text(path_text)
        panel_content.append("\n")
        panel_content.append_text(score_text)
        panel_content.append("\n")
        panel_content.append_text(chunk_text)

        console.print(Panel(panel_content, expand=False))

    console.print(f"\n[dim]{len(results)} result(s) found.[/dim]")


def _print_compile_result(result: object, json_output: bool) -> None:
    """Display compilation results in the appropriate format.

    *result* is a ``CompileResult`` instance from the compile engine.
    Typed as ``object`` to avoid a circular import at module level.
    """
    if json_output:
        if result.dry_run:
            _ndjson_result(
                {
                    "dry_run": True,
                    "estimated_tokens": result.estimated_tokens,
                    "estimated_cost": result.estimated_cost,
                }
            )
        else:
            _ndjson_result(
                {
                    "articles_created": result.articles_created,
                    "articles_updated": result.articles_updated,
                    "articles_skipped_pinned": result.articles_skipped_pinned,
                    "human_blocks_preserved": result.human_blocks_preserved,
                    "cost_usd": result.cost_usd,
                }
            )
        return

    # Rich terminal output.
    if result.dry_run:
        console.print("\n[bold]Dry run estimate:[/bold]")
        console.print(f"  Estimated input tokens: {result.estimated_tokens:,}")
        console.print(f"  Estimated cost: ${result.estimated_cost:.4f}")
        console.print("\n[dim]No LLM call made. Remove --dry-run to compile.[/dim]")
        return

    console.print("\n[bold green]Compilation complete.[/bold green]")

    from rich.table import Table

    table = Table(title="Compile Summary", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Articles created", str(result.articles_created))
    table.add_row("Articles updated", str(result.articles_updated))
    table.add_row("Pinned skipped", str(result.articles_skipped_pinned))
    table.add_row("Human blocks preserved", str(result.human_blocks_preserved))
    table.add_row("Input tokens", f"{result.total_tokens_input:,}")
    table.add_row("Output tokens", f"{result.total_tokens_output:,}")
    table.add_row("Cost", f"${result.cost_usd:.4f}")
    console.print(table)

    if result.ratchet_warnings:
        console.print("\n[yellow]Ratchet warnings:[/yellow]")
        for warning in result.ratchet_warnings:
            console.print(f"  [yellow]WARN[/yellow] {warning}")


# ------------------------------------------------------------------
# grove query command
# ------------------------------------------------------------------


@app.command()
def query(
    question: str = typer.Argument(..., help="Question to ask the knowledge base."),  # noqa: B008
    quick: bool = typer.Option(
        False,
        "--quick",
        help="Use quick mode (index-only, fast LLM tier).",
    ),
    output: str = typer.Option(
        "terminal",
        "--output",
        "-o",
        help="Output format: terminal (default), md, slides.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit NDJSON result to stdout (for Obsidian plugin).",
    ),
) -> None:
    """Query the compiled wiki.

    By default uses deep mode: FTS5 search for the top-5 relevant
    articles, loads full content, and synthesises an answer via the
    standard LLM tier.  Pass --quick for fast, index-only queries
    using the fast LLM tier.

    Output formats: terminal (default), md (markdown file), slides (Marp).
    Every query result is auto-saved to queries/.
    """
    grove_root = _find_grove_root()

    from grove.compile.prompt import PromptBuilder
    from grove.config.loader import ConfigLoader
    from grove.llm.router import LLMRouter

    # Validate output format.
    valid_formats = {"terminal", "md", "slides"}
    if output not in valid_formats:
        choices = ", ".join(sorted(valid_formats))
        msg = f"Invalid output format '{output}'. Choose from: {choices}"
        if json_output:
            _ndjson_error(msg, "invalid_format")
        else:
            console.print(f"[red]{msg}[/red]")
        raise typer.Exit(code=1)

    # Load config.
    try:
        config = ConfigLoader(grove_root).load()
    except (FileNotFoundError, ValueError) as exc:
        if json_output:
            _ndjson_error(str(exc), "config_error")
        else:
            console.print(f"[red]Configuration error: {exc}[/red]")
        raise typer.Exit(code=1) from None

    router = LLMRouter(config, grove_root)
    prompt_builder = PromptBuilder(grove_root)

    if quick:
        from grove.query.quick import QuickQuery

        engine = QuickQuery(grove_root, router, prompt_builder)
        mode_label = "Quick query"
    else:
        from grove.query.deep import DeepQuery

        engine = DeepQuery(grove_root, router, prompt_builder)
        mode_label = "Deep query"

    try:
        result = engine.query(question)
    except Exception as exc:  # noqa: BLE001
        if json_output:
            _ndjson_error(str(exc), "query_error")
        else:
            console.print(f"[red]{mode_label} failed: {exc}[/red]")
            logger.exception("%s error", mode_label)
        raise typer.Exit(code=1) from None

    # Auto-save every query result.
    from grove.query.filer import QueryFiler

    filer = QueryFiler(grove_root)
    try:
        saved_path = filer.save_query(result)
    except Exception as exc:  # noqa: BLE001
        saved_path = None
        logger.warning("Could not auto-save query: %s", exc)

    # Output results.
    if json_output:
        result_data = result.model_dump()
        if saved_path is not None:
            result_data["saved_to"] = str(saved_path)
        _ndjson_result(result_data)
        return

    # Format based on --output flag.
    from grove.query.formatter import AnswerFormatter

    formatter = AnswerFormatter()

    if output == "md":
        typer.echo(formatter.format_markdown(result))
    elif output == "slides":
        typer.echo(formatter.format_slides(result))
    else:
        _print_query_result(result)

    # Print saved path.
    if saved_path is not None:
        console.print(f"\n[dim]Saved to {saved_path}[/dim]")


# ------------------------------------------------------------------
# grove file command
# ------------------------------------------------------------------


@app.command(name="file")
def file_query(
    path: str | None = typer.Argument(
        None, help="Path to a query file to promote. Defaults to the most recent query."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit NDJSON result to stdout (for Obsidian plugin).",
    ),
) -> None:
    """Promote a query result to the wiki.

    If a path is provided, promotes that specific query file.
    Otherwise, promotes the most recent query from queries/.
    Adds origin: query and pinned: true to front matter, copies
    to wiki/queries/, and commits via git.
    """
    grove_root = _find_grove_root()

    from grove.query.filer import QueryFiler

    filer = QueryFiler(grove_root)

    # Determine which file to promote.
    if path is not None:
        query_path = Path(path).resolve()
    else:
        query_path = filer.get_latest_query()
        if query_path is None:
            msg = "No query results found in queries/. Run `grove query` first."
            if json_output:
                _ndjson_error(msg, "no_queries")
            else:
                console.print(f"[yellow]{msg}[/yellow]")
            raise typer.Exit(code=1)

    # Promote to wiki.
    try:
        wiki_path = filer.file_to_wiki(query_path)
    except FileNotFoundError as exc:
        if json_output:
            _ndjson_error(str(exc), "file_not_found")
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except Exception as exc:  # noqa: BLE001
        if json_output:
            _ndjson_error(str(exc), "file_error")
        else:
            console.print(f"[red]Failed to file query: {exc}[/red]")
            logger.exception("File query error")
        raise typer.Exit(code=1) from None

    # Output result.
    if json_output:
        _ndjson_result(
            {
                "filed": True,
                "source": str(query_path),
                "wiki_path": str(wiki_path),
            }
        )
        return

    console.print(f"[green]Filed to {wiki_path}[/green]")


def _print_query_result(result: object) -> None:
    """Display a query result in the terminal.

    *result* is a ``QueryResult`` instance from the query module.
    """
    from rich.markdown import Markdown
    from rich.panel import Panel

    console.print()
    console.print(
        Panel(
            Markdown(result.answer),
            title=f"[bold]{result.question}[/bold]",
            subtitle=(
                f"mode={result.mode} | model={result.model_used}"
                f" | cost=${result.cost_usd:.4f}"
            ),
            border_style="green",
        )
    )

    if result.citations:
        console.print("\n[bold]Citations:[/bold]")
        for citation in result.citations:
            console.print(f"  [cyan][wiki: {citation}][/cyan]")

    if result.follow_up_questions:
        console.print("\n[bold]Follow-up questions:[/bold]")
        for i, question in enumerate(result.follow_up_questions, 1):
            console.print(f"  {i}. {question}")


# ------------------------------------------------------------------
# grove raw subcommand group (TASK-023)
# ------------------------------------------------------------------

raw_app = typer.Typer(
    name="raw",
    help="Manage raw ingested sources.",
)
app.add_typer(raw_app, name="raw")


@raw_app.command(name="list")
def raw_list(
    failed: bool = typer.Option(
        False,
        "--failed",
        help="Show only failed or poor-quality sources.",
    ),
) -> None:
    """List all ingested sources with quality flags."""
    grove_root = _find_grove_root()

    from rich.table import Table

    from grove.ingest.manifest import ManifestWriter

    manifest = ManifestWriter(grove_root)
    entries = manifest.read()

    if failed:
        entries = [e for e in entries if e.quality in ("poor", "failed")]

    if not entries:
        label = "failed/poor" if failed else ""
        console.print(f"[dim]No {label} sources found.[/dim]")
        raise typer.Exit()

    table = Table(title="Raw Sources")
    table.add_column("Source", style="cyan")
    table.add_column("Quality")
    table.add_column("Words", justify="right")
    table.add_column("Concepts")
    table.add_column("Ingested")

    for entry in entries:
        quality_colour = {
            "good": "green",
            "partial": "yellow",
            "poor": "red",
            "failed": "red",
        }.get(entry.quality, "white")
        concepts_str = ", ".join(entry.concepts) if entry.concepts else ""
        table.add_row(
            entry.source_path,
            f"[{quality_colour}]{entry.quality}[/{quality_colour}]",
            str(entry.word_count),
            concepts_str,
            entry.ingested_at[:10] if entry.ingested_at else "",
        )

    console.print(table)


@raw_app.command()
def retry(
    path: str = typer.Argument(..., help="Path to the source file to re-ingest."),  # noqa: B008
) -> None:
    """Re-run the full ingest pipeline on a single source."""
    grove_root = _find_grove_root()
    source_path = Path(path).resolve()

    if not source_path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(code=1)

    from grove.ingest.manifest import ManifestWriter

    # Remove existing manifest entry before re-ingesting
    manifest = ManifestWriter(grove_root)
    manifest.remove(source_path)

    try:
        result = _run_ingest_pipeline(source_path, str(source_path), grove_root)
        _print_ingest_result(result)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Retry failed: {exc}[/red]")
        logger.exception("Retry pipeline error")
        raise typer.Exit(code=1) from None


@raw_app.command()
def drop(
    path: str = typer.Argument(..., help="Path to the source file to remove."),  # noqa: B008
) -> None:
    """Remove a source file and its manifest entry.

    Flags any wiki articles that referenced this source as stale
    in state.json.
    """
    grove_root = _find_grove_root()
    source_path = Path(path).resolve()

    if not source_path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(code=1)

    from grove.config.state import StateManager
    from grove.ingest.manifest import ManifestWriter

    # Remove manifest entry
    manifest = ManifestWriter(grove_root)
    manifest.remove(source_path)

    # Flag affected wiki articles as stale in state.json
    state = StateManager(grove_root)
    try:
        relative = str(source_path.resolve().relative_to(grove_root.resolve()))
    except ValueError:
        relative = str(source_path)

    stale_sources: list[str] = state.get("stale_sources", [])
    if relative not in stale_sources:
        stale_sources.append(relative)
        state.set("stale_sources", stale_sources)

    # Delete the source file
    source_path.unlink()

    console.print(f"[green]Dropped:[/green] {path}")
    console.print(
        "[dim]Affected wiki articles flagged as stale. "
        "Run grove compile to refresh.[/dim]"
    )


# ------------------------------------------------------------------
# grove pin / grove unpin commands (TASK-023a)
# ------------------------------------------------------------------


def _read_front_matter(article_path: Path) -> tuple[dict[str, object], str]:
    """Read YAML front matter and body from a markdown file.

    Returns (front_matter_dict, body_text).
    Raises ValueError if the file has no YAML front matter.
    """
    text = article_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"No YAML front matter found in {article_path}")

    end_idx = text.find("\n---", 3)
    if end_idx == -1:
        raise ValueError(f"Malformed YAML front matter in {article_path}")

    yaml_block = text[4:end_idx]
    body = text[end_idx + 4 :]

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {article_path}: {exc}") from exc

    if not isinstance(meta, dict):
        meta = {}

    return meta, body


def _write_front_matter(article_path: Path, meta: dict[str, object], body: str) -> None:
    """Write YAML front matter and body back to a markdown file."""
    front = yaml.dump(meta, default_flow_style=False, sort_keys=False).rstrip("\n")
    article_path.write_text(
        f"---\n{front}\n---{body}",
        encoding="utf-8",
    )


@app.command()
def pin(
    path: str = typer.Argument(..., help="Path to the wiki article to pin."),  # noqa: B008
) -> None:
    """Pin an article so it is preserved across recompilations.

    Sets ``pinned: true`` in the article's YAML front matter and
    auto-commits the change.
    """
    grove_root = _find_grove_root()
    article_path = Path(path).resolve()

    if not article_path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(code=1)

    # Ensure the file is inside wiki/
    wiki_dir = grove_root / "wiki"
    try:
        article_path.relative_to(wiki_dir)
    except ValueError:
        console.print(f"[red]{path} is not inside wiki/[/red]")
        raise typer.Exit(code=1) from None

    try:
        meta, body = _read_front_matter(article_path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    if meta.get("pinned") is True:
        console.print(f"[dim]{path} is already pinned.[/dim]")
        raise typer.Exit()

    meta["pinned"] = True
    _write_front_matter(article_path, meta, body)

    # Auto-commit via AutoCommitter
    try:
        from grove.git.auto_commit import AutoCommitter

        committer = AutoCommitter(grove_root)
        committer._repo.git.add(str(article_path))
        committer._repo.index.commit(f"grove: pin {article_path.name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-commit failed for pin: %s", exc)

    console.print(f"[green]Pinned:[/green] {path}")


@app.command()
def unpin(
    path: str = typer.Argument(..., help="Path to the wiki article to unpin."),  # noqa: B008
) -> None:
    """Unpin an article so it can be updated by recompilations.

    Removes ``pinned: true`` from the article's YAML front matter
    and auto-commits the change.
    """
    grove_root = _find_grove_root()
    article_path = Path(path).resolve()

    if not article_path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(code=1)

    # Ensure the file is inside wiki/
    wiki_dir = grove_root / "wiki"
    try:
        article_path.relative_to(wiki_dir)
    except ValueError:
        console.print(f"[red]{path} is not inside wiki/[/red]")
        raise typer.Exit(code=1) from None

    try:
        meta, body = _read_front_matter(article_path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    if "pinned" not in meta:
        console.print(f"[dim]{path} is not pinned.[/dim]")
        raise typer.Exit()

    del meta["pinned"]
    _write_front_matter(article_path, meta, body)

    # Auto-commit via AutoCommitter
    try:
        from grove.git.auto_commit import AutoCommitter

        committer = AutoCommitter(grove_root)
        committer._repo.git.add(str(article_path))
        committer._repo.index.commit(f"grove: unpin {article_path.name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-commit failed for unpin: %s", exc)

    console.print(f"[green]Unpinned:[/green] {path}")


# ------------------------------------------------------------------
# grove costs command (TASK-024)
# ------------------------------------------------------------------


@app.command()
def costs(
    today: bool = typer.Option(
        False,
        "--today",
        help="Show only today's costs.",
    ),
) -> None:
    """Display LLM cost summary aggregated by task type and model."""
    grove_root = _find_grove_root()

    from rich.table import Table

    from grove.llm.cost import CostTracker

    tracker = CostTracker(grove_root / ".grove" / "logs")
    summary = tracker.get_cost_summary(today_only=today)

    if not summary:
        label = "today" if today else "on record"
        console.print(f"[dim]No costs {label}.[/dim]")
        raise typer.Exit()

    table = Table(title="Cost Summary" + (" (today)" if today else ""))
    table.add_column("Task Type", style="cyan")
    table.add_column("Model", style="magenta")
    table.add_column("Cost (USD)", justify="right", style="green")

    grand_total = 0.0
    for task_type, models in sorted(summary.items()):
        for model, cost in sorted(models.items()):
            table.add_row(task_type, model, f"${cost:.4f}")
            grand_total += cost

    table.add_row("", "[bold]Total[/bold]", f"[bold]${grand_total:.4f}[/bold]")

    console.print(table)

    if today:
        today_spend = tracker.get_today_spend()
        console.print(
            f"\n[dim]Daily budget: ${tracker.daily_limit_usd:.2f} "
            f"| Spent today: ${today_spend:.4f}[/dim]"
        )


# ------------------------------------------------------------------
# grove log, grove diff, grove rollback commands (TASK-025)
# ------------------------------------------------------------------


@app.command(name="log")
def grove_log() -> None:
    """List grove auto-commit history with dates and article counts."""
    grove_root = _find_grove_root()

    from rich.table import Table

    from grove.git.log import CompileLog

    compile_log = CompileLog(grove_root)
    history = compile_log.get_history()

    if not history:
        console.print("[dim]No grove commits found.[/dim]")
        raise typer.Exit()

    table = Table(title="Grove Commit History")
    table.add_column("SHA", style="cyan", no_wrap=True)
    table.add_column("Date", style="dim")
    table.add_column("Articles", justify="right")
    table.add_column("Message")

    for commit in history:
        date_str = commit.timestamp[:10] if commit.timestamp else ""
        articles = str(commit.articles_affected) if commit.articles_affected else "-"
        # Truncate long messages for table display
        msg = (
            commit.message[:60] + "..." if len(commit.message) > 60 else commit.message
        )
        table.add_row(commit.sha[:8], date_str, articles, msg)

    console.print(table)


@app.command()
def diff() -> None:
    """Show article-level changes in the most recent grove commit."""
    grove_root = _find_grove_root()

    from rich.table import Table

    from grove.git.diff import CompileDiff

    compile_diff = CompileDiff(grove_root)
    changes = compile_diff.diff_last()

    if not changes:
        console.print("[dim]No changes found in the latest grove commit.[/dim]")
        raise typer.Exit()

    table = Table(title="Article Changes (latest grove commit)")
    table.add_column("Status")
    table.add_column("Path", style="cyan")

    status_styles = {
        "added": "[green]added[/green]",
        "modified": "[yellow]modified[/yellow]",
        "deleted": "[red]deleted[/red]",
    }

    for change in changes:
        styled_status = status_styles.get(change.status, change.status)
        table.add_row(styled_status, change.path)

    console.print(table)


@app.command()
def rollback(
    to: str | None = typer.Option(
        None,
        "--to",
        help="SHA of the commit to roll back to.",
    ),
) -> None:
    """Revert the last grove commit, or roll back to a specific SHA.

    Without --to, reverts the most recent grove: commit using git revert
    (history is preserved).  With --to <sha>, restores wiki/ to its state
    at that commit.
    """
    grove_root = _find_grove_root()

    from grove.git.rollback import RollbackError, RollbackManager

    manager = RollbackManager(grove_root)

    try:
        if to:
            sha = manager.rollback_to(to)
            console.print(f"[green]Rolled back to {to[:8]}.[/green]")
        else:
            sha = manager.rollback_last()
            console.print("[green]Reverted the last grove commit.[/green]")

        console.print(f"[dim]New commit: {sha[:8]}[/dim]")

    except RollbackError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None


# ------------------------------------------------------------------
# grove health command (TASK-019)
# ------------------------------------------------------------------


@app.command()
def health(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit NDJSON output for the Obsidian plugin.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Auto-create stub articles for broken wiki-links, then commit.",
    ),
) -> None:
    """Run health checks on the wiki and report issues.

    By default prints a Rich formatted report showing each check's
    status.  With --json, emits NDJSON for the Obsidian plugin.
    With --fix, creates stub articles for broken wiki-links and
    commits the changes via git.
    """
    grove_root = _find_grove_root()

    from grove.health.reporter import HealthReporter

    reporter = HealthReporter(grove_root)
    report = reporter.run()

    # --fix mode: create stubs and commit.
    fixes_applied: list[str] = []
    if fix:
        fixes_applied = reporter.fix(report)
        if fixes_applied:
            # Write the health report markdown file.
            reporter.write_health_report(report)

            # Auto-commit the stubs.
            try:
                from grove.git.auto_commit import AutoCommitter

                committer = AutoCommitter(grove_root)
                wiki_dir = grove_root / "wiki"
                for md_file in wiki_dir.rglob("*.md"):
                    committer._repo.git.add(str(md_file))
                committer._repo.index.commit(
                    f"grove: health --fix ({len(fixes_applied)} stub(s) created)"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Auto-commit failed for health --fix: %s", exc)

    # Write the health report markdown file (always, for wiki/_health.md).
    reporter.write_health_report(report)

    # Output results.
    if json_output:
        _print_health_ndjson(report, fixes_applied)
        return

    _print_health_rich(report, fixes_applied)


def _print_health_ndjson(
    report: object,
    fixes_applied: list[str],
) -> None:
    """Emit the health report as NDJSON events."""
    import sys

    # Emit each check as a separate NDJSON line.
    for name, check in report.checks.items():
        event = {
            "type": "health_check",
            "name": name,
            "status": check.status,
            "message": check.message,
            "details": check.details,
            "auto_fixable": check.auto_fixable,
        }
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()

    # Emit the summary.
    summary = {
        "type": "health_summary",
        "overall_status": report.overall_status,
        "total_articles": report.total_articles,
        "timestamp": report.timestamp,
        "fixes_applied": fixes_applied,
    }
    sys.stdout.write(json.dumps(summary) + "\n")
    sys.stdout.flush()


def _print_health_rich(
    report: object,
    fixes_applied: list[str],
) -> None:
    """Print the health report as a Rich formatted table and panels."""
    from rich.panel import Panel
    from rich.table import Table

    status_colours = {
        "healthy": "green",
        "warnings": "yellow",
        "issues": "red",
    }
    check_icons = {
        "pass": "[green]PASS[/green]",
        "warn": "[yellow]WARN[/yellow]",
        "fail": "[red]FAIL[/red]",
    }

    overall_colour = status_colours.get(report.overall_status, "white")
    console.print(
        Panel(
            f"[bold {overall_colour}]"
            f"{report.overall_status.upper()}"
            f"[/bold {overall_colour}]"
            f"  |  {report.total_articles} article(s)"
            f"  |  {report.timestamp}",
            title="[bold]Grove Health Report[/bold]",
            border_style=overall_colour,
        )
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Summary")

    for name, check in report.checks.items():
        icon = check_icons.get(check.status, check.status)
        table.add_row(name, icon, check.message)

    console.print(table)

    # Print details for non-passing checks.
    for name, check in report.checks.items():
        if check.details:
            console.print(f"\n[bold]{name}[/bold] details:")
            for detail in check.details:
                console.print(f"  - {detail}")

    # Print fixes if any were applied.
    if fixes_applied:
        console.print(
            f"\n[bold green]{len(fixes_applied)} fix(es) applied:[/bold green]"
        )
        for fix_desc in fixes_applied:
            console.print(f"  [green]+[/green] {fix_desc}")


# ------------------------------------------------------------------
# grove serve command (TASK-022)
# ------------------------------------------------------------------


@app.command()
def serve(
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to."),
) -> None:
    """Start a local web UI for searching the wiki.

    Serves a FastAPI + HTMX application with hybrid search,
    article previews, and Obsidian deep links.
    """
    grove_root = _find_grove_root()

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn is required for grove serve.[/red]\n"
            "Install it with: [cyan]pip install grove-kb[full][/cyan]"
        )
        raise typer.Exit(code=1) from None

    from grove.search.serve import create_app

    web_app = create_app(grove_root)

    console.print(
        f"[bold green]Grove search UI[/bold green] "
        f"running at [cyan]http://{host}:{port}[/cyan]"
    )
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    uvicorn.run(web_app, host=host, port=port, log_level="warning")
