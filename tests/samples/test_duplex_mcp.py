"""The MCP tools on the four Duplex files, with the figures docs/mcp.md cites.

Deselected by default. Run with::

    make samples && uv run pytest -m sample

The data quality figures must equal those ``test_duplex_dq.py`` pins for the runs
themselves, the table in docs/data-quality.md.
"""

import dataclasses
from collections.abc import Iterator

import pytest
import quirks_report as report
from mcp.server.mcpserver import MCPServer
from mcp_client import call, connect
from sqlalchemy import Engine
from test_duplex_dq import EXPECTED_FINDINGS, EXPECTED_SEVERITIES

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.dq.run import DqResult
from ifc_sql_mcp.ingest.pipeline import IngestResult
from ifc_sql_mcp.server.app import build_server

pytestmark = [pytest.mark.sample, pytest.mark.anyio]

EXPECTED_MODELS = {
    "arc": {"storeys": 4, "spaces": 21, "elements": 268, "materials": 18, "length_unit": "m"},
    "mep": {"storeys": 3, "spaces": 42, "elements": 926, "materials": 0, "length_unit": "m"},
    "ele": {"storeys": 3, "spaces": 1, "elements": 103, "materials": 6, "length_unit": "ft"},
    "plu": {"storeys": 3, "spaces": 1, "elements": 498, "materials": 3, "length_unit": "mm"},
}
# Openings in each file (IfcOpeningElement), which takeoffs leave out by default (A3).
OPENINGS = {"arc": 50, "mep": 0, "ele": 0, "plu": 0}
# Storey name, elevation in metres, elements and spaces on it, from list_storeys.
EXPECTED_STOREYS = {
    "arc": [("T/FDN", -1.25, 14, 0), ("Level 1", 0.0, 117, 10), ("Level 2", 3.1, 125, 10),
            ("Roof", 6.0, 12, 1)],
    "mep": [("Level 1", 0.0, 497, 20), ("Level 2", 3.1, 422, 20), ("Roof", 6.0, 7, 2)],
    "ele": [("Level 1", 0.0, 56, 0), ("Level 2", 3.1, 45, 0), ("Roof", 6.0, 2, 1)],
    "plu": [("Level 1", 0.0, 292, 0), ("Level 2", 3.1, 201, 0), ("Roof", 6.0, 5, 1)],
}  # fmt: skip


@pytest.fixture(scope="module")
def server(
    settings: Settings,
    duplex: tuple[Engine, dict[str, IngestResult]],
    checked: dict[str, DqResult],
) -> Iterator[tuple[MCPServer, dict[str, int]]]:
    """The server on the checked sample database, as ifc_reader, and each file's model id."""
    engine, ingested = duplex
    url = engine.url.render_as_string(hide_password=False)
    reader = make_reader_engine(dataclasses.replace(settings, database_url=url))
    yield build_server(reader), {key: ingested[key].model_id for key in report.FILES}
    reader.dispose()


@pytest.mark.parametrize("key", report.FILES)
async def test_list_models_shows_each_file_and_its_latest_check(
    server: tuple[MCPServer, dict[str, int]], key: str
) -> None:
    mcp, model_ids = server
    async with connect(mcp) as client:
        models = {m["model_id"]: m for m in (await call(client, "list_models"))["models"]}

    model = models[model_ids[key]]
    assert {field: model[field] for field in EXPECTED_MODELS[key]} == EXPECTED_MODELS[key]
    quality = model["data_quality"]
    assert quality["status"] == "checked"
    assert {
        "error": quality["errors"],
        "warning": quality["warnings"],
        "info": quality["info"],
    } == EXPECTED_SEVERITIES[key]


@pytest.mark.parametrize("key", report.FILES)
async def test_list_storeys_and_the_takeoff_by_storey_agree(
    server: tuple[MCPServer, dict[str, int]], key: str
) -> None:
    mcp, model_ids = server
    async with connect(mcp) as client:
        storeys = await call(client, "list_storeys", model_id=model_ids[key])
        takeoff = await call(
            client,
            "quantity_takeoff",
            model_id=model_ids[key],
            group_by="storey",
            metric="count",
            include_openings=True,
        )
        without_openings = await call(
            client, "quantity_takeoff", model_id=model_ids[key], group_by="storey", metric="count"
        )

    assert [
        (s["name"], s["elevation_m"], s["elements"], s["spaces"]) for s in storeys["storeys"]
    ] == EXPECTED_STOREYS[key]
    assert storeys["elements_without_storey"] == 0
    # list_storeys counts every IfcElement, as IFC does; so does the takeoff with openings.
    assert [(g["group"], g["value"]) for g in takeoff["groups"]] == [
        (name, elements) for name, _, elements, _ in EXPECTED_STOREYS[key]
    ]
    assert takeoff["total"] == EXPECTED_MODELS[key]["elements"]
    assert without_openings["total"] == EXPECTED_MODELS[key]["elements"] - OPENINGS[key]
    assert without_openings["openings_left_out"] == OPENINGS[key]


