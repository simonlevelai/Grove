"""CostTracker -- records token usage and enforces budget limits.

Appends a JSON line to ``.grove/logs/costs.jsonl`` after each LLM call.
Implements budget checking against daily spend limits from config.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from grove.llm.models import LLMResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table -- USD per 1M tokens (input / output)
# Updated as of 2026-04-03.  Add new models here as they become available.
# ---------------------------------------------------------------------------

_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic models (per 1M tokens: input, output)
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    # Azure AI Foundry -- Claude models via serverless endpoints.
    # Pricing mirrors direct Anthropic rates; adjust if Azure differs.
    "claude-sonnet-4-6-azure": (3.0, 15.0),
    "claude-opus-4-6-azure": (15.0, 75.0),
    # OpenAI models (per 1M tokens: input, output)
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o3": (2.0, 8.0),
    "o4-mini": (1.10, 4.40),
    # Mistral models (per 1M tokens: input, output)
    "mistral-large-latest": (2.0, 6.0),
    "mistral-small-latest": (0.10, 0.30),
    "codestral-latest": (0.30, 0.90),
    # Ollama models are free (local inference)
    "llama3.2": (0.0, 0.0),
}


class BudgetExceededError(Exception):
    """Raised when daily API spend exceeds the configured limit."""


class CostTracker:
    """Tracks LLM costs and enforces budget limits.

    Each completed LLM call is logged as a JSON line in the costs file.
    Budget checks compare today's accumulated spend against the configured
    daily limit, raising ``BudgetExceededError`` if exceeded.
    """

    def __init__(
        self,
        logs_dir: Path,
        daily_limit_usd: float = 5.00,
        warn_at_usd: float = 3.00,
    ) -> None:
        self.costs_path = logs_dir / "costs.jsonl"
        self.daily_limit_usd = daily_limit_usd
        self.warn_at_usd = warn_at_usd

        # Ensure the logs directory exists
        logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Cost estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """Return estimated cost in USD for a given model and token counts."""
        input_price, output_price = _PRICING.get(model, (0.0, 0.0))
        cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        return round(cost, 6)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, response: LLMResponse, task_type: str) -> None:
        """Append a cost record for a completed LLM call."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "task_type": task_type,
            "model": response.model,
            "provider": response.provider,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
        }
        with self.costs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------
    # Budget checking
    # ------------------------------------------------------------------

    def get_today_spend(self) -> float:
        """Sum all costs recorded for the current UTC date."""
        if not self.costs_path.exists():
            return 0.0

        today = datetime.now(UTC).date().isoformat()
        total = 0.0

        with self.costs_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", "")
                if ts.startswith(today):
                    total += entry.get("cost_usd", 0.0)

        return round(total, 6)

    def get_cost_summary(self, today_only: bool = False) -> dict[str, dict[str, float]]:
        """Aggregate costs by task type and model.

        Returns a nested dict: ``{task_type: {model: total_usd}}``.
        When *today_only* is True, only entries from the current UTC date
        are included.
        """
        if not self.costs_path.exists():
            return {}

        today = datetime.now(UTC).date().isoformat() if today_only else None
        summary: dict[str, dict[str, float]] = {}

        with self.costs_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if today and not entry.get("timestamp", "").startswith(today):
                    continue

                task_type = entry.get("task_type", "unknown")
                model = entry.get("model", "unknown")
                cost = entry.get("cost_usd", 0.0)

                if task_type not in summary:
                    summary[task_type] = {}
                summary[task_type][model] = summary[task_type].get(model, 0.0) + cost

        return summary

    def check_budget(self) -> None:
        """Check whether today's spend is within budget.

        Raises ``BudgetExceededError`` if the daily limit is exceeded.
        Logs a warning if the warning threshold is crossed.
        """
        today_spend = self.get_today_spend()

        if today_spend >= self.daily_limit_usd:
            raise BudgetExceededError(
                f"Daily budget exceeded: ${today_spend:.2f} spent "
                f"(limit: ${self.daily_limit_usd:.2f})"
            )

        if today_spend >= self.warn_at_usd:
            logger.warning(
                "Budget warning: $%.2f spent today (warning threshold: $%.2f, "
                "limit: $%.2f)",
                today_spend,
                self.warn_at_usd,
                self.daily_limit_usd,
            )
