"""RollbackManager -- revert or restore grove compiles.

Two modes:

* **rollback_last** -- ``git revert`` on the most recent grove: commit.
  Creates a new revert commit so history is never rewritten.

* **rollback_to** -- ``git checkout <sha> -- wiki/`` to restore the
  wiki directory to its state at a given commit, then creates a new
  commit recording the restoration.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import git

from grove.git.log import CompileLog

logger = logging.getLogger(__name__)


class RollbackError(Exception):
    """Raised when a rollback operation cannot be completed."""


class RollbackManager:
    """Revert or restore grove auto-commits."""

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)
        self._compile_log = CompileLog(repo_path)

    def rollback_last(self) -> str:
        """Revert the most recent grove: commit.

        Uses ``git revert`` so that history is preserved.
        Returns the hexsha of the new revert commit.

        Raises RollbackError if there is no grove: commit to revert.
        """
        latest = self._compile_log.get_latest()
        if latest is None:
            raise RollbackError("No grove: commit found to revert.")

        logger.info("Reverting grove commit %s: %s", latest.sha[:8], latest.message)

        # git revert --no-edit <sha>
        self._repo.git.revert(latest.sha, no_edit=True)

        return self._repo.head.commit.hexsha

    def rollback_to(self, target_sha: str) -> str:
        """Restore wiki/ to its state at *target_sha*.

        Removes all current wiki/ content, then checks out wiki/ from the
        target commit.  This ensures files added *after* the target commit
        are deleted -- ``git checkout <sha> -- wiki/`` alone would leave
        them in place.

        Returns the hexsha of the new commit.

        Raises RollbackError if the target SHA cannot be resolved.
        """
        # Validate the SHA exists in the repo.
        try:
            self._repo.commit(target_sha)
        except (git.BadName, git.GitCommandError, ValueError) as exc:
            raise RollbackError(f"Cannot resolve commit {target_sha}: {exc}") from exc

        logger.info("Restoring wiki/ to state at %s", target_sha[:8])

        wiki_dir = self._repo_path / "wiki"

        # Remove all tracked wiki/ files so that files added after the
        # target commit do not survive the checkout.
        self._repo.git.rm("-r", "--cached", "--ignore-unmatch", "wiki/")
        # Also remove working-tree copies (git rm --cached leaves them).

        for child in wiki_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        # Checkout wiki/ from the target commit.
        try:
            self._repo.git.checkout(target_sha, "--", "wiki/")
        except git.GitCommandError as exc:
            raise RollbackError(
                f"Cannot restore wiki/ from {target_sha}: {exc}"
            ) from exc

        # Stage and commit the restoration.
        self._repo.git.add("wiki/", "--all")
        message = f"grove: rollback to {target_sha[:8]}"
        commit = self._repo.index.commit(message)

        return commit.hexsha
