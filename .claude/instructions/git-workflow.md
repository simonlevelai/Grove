# Git Workflow — Grove

## Commit Convention

Conventional commits:

```
<type>(<scope>): <description>

[optional body]
[optional footer]
```

**Types:** `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

**Scopes:** `ingest`, `compile`, `query`, `health`, `search`, `cli`, `plugin`, `prompts`, `config`

## Grove Auto-Commits (runtime)

The engine auto-commits to user knowledge bases with the format:

```
grove: <operation> — <stats>

Examples:
grove: initial compile — 24 sources, 18 articles
grove: incremental compile — 1 new source, 3 articles updated
grove: health fix — 2 broken links resolved
grove: file query — queries/2026-04-03-comparison.md
```

These are *runtime* commits to the user's knowledge base, not the Grove source repo.

## Branch Strategy

- `main` — stable, releasable
- `feature/<name>` — feature work
- `fix/<name>` — bug fixes
- `spike/<name>` — experiments / validation spikes

## Pull Requests

- Must pass: Ruff, Black, ESLint, Prettier
- Must pass: all unit tests (including slow tests for compile-related changes)
- PRD/ARCH/TASKS must be updated if the change affects product scope or architecture
