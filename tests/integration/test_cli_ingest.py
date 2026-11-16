"""``ifc-sql ingest`` end to end: exit codes, what goes to stdout and what to stderr."""

import re
from collections.abc import Callable
from pathlib import Path

import make_revit_quirks
import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import head_revision, upgrade
from ifc_sql_mcp.ingest.pipeline import IngestResult

FIXTURE = make_revit_quirks.OUTPUT
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _ingest(path: Path, discipline: str = "ARC") -> Result:
    return CliRunner().invoke(cli.app, ["ingest", str(path), "--discipline", discipline])


def _plain(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


@pytest.fixture
def database(
    ingest_settings: Settings, ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Engine:
    """Point the CLI at the ingest database (emptied after the test)."""
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", ingest_settings.database_url)
    return ingest_engine


def test_ingest_loads_then_skips_and_exits_0_both_times(database: Engine) -> None:
    first = _ingest(FIXTURE)
    second = _ingest(FIXTURE)

    assert (first.exit_code, second.exit_code) == (0, 0), first.output + second.output
    assert first.stdout == (
        "loaded revit_quirks.ifc as model 1 (ARC): 20 objects, 36 property values, "
        "8 issues (run 1)\n"
    )
    assert second.stdout == (
        "revit_quirks.ifc is already loaded as model 1 (ARC); nothing written (run 2, skipped)\n"
    )
    # Logs go to stderr, one line per issue code; stdout stays the one-line summary.
    assert 'msg="transform issues" run_id=1 code=negative_quantity count=1' in first.stderr


def test_ingest_of_a_corrupt_file_exits_1_with_the_error_and_no_traceback(
    database: Engine, tmp_path: Path
) -> None:
    corrupt = tmp_path / "corrupt.ifc"
    corrupt.write_text("this is not an IFC file\n")

    result = _ingest(corrupt)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "ingest of corrupt.ifc failed (run 1): ExtractError: corrupt.ifc: not a readable" in (
        result.stderr
    )
    assert "Traceback" not in result.output
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("revision", "found"), [(None, "not migrated"), ("0004", "at revision 0004")]
)
def test_ingest_into_a_database_that_is_not_at_the_latest_revision_exits_1(
    create_database: Callable[[str], Settings],
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    found: str,
) -> None:
    settings = create_database("test_outdated")
    if revision is not None:
        upgrade(settings, revision)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", settings.database_url)

    result = _ingest(FIXTURE)

    assert result.exit_code == 1
    assert (
        f"is {found}, but ingest needs revision {head_revision()}: run `ifc-sql migrate` first"
        in _plain(result.stderr)
    )


def test_ingest_says_when_a_concurrent_ingest_loaded_the_file(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    concurrent = IngestResult(
        run_id=7, status="skipped", model_id=3, discipline="ARC", concurrent=True
    )
    monkeypatch.setattr(cli, "ingest_file", lambda *args: concurrent)

    result = _ingest(FIXTURE)

    assert result.exit_code == 0
    assert result.stdout == (
        "revit_quirks.ifc was loaded as model 3 (ARC) by another ingest running at the same "
        "time; nothing written (run 7, skipped)\n"
    )


def test_ingest_reports_a_file_it_cannot_read(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typer checks the path first; this covers a file that becomes unreadable after that."""

    def unreadable(*args: object) -> IngestResult:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "ingest_file", unreadable)

    result = _ingest(FIXTURE)

    assert result.exit_code == 1
    assert f"cannot read {FIXTURE}: Permission denied" in _plain(result.stderr)
