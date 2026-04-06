"""Tests for the Grove CLI entry point."""

from typer.testing import CliRunner

from grove.cli import app

runner = CliRunner()


def test_help_prints_usage() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "grove" in result.output.lower()
    assert "COMMAND" in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "grove 0.1.0" in result.output


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "grove 0.1.0" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "COMMAND" in result.output
