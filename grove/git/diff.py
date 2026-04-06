"""CompileDiff -- article-level diff between grove commits.

Reports which wiki articles were added, modified, or deleted between
two commits (or within a single commit compared to its parent).
"""

from __future__ import annotations

import logging
from pathlib import Path

import git
from pydantic import BaseModel

from grove.git.log import CompileLog

logger = logging.getLogger(__name__)


class ArticleDiff(BaseModel):
    """A single file-level change in wiki/."""

    path: str
    status: str  # "added", "modified", "deleted"


# Map gitpython diff change types to our status strings.
_STATUS_MAP: dict[str | None, str] = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "modified",  # renames treated as modifications
    "C": "added",  # copies treated as additions
    "T": "modified",  # type changes
}


class CompileDiff:
    """Article-level diffs between grove commits."""

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)
        self._compile_log = CompileLog(repo_path)

    def diff_last(self) -> list[ArticleDiff]:
        """Show article-level changes in the most recent grove: commit.

        Compares the latest grove: commit to its parent.
        Returns an empty list if there is no grove: commit.
        """
        latest = self._compile_log.get_latest()
        if latest is None:
            return []

        commit = self._repo.commit(latest.sha)
        if not commit.parents:
            # First commit -- everything is an addition.
            return self._diff_initial_commit(commit)

        parent = commit.parents[0]
        return self._diff_between_commits(parent, commit)

    def diff_between(self, sha_old: str, sha_new: str) -> list[ArticleDiff]:
        """Show article-level changes between two commits."""
        old_commit = self._repo.commit(sha_old)
        new_commit = self._repo.commit(sha_new)
        return self._diff_between_commits(old_commit, new_commit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _diff_between_commits(
        old_commit: git.Commit,
        new_commit: git.Commit,
    ) -> list[ArticleDiff]:
        """Compute article-level diffs for wiki/ files between two commits."""
        diffs = old_commit.diff(new_commit)
        results: list[ArticleDiff] = []

        for diff_item in diffs:
            # Use the most relevant path.
            path = diff_item.b_path or diff_item.a_path
            if not path:
                continue

            # Only report changes inside wiki/.
            if not (path.startswith("wiki/") or path.startswith("wiki\\")):
                continue

            status = _STATUS_MAP.get(diff_item.change_type, "modified")

            # For deletions, use the old path (a_path).
            if diff_item.change_type == "D" and diff_item.a_path:
                path = diff_item.a_path

            results.append(ArticleDiff(path=path, status=status))

        return results

    @staticmethod
    def _diff_initial_commit(commit: git.Commit) -> list[ArticleDiff]:
        """Treat every wiki/ file in an initial commit as added."""
        results: list[ArticleDiff] = []
        for blob in commit.tree.traverse():
            if hasattr(blob, "path") and (
                blob.path.startswith("wiki/") or blob.path.startswith("wiki\\")
            ):
                results.append(ArticleDiff(path=blob.path, status="added"))
        return results
