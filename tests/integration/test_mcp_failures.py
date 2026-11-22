"""A bug in the server: the client gets a generic error, the log gets the traceback."""

import logging

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR
from mcp_client import call_error, connect
from sqlalchemy import Connection, Engine

from ifc_sql_mcp.server import app as server_app
from ifc_sql_mcp.server import tools
from ifc_sql_mcp.server.app import build_server

pytestmark = pytest.mark.anyio

SECRET = "postgresql://ifc:hunter2@db/ifc"


def _crash(connection: Connection) -> str:
    raise RuntimeError(f"unexpected, with {SECRET} in the message")


@pytest.fixture
def server(mcp_engine: Engine) -> MCPServer:
    return build_server(mcp_engine)


async def test_an_unexpected_error_in_a_tool_is_reported_without_its_text(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tools, "list_models", _crash)
    caplog.set_level(logging.INFO, logger=server_app.__name__)

    async with connect(server) as client:
        message = await call_error(client, "list_models")

    assert message == "internal error in list_models; the server's log has the details"
    crash, call = caplog.records
    assert crash.levelno == logging.ERROR
    assert crash.exc_info is not None
    assert (call.outcome, call.error) == ("error", message)  # type: ignore[attr-defined]


async def test_an_unexpected_error_in_a_resource_is_reported_without_its_text(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(server_app, "describe_schema", _crash)
    caplog.set_level(logging.INFO, logger=server_app.__name__)

    async with connect(server) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.read_resource("ifc://schema")

    assert (excinfo.value.code, excinfo.value.message) == (
        INTERNAL_ERROR,
        "internal error reading ifc://schema; the server's log has the details",
    )
    assert SECRET not in excinfo.value.message
    [crash] = [r for r in caplog.records if r.name == server_app.__name__]
    assert crash.exc_info is not None
