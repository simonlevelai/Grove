"""Tests for the Grove health check module and ``grove health`` CLI command.

Covers TASK-019: ProvenanceChecker, StalenessChecker, GapDetector,
OrphanDetector, HealthReporter (aggregation, --fix), and the CLI
command in both Rich and NDJSON output modes.

Uses ``tmp_path`` fixtures exclusively -- no mocking of filesystem.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import git
import pytest
import yaml
from typer.testing import CliRunner

from grove.cli import app
from grove.config.state import StateManager
from grove.health.gaps import GapDetector
from grove.health.models import CheckResult, HealthReport
from grove.health.orphans import OrphanDetector
from grove.health.provenance import ProvenanceChecker
from grove.health.reporter import HealthReporter
from grove.health.staleness import StalenessChecker

runner = CliRunner()


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def grove_root(tmp_path: Path) -> Path:
    """Create a minimal grove directory structure.

    Returns the grove root path with .grove/, wiki/, and raw/ directories.
    """
    (tmp_path / ".grove" / "logs").mkdir(parents=True)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw" / "articles").mkdir(parents=True)

    config = {"llm": {"providers": {}}}
    (tmp_path / ".grove" / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )

    (tmp_path / ".grove" / "state.json").write_text(
        json.dumps({}, indent=2) + "\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def grove_git(grove_root: Path) -> Path:
    """Extend ``grove_root`` with an initialised git repo and initial commit."""
    repo = git.Repo.init(grove_root)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    (grove_root / "wiki" / ".gitkeep").touch()
    repo.index.add(["wiki/.gitkeep"])
    repo.index.commit("initial")

    return grove_root


def _write_article(
    wiki_dir: Path,
    slug: str,
    body: str,
    *,
    front_matter: dict[str, object] | None = None,
) -> Path:
    """Helper to write a wiki article with optional YAML front matter."""
    fm = front_matter or {}
    fm_text = yaml.dump(fm, default_flow_style=False).rstrip("\n")
    content = f"---\n{fm_text}\n---\n\n{body}\n"
    article_path = wiki_dir / f"{slug}.md"
    article_path.write_text(content, encoding="utf-8")
    return article_path


# ------------------------------------------------------------------
# ProvenanceChecker
# ------------------------------------------------------------------


class TestProvenanceChecker:
    """Provenance coverage: well-cited passes, poorly-cited fails."""

    def test_well_cited_passes(self, grove_root: Path) -> None:
        """An article where all factual sentences have citations should pass."""
        wiki_dir = grove_root / "wiki"
        # Citations must be within the same sentence (before the full stop)
        # because the splitter attaches trailing text to the next sentence.
        body = (
            "The population increased by 20% in 2024 [source: ONS 2024].\n"
            "Revenue grew more than expected [source: Annual Report].\n"
            "According to research shows outcomes improved [source: Study A].\n"
        )
        _write_article(wiki_dir, "well-cited", body)

        checker = ProvenanceChecker(wiki_dir)
        result = checker.check()

        assert result.name == "provenance"
        assert result.status == "pass"

    def test_poorly_cited_fails(self, grove_root: Path) -> None:
        """An article with no citations on factual sentences should fail."""
        wiki_dir = grove_root / "wiki"
        body = (
            "The population increased by 20% in 2024.\n"
            "Revenue grew more than expected.\n"
            "Because of new policies, outcomes improved.\n"
            "Studies show this trend is accelerating.\n"
            "The budget decreased by 15% last year.\n"
        )
        _write_article(wiki_dir, "uncited", body)

        checker = ProvenanceChecker(wiki_dir)
        result = checker.check()

        assert result.name == "provenance"
        assert result.status == "fail"
        assert result.details  # Should list the poorly cited article

    def test_no_articles_passes(self, grove_root: Path) -> None:
        """No wiki articles should result in a pass."""
        wiki_dir = grove_root / "wiki"
        checker = ProvenanceChecker(wiki_dir)
        result = checker.check()

        assert result.status == "pass"

    def test_no_factual_sentences_passes(self, grove_root: Path) -> None:
        """Articles with no factual language should pass."""
        wiki_dir = grove_root / "wiki"
        body = "This is a simple descriptive paragraph with no claims."
        _write_article(wiki_dir, "simple", body)

        checker = ProvenanceChecker(wiki_dir)
        result = checker.check()

        assert result.status == "pass"

    def test_skip_files_excluded(self, grove_root: Path) -> None:
        """Index and meta files are excluded from provenance checks."""
        wiki_dir = grove_root / "wiki"
        # Write a skip file with poor provenance -- should be ignored.
        (wiki_dir / "_index.md").write_text(
            "The population increased by 20%.\n", encoding="utf-8"
        )
        checker = ProvenanceChecker(wiki_dir)
        result = checker.check()

        assert result.status == "pass"
        assert "No wiki articles found" in result.message


# ------------------------------------------------------------------
# StalenessChecker
# ------------------------------------------------------------------


class TestStalenessChecker:
    """Staleness: detects changed checksums, passes when current."""

    def test_current_sources_pass(self, grove_root: Path) -> None:
        """Articles whose sources have not changed should pass."""
        wiki_dir = grove_root / "wiki"
        raw_dir = grove_root / "raw" / "articles"

        # Create a raw source file.
        source_content = "Some raw content for testing."
        source_path = raw_dir / "source.md"
        source_path.write_text(source_content, encoding="utf-8")
        source_checksum = hashlib.sha256(source_content.encode("utf-8")).hexdigest()

        # Register the checksum in state.json: {checksum: relative_path}.
        state = StateManager(grove_root)
        state.set("checksums", {source_checksum: "raw/articles/source.md"})

        # Write a wiki article that references this source.
        _write_article(
            wiki_dir,
            "test-article",
            "Some compiled content.",
            front_matter={"compiled_from": ["raw/articles/source.md"]},
        )

        checker = StalenessChecker(grove_root, state)
        result = checker.check()

        assert result.name == "staleness"
        assert result.status == "pass"

    def test_changed_source_detected(self, grove_root: Path) -> None:
        """A changed source file should produce a staleness warning."""
        wiki_dir = grove_root / "wiki"
        raw_dir = grove_root / "raw" / "articles"

        # Write the source file.
        source_path = raw_dir / "source.md"
        source_path.write_text("Original content.", encoding="utf-8")

        # Register an OLD checksum (different from current file).
        old_checksum = hashlib.sha256(b"Old content.").hexdigest()
        state = StateManager(grove_root)
        state.set("checksums", {old_checksum: "raw/articles/source.md"})

        # Wiki article referencing this source.
        _write_article(
            wiki_dir,
            "stale-article",
            "Compiled from old content.",
            front_matter={"compiled_from": ["raw/articles/source.md"]},
        )

        checker = StalenessChecker(grove_root, state)
        result = checker.check()

        assert result.name == "staleness"
        assert result.status == "warn"
        assert any("changed" in d for d in result.details)

    def test_missing_source_detected(self, grove_root: Path) -> None:
        """A source path that no longer exists should flag staleness."""
        wiki_dir = grove_root / "wiki"

        state = StateManager(grove_root)
        old_checksum = hashlib.sha256(b"Gone.").hexdigest()
        state.set("checksums", {old_checksum: "raw/articles/gone.md"})

        _write_article(
            wiki_dir,
            "orphaned-article",
            "Content from a deleted source.",
            front_matter={"compiled_from": ["raw/articles/gone.md"]},
        )

        checker = StalenessChecker(grove_root, state)
        result = checker.check()

        assert result.status == "warn"
        assert any("missing" in d for d in result.details)

    def test_no_wiki_passes(self, tmp_path: Path) -> None:
        """No wiki directory at all should pass cleanly."""
        # Create a grove root without a wiki/ directory.
        (tmp_path / ".grove").mkdir()
        (tmp_path / ".grove" / "state.json").write_text("{}", encoding="utf-8")
        state = StateManager(tmp_path)

        checker = StalenessChecker(tmp_path, state)
        result = checker.check()

        assert result.status == "pass"


# ------------------------------------------------------------------
# GapDetector
# ------------------------------------------------------------------


class TestGapDetector:
    """Gap detection: broken [[wiki-links]] and source concept gaps."""

    def test_broken_wiki_link_detected(self, grove_root: Path) -> None:
        """A [[link]] to a non-existent article should be detected."""
        wiki_dir = grove_root / "wiki"
        body = "See also [[missing-concept]] for more details."
        _write_article(wiki_dir, "linking-article", body)

        detector = GapDetector(grove_root)
        result = detector.check()

        assert result.name == "gaps"
        assert result.status == "warn"
        assert "missing-concept" in result.details
        assert result.auto_fixable is True

    def test_valid_links_pass(self, grove_root: Path) -> None:
        """All links resolving to real articles should pass."""
        wiki_dir = grove_root / "wiki"
        _write_article(wiki_dir, "alpha", "See [[beta]] for details.")
        _write_article(wiki_dir, "beta", "See [[alpha]] for details.")

        detector = GapDetector(grove_root)
        result = detector.check()

        assert result.status == "pass"

    def test_source_concept_gap(self, grove_root: Path) -> None:
        """A concept in raw source front matter with no wiki article is a gap."""
        wiki_dir = grove_root / "wiki"
        raw_dir = grove_root / "raw" / "articles"

        # Write a raw source with a concept.
        raw_content = "---\ngrove_concepts:\n  - novel-concept\n---\n\nSome content.\n"
        (raw_dir / "source.md").write_text(raw_content, encoding="utf-8")

        # No wiki article for "novel-concept" exists.
        _write_article(wiki_dir, "existing-article", "Some content.")

        detector = GapDetector(grove_root)
        result = detector.check()

        assert result.status == "warn"
        assert "novel-concept" in result.details

    def test_get_broken_links_method(self, grove_root: Path) -> None:
        """The ``get_broken_links`` method returns the correct slugs."""
        wiki_dir = grove_root / "wiki"
        _write_article(
            wiki_dir,
            "article-a",
            "Links to [[missing-one]] and [[missing-two]].",
        )

        detector = GapDetector(grove_root)
        broken = detector.get_broken_links()

        assert "missing-one" in broken
        assert "missing-two" in broken

    def test_no_gaps_in_empty_wiki(self, grove_root: Path) -> None:
        """An empty wiki should report no gaps."""
        detector = GapDetector(grove_root)
        result = detector.check()

        assert result.status == "pass"


# ------------------------------------------------------------------
# OrphanDetector
# ------------------------------------------------------------------


class TestOrphanDetector:
    """Orphan detection: articles with no incoming links."""

    def test_orphan_detected(self, grove_root: Path) -> None:
        """An article with no incoming [[links]] from others is an orphan."""
        wiki_dir = grove_root / "wiki"
        _write_article(wiki_dir, "linked", "See [[linked]] for details.")
        _write_article(wiki_dir, "orphan", "Nobody links here.")

        detector = OrphanDetector(wiki_dir)
        result = detector.check()

        assert result.name == "orphans"
        assert result.status == "warn"
        assert "orphan" in result.details

    def test_no_orphans(self, grove_root: Path) -> None:
        """Articles that mutually link each other should pass."""
        wiki_dir = grove_root / "wiki"
        _write_article(wiki_dir, "alpha", "See [[beta]].")
        _write_article(wiki_dir, "beta", "See [[alpha]].")

        detector = OrphanDetector(wiki_dir)
        result = detector.check()

        assert result.status == "pass"

    def test_self_link_not_counted(self, grove_root: Path) -> None:
        """An article linking to itself does not count as an incoming link."""
        wiki_dir = grove_root / "wiki"
        _write_article(wiki_dir, "self-ref", "See [[self-ref]] for details.")

        detector = OrphanDetector(wiki_dir)
        result = detector.check()

        assert result.status == "warn"
        assert "self-ref" in result.details

    def test_empty_wiki_passes(self, grove_root: Path) -> None:
        """An empty wiki should report no orphans."""
        wiki_dir = grove_root / "wiki"
        detector = OrphanDetector(wiki_dir)
        result = detector.check()

        assert result.status == "pass"


# ------------------------------------------------------------------
# HealthReporter
# ------------------------------------------------------------------


class TestHealthReporter:
    """Reporter aggregation: correct overall status and fix mode."""

    def test_healthy_wiki(self, grove_root: Path) -> None:
        """A wiki with no issues should report 'healthy'."""
        wiki_dir = grove_root / "wiki"
        _write_article(
            wiki_dir,
            "alpha",
            "See [[beta]]. Simple descriptive text.",
        )
        _write_article(
            wiki_dir,
            "beta",
            "See [[alpha]]. More descriptive text.",
        )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        assert report.overall_status == "healthy"
        assert report.total_articles == 2
        assert "provenance" in report.checks
        assert "staleness" in report.checks
        assert "gaps" in report.checks
        assert "orphans" in report.checks
        assert "contradictions" in report.checks

    def test_warnings_aggregation(self, grove_root: Path) -> None:
        """If any check warns, overall status should be 'warnings'."""
        wiki_dir = grove_root / "wiki"
        # Create an orphan to trigger a warning.
        _write_article(wiki_dir, "lonely", "Nobody links here.")

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        assert report.overall_status == "warnings"

    def test_issues_aggregation(self, grove_root: Path) -> None:
        """If any check fails, overall status should be 'issues'."""
        wiki_dir = grove_root / "wiki"
        # Create poorly cited articles to trigger a provenance failure.
        for i in range(5):
            body = (
                f"The population increased by {i * 10}% in 2024.\n"
                "Revenue grew more than expected.\n"
                "Because of new policies, outcomes improved.\n"
                "Studies show this trend is accelerating.\n"
                "The budget decreased by 15% last year.\n"
            )
            _write_article(wiki_dir, f"uncited-{i}", body)

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        assert report.overall_status == "issues"

    def test_fix_creates_stubs(self, grove_root: Path) -> None:
        """``--fix`` should create stub articles for broken wiki-links."""
        wiki_dir = grove_root / "wiki"
        _write_article(
            wiki_dir,
            "referrer",
            "See [[stub-target]] for more info.",
        )

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        # Confirm the gap is detected before fix.
        assert report.checks["gaps"].status == "warn"
        assert "stub-target" in report.checks["gaps"].details

        # Apply fixes.
        fixes = reporter.fix(report)

        assert len(fixes) >= 1
        assert any("stub-target" in f for f in fixes)

        # Verify the stub was actually created on disk.
        stub_path = wiki_dir / "stub-target.md"
        assert stub_path.exists()
        content = stub_path.read_text(encoding="utf-8")
        assert "status: stub" in content
        assert "Stub Target" in content  # Title-cased slug

    def test_fix_does_not_overwrite_existing(self, grove_root: Path) -> None:
        """Fix mode should skip stubs for articles that already exist."""
        wiki_dir = grove_root / "wiki"
        _write_article(wiki_dir, "referrer", "See [[existing]].")
        _write_article(wiki_dir, "existing", "Already here.")

        reporter = HealthReporter(grove_root)
        report = reporter.run()
        fixes = reporter.fix(report)

        # No fixes needed -- "existing" already exists.
        assert not any("existing" in f for f in fixes)

    def test_write_health_report_file(self, grove_root: Path) -> None:
        """``write_health_report`` should create wiki/_health.md."""
        reporter = HealthReporter(grove_root)
        report = reporter.run()
        path = reporter.write_health_report(report)

        assert path.exists()
        assert path.name == "_health.md"
        content = path.read_text(encoding="utf-8")
        assert "Health Report" in content
        assert report.overall_status in content

    def test_total_articles_excludes_meta(self, grove_root: Path) -> None:
        """Article count should exclude _index.md, _concepts.md, _health.md."""
        wiki_dir = grove_root / "wiki"
        _write_article(wiki_dir, "real", "A real article.")
        (wiki_dir / "_index.md").write_text("Index.\n", encoding="utf-8")
        (wiki_dir / "_concepts.md").write_text("Concepts.\n", encoding="utf-8")

        reporter = HealthReporter(grove_root)
        report = reporter.run()

        assert report.total_articles == 1


# ------------------------------------------------------------------
# CLI: grove health
# ------------------------------------------------------------------


class TestHealthCLI:
    """Integration tests for the ``grove health`` CLI command."""

    def test_health_runs_without_errors(
        self, grove_git: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``grove health`` should exit 0 on a valid grove."""
        monkeypatch.chdir(grove_git)
        result = runner.invoke(app, ["health"])

        assert result.exit_code == 0
        assert "Health Report" in result.output

    def test_health_json_output(
        self, grove_git: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``grove health --json`` should emit valid NDJSON lines."""
        monkeypatch.chdir(grove_git)
        result = runner.invoke(app, ["health", "--json"])

        assert result.exit_code == 0
        lines = [line for line in result.output.strip().split("\n") if line.strip()]
        # Should have at least one check line and one summary line.
        assert len(lines) >= 2

        for line in lines:
            parsed = json.loads(line)
            assert "type" in parsed

        # Last line should be the summary.
        summary = json.loads(lines[-1])
        assert summary["type"] == "health_summary"
        assert "overall_status" in summary

    def test_health_fix_creates_stubs(
        self, grove_git: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``grove health --fix`` should create stub articles and commit."""
        monkeypatch.chdir(grove_git)
        wiki_dir = grove_git / "wiki"

        # Create an article with a broken link.
        _write_article(wiki_dir, "referrer", "See [[fix-me-stub]].")

        # Stage and commit the referrer so the repo is clean.
        repo = git.Repo(grove_git)
        repo.index.add(["wiki/referrer.md"])
        repo.index.commit("grove: add referrer")

        result = runner.invoke(app, ["health", "--fix"])

        assert result.exit_code == 0

        # Verify the stub was created.
        stub_path = wiki_dir / "fix-me-stub.md"
        assert stub_path.exists()

    def test_health_outside_grove(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running ``grove health`` outside a grove should fail."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["health"])

        assert result.exit_code == 1

    def test_health_shows_check_names(
        self, grove_git: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rich output should display the names of all checks."""
        monkeypatch.chdir(grove_git)
        result = runner.invoke(app, ["health"])

        assert result.exit_code == 0
        assert "provenance" in result.output
        assert "staleness" in result.output
        assert "gaps" in result.output
        assert "orphans" in result.output


# ------------------------------------------------------------------
# Model serialisation
# ------------------------------------------------------------------


class TestModels:
    """Health check model serialisation and defaults."""

    def test_check_result_defaults(self) -> None:
        """CheckResult should have sensible defaults."""
        result = CheckResult(name="test", status="pass", message="OK.")
        assert result.details == []
        assert result.auto_fixable is False

    def test_health_report_serialisation(self) -> None:
        """HealthReport should serialise cleanly to JSON."""
        report = HealthReport(
            timestamp="2026-04-03T12:00:00Z",
            overall_status="healthy",
            total_articles=5,
            checks={
                "test": CheckResult(name="test", status="pass", message="All good.")
            },
        )
        data = report.model_dump()

        assert data["overall_status"] == "healthy"
        assert data["total_articles"] == 5
        assert "test" in data["checks"]

        # Should round-trip through JSON.
        json_str = report.model_dump_json()
        restored = HealthReport.model_validate_json(json_str)
        assert restored.overall_status == "healthy"
