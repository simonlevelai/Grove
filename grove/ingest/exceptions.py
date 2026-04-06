"""Ingest pipeline exceptions."""

from __future__ import annotations


class ConversionError(Exception):
    """Raised when file conversion fails after all fallbacks are exhausted."""


class UnsupportedFormatError(ConversionError):
    """Raised when the file's MIME type has no registered converter."""
