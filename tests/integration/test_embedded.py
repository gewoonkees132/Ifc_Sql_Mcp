"""The whole workflow on the embedded PostgreSQL, with no server configured (A18.4).

Each test runs ifc-sql's commands with only IFC_SQL_DATA_DIR set, as an architect
without Docker would: migrate, ingest, check, and serve to an MCP client as ifc_reader.
Needs the ``embedded`` extra (``uv sync --extra embedded``), which CI installs.
"""

from pathlib import Path

import make_revit_quirks
import pytest
from mcp_client import call, connect
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.embedded import embedded_database_url
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.server.app import build_server

pytest.importorskip("pixeltable_pgserver")
pytestmark = pytest.mark.anyio


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty folder for the embedded database, and an environment that points at it."""
    folder = tmp_path / "ifc-sql-data"
    monkeypatch.delenv("IFC_SQL_DATABASE_URL", raising=False)
    monkeypatch.setenv("IFC_SQL_DATA_DIR", str(folder))
    return folder


def _run(*arguments: str) -> str:
    result = CliRunner().invoke(cli.app, list(arguments))
    assert result.exit_code == 0, result.output
    return result.stdout


async def test_migrate_ingest_check_and_serve_with_no_database_server(data_dir: Path) -> None:
    fixture = make_revit_quirks.OUTPUT

    migrated = _run("migrate")
    loaded = _run("ingest", str(fixture), "--discipline", "ARC")
    checked = _run("dq")

    assert migrated == "database is at revision 0019\n"
    assert loaded.startswith(f"loaded {fixture.name} as model 1 (ARC)")
    assert checked.startswith(f"checked model 1 (ARC, {fixture.name}) in dq run 1")
    # The MCP server connects as ifc_reader, read-only, as on any server.
    url = embedded_database_url(data_dir)
    engine = make_reader_engine(Settings.from_env({"IFC_SQL_DATABASE_URL": url}))
    try:
        async with connect(build_server(engine)) as client:
            models = await call(client, "list_models")
            refused = await client.call_tool("run_sql", {"sql": "DELETE FROM model"})
    finally:
        engine.dispose()
    assert [m["model_id"] for m in models["models"]] == [1]
    assert refused.is_error


def test_the_database_is_created_once_and_found_again(data_dir: Path) -> None:
    first = embedded_database_url(data_dir)
    again = embedded_database_url(data_dir)

    assert first == again
    assert first.startswith("postgresql+psycopg://postgres:@")
    engine = create_engine(first)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
            database = connection.execute(text("SELECT current_database()")).scalar_one()
    finally:
        engine.dispose()
    assert (version.split(".")[0], database) == ("16", "ifc")
