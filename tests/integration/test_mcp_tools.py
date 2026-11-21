"""The fixed tools, called through the MCP protocol, on the synthetic fixture.

``mcp_settings`` (conftest) loads the fixture as model 1, checked by the data quality
rules, and as model 2, never checked. The figures follow from
``tests/fixtures/make_revit_quirks.py``; the fixture's lengths are in feet, so they are
converted values (a 13 ft 1.5 in wall is 3.9999999999999996 m).
"""

from datetime import datetime
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp_client import call, call_error, connect
from sqlalchemy import Engine, text

from ifc_sql_mcp.server import tools
from ifc_sql_mcp.server.app import build_server

pytestmark = pytest.mark.anyio

WALL = 20
SPACE_KITCHEN = 15
UNKNOWN_MODEL = "no model with id 99; list_models shows the loaded models"


@pytest.fixture
def server(mcp_engine: Engine) -> MCPServer:
    return build_server(mcp_engine)


def _without(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in keys}


def _is_timestamp(value: object) -> bool:
    return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None


# --------------------------------------------------------------------------- list_models


async def test_list_models_describes_each_model_and_its_data_quality(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(client, "list_models")

    first, second = result["models"]
    assert result["truncated"] is False
    common = {
        "file_name": "revit_quirks.ifc",
        "ifc_schema": "IFC2X3",
        "mvd": "CoordinationView",
        "authoring_app": "ifc-sql-mcp fixture generator 1",
        "project_name": "0042",
        "project_long_name": "Fixture House",
        "length_unit": "ft",
        "storeys": 3,
        "spaces": 2,
        "elements": 13,
        "property_values": 36,
        "quantities": 8,
        "materials": 6,
    }
    assert _without(first, "ingested_at", "data_quality") == {
        "model_id": 1,
        "discipline": "ARC",
        **common,
    }
    assert _without(first["data_quality"], "finished_at") == {
        "status": "checked",
        "dq_run_id": 1,
        "errors": 0,
        "warnings": 11,
        "info": 5,
    }
    assert _is_timestamp(first["ingested_at"])
    assert _is_timestamp(first["data_quality"]["finished_at"])
    assert _without(second, "ingested_at", "data_quality") == {
        "model_id": 2,
        "discipline": "MEP",
        **common,
    }
    # Never checked is not "no findings": the counts are null, not 0.
    assert second["data_quality"] == {
        "status": "never_checked",
        "dq_run_id": None,
        "finished_at": None,
        "errors": None,
        "warnings": None,
        "info": None,
    }


async def test_list_models_stops_at_its_limit_and_says_so(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "MAX_MODELS", 1)

    async with connect(server) as client:
        result = await call(client, "list_models")

    assert [model["model_id"] for model in result["models"]] == [1]
    assert result["truncated"] is True


# --------------------------------------------------------------------------- list_storeys


async def test_list_storeys_lists_storeys_lowest_first(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(client, "list_storeys", model_id=1)

    assert result == {
        "model_id": 1,
        "storeys": [
            {
                "storey_id": 5,
                "global_id": "0foundation___________",
                "name": "Foundation",
                "long_name": None,
                "elevation_m": -2.0,
                "elements": 0,
                "spaces": 0,
            },
            {
                "storey_id": 6,
                "global_id": "0level1_______________",
                "name": "Level 1",
                "long_name": None,
                "elevation_m": 0.0,
                "elements": 10,
                "spaces": 1,
            },
            {
                "storey_id": 7,
                "global_id": "0level2_______________",
                "name": "Level 2",
                "long_name": None,
                "elevation_m": 3.0,
                "elements": 3,
                "spaces": 1,
            },
        ],
        "elements_without_storey": 0,
        "truncated": False,
    }


async def test_list_storeys_stops_at_its_limit_and_says_so(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "MAX_STOREYS", 2)

    async with connect(server) as client:
        result = await call(client, "list_storeys", model_id=1)

    assert [storey["name"] for storey in result["storeys"]] == ["Foundation", "Level 1"]
    assert result["truncated"] is True


# --------------------------------------------------------------------------- find_elements

WALL_ROW = {
    "object_id": WALL,
    "global_id": "0wall_stud____________",
    "ifc_class": "IfcWallStandardCase",
    "name": "Basic Wall:Interior - Stud Partition:200001",
    "revit_family": "Basic Wall",
    "revit_type": "Interior - Stud Partition",
    "storey_name": "Level 1",
    "space_number": None,
    "length_m": 3.9999999999999996,
    "area_m2": 10.4,
    "volume_m3": 1.3,
}


async def test_find_elements_returns_a_page_and_the_total(server: MCPServer) -> None:
    async with connect(server) as client:
        first = await call(client, "find_elements", model_id=1, limit=5)
        last = await call(client, "find_elements", model_id=1, limit=5, offset=10)

    assert _without(first, "elements") == {
        "model_id": 1,
        "total": 13,
        "offset": 0,
        "limit": 5,
        "next_offset": 5,
    }
    assert [e["object_id"] for e in first["elements"]] == [1, 3, 4, 8, 9]
    assert _without(last, "elements") == {
        "model_id": 1,
        "total": 13,
        "offset": 10,
        "limit": 5,
        "next_offset": None,
    }
    assert [e["object_id"] for e in last["elements"]] == [18, 19, WALL]
    assert last["elements"][-1] == WALL_ROW


@pytest.mark.parametrize(
    ("filters", "object_ids"),
    [
        pytest.param({"ifc_class": "IfcWall"}, [WALL], id="normalised class"),
        pytest.param({"ifc_class": "ifcwallstandardcase"}, [WALL], id="exact class, any case"),
        pytest.param({"ifc_class": "IfcOpeningElement"}, [8, 9], id="class"),
        pytest.param({"storey_name": "LEVEL 2"}, [10, 11, 12], id="storey"),
        pytest.param({"revit_family": "stair"}, [17, 18, 19], id="family"),
        pytest.param({"revit_type": "225 a"}, [10, 11], id="type"),
        pytest.param({"name_contains": "PANEL"}, [10, 11], id="name"),
        pytest.param({"name_contains": "%"}, [], id="percent is a character, not a wildcard"),
        # As a LIKE wildcard, _ would match every named element; here only an underscore.
        pytest.param(
            {"name_contains": "_"}, [1, 3, 4, 8, 10, 11], id="underscore is a character too"
        ),
        # The door's opening (8) is named after the door but is no door of the family.
        pytest.param(
            {"storey_name": "Level 1", "revit_family": "M_Single-Flush"}, [4], id="two filters"
        ),
        pytest.param({"ifc_class": "IfcWindow"}, [], id="no match"),
    ],
)
async def test_find_elements_filters_case_insensitively_with_every_filter_given(
    server: MCPServer, filters: dict[str, str], object_ids: list[int]
) -> None:
    async with connect(server) as client:
        result = await call(client, "find_elements", model_id=1, **filters)

    assert [e["object_id"] for e in result["elements"]] == object_ids
    assert result["total"] == len(object_ids)


async def test_find_elements_treats_a_filter_value_as_data(server: MCPServer) -> None:
    """A filter is a bind parameter: SQL in it matches nothing and changes nothing."""
    async with connect(server) as client:
        result = await call(client, "find_elements", model_id=1, name_contains="' OR '1'='1")

    assert result["total"] == 0


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": tools.MAX_PAGE_SIZE + 1}, "limit"),
        ({"offset": -1}, "offset"),
        ({"name_contains": ""}, "name_contains"),
        ({"name_contains": "x" * 201}, "name_contains"),
    ],
)
async def test_find_elements_refuses_arguments_outside_their_limits(
    server: MCPServer, arguments: dict[str, Any], field: str
) -> None:
    async with connect(server) as client:
        message = await call_error(client, "find_elements", model_id=1, **arguments)

    assert message.startswith("Error executing tool find_elements: 1 validation error")
    assert f"\n{field}\n" in message


