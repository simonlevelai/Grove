"""Tests for quality scoring and deduplication.

Covers all three quality grades, pdfminer fallback triggering partial,
and SHA-256 duplicate detection via StateManager.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.config.state import StateManager
from grove.ingest.dedup import Deduplicator
from grove.ingest.models import ConversionResult
from grove.ingest.quality import QualityScorer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    word_count: int,
    has_headings: bool = True,
    converter_used: str = "pymupdf4llm",
) -> ConversionResult:
    """Build a ConversionResult with the given metrics.

    The ``content`` field is filled with placeholder words matching
    *word_count* so the model validates correctly.
    """
    content = " ".join(["word"] * word_count)
    return ConversionResult(
        content=content,
        source_path="/tmp/test.pdf",
        mime_type="application/pdf",
        converter_used=converter_used,
        word_count=word_count,
        has_headings=has_headings,
    )


# ---------------------------------------------------------------------------
# QualityScorer
# ---------------------------------------------------------------------------


class TestQualityScorer:
    """QualityScorer assigns good / partial / poor based on metrics."""

    def setup_method(self) -> None:
        self.scorer = QualityScorer()

    def test_good_above_500_words_with_headings(self) -> None:
        """More than 500 words with heading structure scores good."""
        result = _make_result(word_count=600, has_headings=True)

        assert self.scorer.score(result) == "good"

    def test_partial_below_500_words_with_headings(self) -> None:
        """Fewer than 500 words (but >= 100) with headings scores partial."""
        result = _make_result(word_count=300, has_headings=True)

        assert self.scorer.score(result) == "partial"

    def test_partial_above_500_words_without_headings(self) -> None:
        """More than 500 words without heading structure scores partial."""
        result = _make_result(word_count=600, has_headings=False)

        assert self.scorer.score(result) == "partial"

    def test_partial_when_converter_is_pdfminer(self) -> None:
        """pdfminer fallback caps quality at partial regardless of metrics."""
        result = _make_result(
            word_count=1000,
            has_headings=True,
            converter_used="pdfminer",
        )

        assert self.scorer.score(result) == "partial"

    def test_poor_below_100_words(self) -> None:
        """Fewer than 100 words scores poor."""
        result = _make_result(word_count=50, has_headings=True)

        assert self.scorer.score(result) == "poor"

    def test_poor_zero_words(self) -> None:
        """Zero words (empty extraction) scores poor."""
        result = _make_result(word_count=0, has_headings=False)

        assert self.scorer.score(result) == "poor"

    def test_boundary_100_words_is_partial(self) -> None:
        """Exactly 100 words is partial, not poor."""
        result = _make_result(word_count=100, has_headings=False)

        assert self.scorer.score(result) == "partial"

    def test_boundary_500_words_with_headings_is_partial(self) -> None:
        """Exactly 500 words with headings is partial (threshold is >500)."""
        result = _make_result(word_count=500, has_headings=True)

        assert self.scorer.score(result) == "partial"

    def test_pdfminer_below_100_words_is_poor(self) -> None:
        """pdfminer with <100 words is poor — word count check wins."""
        result = _make_result(
            word_count=50,
            has_headings=False,
            converter_used="pdfminer",
        )

        assert self.scorer.score(result) == "poor"


# ---------------------------------------------------------------------------
# Deduplicator
# ---------------------------------------------------------------------------


class TestDeduplicator:
    """Deduplicator uses SHA-256 checksums stored in state.json."""

    @pytest.fixture()
    def state(self, tmp_path: Path) -> StateManager:
        """Create a StateManager backed by a temporary directory."""
        grove_dir = tmp_path / ".grove"
        grove_dir.mkdir()
        return StateManager(tmp_path)

    @pytest.fixture()
    def dedup(self, state: StateManager) -> Deduplicator:
        return Deduplicator(state)

    def test_detects_exact_duplicate(
        self, dedup: Deduplicator, state: StateManager
    ) -> None:
        """Content ingested once is detected as a duplicate on second check."""
        content = "This is some document content for testing."
        first = dedup.check(content)
        assert first.is_duplicate is False

        # Store the checksum after "successful ingest"
        dedup.store(first.checksum, "/raw/original.md")

        # Same content should now be flagged
        second = dedup.check(content)
        assert second.is_duplicate is True
        assert second.duplicate_of == "/raw/original.md"

    def test_allows_different_content(self, dedup: Deduplicator) -> None:
        """Different content should not be flagged as a duplicate."""
        result_a = dedup.check("Document A content.")
        dedup.store(result_a.checksum, "/raw/a.md")

        result_b = dedup.check("Document B content — completely different.")
        assert result_b.is_duplicate is False

    def test_stores_checksum_in_state(
        self, dedup: Deduplicator, state: StateManager
    ) -> None:
        """After store(), the checksum is persisted in state.json."""
        content = "Persistent content for checksum storage."
        result = dedup.check(content)
        dedup.store(result.checksum, "/raw/stored.md")

        # Read state directly to verify persistence
        checksums = state.get("checksums", {})
        assert result.checksum in checksums
        assert checksums[result.checksum] == "/raw/stored.md"

    def test_checksum_is_deterministic(self) -> None:
        """Same content always produces the same SHA-256 checksum."""
        content = "Deterministic test content."
        checksum_a = Deduplicator.compute_checksum(content)
        checksum_b = Deduplicator.compute_checksum(content)

        assert checksum_a == checksum_b

    def test_checksum_is_sha256_hex(self) -> None:
        """Checksum should be a 64-character hexadecimal string."""
        checksum = Deduplicator.compute_checksum("test")

        assert len(checksum) == 64
        assert all(c in "0123456789abcdef" for c in checksum)

    def test_empty_state_has_no_duplicates(self, dedup: Deduplicator) -> None:
        """Fresh state should never report duplicates."""
        result = dedup.check("Any content at all.")

        assert result.is_duplicate is False
        assert result.duplicate_of is None

    def test_dedup_result_carries_checksum(self, dedup: Deduplicator) -> None:
        """DedupResult always carries the computed checksum."""
        result = dedup.check("Checksum carrier test.")

        expected = Deduplicator.compute_checksum("Checksum carrier test.")
        assert result.checksum == expected
