"""Drive ``ifc-sql serve`` over stdio as an MCP client, asking an architect's questions.

Usage: .venv/bin/python -I architect_workflow.py <ifc-sql binary> <out.json> <model_id>...

Each step is one tool call, prompt or resource read, logged with its arguments, wall time,
the size of the text block the client receives (what an LLM would read), and the
structured result, so the audit can compare it with ground truth from IfcOpenShell.
"""

# ruff: noqa: T201

import json
import sys
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

LOG: list[dict[str, Any]] = []
MAX_DOOR_PAGES = 30


async def call(client: Client, step: str, tool: str, **args: Any) -> dict[str, Any] | None:
    """Call one tool and log it; return its structured result, or None on a tool error."""
    start = time.perf_counter()
    result = await client.call_tool(tool, args)
    seconds = time.perf_counter() - start
    text = " ".join(getattr(block, "text", "") for block in result.content)
    LOG.append(
        {
            "step": step,
            "tool": tool,
            "args": args,
            "seconds": round(seconds, 3),
            "is_error": bool(result.is_error),
            "text_chars": len(text),
            "approx_tokens": len(text) // 4,
            "result": text if result.is_error else result.structured_content,
        }
    )
    return None if result.is_error else result.structured_content


async def read(client: Client, step: str, uri: str) -> str:
    """Read one resource and log its size."""
    start = time.perf_counter()
    contents = (await client.read_resource(uri)).contents
    text = getattr(contents[0], "text", "")
    LOG.append(
        {
            "step": step,
            "resource": uri,
            "seconds": round(time.perf_counter() - start, 3),
            "text_chars": len(text),
            "approx_tokens": len(text) // 4,
            "result": text[:3000],
        }
    )
    return str(text)


# The queries an LLM (or a developer helping an architect) would write through run_sql.
# ":m" is replaced by the model id.
QUERIES = {
    "external_walls_by_type": """
        SELECT e.revit_type, count(*) AS walls, round(sum(e.area_m2)::numeric, 1) AS area_m2
        FROM v_element AS e JOIN property AS p ON p.object_id = e.object_id
        WHERE e.model_id = :m AND e.ifc_class_base = 'IfcWall'
          AND p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'IsExternal' AND p.value_bool
        GROUP BY e.revit_type ORDER BY walls DESC""",
    "doors_per_storey": """
        SELECT storey_name, count(*) AS doors FROM v_element
        WHERE model_id = :m AND ifc_class_base = 'IfcDoor'
        GROUP BY storey_name ORDER BY doors DESC LIMIT 10""",
    "door_schedule_by_type": """
        SELECT e.revit_type, count(*) AS doors,
               count(fr.value_text) AS with_fire_rating,
               string_agg(DISTINCT fr.value_text, ', ') AS fire_ratings,
               count(*) FILTER (WHERE ext.value_bool) AS external
        FROM v_element e
        LEFT JOIN property fr ON fr.object_id = e.object_id
             AND fr.pset_name = 'Pset_DoorCommon' AND fr.prop_name = 'FireRating'
        LEFT JOIN property ext ON ext.object_id = e.object_id
             AND ext.pset_name = 'Pset_DoorCommon' AND ext.prop_name = 'IsExternal'
        WHERE e.model_id = :m AND e.ifc_class_base = 'IfcDoor'
        GROUP BY e.revit_type ORDER BY doors DESC LIMIT 15""",
    "door_sizes_anywhere": """
        SELECT p.pset_name, p.prop_name, count(*) AS n
        FROM property p JOIN object o USING (object_id)
        WHERE o.model_id = :m AND o.ifc_class IN ('IfcDoor','IfcWindow')
          AND (p.prop_name ILIKE '%width%' OR p.prop_name ILIKE '%height%')
        GROUP BY 1, 2 ORDER BY 3 DESC""",
    "fire_rated_walls": """
        SELECT coalesce(p.value_text, '(none)') AS fire_rating, count(*) AS walls
        FROM v_element e LEFT JOIN property p ON p.object_id = e.object_id
             AND p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'FireRating'
        WHERE e.model_id = :m AND e.ifc_class_base = 'IfcWall'
        GROUP BY 1 ORDER BY 2 DESC""",
    "wall_area_naive_from_qto": """
        SELECT round(sum(p.value_num)::numeric, 1) AS net_side_area_m2, count(*) AS values
        FROM property p JOIN object o USING (object_id)
        WHERE o.model_id = :m AND p.pset_name = 'Qto_WallBaseQuantities'
          AND p.prop_name = 'NetSideArea'""",
    "wall_area_by_type_from_qto": """
        SELECT e.revit_type, count(*) AS walls,
               round(sum(p.value_num)::numeric, 1) AS net_side_area_m2
        FROM v_element e JOIN property p ON p.object_id = e.object_id
        WHERE e.model_id = :m AND p.pset_name = 'Qto_WallBaseQuantities'
          AND p.prop_name = 'NetSideArea'
        GROUP BY 1 ORDER BY 3 DESC LIMIT 8""",
    "space_areas_from_qto": """
        SELECT count(*) AS spaces, round(sum(p.value_num)::numeric, 1) AS net_floor_area_m2
        FROM property p JOIN object o USING (object_id)
        WHERE o.model_id = :m AND o.ifc_class = 'IfcSpace'
          AND p.pset_name = 'Qto_SpaceBaseQuantities' AND p.prop_name = 'NetFloorArea'""",
    "space_volume_trap": """
        SELECT o.name, s.long_name, a.value_num AS net_floor_area_m2, h.value_num AS height_m,
               v.value_num AS gross_volume_m3,
               round((a.value_num * h.value_num)::numeric, 2) AS area_x_height
        FROM object o JOIN spatial_element s USING (object_id)
        JOIN property a ON a.object_id = o.object_id
             AND a.pset_name = 'Qto_SpaceBaseQuantities' AND a.prop_name = 'NetFloorArea'
        JOIN property h ON h.object_id = o.object_id
             AND h.pset_name = 'Qto_SpaceBaseQuantities' AND h.prop_name = 'Height'
        JOIN property v ON v.object_id = o.object_id
             AND v.pset_name = 'Qto_SpaceBaseQuantities' AND v.prop_name = 'GrossVolume'
        WHERE o.model_id = :m ORDER BY a.value_num DESC LIMIT 5""",
    "find_by_revit_element_id": """
        SELECT object_id, ifc_class, name, storey_name FROM v_element
        WHERE model_id = :m AND revit_element_id = (
          SELECT revit_element_id FROM v_element WHERE model_id = :m AND ifc_class_base = 'IfcDoor'
          ORDER BY object_id LIMIT 1)""",
    "materials_of_windows": """
        SELECT count(*) AS windows, count(materials) AS with_materials FROM v_element
        WHERE model_id = :m AND ifc_class_base = 'IfcWindow'""",
}


