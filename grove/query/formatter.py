"""AnswerFormatter -- renders QueryResult as terminal, markdown, or Marp slides.

Each format targets a different output context:
- **terminal**: Rich-formatted text for interactive CLI use (default).
- **markdown**: Standalone ``.md`` file with YAML front matter, suitable
  for saving to ``queries/`` or promoting to ``wiki/``.
- **slides**: Marp-compatible markdown for presentation output.

See ARCH.md ``grove/query/`` table for the authoritative spec.
"""

from __future__ import annotations

import yaml

from grove.query.models import QueryResult


class AnswerFormatter:
    """Render a ``QueryResult`` into various output formats.

    Stateless -- each method is a pure function from ``QueryResult``
    to a formatted string.
    """

    # ------------------------------------------------------------------
    # Terminal output (Rich-compatible plain text)
    # ------------------------------------------------------------------

    def format_terminal(self, result: QueryResult) -> str:
        """Rich-formatted terminal output (default).

        Produces a human-readable string suitable for ``rich.print()``
        or direct ``typer.echo()``.
        """
        lines: list[str] = []

        # Header
        lines.append(f"[bold]{result.question}[/bold]")
        lines.append("")

        # Answer body
        lines.append(result.answer)
        lines.append("")

        # Metadata line
        meta_parts: list[str] = [f"mode={result.mode}"]
        if result.model_used:
            meta_parts.append(f"model={result.model_used}")
        if result.cost_usd > 0:
            meta_parts.append(f"cost=${result.cost_usd:.4f}")
        lines.append(f"[dim]{' | '.join(meta_parts)}[/dim]")

        # Citations
        if result.citations:
            lines.append("")
            lines.append("[bold]Citations:[/bold]")
            for citation in result.citations:
                lines.append(f"  [cyan][wiki: {citation}][/cyan]")

        # Follow-up questions
        if result.follow_up_questions:
            lines.append("")
            lines.append("[bold]Follow-up questions:[/bold]")
            for i, question in enumerate(result.follow_up_questions, 1):
                lines.append(f"  {i}. {question}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Markdown output (for saving to queries/ or wiki/)
    # ------------------------------------------------------------------

    def format_markdown(self, result: QueryResult) -> str:
        """Standalone markdown file with YAML front matter.

        Produces the auto-save format used by ``QueryFiler`` when
        writing to ``queries/<timestamp>-<slug>.md``.
        """
        # Build front matter dict -- order matters for readability.
        front_matter: dict[str, object] = {
            "question": result.question,
            "mode": result.mode,
            "timestamp": result.timestamp,
        }

        if result.citations:
            front_matter["citations"] = result.citations

        if result.model_used:
            front_matter["model_used"] = result.model_used

        if result.cost_usd > 0:
            front_matter["cost_usd"] = round(result.cost_usd, 4)

        fm_str = yaml.dump(
            front_matter,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).rstrip("\n")

        # Build the body.
        sections: list[str] = [
            f"---\n{fm_str}\n---",
            "",
            f"# {result.question}",
            "",
            result.answer,
        ]

        if result.follow_up_questions:
            sections.append("")
            sections.append("## Follow-up Questions")
            sections.append("")
            for i, question in enumerate(result.follow_up_questions, 1):
                sections.append(f"{i}. {question}")

        # Trailing newline for POSIX compliance.
        return "\n".join(sections) + "\n"

    # ------------------------------------------------------------------
    # Marp slides output
    # ------------------------------------------------------------------

    def format_slides(self, result: QueryResult) -> str:
        """Marp-formatted markdown slides.

        Generates a presentation with:
        - Title slide (question as heading).
        - Answer slide(s) -- split on ``##`` headings if present.
        - Citations slide (if any).
        - Follow-up questions slide (if any).
        """
        slides: list[str] = []

        # Marp front matter (always the first slide directive).
        slides.append("---")
        slides.append("marp: true")
        slides.append("theme: default")
        slides.append("paginate: true")
        slides.append("---")
        slides.append("")

        # Title slide
        slides.append(f"# {result.question}")
        slides.append("")
        meta_parts: list[str] = [f"Mode: {result.mode}"]
        if result.model_used:
            meta_parts.append(f"Model: {result.model_used}")
        slides.append(f"*{' | '.join(meta_parts)}*")

        # Answer slide(s): split on ## headings to create separate slides.
        answer_sections = _split_on_headings(result.answer)
        for section in answer_sections:
            section_text = section.strip()
            if section_text:
                slides.append("")
                slides.append("---")
                slides.append("")
                slides.append(section_text)

        # Citations slide
        if result.citations:
            slides.append("")
            slides.append("---")
            slides.append("")
            slides.append("## Sources")
            slides.append("")
            for citation in result.citations:
                slides.append(f"- `{citation}`")

        # Follow-up questions slide
        if result.follow_up_questions:
            slides.append("")
            slides.append("---")
            slides.append("")
            slides.append("## Follow-up Questions")
            slides.append("")
            for i, question in enumerate(result.follow_up_questions, 1):
                slides.append(f"{i}. {question}")

        return "\n".join(slides) + "\n"


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _split_on_headings(text: str) -> list[str]:
    """Split markdown text on ``##`` headings, keeping headings attached.

    If the text contains no ``##`` headings, returns the entire text
    as a single-element list.
    """
    import re

    parts = re.split(r"(?=\n## )", text)
    if not parts:
        return [text]
    return parts