# --------------------------------------------------------------------------- get_element


async def test_get_element_returns_everything_about_an_element(server: MCPServer) -> None:
    async with connect(server) as client:
        element = await call(client, "get_element", object_id=WALL)

    assert _without(
        element, "quantities", "property_sets", "classifications", "materials", "data_quality"
    ) == {
        "object_id": WALL,
        "model_id": 1,
        "discipline": "ARC",
        "global_id": "0wall_stud____________",
        "object_kind": "element",
        "ifc_class": "IfcWallStandardCase",
        "ifc_class_base": "IfcWall",
        "is_feature": False,
        "name": "Basic Wall:Interior - Stud Partition:200001",
        "long_name": None,
        "revit_family": "Basic Wall",
        "revit_type": "Interior - Stud Partition",
        "host_revit_family": None,
        "host_revit_type": None,
        "revit_element_id": 200001,
        "type_name": None,
        "predefined_type": None,
        "tag": "200001",
        "elevation_m": None,
        "storey_id": 6,
        "storey_name": "Level 1",
        "storey_elevation_m": 0.0,
        "space_id": None,
        "space_number": None,
        "space_name": None,
        "parent_id": None,
        "parent_ifc_class": None,
        "parent_name": None,
        "properties_truncated": False,
    }
    assert element["quantities"] == [
        {"name": "area_m2", "value": 10.4, "source": "revit_pset",
         "source_ref": "PSet_Revit_Dimensions.Area"},
        {"name": "length_m", "value": 3.9999999999999996, "source": "revit_pset",
         "source_ref": "PSet_Revit_Dimensions.Length"},
        {"name": "volume_m3", "value": 1.3, "source": "revit_pset",
         "source_ref": "PSet_Revit_Dimensions.Volume"},
    ]  # fmt: skip
    assert {s["name"]: [p["name"] for p in s["properties"]] for s in element["property_sets"]} == {
        "PSet_Revit_Constraints": ["Top Offset"],
        "PSet_Revit_Dimensions": ["Area", "Length", "Volume"],
        "PSet_Revit_Other": ["BarCode", "Manufacturer"],
        "PSet_Revit_Type_Construction": ["Width"],
        "Pset_WallCommon": ["IsExternal", "LoadBearing", "Reference"],
    }
    values = {(s["name"], p["name"]): p for s in element["property_sets"] for p in s["properties"]}
    assert values["PSet_Revit_Constraints", "Top Offset"] == {
        "name": "Top Offset", "value": -0.3, "value_type": "IfcLengthMeasure", "unit": "m",
        "from_type": False,
    }  # fmt: skip
    assert values["PSet_Revit_Other", "BarCode"]["value"] == "BarCode"
    assert values["PSet_Revit_Other", "Manufacturer"]["value"] == ""
    assert values["Pset_WallCommon", "IsExternal"]["value"] is False
    assert values["PSet_Revit_Type_Construction", "Width"]["from_type"] is True
    assert element["classifications"] == []
    assert element["materials"] == [
        {"layer_index": 1, "material": "Plasterboard", "thickness_m": 0.0125},
        {"layer_index": 2, "material": "Metal Stud", "thickness_m": 0.075},
        {"layer_index": 3, "material": "Plasterboard", "thickness_m": 0.0125},
    ]
    assert element["data_quality"] == {
        "status": "checked",
        "dq_run_id": 1,
        "findings": [
            {"rule_id": "value_is_property_name", "severity": "warning",
             "dimension": "validity",
             "message": "PSet_Revit_Other.BarCode = 'BarCode', the parameter's own name: a "
             "placeholder, not a value"},
            {"rule_id": "value_is_empty", "severity": "info", "dimension": "completeness",
             "message": "PSet_Revit_Other.Manufacturer = '': no value"},
            {"rule_id": "value_is_empty", "severity": "info", "dimension": "completeness",
             "message": "Pset_WallCommon.Reference = '': no value"},
        ],
        "truncated": False,
    }  # fmt: skip


