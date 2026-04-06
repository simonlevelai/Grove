"""PromptBuilder — loads, merges, and renders prompt templates.

Shipped default prompts live in ``prompts/`` at the repository root.
User overrides live in ``.grove/prompts/`` within the grove directory.
When both exist for a given prompt name, the user override wins entirely
(no partial merge).

Prompts use Python ``string.Template`` for variable substitution
(``$variable`` syntax) as mandated by coding-standards invariant 7.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

# Locate the shipped prompts directory relative to this file.
# prompt.py lives at grove/compile/prompt.py, so the repo root
# is two levels up: grove/compile/ -> grove/ -> repo root.
_SHIPPED_PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "prompts"


class PromptBuilder:
    """Build prompt strings from shipped defaults and optional user overrides.

    The merge strategy is simple: if a user override exists at
    ``.grove/prompts/{name}``, it replaces the shipped default entirely.
    There is no partial merge — the user file wins.

    Parameters
    ----------
    grove_root:
        Path to the grove project root (the directory that contains
        ``.grove/``).  If ``None``, only shipped defaults are available.
    """

    def __init__(self, grove_root: Path | None = None) -> None:
        self._grove_root = grove_root
        self._user_prompts_dir: Path | None = None

        if grove_root is not None:
            candidate = grove_root / ".grove" / "prompts"
            if candidate.is_dir():
                self._user_prompts_dir = candidate

    def build(self, prompt_name: str, **variables: str) -> str:
        """Load a prompt template, substitute variables, and return the result.

        Resolution order:
        1. ``.grove/prompts/{prompt_name}`` (user override)
        2. shipped ``prompts/{prompt_name}`` (default)

        Raises
        ------
        FileNotFoundError
            If the prompt name does not exist in either location.
        KeyError
            If required template variables are missing from *variables*.
        """
        template_text = self._load_template(prompt_name)
        template = Template(template_text)

        # Template.substitute() raises KeyError for missing variables,
        # which is exactly the behaviour we want — fail loudly rather
        # than silently leaving $placeholders in the output.
        return template.substitute(**variables)

    def list_prompts(self) -> list[str]:
        """Return the names of all available prompts (shipped + user overrides).

        Names from both directories are combined and deduplicated.
        The list is sorted alphabetically for deterministic output.
        """
        names: set[str] = set()

        if _SHIPPED_PROMPTS_DIR.is_dir():
            for path in _SHIPPED_PROMPTS_DIR.iterdir():
                if path.is_file():
                    names.add(path.name)

        if self._user_prompts_dir is not None and self._user_prompts_dir.is_dir():
            for path in self._user_prompts_dir.iterdir():
                if path.is_file():
                    names.add(path.name)

        return sorted(names)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_template(self, prompt_name: str) -> str:
        """Resolve and read the template file for *prompt_name*."""
        # Check user override first
        if self._user_prompts_dir is not None:
            user_path = self._user_prompts_dir / prompt_name
            if user_path.is_file():
                return user_path.read_text(encoding="utf-8")

        # Fall back to shipped default
        shipped_path = _SHIPPED_PROMPTS_DIR / prompt_name
        if shipped_path.is_file():
            return shipped_path.read_text(encoding="utf-8")

        raise FileNotFoundError(
            f"Prompt '{prompt_name}' not found in shipped defaults "
            f"({_SHIPPED_PROMPTS_DIR}) or user overrides "
            f"({self._user_prompts_dir or 'not configured'})"
        )
