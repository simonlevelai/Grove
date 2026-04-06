"""Deduplication via SHA-256 checksums.

Before a source enters the ingest pipeline, its content is checksummed
and compared against previously ingested sources stored in
``.grove/state.json`` (via :class:`~grove.config.state.StateManager`).

This catches exact duplicates — content that has already been ingested
under a different filename or path.  Near-duplicate detection (fuzzy
matching) is out of scope for Phase 1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from grove.config.state import StateManager

# Top-level key in state.json where checksums are stored.
_CHECKSUMS_KEY = "checksums"


@dataclass(frozen=True)
class DedupResult:
    """Outcome of a deduplication check.

    Attributes:
        is_duplicate: ``True`` if the content has been seen before.
        checksum: The SHA-256 hex digest of the content.
        duplicate_of: The source path that originally stored this
            checksum, or ``None`` if not a duplicate.
    """

    is_duplicate: bool
    checksum: str
    duplicate_of: str | None = None


class Deduplicator:
    """SHA-256 checksum deduplication against ``state.json``.

    Usage::

        dedup = Deduplicator(state_manager)
        result = dedup.check(content)
        if result.is_duplicate:
            print(f"Duplicate of {result.duplicate_of}")
        else:
            dedup.store(result.checksum, source_path)
    """

    def __init__(self, state: StateManager) -> None:
        self._state = state

    @staticmethod
    def compute_checksum(content: str) -> str:
        """Return the SHA-256 hex digest of *content*."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def check(self, content: str) -> DedupResult:
        """Check whether *content* has already been ingested.

        Compares the SHA-256 checksum against all checksums stored in
        ``state.json``.  Does **not** store the checksum — call
        :meth:`store` after a successful ingest.
        """
        checksum = self.compute_checksum(content)
        checksums: dict[str, str] = self._state.get(_CHECKSUMS_KEY, {})

        # checksums is stored as {checksum: source_path}
        if checksum in checksums:
            return DedupResult(
                is_duplicate=True,
                checksum=checksum,
                duplicate_of=checksums[checksum],
            )

        return DedupResult(is_duplicate=False, checksum=checksum)

    def store(self, checksum: str, source_path: str) -> None:
        """Record *checksum* → *source_path* in ``state.json``.

        Call this after a source has been successfully ingested so
        future duplicates are detected.
        """
        checksums: dict[str, str] = self._state.get(_CHECKSUMS_KEY, {})
        checksums[checksum] = source_path
        self._state.set(_CHECKSUMS_KEY, checksums)
