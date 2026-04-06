"""ContradictionDetector -- LLM-powered contradiction detection.

Groups wiki articles by shared concepts (from YAML front matter).
For pairs sharing 2+ concepts, calls the fast LLM tier to check
for contradictions.  Skipped entirely if no LLM router is provided.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from grove.health.models import CheckResult

logger = logging.getLogger(__name__)

# Files that are not regular articles.
_SKIP_FILES = {"_index.md", "_concepts.md", "_health.md"}


def _parse_front_matter(content: str) -> dict[str, Any] | None:
    """Extract YAML front matter as a dict, or ``None`` on failure."""
    stripped = content.lstrip("\n")
    if not stripped.startswith("---"):
        return None

    end_idx = stripped.find("\n---", 3)
    if end_idx == -1:
        return None

    yaml_str = stripped[3:end_idx]
    try:
        meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return None

    if not isinstance(meta, dict):
        return None

    return meta


class ContradictionDetector:
    """Detect contradictions between wiki articles sharing concepts.

    Requires an ``LLMRouter`` and ``PromptBuilder`` to function.
    If either is ``None``, the check is skipped gracefully.
    """

    def __init__(
        self,
        wiki_dir: Path,
        router: Any | None = None,
        prompt_builder: Any | None = None,
    ) -> None:
        self._wiki_dir = wiki_dir
        self._router = router
        self._prompt_builder = prompt_builder

    def check(self) -> CheckResult:
        """Run the contradiction check across article pairs."""
        if self._router is None or self._prompt_builder is None:
            return CheckResult(
                name="contradictions",
                status="pass",
                message="Skipped (no LLM router configured).",
            )

        articles = self._load_articles()
        if len(articles) < 2:
            return CheckResult(
                name="contradictions",
                status="pass",
                message="Fewer than 2 articles; no pairs to check.",
            )

        pairs = self._find_concept_pairs(articles)
        if not pairs:
            return CheckResult(
                name="contradictions",
                status="pass",
                message="No article pairs share 2+ concepts.",
            )

        contradictions: list[str] = []
        for i, j in pairs:
            result = self._check_pair(articles[i], articles[j])
            if result is not None:
                contradictions.append(result)

        if contradictions:
            return CheckResult(
                name="contradictions",
                status="fail",
                message=f"{len(contradictions)} contradiction(s) found.",
                details=contradictions,
            )

        return CheckResult(
            name="contradictions",
            status="pass",
            message=f"{len(pairs)} pair(s) checked; no contradictions.",
        )

    def _load_articles(
        self,
    ) -> list[tuple[str, str, list[str]]]:
        """Return ``(relative_path, content, concepts)`` for each article."""
        results: list[tuple[str, str, list[str]]] = []
        if not self._wiki_dir.exists():
            return results

        for md_file in sorted(self._wiki_dir.rglob("*.md")):
            if md_file.name in _SKIP_FILES:
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            meta = _parse_front_matter(content)
            concepts = meta.get("concepts", []) if meta else []
            if not isinstance(concepts, list):
                concepts = []

            rel_path = str(md_file.relative_to(self._wiki_dir))
            results.append((rel_path, content, concepts))

        return results

    def _find_concept_pairs(
        self,
        articles: list[tuple[str, str, list[str]]],
    ) -> list[tuple[int, int]]:
        """Find article index pairs sharing 2+ concepts."""
        concept_index: dict[str, list[int]] = {}
        for i, (_, _, concepts) in enumerate(articles):
            for concept in concepts:
                concept_index.setdefault(concept, []).append(i)

        pair_set: set[tuple[int, int]] = set()
        for indices in concept_index.values():
            for a_idx in indices:
                for b_idx in indices:
                    if a_idx < b_idx:
                        pair_set.add((a_idx, b_idx))

        # Filter to pairs with 2+ shared concepts.
        result: list[tuple[int, int]] = []
        for a_idx, b_idx in pair_set:
            shared = set(articles[a_idx][2]) & set(articles[b_idx][2])
            if len(shared) >= 2:
                result.append((a_idx, b_idx))

        return result

    def _check_pair(
        self,
        article_a: tuple[str, str, list[str]],
        article_b: tuple[str, str, list[str]],
    ) -> str | None:
        """Call the LLM to check a single pair; return description or None."""
        path_a, content_a, _ = article_a
        path_b, content_b, _ = article_b

        try:
            prompt_text = self._prompt_builder.build(
                "contradiction.md",
                article_a=content_a,
                article_b=content_b,
            )
        except (FileNotFoundError, KeyError) as exc:
            logger.warning("Could not build contradiction prompt: %s", exc)
            return None

        from grove.llm.models import LLMRequest

        request = LLMRequest(
            prompt=prompt_text,
            tier="fast",
            task_type="contradiction_check",
            max_tokens=2048,
            temperature=0.0,
        )

        try:
            response = self._router.complete_sync(request)
        except Exception as exc:
            logger.warning(
                "Contradiction check failed for %s vs %s: %s",
                path_a,
                path_b,
                exc,
            )
            return None

        content = response.content.strip()
        if content.upper() == "NONE":
            return None

        return f"{path_a} vs {path_b}: {content}"
