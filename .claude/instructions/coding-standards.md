# Coding Standards — Grove

## Python (Engine + CLI)

- **Python 3.11+** minimum
- Type hints throughout — no `Any` unless absolutely necessary
- **Ruff** for linting, **Black** for formatting (line length 88)
- Docstrings on public functions: explain *why*, not *what*
- UK English in all strings, comments, and docs

### Structure
- Separate concerns strictly: `ingest/`, `compile/`, `query/`, `health/`, `search/`, `llm/`, `git/`
- CLI in `cli.py` (Typer) — thin layer only, no business logic
- No hardcoded secrets — all via env vars or `config.yaml`
- Configuration loaded at startup via Pydantic Settings

### Error handling
- Ingest failures: collect and report at end, never abort entire batch
- Compile failures: leave wiki unchanged, report and exit
- LLM failures: retry once with exponential backoff, then raise with context
- Always log enough to debug in production

### Testing
- Unit tests in `tests/` mirroring module structure
- No mocking of filesystem — use `tmp_path` fixtures
- LLM calls: use `pytest-recording` (VCR cassettes) for deterministic tests
- Mark slow/expensive tests with `@pytest.mark.slow`

## TypeScript (Obsidian Plugin)

- Strict mode enabled
- ESLint + Prettier (Obsidian community plugin defaults)
- No jQuery — use Obsidian API (`this.app`, `Notice`, `Modal`, etc.)
- Keep plugin thin: all compilation logic stays in Python

## Git

See `git-workflow.md` for commit conventions.

## Key Invariants

These are non-negotiable. Any PR that breaks them should be rejected:

1. **A failed compile never makes the wiki worse.** Partial successes must be committed; failures must leave previous state intact.
2. **`<!-- grove:human -->` blocks are never modified by the compiler.** Regex test this.
3. **Filed query answers (`origin: query`) are never loaded as compilation sources.**
4. **Every article compilation is an atomic git commit.** Never write to wiki/ without committing.
5. **Dry-run must never touch the filesystem or make LLM calls.**
6. **Article separation format is `<!-- grove:article wiki/path.md -->`** — the parser and prompt must agree on this exact format.
7. **Prompts use `string.Template` (`$variable`)**, not Jinja2 or f-strings.
8. **Config uses the nested provider/routing structure** from the spec. Don't flatten it.
