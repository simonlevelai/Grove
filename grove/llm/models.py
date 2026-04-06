"""Pydantic models for LLM request/response I/O.

These models define the contract between the router, providers, and
cost tracker.  All providers accept an ``LLMRequest`` and return an
``LLMResponse``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    """Describes a single LLM completion request.

    The ``model`` field is populated by the router based on the tier and
    routing config -- callers do not need to set it directly.
    """

    prompt: str
    system: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    tier: str = "standard"
    task_type: str = "unknown"
    model: str = ""


class LLMResponse(BaseModel):
    """Result returned by every provider after a successful completion."""

    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = Field(default=0.0, description="Estimated cost in USD")
