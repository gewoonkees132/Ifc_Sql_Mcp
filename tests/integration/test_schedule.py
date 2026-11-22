"""Schedules that fit in a conversation: the schedule tool and a paged space_schedule (A11).

A door schedule built from find_elements and get_element meant paging every door,
about 490,000 characters on the audit's tower; space_schedule returned up to 500 spaces
at once, about 112,000. These tests build the cases row by row: a schedule grouped by
type and by storey, numbers that differ only in Revit's float noise, a property no
element has, a schedule too large to return whole, and the room schedule's pages.
"""

import pytest
from rows import add_element, add_model, add_property, add_quantity, add_spatial
from sqlalchemy import Connection

from ifc_sql_mcp.server import tools

DOOR = "M_Single-Flush:0915 x 2134mm"
WIDE = "M_Double-Flush:1830 x 2134mm"


def _doors(conn: Connection) -> int:
    """Five doors on two storeys: three of one type, two of another, one unrated."""
    model_id = add_model(conn)
    ground = add_spatial(conn, model_id, "IfcBuildingStorey", "Level 1", elevation_m=0.0)
    first = add_spatial(conn, model_id, "IfcBuildingStorey", "Level 2", elevation_m=3.0)
    doors = [
        (DOOR, ground, 0.915000000000002, "60 min"),
        (DOOR, ground, 0.915000000000012, "60 min"),
        (DOOR, first, 0.915, None),
        (WIDE, first, 1.83, "30 min"),
        (WIDE, first, 1.83, "30 min"),
    ]
    for n, (name, storey, width, rating) in enumerate(doors):
        family, type_ = name.split(":")
        door = add_element(
            conn,
            model_id,
            "IfcDoor",
            f"{name}:{n}",
            storey_id=storey,
            revit_family=family,
            revit_type=type_,
        )
        add_quantity(
            conn, door, "width_m", width, source="ifc_attribute", source_ref="IfcDoor.OverallWidth"
        )
        if rating is not None:
            add_property(
                conn,
                door,
                "Pset_DoorCommon",
                "FireRating",
                value_type="IfcLabel",
                value_text=rating,
            )
        add_property(
            conn, door, "Pset_DoorCommon", "IsExternal", value_type="IfcBoolean", value_bool=n == 0
        )
    # A wall on the same storey is not in a door schedule.
    add_element(conn, model_id, "IfcWall", "Basic Wall:Generic:9", storey_id=ground)
    return model_id


def test_a_door_schedule_by_type_counts_and_lists_values(conn: Connection) -> None:
    model_id = _doors(conn)

    result = tools.schedule(
        conn, model_id, "IfcDoor", ["revit_type"], ["width_m", "Pset_DoorCommon.FireRating"]
    )

    assert (result.elements, result.group_count, result.truncated, result.note) == (
        5,
        2,
        False,
        None,
    )
    single, double = result.groups
    assert (single.group, single.elements, double.group, double.elements) == (
        [DOOR],
        3,
        [WIDE],
        2,
    )
    width = single.fields["width_m"]
    # Revit's float noise does not make three 0.915 m doors three widths; min and max stay exact.
    assert [(v.value, v.elements) for v in width.values] == [(0.915, 3)]
    assert (width.elements_with_value, width.distinct_values, width.unit) == (3, 1, "m")
    assert (width.min, width.max) == (0.915, 0.915000000000012)
    assert width.sum == pytest.approx(2.745)
    rating = single.fields["Pset_DoorCommon.FireRating"]
    assert (rating.elements_with_value, [(v.value, v.elements) for v in rating.values]) == (
        2,
        [("60 min", 2)],
    )
    assert (rating.unit, rating.min, rating.max, rating.sum) == (None, None, None, None)


def test_grouping_by_storey_goes_up_the_building_and_matches_any_case(conn: Connection) -> None:
    model_id = _doors(conn)

    result = tools.schedule(
        conn, model_id, "ifcdoor", ["storey", "revit_type"], ["pset_doorcommon.isexternal"]
    )

    assert [(row.group, row.elements) for row in result.groups] == [
        (["Level 1", DOOR], 2),
        (["Level 2", WIDE], 2),
        (["Level 2", DOOR], 1),
    ]
    external = result.groups[0].fields["pset_doorcommon.isexternal"]
    assert [(v.value, v.elements) for v in external.values] == [(False, 1), (True, 1)]


