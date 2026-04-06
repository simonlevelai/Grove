"""Tests for utility CLI commands: raw, pin/unpin, costs, log, diff, rollback.

Covers TASK-023, TASK-023a, TASK-024, and TASK-025.
Uses tmp_path fixtures to avoid touching real repositories or groves.
"""

from __future__ import annotations

import json
from pathlib import Path

import git
import pytest
import yaml
from typer.testing import CliRunner

from grove.cli import app

runner = CliRunner()


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def grove_project(tmp_path: Path) -> Path:
    """Create a minimal grove directory structure with git repo.

    Returns the grove root path.
    """
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    # Directory structure
    (tmp_path / ".grove" / "logs").mkdir(parents=True)
    (tmp_path / "raw" / "articles").mkdir(parents=True)
    (tmp_path / "wiki").mkdir()

    # Minimal config.yaml so _find_grove_root succeeds
    config = {"llm": {"providers": {}}}
    (tmp_path / ".grove" / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )

    # Empty state.json
    (tmp_path / ".grove" / "state.json").write_text(
        json.dumps({}, indent=2) + "\n",
        encoding="utf-8",
    )

    # Initial commit so HEAD exists
    (tmp_path / "wiki" / ".gitkeep").touch()
    repo.index.add(["wiki/.gitkeep"])
    repo.index.commit("initial")

    return tmp_path


