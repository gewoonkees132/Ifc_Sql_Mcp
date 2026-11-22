"""The embedded database's failures, which need no server (A18.4)."""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ifc_sql_mcp import cli
from ifc_sql_mcp.db import embedded


def test_without_the_extra_the_error_says_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A module set to None in sys.modules raises ImportError when imported.
    monkeypatch.setitem(sys.modules, "pixeltable_pgserver.postgres_server", None)

    with pytest.raises(embedded.EmbeddedError, match="uv sync --extra embedded"):
        embedded.embedded_database_url(tmp_path / "db")


def test_a_server_that_does_not_start_is_reported_with_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pgserver = pytest.importorskip("pixeltable_pgserver.postgres_server")

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Postgres server not ready after 60s")

    monkeypatch.setattr(pgserver, "get_server", refuse)

    with pytest.raises(embedded.EmbeddedError) as failure:
        embedded.embedded_database_url(tmp_path / "db")

    assert str(failure.value) == (
        f"the embedded database in {tmp_path / 'db'} did not start: "
        "Postgres server not ready after 60s"
    )


def test_a_command_reports_an_embedded_database_that_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(data_dir: Path) -> str:
        raise embedded.EmbeddedError(f"the embedded database in {data_dir} did not start: no")

    monkeypatch.setattr(cli, "embedded_database_url", refuse)
    monkeypatch.delenv("IFC_SQL_DATABASE_URL", raising=False)
    monkeypatch.setenv("IFC_SQL_DATA_DIR", str(tmp_path / "db"))

    result = CliRunner().invoke(cli.app, ["migrate"])

    assert result.exit_code == 1
    assert result.stderr == f"the embedded database in {tmp_path / 'db'} did not start: no\n"