def test_one_storey_only(conn: Connection) -> None:
    model_id = _doors(conn)

    result = tools.schedule(
        conn, model_id, "IfcDoor", ["ifc_class"], ["width_m"], storey_name="level 2"
    )

    [row] = result.groups
    assert (row.group, row.elements, result.storey_name) == (["IfcDoor"], 3, "level 2")
    assert [(v.value, v.elements) for v in row.fields["width_m"].values] == [(1.83, 2), (0.915, 1)]


def test_a_field_no_element_has_is_empty_and_the_note_names_what_exists(conn: Connection) -> None:
    model_id = _doors(conn)

    result = tools.schedule(
        conn, model_id, "IfcDoor", ["revit_type"], ["Pset_DoorCommon.FireRatng", "area_m2"]
    )

    assert all(
        row.fields["Pset_DoorCommon.FireRatng"] == row.fields["area_m2"] == tools._EMPTY_FIELD
        for row in result.groups
    )
    assert result.note == (
        "No element in this schedule has Pset_DoorCommon.FireRatng. The closest these "
        "elements have: Pset_DoorCommon.FireRating, Pset_DoorCommon.IsExternal. No element in "
        "this schedule has area_m2."
    )


def test_a_class_with_no_element_says_where_to_look(conn: Connection) -> None:
    model_id = _doors(conn)

    result = tools.schedule(conn, model_id, "IfcWindow", ["revit_type"], ["width_m"])

    assert (result.elements, result.groups, result.group_count) == (0, [], 0)
    assert result.note is not None
    assert result.note.startswith("No element of class IfcWindow matches")


def test_a_schedule_too_large_is_cut_and_says_how_to_narrow_it(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_id = _doors(conn)
    monkeypatch.setattr(tools, "MAX_GROUPS", 1)

    by_count = tools.schedule(conn, model_id, "IfcDoor", ["revit_type"], ["width_m"])
    monkeypatch.setattr(tools, "MAX_GROUPS", 200)
    monkeypatch.setattr(tools, "MAX_SCHEDULE_CHARS", 300)
    by_size = tools.schedule(
        conn, model_id, "IfcDoor", ["revit_type"], ["width_m", "Pset_DoorCommon.Nope"]
    )

    for result in (by_count, by_size):
        assert (len(result.groups), result.group_count, result.truncated) == (1, 2, True)
        assert result.elements == 5  # all of them, not only those shown
        assert result.note is not None
        assert result.note.startswith("Showing the first 1 of 2 groups")
    assert by_size.note is not None
    assert "No element in this schedule has Pset_DoorCommon.Nope." in by_size.note


@pytest.mark.parametrize(
    ("group_by", "fields", "message"),
    [
        ([], ["width_m"], "give at least one group_by"),
        (["revit_type"], [], "give 1 to 8 fields"),
        (["revit_type"], [f"P.p{n}" for n in range(9)], "give 1 to 8 fields"),
        (["revit_type"], ["FireRating"], "field 'FireRating' is neither a quantity"),
        (["revit_type"], [".FireRating"], "field '.FireRating' is neither a quantity"),
    ],
)
def test_arguments_that_make_no_schedule_are_refused(
    conn: Connection, group_by: list[tools.ScheduleGroupBy], fields: list[str], message: str
) -> None:
    model_id = _doors(conn)

    with pytest.raises(tools.ToolInputError, match=message):
        tools.schedule(conn, model_id, "IfcDoor", group_by, fields)


def test_an_unknown_model_is_refused(conn: Connection) -> None:
    with pytest.raises(tools.ToolInputError, match="no model with id 0"):
        tools.schedule(conn, 0, "IfcDoor", ["revit_type"], ["width_m"])


# --------------------------------------------------------------------------- space_schedule


def test_the_room_schedule_comes_in_pages_with_totals_for_all_of_them(conn: Connection) -> None:
    model_id = add_model(conn)
    for number, area in (("101", 10.0), ("102", 12.5), ("103", None)):
        space = add_spatial(conn, model_id, "IfcSpace", number)
        if area is not None:
            add_quantity(conn, space, "area_m2", area)

    first = tools.space_schedule(conn, model_id, limit=2)
    last = tools.space_schedule(conn, model_id, limit=2, offset=2)

    assert [s.number for s in first.spaces] == ["101", "102"]
    assert [s.number for s in last.spaces] == ["103"]
    assert (first.next_offset, last.next_offset) == (2, None)
    for page in (first, last):
        assert (page.space_count, page.total_area_m2, page.spaces_without_area) == (3, 22.5, 1)
        assert page.note == "1 of 3 spaces have no area; the total covers the others."
    assert (first.offset, first.limit, last.offset) == (0, 2, 2)


def test_a_page_holds_at_most_150_spaces() -> None:
    assert tools.SPACE_PAGE_SIZE == 150