async def page_all_doors(client: Client, model_id: int, offset: int | None, step: str) -> None:
    """Page through every door, as an LLM building a door schedule would have to."""
    pages, start = 1, time.perf_counter()
    while offset is not None and pages < MAX_DOOR_PAGES:
        arguments = {"model_id": model_id, "ifc_class": "IfcDoor", "limit": 100, "offset": offset}
        result = await client.call_tool("find_elements", arguments)
        offset = (result.structured_content or {}).get("next_offset")
        pages += 1
    LOG.append({"step": step, "pages": pages, "seconds": round(time.perf_counter() - start, 3)})


async def one_model(client: Client, m: int) -> None:
    """The architect's questions on one model."""
    p = f"m{m} "
    await call(client, p + "S2 storeys", "list_storeys", model_id=m)
    for step, group_by, metric in (
        ("S3 count by class", "ifc_class", "count"),
        ("S4 wall area by type", "revit_type", "area_m2"),
        ("S4b area by storey", "storey", "area_m2"),
        ("S5 material area", "material", "area_m2"),
    ):
        await call(
            client, p + step, "quantity_takeoff", model_id=m, group_by=group_by, metric=metric
        )
    doors = await call(
        client,
        p + "S6 list doors page 1",
        "find_elements",
        model_id=m,
        ifc_class="IfcDoor",
        limit=100,
    )
    await page_all_doors(client, m, (doors or {}).get("next_offset"), p + "S6b all door pages")
    if doors and doors["elements"]:
        await call(
            client, p + "S7 door detail", "get_element", object_id=doors["elements"][0]["object_id"]
        )
    walls = await call(
        client, p + "S7b first wall", "find_elements", model_id=m, ifc_class="IfcWall", limit=1
    )
    if walls and walls["elements"]:
        await call(
            client,
            p + "S7c wall detail",
            "get_element",
            object_id=walls["elements"][0]["object_id"],
        )
    await call(client, p + "S8 room schedule", "space_schedule", model_id=m)
    await call(client, p + "S9 dq report", "data_quality_report", model_id=m)
    for name, body in QUERIES.items():
        await call(
            client, p + f"S10 sql {name}", "run_sql", sql=body.replace(":m", str(m)), max_rows=50
        )
    await read(client, p + "S11 summary resource", f"ifc://models/{m}/summary")
    prompt = await client.get_prompt("model_briefing", {"model_id": str(m)})
    chars = sum(len(getattr(x.content, "text", "")) for x in prompt.messages)
    LOG.append({"step": p + "S12 briefing prompt", "text_chars": chars})


async def session(server: StdioServerParameters, models: list[int]) -> None:
    """One MCP session over stdio, as a client would hold it."""
    async with Client(stdio_client(server)) as client:
        start = time.perf_counter()
        tools = (await client.list_tools()).tools
        LOG.append(
            {
                "step": "connect",
                "seconds": round(time.perf_counter() - start, 3),
                "tools": [t.name for t in tools],
            }
        )
        await call(client, "S1 what is loaded", "list_models")
        await read(client, "S0 schema resource", "ifc://schema")
        for m in models:
            await one_model(client, m)
        await call(
            client,
            "S13 storey alignment across models",
            "run_sql",
            sql="SELECT * FROM v_storey_alignment ORDER BY 1 LIMIT 40",
            max_rows=40,
        )


def main() -> None:
    """Run the session and write the log."""
    binary, out = sys.argv[1], Path(sys.argv[2])
    models = [int(m) for m in sys.argv[3:]]
    server = StdioServerParameters(command=binary, args=["serve"], env={})
    anyio.run(session, server, models)
    out.write_text(json.dumps(LOG, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    for entry in LOG:
        what = entry.get("tool", entry.get("resource", ""))
        error = "ERROR" if entry.get("is_error") else ""
        print(
            f"{entry['step'][:46]:46} {what:20} {entry.get('seconds', ''):>7} s "
            f"{entry.get('text_chars', ''):>8} ch {error}"
        )


if __name__ == "__main__":
    main()
