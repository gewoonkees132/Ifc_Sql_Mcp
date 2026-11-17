"""The server on a database that does not answer: clear errors, no URL, no password.

No database is needed: port 1 on loopback refuses connections at once.
"""

import dataclasses
import logging

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR
from mcp_client import call_error, connect

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.server import app as server_app
from ifc_sql_mcp.server.app import build_server

pytestmark = pytest.mark.anyio

UNAVAILABLE = "the database cannot be reached right now; try again later"
SECRET = "s3cret-reader-password"


@pytest.fixture
def server() -> server_app.MCPServer:
    settings = dataclasses.replace(
        Settings.from_env({}),
        database_url="postgresql+psycopg://ifc:owner-password@127.0.0.1:1/ifc",
        reader_password=SECRET,
    )
    return build_server(make_reader_engine(settings))


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("list_models", {}), ("list_storeys", {"model_id": 1}), ("run_sql", {"sql": "SELECT 1"})],
)
async def test_a_tool_says_the_database_is_unavailable(
    server: server_app.MCPServer,
    tool: str,
    arguments: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=server_app.__name__)

    async with connect(server) as client:
        message = await call_error(client, tool, **arguments)

    assert message == UNAVAILABLE
    [record] = caplog.records
    assert (record.tool, record.outcome) == (tool, "unavailable")  # type: ignore[attr-defined]
    assert SECRET not in caplog.text
    assert "owner-password" not in caplog.text


async def test_a_resource_says_the_database_is_unavailable(server: server_app.MCPServer) -> None:
    async with connect(server) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.read_resource("ifc://schema")

    assert (excinfo.value.code, excinfo.value.message) == (INTERNAL_ERROR, UNAVAILABLE)


async def test_a_refused_query_needs_no_database(server: server_app.MCPServer) -> None:
    """The validator runs before a session opens, so a refused query never reaches one."""
    async with connect(server) as client:
        message = await call_error(client, "run_sql", sql="DELETE FROM ifc.property")

    assert message.startswith("query refused: only a query that reads data is allowed")
