# Testing — Grove

## Philosophy

The quality ratchet is the product. Tests for the compilation pipeline are non-negotiable.

## Python Tests

**Framework:** pytest + pytest-recording (VCR for LLM calls)

**Structure:** Mirror module structure in `tests/`

```
tests/
  test_ingest.py        # PDF/HTML conversion, quality scoring, dedup
  test_compile.py       # Compilation pipeline, prompt rendering, article parsing
  test_query.py         # Query modes, citation format, filing
  test_health.py        # Quality ratchet checks
  test_search.py        # FTS5 + embedding queries
  test_git.py           # Auto-commit, rollback
  fixtures/
    sources/            # Sample markdown sources for test compilation
    wikis/              # Expected wiki output snapshots
    cassettes/          # VCR cassettes for LLM call replay
```

## Key Test Cases

### Compile pipeline
- `<!-- grove:human -->` blocks are preserved verbatim (regex + snapshot)
- `pinned: true` articles are never overwritten
- `origin: query` articles are never loaded as sources
- Failed compile leaves wiki unchanged (check git state)
- Dry-run makes zero filesystem changes and zero LLM calls

### Ingest
- Garbled PDF is flagged `poor`
- Duplicate source is detected and rejected
- Bulk ingest failure in one file doesn't abort others

### Health
- Broken wiki-link detected
- Orphan article detected
- Provenance below 50% blocks commit

## Marking Slow Tests

```python
@pytest.mark.slow   # skipped in CI unless --slow flag
def test_full_compile_25_sources():
    ...
```

## TypeScript Tests (Plugin)

Obsidian plugin tests via Jest. Focus on:
- Settings persistence
- Python backend communication (mock the subprocess)
- Error notification display
