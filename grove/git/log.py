"""CompileLog -- structured history of grove auto-commits.

Reads the git log, filters for commits whose messages start with
``grove:``, and returns them as typed Pydantic models.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import git
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Matches the articles count from compile messages like
# "grove: compile -- 5 articles created, 2 updated"
_ARTICLES_RE = re.compile(r"(\d+)\s+articles?\s+created")
_UPDATED_RE = re.compile(r"(\d+)\s+updated")


class GroveCommit(BaseModel):
    """A single grove auto-commit extracted from the git log."""

    sha: str
    message: str
    timestamp: str  # ISO 8601 format
    articles_affected: int | None = None  # parsed from commit message if available


class CompileLog:
    """Read and filter grove-prefixed commits from git history."""

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)

    def get_history(self, limit: int = 50) -> list[GroveCommit]:
        """Return up to *limit* grove: commits, newest first."""
        results: list[GroveCommit] = []

        for commit in self._repo.iter_commits():
            if commit.message.strip().startswith("grove:"):
                results.append(self._to_grove_commit(commit))
                if len(results) >= limit:
                    break

        return results

    def get_latest(self) -> GroveCommit | None:
        """Return the most recent grove: commit, or None."""
        history = self.get_history(limit=1)
        return history[0] if history else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_grove_commit(commit: git.Commit) -> GroveCommit:
        """Convert a gitpython Commit to a GroveCommit model."""
        message = commit.message.strip()

        # Parse articles affected from compile messages.
        articles_affected: int | None = None
        created_match = _ARTICLES_RE.search(message)
        updated_match = _UPDATED_RE.search(message)
        if created_match or updated_match:
            total = 0
            if created_match:
                total += int(created_match.group(1))
            if updated_match:
                total += int(updated_match.group(1))
            articles_affected = total

        # Convert authored_datetime to ISO 8601.
        ts = datetime.fromtimestamp(commit.authored_date, tz=UTC).isoformat()

        return GroveCommit(
            sha=commit.hexsha,
            message=message,
            timestamp=ts,
            articles_affected=articles_affected,
        )
