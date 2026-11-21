"""An unmeasured total is unknown, not zero (audit finding A8).

The audit's Revit 2022 tower has no quantities at all, and quantity_takeoff answered
``"total": 0`` next to groups whose values were all null. A model reading that JSON, or
a person scanning it, takes 0 for zero square metres. These tests build the cases row
by row: no quantities in the model, quantities but not of the metric asked, some
elements measured, and spaces without an area.
"""

import pytest
from rows import add_element, add_model, add_quantity, add_spatial
from sqlalchemy import Connection

from ifc_sql_mcp.server import tools


def _walls(conn: Connection, count: int) -> tuple[int, list[int]]:
    model_id = add_model(conn)
    storey = add_spatial(conn, model_id, "IfcBuildingStorey", "Level 1")
    walls = [
        add_element(conn, model_id, "IfcWall", f"Basic Wall:Generic:{n}", storey_id=storey)
        for n in range(1, count + 1)
    ]
    return model_id, walls


@pytest.mark.parametrize("group_by", ["storey", "ifc_class", "revit_type", "material"])
def test_a_model_without_quantities_has_no_total_and_says_why(
    conn: Connection, group_by: tools.GroupBy
) -> None:
    model_id, _ = _walls(conn, 3)

    takeoff = tools.quantity_takeoff(conn, model_id, group_by, "area_m2")

    assert takeoff.total is None
    assert takeoff.coverage == tools.Coverage(
        elements=3, elements_with_value=0, summary="0 of 3 elements have area_m2"
    )
    assert takeoff.note.startswith("WARNING: no element in this takeoff has area_m2")
    assert "Export base quantities" in takeoff.note


def test_quantities_of_another_kind_point_at_get_element(conn: Connection) -> None:
    model_id, walls = _walls(conn, 2)
    add_quantity(conn, walls[0], "length_m", 4.0)

    takeoff = tools.quantity_takeoff(conn, model_id, "ifc_class", "area_m2")

    assert takeoff.total is None
    assert takeoff.note.startswith("WARNING: no element in this takeoff has area_m2")
    assert "get_element" in takeoff.note
    assert "Export base quantities" not in takeoff.note


def test_a_partly_measured_total_counts_what_was_measured_and_says_so(conn: Connection) -> None:
    model_id, walls = _walls(conn, 4)
    add_quantity(conn, walls[0], "area_m2", 10.0)
    add_quantity(conn, walls[1], "area_m2", 2.5)

    takeoff = tools.quantity_takeoff(conn, model_id, "ifc_class", "area_m2")

    assert takeoff.total == 12.5
    assert takeoff.coverage.summary == "2 of 4 elements have area_m2"
    assert takeoff.note.startswith("Only 2 of 4 elements have area_m2: the total covers those")


def test_a_fully_measured_total_carries_no_warning(conn: Connection) -> None:
    model_id, walls = _walls(conn, 1)
    add_quantity(conn, walls[0], "area_m2", 10.0)

    takeoff = tools.quantity_takeoff(conn, model_id, "storey", "area_m2")

    assert takeoff.total == 10.0
    assert takeoff.coverage.summary == "1 of 1 element has area_m2"
    assert takeoff.note == tools.ELEMENT_NOTE


def test_a_count_is_always_complete(conn: Connection) -> None:
    model_id, _ = _walls(conn, 2)

    takeoff = tools.quantity_takeoff(conn, model_id, "ifc_class", "count")

    assert (takeoff.total, takeoff.coverage.summary) == (2, "2 of 2 elements have elements")
    assert takeoff.note == tools.ELEMENT_NOTE


def test_openings_left_out_are_not_in_the_coverage(conn: Connection) -> None:
    model_id, walls = _walls(conn, 1)
    add_element(conn, model_id, "IfcOpeningElement", is_feature=True)
    add_quantity(conn, walls[0], "area_m2", 10.0)

    without = tools.quantity_takeoff(conn, model_id, "ifc_class", "area_m2")
    including = tools.quantity_takeoff(
        conn, model_id, "ifc_class", "area_m2", include_openings=True
    )

    assert without.coverage.summary == "1 of 1 element has area_m2"
    assert including.coverage.summary == "1 of 2 elements have area_m2"


def test_spaces_without_any_area_have_no_total(conn: Connection) -> None:
    model_id = add_model(conn)
    for number in ("101", "102"):
        add_spatial(conn, model_id, "IfcSpace", number)

    schedule = tools.space_schedule(conn, model_id)

    assert (schedule.total_area_m2, schedule.spaces_without_area) == (None, 2)
    assert schedule.note is not None
    assert schedule.note.startswith("WARNING: none of the 2 spaces has an area")


def test_spaces_all_measured_have_a_total_and_no_note(conn: Connection) -> None:
    model_id = add_model(conn)
    space = add_spatial(conn, model_id, "IfcSpace", "101")
    add_quantity(conn, space, "area_m2", 14.5)

    schedule = tools.space_schedule(conn, model_id)

    assert (schedule.total_area_m2, schedule.spaces_without_area, schedule.note) == (
        14.5,
        0,
        None,
    )