async def test_get_element_finds_a_space_by_global_id(server: MCPServer) -> None:
    async with connect(server) as client:
        space = await call(client, "get_element", model_id=1, global_id="0space_kitchen________")

    assert space["object_id"] == SPACE_KITCHEN
    assert (space["object_kind"], space["name"], space["long_name"]) == (
        "spatial_element",
        "F101",
        "Kitchen",
    )
    # A space's storey is the storey it belongs to.
    assert (space["parent_id"], space["parent_ifc_class"], space["parent_name"]) == (
        6,
        "IfcBuildingStorey",
        "Level 1",
    )
    assert (space["storey_id"], space["storey_name"]) == (6, "Level 1")
    # The quantity set wins over the Revit parameter (ADR 3), and says so.
    assert space["quantities"][0] == {
        "name": "area_m2",
        "value": 12.499999999999998,
        "source": "ifc_qto",
        "source_ref": "GSA Space Areas.GSA BIM Area",
    }
    assert space["classifications"] == [
        {"system": "OmniClass Table 13", "code": "13-51 24 17", "title": "Kitchen Space"}
    ]


async def test_get_element_of_a_model_never_checked_has_no_findings_to_report(
    server: MCPServer,
) -> None:
    async with connect(server) as client:
        wall = await call(client, "get_element", model_id=2, global_id="0wall_stud____________")

    assert wall["model_id"] == 2
    assert wall["data_quality"] == {
        "status": "never_checked",
        "dq_run_id": None,
        "findings": [],
        "truncated": False,
    }


