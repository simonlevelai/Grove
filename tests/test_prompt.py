"""Tests for grove.compile.prompt — PromptBuilder.

Covers: loading shipped defaults, variable substitution, missing variable
errors, user override precedence, list_prompts, and templates with no
variables.  All filesystem state uses tmp_path fixtures (no mocking).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.compile.prompt import _SHIPPED_PROMPTS_DIR, PromptBuilder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def grove_project(tmp_path: Path) -> Path:
    """Create a minimal grove project structure with .grove/prompts/."""
    prompts_dir = tmp_path / ".grove" / "prompts"
    prompts_dir.mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Test: shipped defaults load correctly
# ---------------------------------------------------------------------------


class TestShippedDefaults:
    """PromptBuilder loads shipped default prompts from prompts/."""

    def test_loads_shipped_compile_wiki(self) -> None:
        builder = PromptBuilder()
        result = builder.build(
            "compile-wiki.md",
            sources="source content here",
            existing_wiki="existing wiki content",
            timestamp="2026-04-03T14:22:00Z",
        )
        assert "source content here" in result
        assert "existing wiki content" in result
        assert "2026-04-03T14:22:00Z" in result

    def test_loads_shipped_query(self) -> None:
        builder = PromptBuilder()
        result = builder.build(
            "query.md",
            question="What is Grove?",
            wiki_index="index content",
            articles="article content",
        )
        assert "What is Grove?" in result
        assert "index content" in result
        assert "article content" in result

    def test_loads_shipped_summarise(self) -> None:
        builder = PromptBuilder()
        result = builder.build("summarise.md", source="document text")
        assert "document text" in result

    def test_loads_shipped_contradiction(self) -> None:
        builder = PromptBuilder()
        result = builder.build(
            "contradiction.md",
            article_a="first article",
            article_b="second article",
        )
        assert "first article" in result
        assert "second article" in result

    def test_shipped_prompts_directory_exists(self) -> None:
        """Sanity check that the shipped prompts directory is resolvable."""
        assert (
            _SHIPPED_PROMPTS_DIR.is_dir()
        ), f"Shipped prompts directory not found: {_SHIPPED_PROMPTS_DIR}"


# ---------------------------------------------------------------------------
# Test: variable substitution
# ---------------------------------------------------------------------------


class TestVariableSubstitution:
    """PromptBuilder substitutes string.Template variables correctly."""

    def test_substitutes_single_variable(self, grove_project: Path) -> None:
        prompt_file = grove_project / ".grove" / "prompts" / "simple.md"
        prompt_file.write_text("Hello $name, welcome.", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build("simple.md", name="Simon")
        assert result == "Hello Simon, welcome."

    def test_substitutes_multiple_variables(self, grove_project: Path) -> None:
        prompt_file = grove_project / ".grove" / "prompts" / "multi.md"
        prompt_file.write_text(
            "Name: $name\nRole: $role\nOrg: $org",
            encoding="utf-8",
        )

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build("multi.md", name="Simon", role="founder", org="Level AI")
        assert "Name: Simon" in result
        assert "Role: founder" in result
        assert "Org: Level AI" in result

    def test_preserves_non_variable_dollar_signs(self, grove_project: Path) -> None:
        """Dollar signs that are not template variables are kept as-is."""
        prompt_file = grove_project / ".grove" / "prompts" / "dollars.md"
        prompt_file.write_text("Cost is $$5.00 for $item", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build("dollars.md", item="coffee")
        # $$ is the escape sequence for a literal $ in string.Template
        assert result == "Cost is $5.00 for coffee"


# ---------------------------------------------------------------------------
# Test: missing variable raises KeyError
# ---------------------------------------------------------------------------


class TestMissingVariables:
    """PromptBuilder raises KeyError when required variables are absent."""

    def test_raises_on_missing_variable(self, grove_project: Path) -> None:
        prompt_file = grove_project / ".grove" / "prompts" / "needs-var.md"
        prompt_file.write_text("Hello $name, you work at $org", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        with pytest.raises(KeyError):
            builder.build("needs-var.md", name="Simon")
            # $org is missing — should raise

    def test_raises_on_all_variables_missing(self, grove_project: Path) -> None:
        prompt_file = grove_project / ".grove" / "prompts" / "needs-all.md"
        prompt_file.write_text("$greeting $target", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        with pytest.raises(KeyError):
            builder.build("needs-all.md")


# ---------------------------------------------------------------------------
# Test: user override takes precedence
# ---------------------------------------------------------------------------


class TestUserOverride:
    """User prompts in .grove/prompts/ override shipped defaults."""

    def test_user_override_wins(self, grove_project: Path) -> None:
        """A user file with the same name as a shipped prompt takes priority."""
        # Write a user override for compile-wiki.md
        user_prompt = grove_project / ".grove" / "prompts" / "compile-wiki.md"
        user_prompt.write_text(
            "CUSTOM: Compile from $sources with $existing_wiki",
            encoding="utf-8",
        )

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build(
            "compile-wiki.md",
            sources="my sources",
            existing_wiki="my wiki",
        )
        assert result.startswith("CUSTOM:")
        assert "my sources" in result
        assert "my wiki" in result

    def test_shipped_default_used_when_no_override(self, grove_project: Path) -> None:
        """When no user override exists, the shipped default is loaded."""
        builder = PromptBuilder(grove_root=grove_project)
        # summarise.md exists only in shipped prompts, not in the user dir
        result = builder.build("summarise.md", source="test content")
        assert "test content" in result
        # Verify it came from the shipped default (contains the standard text)
        assert "150 word" in result

    def test_user_only_prompt_not_in_shipped(self, grove_project: Path) -> None:
        """A prompt that exists only in user overrides is still loadable."""
        custom_prompt = grove_project / ".grove" / "prompts" / "custom-task.md"
        custom_prompt.write_text("Do $task for $target", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build("custom-task.md", task="analysis", target="report")
        assert result == "Do analysis for report"


# ---------------------------------------------------------------------------
# Test: FileNotFoundError for missing prompts
# ---------------------------------------------------------------------------


class TestMissingPrompt:
    """PromptBuilder raises FileNotFoundError for non-existent prompt names."""

    def test_raises_for_unknown_prompt(self) -> None:
        builder = PromptBuilder()
        with pytest.raises(FileNotFoundError, match="does-not-exist.md"):
            builder.build("does-not-exist.md")

    def test_raises_for_unknown_prompt_with_grove_root(
        self, grove_project: Path
    ) -> None:
        builder = PromptBuilder(grove_root=grove_project)
        with pytest.raises(FileNotFoundError, match="nonexistent.md"):
            builder.build("nonexistent.md")


# ---------------------------------------------------------------------------
# Test: list_prompts
# ---------------------------------------------------------------------------


class TestListPrompts:
    """list_prompts returns all available prompt names."""

    def test_lists_shipped_defaults(self) -> None:
        builder = PromptBuilder()
        prompts = builder.list_prompts()
        assert "compile-wiki.md" in prompts
        assert "query.md" in prompts
        assert "summarise.md" in prompts
        assert "contradiction.md" in prompts

    def test_includes_user_prompts(self, grove_project: Path) -> None:
        custom = grove_project / ".grove" / "prompts" / "my-custom.md"
        custom.write_text("Custom prompt", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        prompts = builder.list_prompts()
        assert "my-custom.md" in prompts
        # Shipped defaults should still be present
        assert "compile-wiki.md" in prompts

    def test_deduplicates_overrides(self, grove_project: Path) -> None:
        """If a user override shares a name with a shipped prompt, it appears once."""
        override = grove_project / ".grove" / "prompts" / "compile-wiki.md"
        override.write_text("Override content", encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        prompts = builder.list_prompts()
        count = prompts.count("compile-wiki.md")
        assert count == 1

    def test_list_is_sorted(self, grove_project: Path) -> None:
        builder = PromptBuilder(grove_root=grove_project)
        prompts = builder.list_prompts()
        assert prompts == sorted(prompts)


# ---------------------------------------------------------------------------
# Test: template with no variables
# ---------------------------------------------------------------------------


class TestNoVariables:
    """A template with no $variables returns its content unchanged."""

    def test_static_template_returns_as_is(self, grove_project: Path) -> None:
        static_prompt = grove_project / ".grove" / "prompts" / "static.md"
        content = "This prompt has no variables at all."
        static_prompt.write_text(content, encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build("static.md")
        assert result == content

    def test_static_template_ignores_extra_variables(self, grove_project: Path) -> None:
        """Extra kwargs are ignored when there are no placeholders."""
        static_prompt = grove_project / ".grove" / "prompts" / "static2.md"
        content = "No placeholders here."
        static_prompt.write_text(content, encoding="utf-8")

        builder = PromptBuilder(grove_root=grove_project)
        result = builder.build("static2.md", unused="value")
        assert result == content


# ---------------------------------------------------------------------------
# Test: grove_root is None
# ---------------------------------------------------------------------------


class TestNoGroveRoot:
    """When grove_root is None, only shipped defaults are available."""

    def test_shipped_prompt_loads_without_grove_root(self) -> None:
        builder = PromptBuilder(grove_root=None)
        result = builder.build("summarise.md", source="content")
        assert "content" in result

    def test_list_prompts_without_grove_root(self) -> None:
        builder = PromptBuilder(grove_root=None)
        prompts = builder.list_prompts()
        assert len(prompts) >= 4  # At least the four shipped defaults


# ---------------------------------------------------------------------------
# Test: grove_root without .grove/prompts/ directory
# ---------------------------------------------------------------------------


class TestMissingUserPromptsDir:
    """When grove_root exists but .grove/prompts/ does not, fall back gracefully."""

    def test_falls_back_to_shipped(self, tmp_path: Path) -> None:
        # Create .grove/ but not .grove/prompts/
        (tmp_path / ".grove").mkdir()

        builder = PromptBuilder(grove_root=tmp_path)
        result = builder.build("summarise.md", source="fallback test")
        assert "fallback test" in result

    def test_list_prompts_without_user_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".grove").mkdir()

        builder = PromptBuilder(grove_root=tmp_path)
        prompts = builder.list_prompts()
        # Should still list shipped defaults
        assert "compile-wiki.md" in prompts
