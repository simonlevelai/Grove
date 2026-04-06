"""Tests for grove.git -- auto-commit, log, rollback, and diff.

Every test creates a temporary git repo via the ``git_grove`` fixture
so there is no risk of touching real repositories.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from grove.git.auto_commit import AutoCommitter
from grove.git.diff import CompileDiff
from grove.git.log import CompileLog
from grove.git.rollback import RollbackError, RollbackManager

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def git_grove(tmp_path: Path) -> tuple[Path, git.Repo]:
    """Create a minimal grove directory with an initialised git repo."""
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    (tmp_path / "wiki").mkdir()
    (tmp_path / ".grove" / "logs").mkdir(parents=True)

    # Initial commit so HEAD exists.
    (tmp_path / "wiki" / ".gitkeep").touch()
    repo.index.add(["wiki/.gitkeep"])
    repo.index.commit("initial")

    return tmp_path, repo


# ------------------------------------------------------------------
# AutoCommitter
# ------------------------------------------------------------------


class TestAutoCommitter:
    """AutoCommitter stages wiki/ and commits with the correct message."""

    def test_commit_compile_stages_and_commits(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove
        (root / "wiki" / "topic-a.md").write_text("# Topic A\nContent here.\n")

        committer = AutoCommitter(root)
        sha = committer.commit_compile(articles_created=3, articles_updated=1)

        assert sha == repo.head.commit.hexsha
        assert "grove: compile" in repo.head.commit.message
        assert "3 articles created" in repo.head.commit.message
        assert "1 updated" in repo.head.commit.message

    def test_commit_compile_with_cost(self, git_grove: tuple[Path, git.Repo]) -> None:
        root, repo = git_grove
        (root / "wiki" / "cost-test.md").write_text("# Cost\n")

        committer = AutoCommitter(root)
        sha = committer.commit_compile(
            articles_created=2, articles_updated=0, cost_usd=0.43
        )

        assert sha
        assert "(cost: $0.43)" in repo.head.commit.message

    def test_commit_compile_zero_articles(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove
        (root / "wiki" / "empty.md").write_text("# Empty\n")

        committer = AutoCommitter(root)
        sha = committer.commit_compile(articles_created=0, articles_updated=0)

        assert sha
        assert "0 articles" in repo.head.commit.message

    def test_commit_health_fix(self, git_grove: tuple[Path, git.Repo]) -> None:
        root, repo = git_grove
        (root / "wiki" / "fix.md").write_text("# Fixed\n")

        committer = AutoCommitter(root)
        sha = committer.commit_health_fix(
            ["2 broken links resolved", "1 orphan removed"]
        )

        assert sha
        assert "grove: health fix" in repo.head.commit.message
        assert "2 broken links resolved" in repo.head.commit.message

    def test_commit_file_query(self, git_grove: tuple[Path, git.Repo]) -> None:
        root, repo = git_grove
        (root / "wiki" / "query-result.md").write_text("# Answer\n")

        committer = AutoCommitter(root)
        sha = committer.commit_file_query("queries/2026-04-03-comparison.md")

        assert sha
        assert "grove: file query" in repo.head.commit.message
        assert "queries/2026-04-03-comparison.md" in repo.head.commit.message

    def test_has_changes_detects_new_file(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        committer = AutoCommitter(root)
        assert committer.has_changes() is False

        (root / "wiki" / "new-article.md").write_text("# New\n")
        assert committer.has_changes() is True

    def test_has_changes_detects_modified_file(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        # Create and commit a file first.
        article = root / "wiki" / "existing.md"
        article.write_text("# Original\n")
        repo.index.add(["wiki/existing.md"])
        repo.index.commit("add existing article")

        committer = AutoCommitter(root)
        assert committer.has_changes() is False

        # Modify it.
        article.write_text("# Updated content\n")
        assert committer.has_changes() is True

    def test_has_changes_false_when_clean(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        committer = AutoCommitter(root)
        assert committer.has_changes() is False

    def test_has_changes_ignores_non_wiki_changes(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        # Create a file outside wiki/.
        (root / "README.md").write_text("# README\n")

        committer = AutoCommitter(root)
        assert committer.has_changes() is False


# ------------------------------------------------------------------
# CompileLog
# ------------------------------------------------------------------


class TestCompileLog:
    """CompileLog filters grove: commits from the full git history."""

    def test_get_history_returns_grove_commits_only(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        # Create a mix of grove and non-grove commits.
        (root / "wiki" / "a.md").write_text("# A\n")
        repo.index.add(["wiki/a.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        (root / "README.md").write_text("# README\n")
        repo.index.add(["README.md"])
        repo.index.commit("docs: add readme")

        (root / "wiki" / "b.md").write_text("# B\n")
        repo.index.add(["wiki/b.md"])
        repo.index.commit("grove: compile \u2014 2 articles created, 1 updated")

        (root / "wiki" / "c.md").write_text("# C\n")
        repo.index.add(["wiki/c.md"])
        repo.index.commit("feat: something unrelated")

        log = CompileLog(root)
        history = log.get_history()

        assert len(history) == 2
        # Newest first.
        assert "2 articles created" in history[0].message
        assert "1 articles created" in history[1].message

    def test_get_history_parses_articles_affected(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        (root / "wiki" / "a.md").write_text("# A\n")
        repo.index.add(["wiki/a.md"])
        repo.index.commit("grove: compile \u2014 5 articles created, 3 updated")

        log = CompileLog(root)
        history = log.get_history()

        assert len(history) == 1
        assert history[0].articles_affected == 8  # 5 + 3

    def test_get_history_respects_limit(self, git_grove: tuple[Path, git.Repo]) -> None:
        root, repo = git_grove

        for i in range(5):
            (root / "wiki" / f"article-{i}.md").write_text(f"# Article {i}\n")
            repo.index.add([f"wiki/article-{i}.md"])
            repo.index.commit(f"grove: compile \u2014 {i + 1} articles created")

        log = CompileLog(root)
        history = log.get_history(limit=3)
        assert len(history) == 3

    def test_get_latest_returns_most_recent_grove_commit(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        (root / "wiki" / "a.md").write_text("# A\n")
        repo.index.add(["wiki/a.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        (root / "README.md").write_text("# README\n")
        repo.index.add(["README.md"])
        repo.index.commit("docs: unrelated commit")

        (root / "wiki" / "b.md").write_text("# B\n")
        repo.index.add(["wiki/b.md"])
        grove_sha = repo.index.commit("grove: compile \u2014 2 articles created").hexsha

        # Another non-grove commit after the latest grove one.
        (root / "notes.txt").write_text("notes\n")
        repo.index.add(["notes.txt"])
        repo.index.commit("chore: add notes")

        log = CompileLog(root)
        latest = log.get_latest()

        assert latest is not None
        assert latest.sha == grove_sha
        assert "2 articles created" in latest.message

    def test_get_latest_returns_none_when_no_grove_commits(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        log = CompileLog(root)
        assert log.get_latest() is None

    def test_grove_commit_has_iso_timestamp(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        (root / "wiki" / "ts.md").write_text("# Timestamp test\n")
        repo.index.add(["wiki/ts.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        log = CompileLog(root)
        latest = log.get_latest()

        assert latest is not None
        # ISO 8601 timestamps contain 'T' separator.
        assert "T" in latest.timestamp


# ------------------------------------------------------------------
# RollbackManager
# ------------------------------------------------------------------


class TestRollbackManager:
    """RollbackManager reverts or restores grove commits."""

    def test_rollback_last_reverts_grove_commit(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        # Create a grove commit that adds an article.
        (root / "wiki" / "to-revert.md").write_text("# Will be reverted\n")
        repo.index.add(["wiki/to-revert.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        assert (root / "wiki" / "to-revert.md").exists()

        manager = RollbackManager(root)
        revert_sha = manager.rollback_last()

        assert revert_sha == repo.head.commit.hexsha
        # The revert should undo the article addition.
        assert not (root / "wiki" / "to-revert.md").exists()
        # Revert commit message should reference the original.
        assert "Revert" in repo.head.commit.message

    def test_rollback_last_raises_when_no_grove_commits(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        manager = RollbackManager(root)
        with pytest.raises(RollbackError, match="No grove: commit found"):
            manager.rollback_last()

    def test_rollback_to_restores_wiki_to_specific_point(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        # State 1: one article.
        (root / "wiki" / "article-1.md").write_text("# Article 1\n")
        repo.index.add(["wiki/article-1.md"])
        target_commit = repo.index.commit("grove: compile \u2014 1 articles created")
        target_sha = target_commit.hexsha

        # State 2: add a second article.
        (root / "wiki" / "article-2.md").write_text("# Article 2\n")
        repo.index.add(["wiki/article-2.md"])
        repo.index.commit("grove: compile \u2014 2 articles created")

        # State 3: modify article 1.
        (root / "wiki" / "article-1.md").write_text("# Article 1 (modified)\n")
        repo.index.add(["wiki/article-1.md"])
        repo.index.commit("grove: compile \u2014 1 articles created, 1 updated")

        # Rollback to state 1.
        manager = RollbackManager(root)
        rollback_sha = manager.rollback_to(target_sha)

        assert rollback_sha == repo.head.commit.hexsha
        # wiki should match state 1: article-1 is original, article-2 gone.
        assert (root / "wiki" / "article-1.md").read_text() == "# Article 1\n"
        assert not (root / "wiki" / "article-2.md").exists()
        # Commit message records the rollback.
        assert f"grove: rollback to {target_sha[:8]}" in repo.head.commit.message

    def test_rollback_to_raises_for_invalid_sha(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        manager = RollbackManager(root)
        with pytest.raises(RollbackError, match="Cannot re(solve|store)"):
            manager.rollback_to("0000000000000000000000000000000000000000")


# ------------------------------------------------------------------
# CompileDiff
# ------------------------------------------------------------------


class TestCompileDiff:
    """CompileDiff shows article-level changes between commits."""

    def test_diff_last_shows_added_articles(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        (root / "wiki" / "new-a.md").write_text("# A\n")
        (root / "wiki" / "new-b.md").write_text("# B\n")
        repo.index.add(["wiki/new-a.md", "wiki/new-b.md"])
        repo.index.commit("grove: compile \u2014 2 articles created")

        diff = CompileDiff(root)
        changes = diff.diff_last()

        paths = {c.path for c in changes}
        statuses = {c.status for c in changes}

        assert "wiki/new-a.md" in paths
        assert "wiki/new-b.md" in paths
        assert statuses == {"added"}

    def test_diff_last_shows_modified_articles(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        # First grove commit: add an article.
        (root / "wiki" / "evolving.md").write_text("# Original\n")
        repo.index.add(["wiki/evolving.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        # Second grove commit: modify the article.
        (root / "wiki" / "evolving.md").write_text("# Updated content\n")
        repo.index.add(["wiki/evolving.md"])
        repo.index.commit("grove: compile \u2014 0 articles created, 1 updated")

        diff = CompileDiff(root)
        changes = diff.diff_last()

        assert len(changes) == 1
        assert changes[0].path == "wiki/evolving.md"
        assert changes[0].status == "modified"

    def test_diff_last_shows_deleted_articles(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        # Add an article via grove commit.
        (root / "wiki" / "doomed.md").write_text("# Doomed\n")
        repo.index.add(["wiki/doomed.md"])
        repo.index.commit("grove: compile \u2014 1 articles created")

        # Remove it via another grove commit.
        (root / "wiki" / "doomed.md").unlink()
        repo.index.remove(["wiki/doomed.md"])
        repo.index.commit("grove: compile \u2014 0 articles created")

        diff = CompileDiff(root)
        changes = diff.diff_last()

        assert len(changes) == 1
        assert changes[0].path == "wiki/doomed.md"
        assert changes[0].status == "deleted"

    def test_diff_last_returns_empty_when_no_grove_commits(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, _repo = git_grove

        diff = CompileDiff(root)
        assert diff.diff_last() == []

    def test_diff_between_shows_changes(self, git_grove: tuple[Path, git.Repo]) -> None:
        root, repo = git_grove

        # Commit 1: add article A.
        (root / "wiki" / "a.md").write_text("# A\n")
        repo.index.add(["wiki/a.md"])
        sha_old = repo.index.commit("grove: compile \u2014 1 articles created").hexsha

        # Commit 2: add article B and modify A.
        (root / "wiki" / "a.md").write_text("# A (updated)\n")
        (root / "wiki" / "b.md").write_text("# B\n")
        repo.index.add(["wiki/a.md", "wiki/b.md"])
        sha_new = repo.index.commit(
            "grove: compile \u2014 1 articles created, 1 updated"
        ).hexsha

        diff = CompileDiff(root)
        changes = diff.diff_between(sha_old, sha_new)

        changes_by_path = {c.path: c.status for c in changes}
        assert changes_by_path["wiki/a.md"] == "modified"
        assert changes_by_path["wiki/b.md"] == "added"

    def test_diff_between_ignores_non_wiki_changes(
        self, git_grove: tuple[Path, git.Repo]
    ) -> None:
        root, repo = git_grove

        (root / "wiki" / "a.md").write_text("# A\n")
        repo.index.add(["wiki/a.md"])
        sha_old = repo.index.commit("grove: compile \u2014 1 articles created").hexsha

        # Change a file outside wiki/.
        (root / "README.md").write_text("# README\n")
        repo.index.add(["README.md"])
        sha_new = repo.index.commit("docs: add readme").hexsha

        diff = CompileDiff(root)
        changes = diff.diff_between(sha_old, sha_new)

        assert changes == []
