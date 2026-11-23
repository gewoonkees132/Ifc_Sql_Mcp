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
from notifications import listening

from ifc_sql_mcp.config import Settings

pytestmark = pytest.mark.anyio

IFC_SQL = Path(sys.executable).with_name("ifc-sql")


@pytest.mark.parametrize("feed", [False, True], ids=["feed off", "feed on"])
async def test_serve_speaks_only_mcp_on_stdout_and_logs_to_stderr(
    mcp_settings: Settings, feed: bool
) -> None:
    """With the viewer's feed on too: the feed goes to PostgreSQL, never to stdout."""
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
            "IFC_SQL_VIEWER_FEED": "1" if feed else "0",
        },
    )
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8") as stderr,
        listening(mcp_settings) as received,
    ):
        async with Client(
            stdio_client(server, errlog=stderr), message_handler=message_handler
        ) as client:
            protocol = client.protocol_version
            tools = [tool.name for tool in (await client.list_tools()).tools]
            result = await client.call_tool("list_storeys", {"model_id": 1})
        stderr.seek(0)
        log = stderr.read()
        published = received(count=1, seconds=3.0 if feed else 1.0)

    assert not_protocol == []
    assert protocol == "2026-07-28"
    assert len(tools) == 11
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
    assert ("viewer_feed=on" in log) is feed
    assert [event["tool"] for event in published] == (["list_storeys"] if feed else [])