@pytest.mark.parametrize("key", report.FILES)
async def test_space_schedule_lists_every_space(
    server: tuple[MCPServer, dict[str, int]], key: str
) -> None:
    mcp, model_ids = server
    async with connect(mcp) as client:
        schedule = await call(client, "space_schedule", model_id=model_ids[key])

    assert schedule["space_count"] == EXPECTED_MODELS[key]["spaces"]
    assert schedule["spaces_without_area"] == 0
    assert schedule["truncated"] is False


async def test_the_architectural_spaces_add_up(server: tuple[MCPServer, dict[str, int]]) -> None:
    mcp, model_ids = server
    async with connect(mcp) as client:
        schedule = await call(client, "space_schedule", model_id=model_ids["arc"])

    assert round(schedule["total_area_m2"], 3) == 422.047
    first = schedule["spaces"][0]
    assert (first["number"], first["name"], first["storey_name"]) == ("A101", "Foyer", "Level 1")


@pytest.mark.parametrize("key", report.FILES)
async def test_data_quality_report_counts_what_the_run_found(
    server: tuple[MCPServer, dict[str, int]], checked: dict[str, DqResult], key: str
) -> None:
    mcp, model_ids = server
    async with connect(mcp) as client:
        quality = await call(client, "data_quality_report", model_id=model_ids[key])

    assert quality["dq_run_id"] == checked[key].run_id
    assert quality["findings"] == EXPECTED_SEVERITIES[key]
    assert {r["rule_id"]: r["findings"] for r in quality["rules"]} == EXPECTED_FINDINGS[key]
    assert all(len(r["samples"]) == min(3, r["findings"]) for r in quality["rules"])
    assert len(quality["rules_without_findings"]) == 31 - len(EXPECTED_FINDINGS[key])


async def test_material_totals_are_not_element_totals(
    server: tuple[MCPServer, dict[str, int]],
) -> None:
    """The architectural model's area by element and by material, as the README cites them."""
    mcp, model_ids = server
    async with connect(mcp) as client:
        by_class = await call(
            client, "quantity_takeoff", model_id=model_ids["arc"], group_by="ifc_class",
            metric="area_m2",
        )  # fmt: skip
        by_material = await call(
            client, "quantity_takeoff", model_id=model_ids["arc"], group_by="material",
            metric="area_m2",
        )  # fmt: skip

    walls = next(g for g in by_class["groups"] if g["group"] == "IfcWall")
    plasterboard = next(g for g in by_material["groups"] if g["group"] == "Plasterboard")
    assert (by_class["basis"], by_material["basis"]) == ("element", "material")
    # Without the 50 openings, 4 of which carry a copy of the roof's 132.9 m2 (A3).
    assert (by_class["openings_left_out"], by_material["openings_left_out"]) == (50, 50)
    assert round(by_class["total"], 1) == 1505.0
    assert round(by_material["total"], 1) == 4797.5
    assert (round(walls["value"], 1), round(plasterboard["value"], 1)) == (587.7, 931.9)


async def test_run_sql_finds_the_external_walls(server: tuple[MCPServer, dict[str, int]]) -> None:
    """The README's run_sql example, as it is written there (the ARC file loads first)."""
    mcp, model_ids = server
    assert model_ids["arc"] == 1
    sql = """
        SELECT e.revit_type, count(*) AS walls, round(sum(e.area_m2)::numeric, 1) AS area_m2
        FROM v_element AS e JOIN property AS p ON p.object_id = e.object_id
        WHERE e.model_id = 1 AND e.ifc_class_base = 'IfcWall'
          AND p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'IsExternal' AND p.value_bool
        GROUP BY e.revit_type ORDER BY area_m2 DESC
    """
    async with connect(mcp) as client:
        result = await call(client, "run_sql", sql=sql)

    assert result["rows"] == [
        ["Exterior - Brick on Block", 12, 272.0],
        ["Foundation - Concrete (417mm)", 4, 64.4],
        ["Party Wall - CMU Residential Unit Dimising Wall", 4, 61.9],
        ["Foundation - Concrete (435mm)", 3, 12.5],
    ]
