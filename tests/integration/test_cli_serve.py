"""``ifc-sql serve``: it checks the database as ifc_reader before it serves, and says why not.

The server itself is tested through the protocol elsewhere; ``test_serve_stdio.py`` runs
this command as a subprocess. Here the stdio loop is replaced, so the checks before it
run in this process.
"""

import dataclasses
import re
from collections.abc import Callable

import pytest
from alembic import command
from mcp.server.mcpserver import MCPServer
from sqlalchemy import make_url
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import reader_database_url, redact
from ifc_sql_mcp.db.migrate import alembic_config, head_revision

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

CreateDatabase = Callable[[str], Settings]


def _serve(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", settings.reader_password)
    return CliRunner().invoke(cli.app, ["serve"])


def _reader_url(settings: Settings) -> str:
    return redact(reader_database_url(settings.database_url, settings.reader_password))


def test_serve_checks_the_database_and_then_serves(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[str] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, transport="stdio": runs.append(transport))
    # The owner's password is not needed: serve connects as ifc_reader only.
    owner_url = make_url(test_settings.database_url)
    without_owner_password = dataclasses.replace(
        test_settings,
        database_url=owner_url.set(password=None).render_as_string(hide_password=False),
    )

    result = _serve(without_owner_password, monkeypatch)

    assert result.exit_code == 0, result.output
    assert runs == ["stdio"]
    assert result.stdout == ""
    assert f"msg=serving database={_reader_url(test_settings)} transport=stdio" in result.stderr
    assert test_settings.reader_password not in result.stderr
    assert owner_url.password is not None
    assert owner_url.password not in result.output


@pytest.mark.usefixtures("test_settings")  # creates the cluster-wide ifc_reader role
def test_serve_refuses_a_database_that_was_never_migrated(
    create_database: CreateDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = create_database("test_serve_empty")

    result = _serve(empty, monkeypatch)

    assert result.exit_code == 1
    # The last line: Alembic logs two lines when it reads the revision, as in ingest and dq.
    assert result.stderr.splitlines()[-1] == (
        f"the database at {_reader_url(empty)} is not migrated, but serve needs revision "
        f"{head_revision()}: run `ifc-sql migrate` first"
    )


def test_serve_refuses_a_database_before_the_reader_could_read_its_revision(
    create_database: CreateDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = create_database("test_serve_0008")
    command.upgrade(alembic_config(older), "0008")

    result = _serve(older, monkeypatch)

    assert result.exit_code == 1
    assert result.stderr.splitlines()[-1] == (
        f"the database at {_reader_url(older)} is at a revision before 0009, whose version "
        f"table ifc_reader may not read, but serve needs revision {head_revision()}: run "
        "`ifc-sql migrate` first"
    )


def test_serve_reports_a_wrong_reader_password_without_it(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = dataclasses.replace(test_settings, reader_password="not-the-password")

    result = _serve(wrong, monkeypatch)

    assert result.exit_code == 1
    stderr = _ANSI_ESCAPE.sub("", result.stderr)
    assert stderr.startswith(f"cannot reach the database at {_reader_url(wrong)}: ")
    assert 'password authentication failed for user "ifc_reader"' in stderr
    assert "not-the-password" not in result.output
