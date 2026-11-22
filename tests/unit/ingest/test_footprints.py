"""Finding the spaces that cover the same floor as another, from their plans (R1).

The cases are the ones the re-check found on the audit's files: a room and an MEP Space
with one plan, a corridor one export splits into parts, rooms that only touch, and Revit
Areas over the rooms.
"""

import dataclasses

import pytest
from raw_builders import gid, model, spatial, value
from shapely import box

from ifc_sql_mcp.ingest.footprints import SpacePlan, plan_from_triangles, space_plans
from ifc_sql_mcp.ingest.raw import RawFootprint
from ifc_sql_mcp.ingest.transform import space_categories, transform

LEVEL = "level"


def _compare(*plans: tuple[str, object], areas: tuple[str, ...] = ()) -> dict[str, str | None]:
    found = space_plans(list(plans), {name: LEVEL for name, _ in plans}, areas)
    return {plan.global_id: plan.repeats_global_id for plan in found}


def test_a_room_exported_twice_repeats_the_first() -> None:
    # The same plan twice: file order decides which is the copy.
    assert _compare(("room", box(0, 0, 4, 3)), ("mep", box(0, 0, 4, 3))) == {
        "room": None,
        "mep": "room",
    }


def test_the_parts_of_a_split_room_repeat_the_whole() -> None:
    # Corridor 49 of file C is one space in one set and 49a-d in the other.
    parts = [(f"49{s}", box(i, 0, i + 1, 2)) for i, s in enumerate("abcd")]

    found = _compare(*parts, ("49", box(0, 0, 4, 2)))

    assert found == {"49a": "49", "49b": "49", "49c": "49", "49d": "49", "49": None}


def test_a_space_mostly_inside_others_repeats_the_one_it_overlaps_most() -> None:
    # 91 % of the small one lies inside the two larger ones together, most of it in "big".
    found = _compare(
        ("big", box(0, 0, 10, 10)),
        ("next", box(10, 0, 20, 10)),
        ("small", box(8.6, -0.1, 11.0, 1.0)),
    )

    assert found == {"big": None, "next": None, "small": "big"}


@pytest.mark.parametrize(
    ("other", "storey"),
    [
        (box(4, 0, 8, 3), LEVEL),  # touches along one side
        (box(2.5, 0, 6.5, 3), LEVEL),  # overlaps by 37.5 %
        (box(0.3, 0, 4.3, 3), "upper"),  # 92.5 %, but on another storey
    ],
)
def test_rooms_that_touch_overlap_partly_or_lie_on_another_storey_repeat_nothing(
    other: object, storey: str
) -> None:
    plans = [("room", box(0, 0, 4, 3)), ("other", other)]

    found = space_plans(plans, {"room": LEVEL, "other": storey})

    assert [plan.repeats_global_id for plan in found] == [None, None]


def test_revit_areas_neither_repeat_rooms_nor_are_repeated() -> None:
    found = space_plans(
        [("gross", box(0, 0, 20, 20)), ("room", box(1, 1, 5, 5))],
        {"gross": LEVEL, "room": LEVEL},
        revit_areas=("gross",),
    )

    assert found == [SpacePlan("gross", 400.0, None), SpacePlan("room", 16.0, None)]


def test_a_plan_is_the_body_seen_from_above() -> None:
    # A 2 x 1 box: two triangles on top, two below, and the four sides seen edge-on.
    verts = [0, 0, 0, 2, 0, 0, 2, 1, 0, 0, 1, 0, 0, 0, 3, 2, 0, 3, 2, 1, 3, 0, 1, 3]
    faces = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4]

    plan = plan_from_triangles(verts, faces)

    assert plan is not None
    assert plan.area == pytest.approx(2.0)
    assert plan_from_triangles([0, 0, 0, 1, 0, 0, 1, 0, 1], [0, 1, 2]) is None  # a wall only


def test_space_categories_come_from_the_first_set_that_states_one() -> None:
    raw = model(
        (
            spatial(
                "area",
                values=(
                    value("Other", "Category", "Rooms"),
                    value("Pset_SpaceCommon", "Category", "Areas"),
                ),
            ),
            spatial(
                "room",
                values=(
                    value("Other", "Category", " Rooms "),
                    value("PSet_Revit_Other", "Category", "Spaces"),
                ),
            ),
            spatial("blank", values=(value("Pset_SpaceCommon", "Category", "  "),)),
            spatial("storey", "IfcBuildingStorey", values=(value("Other", "Category", "Levels"),)),
        )
    )
    spaces = {gid(label) for label in ("area", "room", "blank")}

    categories = space_categories(transform(raw).properties, spaces)

    assert categories == {spatial("area").global_id: "Areas", spatial("room").global_id: "Rooms"}


def test_transform_records_each_spaces_plan_and_what_it_repeats() -> None:
    level = spatial(LEVEL, "IfcBuildingStorey")
    rooms = (
        spatial("room", parent_global_id=level.global_id),
        spatial("mep", parent_global_id=level.global_id),
        spatial(
            "gross",
            parent_global_id=level.global_id,
            values=(value("Pset_SpaceCommon", "Category", "Areas"),),
        ),
        spatial("unmeasured", parent_global_id=level.global_id),
    )
    raw = dataclasses.replace(
        model((level, *rooms)),
        footprints=(
            RawFootprint(rooms[0].global_id, box(0, 0, 4, 3)),
            RawFootprint(rooms[1].global_id, box(0, 0, 4, 3)),
            RawFootprint(rooms[1].global_id, box(0, 0, 1, 1)),  # a GlobalId used twice
            RawFootprint(rooms[2].global_id, box(0, 0, 9, 9)),
            RawFootprint("0not_a_loaded_object__", box(0, 0, 9, 9)),
        ),
    )

    records = {s.global_id: s for s in transform(raw).spatial_elements}

    assert [
        (records[r.global_id].footprint_m2, records[r.global_id].repeats_global_id) for r in rooms
    ] == [(12.0, None), (12.0, rooms[0].global_id), (81.0, None), (None, None)]
    assert [records[r.global_id].revit_category for r in rooms] == [None, None, "Areas", None]
    assert records[level.global_id].footprint_m2 is None
