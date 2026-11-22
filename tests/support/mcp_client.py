"""Talking to the MCP server in tests through the SDK's own client.

``Client(server, mode="legacy")`` is the SDK's in-process test client in its
initialize-handshake mode: every request and result travels as a JSON-RPC message over
in-memory streams, through the same dispatcher a stdio connection uses. (The default
mode skips the JSON-RPC layer for in-process servers.) ``test_mcp_protocol.py`` also runs
the newer 2026-07-28 negotiation, and ``test_serve_stdio.py`` real stdio.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent


@asynccontextmanager
async def connect(server: MCPServer, mode: str = "legacy") -> AsyncIterator[Client]:
    async with Client(server, mode=mode) as client:
        yield client


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool that must succeed; return its structured content.

    Also checks that the text content carries the same data as compact JSON: indented
    JSON has line breaks, compact JSON none (a line break inside a string is escaped).
    """
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    [content] = result.content
    assert isinstance(content, TextContent)
    assert result.structured_content is not None
    assert json.loads(content.text) == result.structured_content
    assert "\n" not in content.text
    return result.structured_content


async def call_error(client: Client, tool: str, **arguments: Any) -> str:
    """Call a tool that must fail; return the error text the LLM would read."""
    result = await client.call_tool(tool, arguments)
    assert result.is_error, result.structured_content
    assert result.structured_content is None
    [content] = result.content
    assert isinstance(content, TextContent)
    return content.text
