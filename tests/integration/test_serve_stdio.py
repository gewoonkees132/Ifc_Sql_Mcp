"""``ifc-sql serve`` as an MCP client starts it: a subprocess spoken to over stdio.

The SDK's stdio client parses every line the server writes to stdout as a JSON-RPC
message and hands anything else to ``message_handler`` as an exception. A log line, a
warning or a stray print on stdout would therefore show up there.
"""

import sys
import tempfile
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.session import IncomingMessage
from mcp.client.stdio import stdio_client

from ifc_sql_mcp.config import Settings

pytestmark = pytest.mark.anyio

IFC_SQL = Path(sys.executable).with_name("ifc-sql")


async def test_serve_speaks_only_mcp_on_stdout_and_logs_to_stderr(mcp_settings: Settings) -> None:
    not_protocol: list[Exception] = []

    async def message_handler(message: IncomingMessage) -> None:
        if isinstance(message, Exception):
            not_protocol.append(message)

    server = StdioServerParameters(
        command=str(IFC_SQL),
        args=["serve"],
        env={
            "IFC_SQL_DATABASE_URL": mcp_settings.database_url,
            "IFC_SQL_READER_PASSWORD": mcp_settings.reader_password,
        },
    )
    with tempfile.TemporaryFile("w+", encoding="utf-8") as stderr:
        async with Client(
            stdio_client(server, errlog=stderr), message_handler=message_handler
        ) as client:
            protocol = client.protocol_version
            tools = [tool.name for tool in (await client.list_tools()).tools]
            result = await client.call_tool("list_storeys", {"model_id": 1})
        stderr.seek(0)
        log = stderr.read()

    assert not_protocol == []
    assert protocol == "2026-07-28"
    assert len(tools) == 10
    assert not result.is_error
    assert result.structured_content is not None
    assert [s["name"] for s in result.structured_content["storeys"]] == [
        "Foundation",
        "Level 1",
        "Level 2",
    ]
    assert "msg=serving database=postgresql+psycopg://ifc_reader:***@" in log
    assert 'msg="tool call" tool=list_storeys outcome=ok' in log
    assert mcp_settings.reader_password not in log
