#!/usr/bin/env python3
"""Start ``ifc-sql serve`` as an MCP client would, and call a few tools.

Usage::

    make smoke          # or: uv run python scripts/mcp_smoke.py

It launches the server over stdio with the SDK's client, using the IFC_SQL_* variables
of this shell (``make`` exports ``.env``), and checks the configured database the way a
client session would: list the tools, resources and prompt, call list_models and
data_quality_report for every model, run one query, and read the schema resource. It
exits 1 at the first call that fails, printing the error the client got; when the
server does not start, its own message is printed above that.
"""

import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(sys.executable).with_name("ifc-sql")
PASSED_ON = ("IFC_SQL_DATABASE_URL", "IFC_SQL_READER_PASSWORD", "IFC_SQL_LOG_LEVEL")
QUERY = "SELECT discipline, count(*) AS elements FROM v_element GROUP BY 1 ORDER BY 1"


class SmokeError(Exception):
    """A call failed; the message is what the client received."""


async def _call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    content: dict[str, Any] | None = result.structured_content
    if result.is_error or content is None:
        text = " ".join(getattr(block, "text", "") for block in result.content)
        raise SmokeError(f"{tool} failed: {text}")
    return content


async def smoke(server: StdioServerParameters) -> list[str]:
    """Run the session against ``server``; return the lines to print."""
    lines: list[str] = []
    async with Client(stdio_client(server)) as client:
        tools = (await client.list_tools()).tools
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
        prompts = (await client.list_prompts()).prompts
        info = client.server_info
        name = f"{info.name} {info.version}" if info else "the server"
        lines.append(
            f"connected to {name} over stdio (protocol {client.protocol_version}): "
            f"{len(tools)} tools, {len(resources)} resource, {len(templates)} resource "
            f"template, {len(prompts)} prompt"
        )
        models = (await _call(client, "list_models"))["models"]
        lines.append(f"list_models: {len(models)} models")
        for model in models:
            report = await _call(
                client, "data_quality_report", model_id=model["model_id"], samples_per_rule=0
            )
            if report["status"] == "never_checked":
                quality = "never checked"
            else:
                counts = report["findings"]
                quality = (
                    f"dq run {report['dq_run_id']}: {counts['error']:,} error, "
                    f"{counts['warning']:,} warning, {counts['info']:,} info"
                )
            lines.append(
                f"  model {model['model_id']} {model['discipline']} {model['file_name']}: "
                f"elements {model['elements']:,}, spaces {model['spaces']:,}; {quality}"
            )
        rows = (await _call(client, "run_sql", sql=QUERY))["rows"]
        lines.append(f"run_sql: {QUERY}")
        lines.extend(f"  {row}" for row in rows)
        schema = (await client.read_resource("ifc://schema")).contents[0]
        lines.append(f"ifc://schema: {len(getattr(schema, 'text', '')):,} characters")
    return lines


def main() -> int:
    """Run the smoke session with this shell's settings; 0 if every call succeeded."""
    environment = {key: os.environ[key] for key in PASSED_ON if key in os.environ}
    server = StdioServerParameters(command=str(SERVER), args=["serve"], env=environment)
    try:
        lines = anyio.run(smoke, server)
    except Exception as exc:  # a failed call, or a server that stopped or never started
        print(f"smoke test failed: {_innermost(exc)}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


def _innermost(exc: BaseException) -> BaseException:
    """The error itself: the client's task groups wrap it in exception groups."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


if __name__ == "__main__":
    sys.exit(main())
