"""StateManager — reads and writes .grove/state.json.

Tracks source checksums, compile history, and concept graph data.
State is a plain JSON file — no schema enforcement beyond being a
valid JSON object.  Individual modules add their own keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateManager:
    """Manages the persistent state file at ``.grove/state.json``.

    State is stored as a flat JSON object.  Callers read/write
    specific top-level keys (e.g. ``checksums``, ``compile_history``,
    ``concept_graph``).
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.state_path = project_root / ".grove" / "state.json"
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        """Read state from disk, caching the result."""
        if self._cache is not None:
            return self._cache

        if not self.state_path.exists():
            self._cache = {}
            return self._cache

        raw = self.state_path.read_text(encoding="utf-8")
        data = json.loads(raw)

        if not isinstance(data, dict):
            raise ValueError(
                f"Expected a JSON object in {self.state_path}, "
                f"got {type(data).__name__}"
            )

        self._cache = data
        return self._cache

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for *key*, or *default* if absent."""
        state = self._load()
        return state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set *key* to *value* and persist to disk."""
        state = self._load()
        state[key] = value
        self._write(state)

    def delete(self, key: str) -> None:
        """Remove *key* from state if it exists, then persist."""
        state = self._load()
        state.pop(key, None)
        self._write(state)

    def read_all(self) -> dict[str, Any]:
        """Return a copy of the entire state dictionary."""
        return dict(self._load())

    def write_all(self, data: dict[str, Any]) -> None:
        """Replace the entire state with *data* and persist."""
        if not isinstance(data, dict):
            raise TypeError(f"State must be a dict, got {type(data).__name__}")
        self._cache = data
        self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        """Atomically write state to disk."""
        self._cache = data
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)

    def invalidate_cache(self) -> None:
        """Force the next read to hit disk."""
        self._cache = None
