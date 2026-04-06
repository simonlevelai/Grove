"""Pydantic models for health check results.

``CheckResult`` represents the outcome of a single health checker;
``HealthReport`` aggregates all check results with an overall status.
Both models serialise cleanly to JSON for the Obsidian plugin NDJSON
protocol.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CheckResult(BaseModel):
    """Outcome of a single health check."""

    name: str = Field(description="Machine-readable check name.")
    status: str = Field(description="Check outcome: 'pass', 'warn', or 'fail'.")
    message: str = Field(description="Human-readable summary.")
    details: list[str] = Field(
        default_factory=list,
        description="Specific items found (article paths, links, etc.).",
    )
    auto_fixable: bool = Field(
        default=False,
        description="Whether ``grove health --fix`` can repair this issue.",
    )


class HealthReport(BaseModel):
    """Aggregated health report across all checkers."""

    timestamp: str = Field(description="ISO-8601 timestamp of the run.")
    overall_status: str = Field(
        description="Overall status: 'healthy', 'warnings', or 'issues'."
    )
    total_articles: int = Field(description="Number of wiki articles scanned.")
    checks: dict[str, CheckResult] = Field(
        default_factory=dict,
        description="Per-check results keyed by check name.",
    )
