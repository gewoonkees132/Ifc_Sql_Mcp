"""``ifc-sql watch``: an export saved into a folder is loaded and checked (audit A18).

The architect's week, in a temporary folder: the first export is loaded and checked; a
new export under the same name replaces it; an unchanged file is passed over; a file
still being written waits for the next look; a broken file is reported and the watch
goes on.
"""

import os
import shutil
from pathlib import Path

import make_revit_quirks
import pytest
from cli_output import plain
from sqlalchemy import Engine, text
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.dq.catalogue import load_catalogue
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.watch import Watcher

FIXTURE = make_revit_quirks.OUTPUT


@pytest.fixture
def database(
    ingest_settings: Settings, ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Engine:
    """Point the CLI at the ingest database (emptied after the test)."""
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", ingest_settings.database_url)
    return ingest_engine


def _watch_once(folder: Path, *options: str) -> Result:
    return CliRunner().invoke(
        cli.app, ["watch", str(folder), "--discipline", "ARC", "--once", *options]
    )


def _new_export(path: Path, long_name: str) -> None:
    """Save another export over ``path``: the fixture with another project name."""
    text_ = FIXTURE.read_text(encoding="utf-8").replace("'Fixture House'", f"'{long_name}'")
    path.write_text(text_, encoding="utf-8")


def _models(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT m.model_id, m.file_name, m.project_key, m.replaces_model_id, "
                    "(SELECT count(*) FROM ifc.dq_run AS r WHERE r.model_id = m.model_id) "
                    "FROM ifc.model AS m ORDER BY 1"
                )
            )
        ]


def test_each_export_is_loaded_checked_and_replaces_the_one_before(
    database: Engine, tmp_path: Path
) -> None:
    export = tmp_path / "House.ifc"
    shutil.copy(FIXTURE, export)
    (tmp_path / "notes.txt").write_text("not a model\n")
    (tmp_path / "old.ifc").mkdir()  # a folder named like a file is passed over

    first = _watch_once(tmp_path, "--project", "house")
    again = _watch_once(tmp_path, "--project", "house")
    _new_export(export, "Fixture House, week 2")
    second = _watch_once(tmp_path, "--project", "house")

    assert (first.exit_code, again.exit_code, second.exit_code) == (0, 0, 0), first.output
    assert first.stdout.splitlines()[0].startswith("loaded House.ifc as model 1 (ARC)")
    assert first.stdout.splitlines()[1].startswith("checked model 1 (ARC, House.ifc) in dq run 1")
    assert again.stdout == ""  # the same bytes: nothing to do, no run recorded
    assert second.stdout.splitlines()[0].startswith("loaded House.ifc as model 2 (ARC)")
    assert _models(database) == [
        (1, "House.ifc", "house", None, 1),
        (2, "House.ifc", "house", 1, 1),
    ]


def test_a_file_is_taken_only_once_it_has_stopped_changing(
    database: Engine, tmp_path: Path
) -> None:
    rules = load_catalogue()
    with database.begin() as connection:
        sync_rules(connection, rules)
    watcher = Watcher(database, tmp_path, "ARC", rules)
    export = tmp_path / "House.ifc"
    shutil.copy(FIXTURE, export)

    seen = watcher.poll()
    os.utime(export, ns=(1, 2))  # Revit is still writing it
    still_writing = watcher.poll()
    stable = watcher.poll()

    assert (seen, still_writing) == ([], [])
    [event] = stable
    assert (event.path, event.error) == (export, None)
    assert event.ingest is not None
    assert event.check is not None
    assert watcher.poll() == []


def test_a_broken_file_is_reported_and_the_others_are_loaded(
    database: Engine, tmp_path: Path
) -> None:
    (tmp_path / "A_broken.ifc").write_text("not IFC\n")
    shutil.copy(FIXTURE, tmp_path / "B_house.ifc")

    result = _watch_once(tmp_path)

    assert result.exit_code == 1
    assert (
        "A_broken.ifc failed: ingest run 1: ExtractError: A_broken.ifc: not a readable IFC file"
        in plain(result.stderr)
    )
    assert "loaded B_house.ifc as model" in result.stdout


def test_a_file_that_cannot_be_read_is_reported(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ifc_sql_mcp import watch

    def unreadable(path: Path) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(watch, "sha256_file", unreadable)
    shutil.copy(FIXTURE, tmp_path / "House.ifc")

    result = _watch_once(tmp_path)

    assert result.exit_code == 1
    assert "House.ifc failed: cannot read it: Permission denied" in result.stderr


def test_a_failed_check_is_reported(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ifc_sql_mcp import watch

    def fail(*args: object) -> object:
        raise watch.DqRunError(7, "RuleError: broken")

    monkeypatch.setattr(watch, "check_model", fail)
    shutil.copy(FIXTURE, tmp_path / "House.ifc")

    result = _watch_once(tmp_path)

    assert result.exit_code == 1
    assert "House.ifc failed: dq run 7: RuleError: broken" in result.stderr


def test_the_watch_looks_again_until_it_is_stopped(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looks: list[float] = []

    def sleep(seconds: float) -> None:
        looks.append(seconds)
        if len(looks) == 1:
            shutil.copy(FIXTURE, tmp_path / "House.ifc")
        if len(looks) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)

    result = CliRunner().invoke(
        cli.app, ["watch", str(tmp_path), "--discipline", "ARC", "--interval", "0.5"]
    )

    assert result.exit_code == 0, result.output
    assert looks == [0.5, 0.5, 0.5]
    assert "watching" in result.stderr
    assert "stopped watching" in result.stderr
    # Seen on the second look, stable on the third.
    assert result.stdout.startswith("loaded House.ifc as model 1 (ARC)")


def test_an_unreachable_database_exits_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc@127.0.0.1:1/ifc")

    result = _watch_once(tmp_path)

    assert result.exit_code == 1
    assert "cannot reach the database" in plain(result.stderr)


def test_ingest_warnings_are_passed_on(database: Engine, tmp_path: Path) -> None:
    text_ = FIXTURE.read_text(encoding="utf-8").replace("'0042',$,$,", "'0001',$,$,")
    (tmp_path / "Default.ifc").write_text(text_, encoding="utf-8")

    result = _watch_once(tmp_path)

    assert result.exit_code == 0
    assert "warning: the project number '0001' is Revit's default" in result.stderr