async def test_get_element_cuts_long_lists_and_says_so(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "MAX_PROPERTIES", 4)
    monkeypatch.setattr(tools, "MAX_ELEMENT_FINDINGS", 1)

    async with connect(server) as client:
        wall = await call(client, "get_element", object_id=WALL)

    assert sum(len(s["properties"]) for s in wall["property_sets"]) == 4
    assert wall["properties_truncated"] is True
    assert len(wall["data_quality"]["findings"]) == 1
    assert wall["data_quality"]["truncated"] is True


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"object_id": 9999}, "no object with id 9999; find_elements and space_schedule list them"),
        (
            {"model_id": 1, "global_id": "0nothing______________"},
            "model 1 has no object with GlobalId '0nothing______________'",
        ),
        ({"model_id": 99, "global_id": "0wall_stud____________"}, UNKNOWN_MODEL),
        ({}, "give either object_id, or both model_id and global_id"),
        ({"model_id": 1}, "give either object_id, or both model_id and global_id"),
        (
            {"object_id": WALL, "model_id": 1, "global_id": "0wall_stud____________"},
            "give either object_id, or both model_id and global_id",
        ),
    ],
)
async def test_get_element_says_what_is_wrong_with_its_arguments(
    server: MCPServer, arguments: dict[str, Any], message: str
) -> None:
    async with connect(server) as client:
        assert await call_error(client, "get_element", **arguments) == message


# --------------------------------------------------------------------------- quantity_takeoff


