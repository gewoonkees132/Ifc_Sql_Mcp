from pathlib import Path

import make_revit_quirks
import pytest
from cli_output import plain
from typer.testing import CliRunner

from ifc_sql_mcp import __version__
from ifc_sql_mcp.cli import app, resolve_settings
from ifc_sql_mcp.config import SettingsError

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"ifc-sql {__version__}"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    # Click-style groups exit with 2 when invoked without a command.
    assert result.exit_code == 2
    assert "Usage: ifc-sql" in plain(result.output)


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


def test_migrate_reports_an_unreachable_database_without_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Port 1 on loopback refuses connections at once; no database is needed.
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc:s3cret@127.0.0.1:1/ifc")

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 1
    assert "cannot reach the database at postgresql+psycopg://ifc:***@127.0.0.1:1/ifc" in (
        plain(result.output)
    )
    assert "s3cret" not in result.output


def test_invalid_environment_setting_is_reported_as_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IFC_SQL_LOG_LEVEL", "verbose")

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 2
    assert "IFC_SQL_LOG_LEVEL" in plain(result.output)


# --------------------------------------------------------------------------- ingest

FIXTURE = str(make_revit_quirks.OUTPUT)


@pytest.mark.parametrize("discipline", ["arc", "A", "ARCHITECT", "AR1", ""])
def test_ingest_refuses_an_invalid_discipline_as_a_usage_error(discipline: str) -> None:
    result = runner.invoke(app, ["ingest", FIXTURE, "--discipline", discipline])

    assert result.exit_code == 2
    assert "capital letters" in plain(result.output)


def test_ingest_requires_a_discipline() -> None:
    result = runner.invoke(app, ["ingest", FIXTURE])

    assert result.exit_code == 2
    assert "--discipline" in plain(result.output)


def test_ingest_refuses_a_missing_file_as_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", str(tmp_path / "nope.ifc"), "--discipline", "ARC"])

    assert result.exit_code == 2
    assert "does not exist" in plain(result.output)


def test_ingest_reports_an_unreachable_database_without_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc:s3cret@127.0.0.1:1/ifc")

    result = runner.invoke(app, ["ingest", FIXTURE, "--discipline", "ARC"])

    assert result.exit_code == 1
    assert "cannot reach the database at postgresql+psycopg://ifc:***@127.0.0.1:1/ifc" in (
        plain(result.output)
    )
    assert "s3cret" not in result.output


# --------------------------------------------------------------------------- dq


def test_dq_reports_an_unreachable_database_without_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc:s3cret@127.0.0.1:1/ifc")

    result = runner.invoke(app, ["dq"])

    assert result.exit_code == 1
    assert "cannot reach the database at postgresql+psycopg://ifc:***@127.0.0.1:1/ifc" in (
        plain(result.output)
    )
    assert "s3cret" not in result.output


def test_dq_refuses_a_model_id_that_is_not_a_number() -> None:
    result = runner.invoke(app, ["dq", "ARC"])

    assert result.exit_code == 2
    assert "Invalid value for 'MODEL_ID'" in plain(result.output)


def test_serve_reports_an_unreachable_database_without_either_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc:s3cret@127.0.0.1:1/ifc")
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", "r3ader")

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
    # serve connects as ifc_reader, so that is the URL it reports.
    assert result.stderr.startswith(
        "cannot reach the database at postgresql+psycopg://ifc_reader:***@127.0.0.1:1/ifc: "
    )
    assert result.stdout == ""
    assert "s3cret" not in result.output
    assert "r3ader" not in result.output
