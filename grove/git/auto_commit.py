"""AutoCommitter -- low-level git operations for grove auto-commits.

Stages changes in ``wiki/`` and commits with a structured message.
This is a thin wrapper around gitpython; higher-level orchestration
(commit-vs-rollback decisions) lives in the compile engine's
GitCommitter.

All commit messages follow the ``grove: <operation> -- <stats>`` format
defined in git-workflow.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

import git

logger = logging.getLogger(__name__)

# Prefix used for all grove auto-commits.
_GROVE_PREFIX = "grove:"


class AutoCommitter:
    """Stage wiki/ changes and produce structured grove commits."""

    def __init__(self, repo_path: Path) -> None:
        """Wrap a gitpython Repo rooted at *repo_path*."""
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def commit_compile(
        self,
        articles_created: int,
        articles_updated: int,
        cost_usd: float | None = None,
    ) -> str:
        """Stage wiki/ and commit with a compile summary message.

        Returns the hexsha of the new commit.
        """
        parts: list[str] = []
        if articles_created:
            parts.append(f"{articles_created} articles created")
        if articles_updated:
            parts.append(f"{articles_updated} updated")
        if not parts:
            parts.append("0 articles")

        message = f"grove: compile \u2014 {', '.join(parts)}"

        if cost_usd is not None:
            message += f" (cost: ${cost_usd:.2f})"

        return self._stage_and_commit(message)

    def commit_health_fix(self, fixes: list[str]) -> str:
        """Commit wiki/ changes produced by a health fix pass.

        *fixes* is a human-readable list of what was repaired.
        Returns the hexsha of the new commit.
        """
        description = "; ".join(fixes) if fixes else "no changes"
        message = f"grove: health fix \u2014 {description}"
        return self._stage_and_commit(message)

    def commit_file_query(self, query_path: str) -> str:
        """Commit a filed query answer.

        Returns the hexsha of the new commit.
        """
        message = f"grove: file query \u2014 {query_path}"
        return self._stage_and_commit(message)

    def has_changes(self) -> bool:
        """Return True if wiki/ has uncommitted changes (staged or not)."""
        wiki_dir = self._repo_path / "wiki"
        if not wiki_dir.exists():
            return False

        # Check for untracked files inside wiki/
        for untracked in self._repo.untracked_files:
            if untracked.startswith("wiki/") or untracked.startswith("wiki\\"):
                return True

        # Check for staged and unstaged diffs that touch wiki/
        for diff_list in (
            self._repo.index.diff("HEAD"),  # staged
            self._repo.index.diff(None),  # unstaged (working tree)
        ):
            for diff_item in diff_list:
                path = diff_item.a_path or diff_item.b_path
                if path and (path.startswith("wiki/") or path.startswith("wiki\\")):
                    return True

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stage_and_commit(self, message: str) -> str:
        """Stage everything under wiki/ and commit with *message*."""
        wiki_dir = self._repo_path / "wiki"
        if not wiki_dir.exists():
            raise FileNotFoundError(f"wiki/ directory not found at {self._repo_path}")

        # Stage all changes in wiki/ (adds, modifications, and deletions).
        self._repo.git.add("wiki/", "--all")

        logger.info("Committing: %s", message)
        commit = self._repo.index.commit(message)
        return commit.hexsha
