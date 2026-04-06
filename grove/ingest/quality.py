"""Quality scoring for conversion output.

Assigns a grade to each converted source based on word count, heading
structure, and which converter produced the output.  The grade determines
whether the source is used in compilation (good/partial) or flagged for
manual review (poor).

Scoring rules (from ARCH.md Decision 3):
    good    — word_count > 500 AND has_headings is True
    partial — word_count >= 100 AND (word_count <= 500 OR not has_headings),
              OR converter_used is a fallback ("pdfminer")
    poor    — word_count < 100
"""

from __future__ import annotations

from typing import Literal

from grove.ingest.models import ConversionResult

QualityGrade = Literal["good", "partial", "poor"]

# Converters whose output is capped at partial regardless of other metrics.
_FALLBACK_CONVERTERS: frozenset[str] = frozenset({"pdfminer"})

_GOOD_WORD_THRESHOLD = 500
_POOR_WORD_THRESHOLD = 100


class QualityScorer:
    """Scores a ConversionResult as ``good``, ``partial``, or ``poor``.

    This is a pure function wrapper — no side effects, no state.  The
    logic is deliberately simple so it can be reasoned about in tests
    and extended later without hidden coupling.
    """

    def score(self, result: ConversionResult) -> QualityGrade:
        """Return a quality grade for *result*.

        The grade is determined by three factors checked in priority
        order:

        1. **Poor** — fewer than 100 words means the extraction almost
           certainly failed or the source is trivially small.
        2. **Fallback converter** — if the converter is a known fallback
           (e.g. ``pdfminer``), the output is capped at ``partial``
           because structure is likely lost.
        3. **Good** — more than 500 words *and* heading structure
           detected.
        4. **Partial** — everything else (100–500 words, or >500 words
           without headings).
        """
        if result.word_count < _POOR_WORD_THRESHOLD:
            return "poor"

        if result.converter_used in _FALLBACK_CONVERTERS:
            return "partial"

        if result.word_count > _GOOD_WORD_THRESHOLD and result.has_headings:
            return "good"

        return "partial"
