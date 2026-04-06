"""Pydantic models shared by quick, deep, and research query modes.

These models define the contract between the query engine, CLI, and
the Obsidian plugin's NDJSON consumer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryResult(BaseModel):
    """Structured result returned by all query modes.

    The ``mode`` field distinguishes quick from deep results so
    downstream consumers (CLI, plugin, formatter) can branch behaviour.
    """

    question: str
    answer: str
    mode: str  # "quick" or "deep"
    citations: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    model_used: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    timestamp: str = ""
