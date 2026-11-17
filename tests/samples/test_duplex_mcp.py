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
            client, "quantity_takeoff", model_id=model_ids[key], group_by="storey", metric="count"
        )

    assert [
        (s["name"], s["elevation_m"], s["elements"], s["spaces"]) for s in storeys["storeys"]
    ] == EXPECTED_STOREYS[key]
    assert storeys["elements_without_storey"] == 0
    assert [(g["group"], g["value"]) for g in takeoff["groups"]] == [
        (name, elements) for name, _, elements, _ in EXPECTED_STOREYS[key]
    ]
    assert takeoff["total"] == EXPECTED_MODELS[key]["elements"]


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
