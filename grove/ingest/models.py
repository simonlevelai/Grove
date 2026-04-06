"""Pydantic models for the ingest pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConversionResult(BaseModel):
    """Output of a file-to-markdown conversion.

    Carries the converted content alongside provenance metadata so
    downstream stages (quality scoring, dedup, manifest) can make
    informed decisions without re-reading the original file.
    """

    content: str = Field(description="The converted markdown text.")
    source_path: str = Field(description="Original file path.")
    mime_type: str = Field(description="Detected MIME type of the source.")
    converter_used: str = Field(
        description=(
            "Which converter produced the output: "
            "'pymupdf4llm', 'pdfminer', 'readability', or 'text'."
        )
    )
    word_count: int = Field(description="Word count of converted content.")
    has_headings: bool = Field(
        description="Whether markdown heading structure was detected."
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Extra metadata (e.g. PDF title, page count).",
    )