@pytest.fixture()
def _chdir_grove(grove_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Change working directory to the grove project for CLI tests."""
    monkeypatch.chdir(grove_project)
    return grove_project


# ------------------------------------------------------------------
# Helper: write a manifest with entries
# ------------------------------------------------------------------


def _write_manifest(grove_root: Path, entries: list[dict[str, object]]) -> None:
    """Write a _manifest.md file with the given entries."""
    lines = [
        f"---\ntotal_sources: {len(entries)}\nlast_updated: '2026-04-03'\n---\n",
        "",
        "| Source | Quality | Words | Concepts | Ingested |",
        "|--------|---------|-------|----------|----------|",
    ]
    for entry in entries:
        concepts = ", ".join(entry.get("concepts", []))
        lines.append(
            f"| {entry['source_path']} "
            f"| {entry['quality']} "
            f"| {entry['word_count']} "
            f"| {concepts} "
            f"| {entry.get('ingested_at', '2026-04-03')} |"
        )
    lines.append("")
    manifest_path = grove_root / "raw" / "_manifest.md"
    manifest_path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------
# TASK-023 — grove raw subcommands
# ------------------------------------------------------------------


class TestRawList:
    """grove raw list displays ingested sources in a table."""

    def test_raw_list_displays_sources(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        _write_manifest(
            grove_project,
            [
                {
                    "source_path": "raw/articles/example.html",
                    "quality": "good",
                    "word_count": 500,
                    "concepts": ["AI", "ethics"],
                },
                {
                    "source_path": "raw/papers/study.pdf",
                    "quality": "poor",
                    "word_count": 100,
                },
            ],
        )

        result = runner.invoke(app, ["raw", "list"])

        assert result.exit_code == 0
        assert "example.html" in result.output
        assert "study.pdf" in result.output
        assert "good" in result.output
        assert "poor" in result.output

    def test_raw_list_failed_filter(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        _write_manifest(
            grove_project,
            [
                {
                    "source_path": "raw/articles/good-one.html",
                    "quality": "good",
                    "word_count": 500,
                },
                {
                    "source_path": "raw/articles/bad-one.html",
                    "quality": "poor",
                    "word_count": 50,
                },
            ],
        )

        result = runner.invoke(app, ["raw", "list", "--failed"])

        assert result.exit_code == 0
        assert "bad-one.html" in result.output
        assert "good-one.html" not in result.output

    def test_raw_list_empty(self, grove_project: Path, _chdir_grove: Path) -> None:
        result = runner.invoke(app, ["raw", "list"])

        assert result.exit_code == 0
        assert "No" in result.output


class TestRawDrop:
    """grove raw drop removes a source file and its manifest entry."""

    def test_raw_drop_removes_source(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        # Create the source file
        source = grove_project / "raw" / "articles" / "to-drop.html"
        source.write_text("<html>content</html>", encoding="utf-8")

        _write_manifest(
            grove_project,
            [
                {
                    "source_path": "raw/articles/to-drop.html",
                    "quality": "good",
                    "word_count": 200,
                },
            ],
        )

        result = runner.invoke(app, ["raw", "drop", str(source)])

        assert result.exit_code == 0
        assert "Dropped" in result.output
        assert not source.exists()

        # Manifest should be empty now
        from grove.ingest.manifest import ManifestWriter

        manifest = ManifestWriter(grove_project)
        assert len(manifest.read()) == 0

    def test_raw_drop_nonexistent_file(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        result = runner.invoke(
            app, ["raw", "drop", str(grove_project / "raw" / "nope.html")]
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ------------------------------------------------------------------
# TASK-023a — grove pin / grove unpin
# ------------------------------------------------------------------


class TestPin:
    """grove pin sets pinned: true in article YAML front matter."""

    def test_pin_adds_pinned_true(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        article = grove_project / "wiki" / "topic-a.md"
        article.write_text(
            "---\ntitle: Topic A\n---\n\n# Topic A\nContent here.\n",
            encoding="utf-8",
        )
        # Stage and commit so AutoCommitter works
        repo = git.Repo(grove_project)
        repo.index.add(["wiki/topic-a.md"])
        repo.index.commit("add topic-a")

        result = runner.invoke(app, ["pin", str(article)])

        assert result.exit_code == 0
        assert "Pinned" in result.output

        # Verify front matter
        text = article.read_text(encoding="utf-8")
        assert "pinned: true" in text or "pinned:true" in text

    def test_pin_file_not_found(self, grove_project: Path, _chdir_grove: Path) -> None:
        result = runner.invoke(
            app, ["pin", str(grove_project / "wiki" / "nonexistent.md")]
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_pin_outside_wiki(self, grove_project: Path, _chdir_grove: Path) -> None:
        outside = grove_project / "raw" / "articles" / "outside.md"
        outside.write_text("---\ntitle: Outside\n---\nContent.\n", encoding="utf-8")

        result = runner.invoke(app, ["pin", str(outside)])

        assert result.exit_code == 1
        assert "not inside wiki" in result.output.lower()

    def test_pin_no_front_matter(self, grove_project: Path, _chdir_grove: Path) -> None:
        article = grove_project / "wiki" / "no-fm.md"
        article.write_text("# No front matter\nJust content.\n", encoding="utf-8")

        result = runner.invoke(app, ["pin", str(article)])

        assert result.exit_code == 1
        assert "front matter" in result.output.lower()


class TestUnpin:
    """grove unpin removes pinned: true from article YAML front matter."""

    def test_unpin_removes_pinned(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        article = grove_project / "wiki" / "pinned-article.md"
        article.write_text(
            "---\ntitle: Pinned Article\npinned: true\n---\n\n# Pinned\nContent.\n",
            encoding="utf-8",
        )
        # Stage and commit so AutoCommitter works
        repo = git.Repo(grove_project)
        repo.index.add(["wiki/pinned-article.md"])
        repo.index.commit("add pinned article")

        result = runner.invoke(app, ["unpin", str(article)])

        assert result.exit_code == 0
        assert "Unpinned" in result.output

        # Verify pinned is removed from front matter
        text = article.read_text(encoding="utf-8")
        assert "pinned" not in text

    def test_unpin_not_pinned(self, grove_project: Path, _chdir_grove: Path) -> None:
        article = grove_project / "wiki" / "not-pinned.md"
        article.write_text(
            "---\ntitle: Not Pinned\n---\n\n# Not Pinned\nContent.\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["unpin", str(article)])

        assert result.exit_code == 0
        assert "not pinned" in result.output.lower()


# ------------------------------------------------------------------
# TASK-024 — grove costs
# ------------------------------------------------------------------


def _write_cost_entries(logs_dir: Path, entries: list[dict[str, object]]) -> None:
    """Write cost entries to costs.jsonl."""
    costs_path = logs_dir / "costs.jsonl"
    with costs_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


class TestCosts:
    """grove costs displays cost summary from costs.jsonl."""

    def test_costs_displays_summary(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        _write_cost_entries(
            grove_project / ".grove" / "logs",
            [
                {
                    "timestamp": "2026-04-03T10:00:00+00:00",
                    "task_type": "compile",
                    "model": "claude-sonnet-4-6",
                    "provider": "anthropic",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cost_usd": 0.0105,
                },
                {
                    "timestamp": "2026-04-03T11:00:00+00:00",
                    "task_type": "query",
                    "model": "claude-haiku-4-5-20251001",
                    "provider": "anthropic",
                    "input_tokens": 500,
                    "output_tokens": 200,
                    "cost_usd": 0.0012,
                },
            ],
        )

        result = runner.invoke(app, ["costs"])

        assert result.exit_code == 0
        assert "compile" in result.output
        assert "query" in result.output
        assert "Total" in result.output

    def test_costs_empty(self, grove_project: Path, _chdir_grove: Path) -> None:
        result = runner.invoke(app, ["costs"])

        assert result.exit_code == 0
        assert "No costs" in result.output

    def test_costs_today_filter(self, grove_project: Path, _chdir_grove: Path) -> None:
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()

        _write_cost_entries(
            grove_project / ".grove" / "logs",
            [
                {
                    "timestamp": f"{today}T10:00:00+00:00",
                    "task_type": "compile",
                    "model": "claude-sonnet-4-6",
                    "provider": "anthropic",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cost_usd": 0.05,
                },
                {
                    "timestamp": "2025-01-01T10:00:00+00:00",
                    "task_type": "query",
                    "model": "claude-sonnet-4-6",
                    "provider": "anthropic",
                    "input_tokens": 500,
                    "output_tokens": 200,
                    "cost_usd": 0.02,
                },
            ],
        )

        result = runner.invoke(app, ["costs", "--today"])

        assert result.exit_code == 0
        assert "compile" in result.output
        # The old query entry should not appear since it is from 2025
        assert "query" not in result.output


# ------------------------------------------------------------------
# TASK-025 — grove log, grove diff, grove rollback
# ------------------------------------------------------------------


class TestGroveLog:
    """grove log displays grove commit history."""

    def test_log_displays_commit_history(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        repo = git.Repo(grove_project)

        (grove_project / "wiki" / "a.md").write_text("# A\n")
        repo.index.add(["wiki/a.md"])
        repo.index.commit("grove: compile \u2014 2 articles created")

        (grove_project / "wiki" / "b.md").write_text("# B\n")
        repo.index.add(["wiki/b.md"])
        repo.index.commit("grove: compile \u2014 1 articles created, 1 updated")

        result = runner.invoke(app, ["log"])

        assert result.exit_code == 0
        assert "2 articles created" in result.output
        # Rich table may wrap long messages across lines
        assert "1 articles created" in result.output or "1 articles" in result.output

    def test_log_empty(self, grove_project: Path, _chdir_grove: Path) -> None:
        result = runner.invoke(app, ["log"])

        assert result.exit_code == 0
        assert "No grove commits" in result.output


class TestGroveDiff:
    """grove diff shows article-level changes in the latest grove commit."""

    def test_diff_shows_article_changes(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        repo = git.Repo(grove_project)

        (grove_project / "wiki" / "new-article.md").write_text("# New\n")
        repo.index.add(["wiki/new-article.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        assert "new-article.md" in result.output
        assert "added" in result.output

    def test_diff_empty(self, grove_project: Path, _chdir_grove: Path) -> None:
        result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        assert "No changes" in result.output


class TestGroveRollback:
    """grove rollback reverts the last grove commit."""

    def test_rollback_reverts_last_commit(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        repo = git.Repo(grove_project)

        (grove_project / "wiki" / "to-revert.md").write_text("# Revert me\n")
        repo.index.add(["wiki/to-revert.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        assert (grove_project / "wiki" / "to-revert.md").exists()

        result = runner.invoke(app, ["rollback"])

        assert result.exit_code == 0
        assert "Reverted" in result.output
        assert not (grove_project / "wiki" / "to-revert.md").exists()

    def test_rollback_to_specific_sha(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        repo = git.Repo(grove_project)

        # State 1
        (grove_project / "wiki" / "keep.md").write_text("# Keep\n")
        repo.index.add(["wiki/keep.md"])
        target_sha = repo.index.commit(
            "grove: compile \u2014 1 articles created"
        ).hexsha

        # State 2
        (grove_project / "wiki" / "remove-me.md").write_text("# Remove\n")
        repo.index.add(["wiki/remove-me.md"])
        repo.index.commit("grove: compile \u2014 2 articles created")

        result = runner.invoke(app, ["rollback", "--to", target_sha])

        assert result.exit_code == 0
        assert "Rolled back" in result.output
        assert (grove_project / "wiki" / "keep.md").exists()
        assert not (grove_project / "wiki" / "remove-me.md").exists()

    def test_rollback_no_grove_commits(
        self, grove_project: Path, _chdir_grove: Path
    ) -> None:
        result = runner.invoke(app, ["rollback"])

        assert result.exit_code == 1
        assert "No grove: commit found" in result.output
