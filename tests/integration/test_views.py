"""The views compute what their comments promise, on a tiny building built in each test.

The building mirrors situations found in the Duplex sample files: a space with both a
GSA quantity set and a Revit area, a three-layer stud wall with plasterboard on both
faces, a chair contained in a space instead of a storey, and a beam whose single
material has no layers.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from rows import (
    add_classification,
    add_dq_run,
    add_element,
    add_layer,
    add_material,
    add_model,
    add_property,
    add_quantity,
    add_spatial,
)
from sqlalchemy import Connection, RowMapping, func, insert, text

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.dq.catalogue import load_catalogue
from ifc_sql_mcp.dq.run import evaluate
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.server import tools


@dataclass(frozen=True)
class Building:
    model_id: int
    level_1: int
    level_2: int
    kitchen: int
    wall: int
    chair: int
    beam: int


@pytest.fixture
def building(conn: Connection) -> Building:
    model_id = add_model(conn, project_name="0001", project_long_name="Duplex Apartment")
    site = add_spatial(conn, model_id, "IfcSite", "Default")
    house = add_spatial(conn, model_id, "IfcBuilding", parent_id=site)
    level_1 = add_spatial(
        conn, model_id, "IfcBuildingStorey", "Level 1", parent_id=house, elevation_m=0
    )
    level_2 = add_spatial(
        conn, model_id, "IfcBuildingStorey", "Level 2", parent_id=house, elevation_m=3.1
    )

    kitchen = add_spatial(
        conn, model_id, "IfcSpace", "A103", parent_id=level_1, long_name="Kitchen"
    )
    add_quantity(conn, kitchen, "area_m2", 13.9, "revit_pset", "PSet_Revit_Dimensions.Area")
    add_quantity(conn, kitchen, "area_m2", 14.0, "ifc_qto", "GSA Space Areas.GSA BIM Area")
    add_quantity(
        conn, kitchen, "perimeter_m", 15.2, "revit_pset", "PSet_Revit_Dimensions.Perimeter"
    )
    add_quantity(conn, kitchen, "volume_m3", 36.1, "revit_pset", "PSet_Revit_Dimensions.Volume")
    add_classification(
        conn, kitchen, "OmniClass Table 13", "13-51 24 11", "General Residential Space"
    )

    wall = add_element(
        conn,
        model_id,
        "IfcWallStandardCase",
        "Basic Wall:Interior - Partition (92mm Stud):138584",
        ifc_class_base="IfcWall",
        storey_id=level_1,
        container_id=level_1,
        revit_family="Basic Wall",
        revit_type="Interior - Partition (92mm Stud)",
        revit_element_id=138584,
        tag="138584",
    )
    add_quantity(conn, wall, "length_m", 3.8)
    add_quantity(conn, wall, "area_m2", 10.0)
    add_quantity(conn, wall, "volume_m3", 1.24)
    add_property(
        conn, wall, "Pset_WallCommon", "IsExternal", value_type="IfcBoolean", value_bool=False
    )
    plasterboard = add_material(conn, model_id, "Plasterboard")
    stud = add_material(conn, model_id, "Metal - Stud Layer")
    add_layer(conn, wall, 1, plasterboard, 0.016)
    add_layer(conn, wall, 2, stud, 0.092)
    add_layer(conn, wall, 3, plasterboard, 0.016)

    chair = add_element(
        conn,
        model_id,
        "IfcFurnishingElement",
        "Chair:Chair:200001",
        storey_id=level_1,
        container_id=kitchen,
    )

    beam = add_element(
        conn,
        model_id,
        "IfcBeam",
        "M_W-Wide Flange:W310X60:208816",
        storey_id=level_2,
        container_id=level_2,
    )
    add_quantity(conn, beam, "length_m", 5.0)
    add_quantity(conn, beam, "volume_m3", 0.04)
    add_layer(conn, beam, 1, add_material(conn, model_id, "Metal - Steel - 345 MPa"))

    return Building(model_id, level_1, level_2, kitchen, wall, chair, beam)


def _rows(conn: Connection, sql: str, **params: object) -> list[RowMapping]:
    return list(conn.execute(text(sql), params).mappings())


@pytest.mark.parametrize(
    ("sources", "chosen"),
    [
        (("geometry", "ifc_attribute", "revit_pset", "ifc_qto"), "ifc_qto"),
        (("geometry", "ifc_attribute", "revit_pset"), "revit_pset"),
        (("geometry", "ifc_attribute"), "ifc_attribute"),
        (("geometry",), "geometry"),
    ],
)
def test_v_quantity_ranks_the_four_sources(
    conn: Connection, building: Building, sources: tuple[str, ...], chosen: str
) -> None:
    door = add_element(conn, building.model_id, "IfcDoor", "M_Single-Flush:0915 x 2134mm:1")
    for rank, source in enumerate(sources):
        add_quantity(conn, door, "width_m", 0.9 + rank / 100, source=source, source_ref=source)

    [row] = _rows(conn, "SELECT source FROM ifc.v_quantity WHERE object_id = :id", id=door)

    assert row["source"] == chosen


def test_v_quantity_prefers_ifc_quantity_sets_over_revit_parameters(
    conn: Connection, building: Building
) -> None:
    rows = _rows(
        conn,
        "SELECT name, value, source, source_ref FROM ifc.v_quantity WHERE object_id = :id "
        "ORDER BY name",
        id=building.kitchen,
    )

    assert [dict(row) for row in rows] == [
        {
            "name": "area_m2",
            "value": 14.0,
            "source": "ifc_qto",
            "source_ref": "GSA Space Areas.GSA BIM Area",
        },
        {
            "name": "perimeter_m",
            "value": 15.2,
            "source": "revit_pset",
            "source_ref": "PSet_Revit_Dimensions.Perimeter",
        },
        {
            "name": "volume_m3",
            "value": 36.1,
            "source": "revit_pset",
            "source_ref": "PSet_Revit_Dimensions.Volume",
        },
    ]


def test_v_element_flattens_identity_location_quantities_and_materials(
    conn: Connection, building: Building
) -> None:
    (wall,) = _rows(conn, "SELECT * FROM ifc.v_element WHERE object_id = :id", id=building.wall)

    assert wall["ifc_class"] == "IfcWallStandardCase"
    assert wall["ifc_class_base"] == "IfcWall"
    assert wall["discipline"] == "ARC"
    assert (wall["revit_family"], wall["revit_type"], wall["revit_element_id"]) == (
        "Basic Wall",
        "Interior - Partition (92mm Stud)",
        138584,
    )
    assert (wall["storey_name"], wall["storey_elevation_m"]) == ("Level 1", Decimal("0.000"))
    assert wall["space_id"] is None
    assert (wall["length_m"], wall["area_m2"], wall["volume_m3"]) == (3.8, 10.0, 1.24)
    assert wall["perimeter_m"] is None
    assert wall["materials"] == "Plasterboard / Metal - Stud Layer / Plasterboard"


def test_v_element_names_the_room_of_an_element_contained_in_a_space(
    conn: Connection, building: Building
) -> None:
    (chair,) = _rows(conn, "SELECT * FROM ifc.v_element WHERE object_id = :id", id=building.chair)

    assert (chair["space_number"], chair["space_name"]) == ("A103", "Kitchen")
    assert chair["storey_name"] == "Level 1"
    assert chair["materials"] is None


def test_v_element_has_one_row_per_element(conn: Connection, building: Building) -> None:
    rows = _rows(
        conn, "SELECT object_id FROM ifc.v_element WHERE model_id = :id", id=building.model_id
    )

    assert sorted(row["object_id"] for row in rows) == sorted(
        [building.wall, building.chair, building.beam]
    )


def test_v_space_schedule(conn: Connection, building: Building) -> None:
    (kitchen,) = _rows(
        conn, "SELECT * FROM ifc.v_space_schedule WHERE model_id = :id", id=building.model_id
    )

    row = dict(kitchen)
    assert row.pop("object_id") == building.kitchen
    assert len(row.pop("global_id")) == 22
    assert row == {
        "model_id": building.model_id,
        "discipline": "ARC",
        "number": "A103",
        "name": "Kitchen",
        "storey_id": building.level_1,
        "storey_name": "Level 1",
        "area_m2": 14.0,
        "perimeter_m": 15.2,
        "volume_m3": 36.1,
        "height_m": None,
        "omniclass_code": "13-51 24 11",
        "omniclass_title": "General Residential Space",
        "duplicate_of": None,
    }


def test_a_room_exported_twice_is_marked_and_counted_once(
    conn: Connection, building: Building
) -> None:
    """Revit Rooms and MEP Spaces exported together: one room, two IfcSpaces (A7)."""
    storey = building.level_1
    room = add_spatial(
        conn, building.model_id, "IfcSpace", "49", long_name="Corridor", parent_id=storey
    )
    mep_space = add_spatial(
        conn, building.model_id, "IfcSpace", "49", long_name="Corridor", parent_id=storey
    )
    other_name = add_spatial(
        conn, building.model_id, "IfcSpace", "49", long_name="Corridor B", parent_id=storey
    )
    # The same number and name but another area: a different space that reuses a number.
    other_area = add_spatial(
        conn, building.model_id, "IfcSpace", "49", long_name="Corridor", parent_id=storey
    )
    for space in (room, mep_space, other_name):
        add_quantity(conn, space, "area_m2", 474.3)
    add_quantity(conn, other_area, "area_m2", 12.0)
    sync_rules(conn, load_catalogue())
    run_id = add_dq_run(conn, building.model_id)
    evaluate(
        conn,
        run_id,
        building.model_id,
        [r for r in load_catalogue() if r.rule_id == "duplicate_space"],
    )

    marks = dict(
        conn.execute(
            text("SELECT object_id, duplicate_of FROM ifc.v_space_schedule WHERE number = '49'")
        ).all()
    )
    schedule = tools.space_schedule(conn, building.model_id)
    [finding] = conn.execute(
        text("SELECT object_id, message FROM ifc.dq_result WHERE dq_run_id = :r"), {"r": run_id}
    ).all()

    assert marks == {room: None, mep_space: room, other_name: None, other_area: None}
    assert (schedule.duplicate_spaces, schedule.total_area_m2) == (
        1,
        pytest.approx(14.0 + 3 * 474.3 + 12.0),
    )
    assert schedule.total_area_without_duplicates_m2 == pytest.approx(14.0 + 2 * 474.3 + 12.0)
    assert schedule.note is not None
    assert "total_area_m2 counts those rooms twice" in schedule.note
    assert finding.object_id == mep_space
    assert finding.message.startswith("space 49 'Corridor' on Level 1 repeats space ")


def test_v_takeoff_splits_layered_elements_by_material(
    conn: Connection, building: Building
) -> None:
    rows = _rows(
        conn,
        "SELECT storey_name, ifc_class_base, material_name, element_count, "
        "length_m, area_m2, volume_m3 FROM ifc.v_takeoff WHERE model_id = :id "
        "ORDER BY storey_name, ifc_class_base, material_name NULLS FIRST",
        id=building.model_id,
    )
    table = {(r["storey_name"], r["ifc_class_base"], r["material_name"]): r for r in rows}

    assert sorted(table, key=str) == sorted(
        [
            ("Level 1", "IfcFurnishingElement", None),
            ("Level 1", "IfcWall", "Metal - Stud Layer"),
            ("Level 1", "IfcWall", "Plasterboard"),
            ("Level 2", "IfcBeam", "Metal - Steel - 345 MPa"),
        ],
        key=str,
    )
    # Plasterboard covers both faces: twice the 10 m2 wall area, 2 x 16 mm thick.
    plasterboard = table["Level 1", "IfcWall", "Plasterboard"]
    assert plasterboard["element_count"] == 1
    assert plasterboard["area_m2"] == pytest.approx(20.0)
    assert plasterboard["volume_m3"] == pytest.approx(10.0 * 0.032)
    stud = table["Level 1", "IfcWall", "Metal - Stud Layer"]
    assert (stud["area_m2"], stud["volume_m3"]) == (pytest.approx(10.0), pytest.approx(0.92))
    # A material without layers takes the element's own quantities.
    steel = table["Level 2", "IfcBeam", "Metal - Steel - 345 MPa"]
    assert (steel["length_m"], steel["volume_m3"]) == (5.0, 0.04)
    # No material at all still counts the element.
    chairs = table["Level 1", "IfcFurnishingElement", None]
    assert (chairs["element_count"], chairs["area_m2"]) == (1, None)


def test_v_model_summary_counts_and_latest_dq_run(conn: Connection, building: Building) -> None:
    summary_sql = "SELECT * FROM ifc.v_model_summary WHERE model_id = :id"

    (before,) = _rows(conn, summary_sql, id=building.model_id)
    assert (before["storey_count"], before["space_count"], before["element_count"]) == (2, 1, 3)
    assert (before["property_count"], before["quantity_count"], before["material_count"]) == (
        1,
        9,
        3,
    )
    assert before["project_long_name"] == "Duplex Apartment"
    # Never checked is NULL, not "no errors".
    assert (before["dq_run_id"], before["dq_error_count"]) == (None, None)

    conn.execute(
        insert(schema.dq_rule).values(
            rule_id="placeholder_value",
            description="Value equals the property name.",
            severity="warning",
            dimension="validity",
        )
    )
    older = _add_dq_run(conn, building.model_id, "succeeded", minutes_ago=60)
    latest = _add_dq_run(conn, building.model_id, "succeeded", minutes_ago=30)
    _add_dq_run(conn, building.model_id, "failed", minutes_ago=5)
    for run_id, severity in [(older, "error"), (latest, "error"), (latest, "warning")]:
        conn.execute(
            insert(schema.dq_result).values(
                dq_run_id=run_id,
                rule_id="placeholder_value",
                object_id=building.wall,
                severity=severity,
                message="Manufacturer = 'Manufacturer'",
            )
        )

    (after,) = _rows(conn, summary_sql, id=building.model_id)
    assert after["dq_run_id"] == latest
    assert (after["dq_error_count"], after["dq_warning_count"], after["dq_info_count"]) == (
        1,
        1,
        0,
    )


def _add_dq_run(conn: Connection, model_id: int, status: str, *, minutes_ago: int) -> int:
    finished = func.now() - func.make_interval(0, 0, 0, 0, 0, minutes_ago)
    run_id = conn.execute(
        insert(schema.dq_run)
        .values(
            model_id=model_id,
            started_at=finished,
            finished_at=finished,
            status=status,
            error="boom" if status == "failed" else None,
        )
        .returning(schema.dq_run.c.dq_run_id)
    ).scalar_one()
    return int(run_id)
