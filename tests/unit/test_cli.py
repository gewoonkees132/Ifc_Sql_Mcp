import re

import pytest
from typer.testing import CliRunner

from ifc_sql_mcp import __version__
from ifc_sql_mcp.cli import app, resolve_settings
from ifc_sql_mcp.config import SettingsError

runner = CliRunner()

# Typer forces coloured Rich output when it detects CI (GITHUB_ACTIONS, FORCE_COLOR), so
# help text can contain ANSI escape codes. Compare against the plain text instead.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"ifc-sql {__version__}"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    # Click-style groups exit with 2 when invoked without a command.
    assert result.exit_code == 2
    assert "Usage: ifc-sql" in _plain(result.output)


def test_resolve_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IFC_SQL_LOG_LEVEL", "warning")

    assert resolve_settings(None).log_level == "WARNING"


def test_resolve_settings_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IFC_SQL_LOG_LEVEL", "warning")

    assert resolve_settings("debug").log_level == "DEBUG"


def test_resolve_settings_rejects_bad_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IFC_SQL_LOG_LEVEL", raising=False)

    with pytest.raises(SettingsError):
        resolve_settings("loud")