async def test_takeoff_by_storey_totals_each_element_once(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(
            client, "quantity_takeoff", model_id=1, group_by="storey", metric="area_m2"
        )

    assert _without(result, "note") == {
        "model_id": 1,
        "group_by": "storey",
        "metric": "area_m2",
        "unit": "m2",
        "basis": "element",
        "include_openings": False,
        "openings_left_out": 2,
        "groups": [
            {"group": "Level 1", "group_id": 6, "elements": 8, "elements_with_value": 1,
             "value": 10.4},
            {"group": "Level 2", "group_id": 7, "elements": 3, "elements_with_value": 0,
             "value": None},
        ],
        "total": 10.4,
        "truncated": False,
    }  # fmt: skip
    assert result["note"] == tools.ELEMENT_NOTE


async def test_takeoff_by_class_counts_elements(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(
            client, "quantity_takeoff", model_id=1, group_by="ifc_class", metric="count"
        )

    assert [(g["group"], g["value"]) for g in result["groups"]] == [
        ("IfcFlowTerminal", 2),
        ("IfcBeam", 1),
        ("IfcBuildingElementProxy", 1),
        ("IfcDoor", 1),
        ("IfcFurnishingElement", 1),
        ("IfcMember", 1),
        ("IfcSlab", 1),
        ("IfcStair", 1),
        ("IfcStairFlight", 1),
        ("IfcWall", 1),
    ]
    # The two openings are left out: they are changes to the wall, not elements.
    assert (result["total"], result["unit"]) == (11, "elements")
    assert (result["include_openings"], result["openings_left_out"]) == (False, 2)


async def test_takeoff_by_revit_type_groups_family_and_type(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(
            client, "quantity_takeoff", model_id=1, group_by="revit_type", metric="count"
        )

    assert [(g["group"], g["value"]) for g in result["groups"]] == [
        ("Stair:Residential - 180mm Riser", 3),
        ("M_Panelboard:225 A", 2),
        ("Basic Wall:Interior - Stud Partition", 1),
        ("Floor:Generic 150mm", 1),
        ("M_Chair-Breuer:Chair-Breuer", 1),
        # The door alone: its opening, named after it, is left out (audit finding A3).
        ("M_Single-Flush:0915 x 2134mm", 1),
        ("M_Timber:200x400mm", 1),
        # The generic model has no Revit type.
        (None, 1),
    ]


async def test_takeoff_counts_openings_under_their_host_only_when_asked(
    server: MCPServer,
) -> None:
    async with connect(server) as client:
        result = await call(
            client,
            "quantity_takeoff",
            model_id=1,
            group_by="revit_type",
            metric="count",
            include_openings=True,
        )

    groups = {g["group"]: g["value"] for g in result["groups"]}
    # Openings have no revit_type of their own: they count in the null group, never
    # under the door they are named after.
    assert (groups["M_Single-Flush:0915 x 2134mm"], groups[None]) == (1, 3)
    assert (result["total"], result["include_openings"], result["openings_left_out"]) == (
        13,
        True,
        0,
    )


async def test_takeoff_by_material_splits_layers_so_it_is_not_an_element_total(
    server: MCPServer,
) -> None:
    """One 10.4 m2 stud wall: plasterboard on both faces is 20.8 m2, the stud 10.4 m2."""
    async with connect(server) as client:
        by_material = await call(
            client, "quantity_takeoff", model_id=1, group_by="material", metric="area_m2"
        )
        by_class = await call(
            client, "quantity_takeoff", model_id=1, group_by="ifc_class", metric="area_m2"
        )

    assert (by_material["basis"], by_material["note"]) == ("material", tools.MATERIAL_NOTE)
    assert by_material["groups"][:2] == [
        {"group": "Plasterboard", "group_id": 4, "elements": 1, "elements_with_value": None,
         "value": 20.8},
        {"group": "Metal Stud", "group_id": 2, "elements": 1, "elements_with_value": None,
         "value": 10.4},
    ]  # fmt: skip
    assert by_material["total"] == pytest.approx(31.2)
    assert by_class["total"] == 10.4


async def test_takeoff_by_material_counts_elements_per_material(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(
            client, "quantity_takeoff", model_id=1, group_by="material", metric="count"
        )

    assert [(g["group"], g["group_id"], g["value"]) for g in result["groups"]] == [
        (None, None, 6),
        ("Paint", 3, 2),
        ("Steel", 5, 2),
        ("Concrete", 1, 1),
        ("Metal Stud", 2, 1),
        ("Plasterboard", 4, 1),
        ("Timber", 6, 1),
    ]
    # 11 elements, but the wall counts under two materials and a panelboard under two.
    assert result["total"] == 14


@pytest.mark.parametrize("group_by", ["storey", "ifc_class", "revit_type"])
@pytest.mark.parametrize("metric", ["count", "length_m", "area_m2", "volume_m3"])
async def test_element_takeoffs_add_up_to_the_elements_own_quantities(
    server: MCPServer, mcp_engine: Engine, group_by: str, metric: str
) -> None:
    """Every element counted once: the groups' total is the plain total over v_element."""
    expression = "count(*)" if metric == "count" else f"coalesce(sum({metric}), 0)"
    with mcp_engine.connect() as connection:
        expected = connection.execute(
            text(f"SELECT {expression} FROM ifc.v_element WHERE model_id = 1 AND NOT is_feature")  # noqa: S608
        ).scalar_one()

    async with connect(server) as client:
        result = await call(
            client, "quantity_takeoff", model_id=1, group_by=group_by, metric=metric
        )

    assert result["basis"] == "element"
    assert result["unit"] == tools.METRIC_UNITS[metric]  # type: ignore[index]
    assert sum(g["elements"] for g in result["groups"]) == 11
    assert result["total"] == pytest.approx(expected)


async def test_takeoff_stops_at_its_limit_and_says_so(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "MAX_GROUPS", 2)

    async with connect(server) as client:
        result = await call(
            client, "quantity_takeoff", model_id=1, group_by="ifc_class", metric="count"
        )

    assert len(result["groups"]) == 2
    assert result["truncated"] is True
    assert result["total"] == 3


@pytest.mark.parametrize(
    ("arguments", "field", "allowed"),
    [
        (
            {"group_by": "floor", "metric": "count"},
            "group_by",
            "Input should be 'storey', 'ifc_class', 'material' or 'revit_type'",
        ),
        (
            {"group_by": "storey", "metric": "weight_kg"},
            "metric",
            "Input should be 'count', 'length_m', 'area_m2' or 'volume_m3'",
        ),
        (
            {"group_by": "storey; DROP TABLE ifc.model", "metric": "count"},
            "group_by",
            "Input should be 'storey', 'ifc_class', 'material' or 'revit_type'",
        ),
    ],
)
async def test_takeoff_refuses_a_grouping_or_metric_it_does_not_know(
    server: MCPServer, arguments: dict[str, str], field: str, allowed: str
) -> None:
    async with connect(server) as client:
        message = await call_error(client, "quantity_takeoff", model_id=1, **arguments)

    assert f"\n{field}\n  {allowed}" in message


# --------------------------------------------------------------------------- space_schedule


async def test_space_schedule_lists_the_rooms(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(client, "space_schedule", model_id=1)

    assert result == {
        "model_id": 1,
        "spaces": [
            {
                "object_id": SPACE_KITCHEN,
                "global_id": "0space_kitchen________",
                "number": "F101",
                "name": "Kitchen",
                "storey_name": "Level 1",
                "area_m2": 12.499999999999998,
                "perimeter_m": 14.5,
                "volume_m3": 32.5,
                "height_m": 2.6,
                "omniclass_code": "13-51 24 17",
                "omniclass_title": "Kitchen Space",
            },
            {
                "object_id": 16,
                "global_id": "0space_store__________",
                "number": "F201",
                "name": "Store",
                "storey_name": "Level 2",
                "area_m2": None,
                "perimeter_m": None,
                "volume_m3": None,
                "height_m": None,
                "omniclass_code": None,
                "omniclass_title": None,
            },
        ],
        "space_count": 2,
        "total_area_m2": 12.499999999999998,
        "spaces_without_area": 1,
        "truncated": False,
    }


async def test_space_schedule_stops_at_its_limit_and_says_so(
    server: MCPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "MAX_SPACES", 1)

    async with connect(server) as client:
        result = await call(client, "space_schedule", model_id=1)

    assert [s["number"] for s in result["spaces"]] == ["F101"]
    assert (result["space_count"], result["truncated"]) == (1, True)


# --------------------------------------------------------------------------- data_quality_report

FIXTURE_RULES = [
    ("conflicting_property", "warning", "consistency", 2),
    ("quantity_not_a_measure", "warning", "validity", 2),
    ("value_is_property_name", "warning", "validity", 2),
    ("incomplete_classification_reference", "warning", "completeness", 1),
    ("negative_quantity", "warning", "validity", 1),
    ("omniclass_unparsed", "warning", "validity", 1),
    ("revit_name_unparsed", "warning", "validity", 1),
    ("space_without_area", "warning", "completeness", 1),
    ("value_is_empty", "info", "completeness", 3),
    ("value_is_not_applicable", "info", "completeness", 2),
]


async def test_report_on_a_checked_model_counts_every_rule(
    server: MCPServer, mcp_engine: Engine
) -> None:
    async with connect(server) as client:
        report = await call(client, "data_quality_report", model_id=1)

    assert (report["status"], report["dq_run_id"]) == ("checked", 1)
    assert _is_timestamp(report["finished_at"])
    assert report["findings"] == {"error": 0, "warning": 11, "info": 5}
    assert [
        (r["rule_id"], r["severity"], r["dimension"], r["findings"]) for r in report["rules"]
    ] == FIXTURE_RULES
    assert sum(len(r["samples"]) for r in report["rules"]) == 16  # all of them, at 3 per rule
    with mcp_engine.connect() as connection:
        descriptions = dict(
            connection.execute(text("SELECT rule_id, description FROM ifc.dq_rule")).all()
        )
    assert all(r["description"] == descriptions[r["rule_id"]] for r in report["rules"])
    empty = next(r for r in report["rules"] if r["rule_id"] == "value_is_empty")
    assert empty["samples"] == [
        {"object_id": SPACE_KITCHEN, "global_id": "0space_kitchen________",
         "ifc_class": "IfcSpace", "object_name": "F101",
         "message": "Pset_SpaceCommon.Reference = '': no value"},
        {"object_id": WALL, "global_id": "0wall_stud____________",
         "ifc_class": "IfcWallStandardCase",
         "object_name": "Basic Wall:Interior - Stud Partition:200001",
         "message": "PSet_Revit_Other.Manufacturer = '': no value"},
        {"object_id": WALL, "global_id": "0wall_stud____________",
         "ifc_class": "IfcWallStandardCase",
         "object_name": "Basic Wall:Interior - Stud Partition:200001",
         "message": "Pset_WallCommon.Reference = '': no value"},
    ]  # fmt: skip
    # The other 21 of the 31 rules found nothing.
    assert len(report["rules_without_findings"]) == 21
    assert "element_without_storey" in report["rules_without_findings"]


async def test_report_on_a_model_never_checked_says_so_instead_of_reporting_nothing(
    server: MCPServer,
) -> None:
    async with connect(server) as client:
        report = await call(client, "data_quality_report", model_id=2)

    assert report == {
        "model_id": 2,
        "status": "never_checked",
        "dq_run_id": None,
        "finished_at": None,
        "findings": None,
        "rules": [],
        "rules_without_findings": [],
    }


async def test_report_filters_by_severity_and_rule(server: MCPServer) -> None:
    async with connect(server) as client:
        info = await call(client, "data_quality_report", model_id=1, severity="info")
        one_rule = await call(
            client, "data_quality_report", model_id=1, rule_id="value_is_empty", samples_per_rule=1
        )
        counts_only = await call(client, "data_quality_report", model_id=1, samples_per_rule=0)

    assert [r["rule_id"] for r in info["rules"]] == ["value_is_empty", "value_is_not_applicable"]
    # The severity counts always cover the whole run.
    assert info["findings"] == {"error": 0, "warning": 11, "info": 5}
    [rule] = one_rule["rules"]
    assert (rule["rule_id"], rule["findings"], len(rule["samples"])) == ("value_is_empty", 3, 1)
    assert all(r["samples"] == [] for r in counts_only["rules"])


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"model_id": 99}, UNKNOWN_MODEL),
        (
            {"model_id": 1, "rule_id": "no_such_rule"},
            "no rule 'no_such_rule'; the rules are in the ifc.dq_rule table, and this report "
            "without rule_id lists those that found something",
        ),
    ],
)
async def test_report_says_what_is_wrong_with_its_arguments(
    server: MCPServer, arguments: dict[str, Any], message: str
) -> None:
    async with connect(server) as client:
        assert await call_error(client, "data_quality_report", **arguments) == message


async def test_report_refuses_a_severity_it_does_not_know(server: MCPServer) -> None:
    async with connect(server) as client:
        message = await call_error(client, "data_quality_report", model_id=1, severity="fatal")

    assert "\nseverity\n  Input should be 'error', 'warning' or 'info'" in message


# --------------------------------------------------------------------------- every per-model tool


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_storeys", {}),
        ("find_elements", {}),
        ("quantity_takeoff", {"group_by": "storey", "metric": "count"}),
        ("space_schedule", {}),
        ("data_quality_report", {}),
    ],
)
async def test_every_per_model_tool_refuses_an_unknown_model(
    server: MCPServer, tool: str, arguments: dict[str, Any]
) -> None:
    async with connect(server) as client:
        assert await call_error(client, tool, model_id=99, **arguments) == UNKNOWN_MODEL
        message = await call_error(client, tool, model_id=0, **arguments)

    assert "\nmodel_id\n  Input should be greater than or equal to 1" in message


@pytest.mark.parametrize(
    ("group_by", "metric"),
    [("storey", "1; DROP TABLE ifc.model"), ("storey; DROP TABLE ifc.model", "count")],
)
def test_takeoff_looks_up_group_by_and_metric_before_it_builds_any_sql(
    mcp_engine: Engine, group_by: str, metric: str
) -> None:
    """The schema stops these at the protocol; the lookups stop them for a Python caller."""
    with mcp_engine.connect() as connection, pytest.raises(KeyError):
        tools.quantity_takeoff(connection, 1, group_by, metric)  # type: ignore[arg-type]
