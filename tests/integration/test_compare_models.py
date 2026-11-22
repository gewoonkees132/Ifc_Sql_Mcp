"""compare_models: what changed between two exports, object by object (audit finding A15).

Two versions of a small model, built row by row: a wall kept with a fire rating added,
a door kept with its rating changed and a note removed, a window kept unchanged, an
opening regenerated with a new GlobalId, and a slab only in the old version.
"""

import pytest
from rows import add_model, add_object, add_property
from sqlalchemy import Connection, insert

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.server import compare
from ifc_sql_mcp.server.tools import ToolInputError

WALL, DOOR, WINDOW = (
    "0wall__________________"[:22],
    "0door_________________",
    "0window_______________",
)


def _element(conn: Connection, model_id: int, ifc_class: str, global_id: str, name: str) -> int:
    object_id = add_object(conn, model_id, ifc_class, global_id=global_id, name=name)
    conn.execute(insert(schema.element).values(object_id=object_id))
    return object_id


def _label(conn: Connection, object_id: int, pset: str, prop: str, text: str) -> None:
    add_property(conn, object_id, pset, prop, value_type="IfcLabel", value_text=text)


@pytest.fixture
def versions(conn: Connection) -> tuple[int, int]:
    old = add_model(conn, project_name="001-00")
    new = add_model(conn, project_name="001-00", replaces_model_id=old)
    for model_id in (old, new):
        wall = _element(conn, model_id, "IfcWall", WALL, "Basic Wall:Generic:1")
        door = _element(conn, model_id, "IfcDoor", DOOR, "M_Single-Flush:0915 x 2134mm:2")
        window = _element(conn, model_id, "IfcWindow", WINDOW, "M_Fixed:0915 x 1220mm:3")
        _label(conn, door, "Pset_DoorCommon", "FireRating", "EI 30" if model_id == old else "EI 60")
        add_property(
            conn,
            window,
            "Pset_WindowCommon",
            "IsExternal",
            value_type="IfcBoolean",
            value_bool=True,
        )
        if model_id == new:
            _label(conn, wall, "Pset_WallCommon", "FireRating", "M2")
        else:
            _label(conn, door, "Identity Data", "Comments", "check the swing")
    _element(conn, old, "IfcOpeningElement", "0opening_old__________", "Basic Wall:Generic:1:1")
    _element(conn, new, "IfcOpeningElement", "0opening_new__________", "Basic Wall:Generic:1:1")
    _element(conn, old, "IfcSlab", "0slab__________________"[:22], "Floor:Generic 150mm:4")
    return old, new


def test_objects_added_and_removed_are_counted_per_class(
    conn: Connection, versions: tuple[int, int]
) -> None:
    old, new = versions

    result = compare.compare_models(conn, new, old)

    assert (result.objects_in_both, result.objects_added, result.objects_removed) == (3, 1, 2)
    assert [(c.ifc_class, c.objects) for c in result.added_by_class] == [("IfcOpeningElement", 1)]
    assert [(c.ifc_class, c.objects) for c in result.removed_by_class] == [
        ("IfcOpeningElement", 1),
        ("IfcSlab", 1),
    ]
    assert result.added_by_class[0].samples == [
        compare.ObjectRef(
            global_id="0opening_new__________",
            ifc_class="IfcOpeningElement",
            name="Basic Wall:Generic:1:1",
        )
    ]
    assert (result.truncated, result.note) == (False, None)


def test_property_values_added_removed_and_changed_are_counted_per_property(
    conn: Connection, versions: tuple[int, int]
) -> None:
    old, new = versions

    result = compare.compare_models(conn, new, old)

    assert [
        (c.pset_name, c.prop_name, c.added, c.removed, c.changed) for c in result.property_changes
    ] == [
        ("Identity Data", "Comments", 0, 1, 0),
        ("Pset_DoorCommon", "FireRating", 0, 0, 1),
        ("Pset_WallCommon", "FireRating", 1, 0, 0),
    ]
    changed = result.property_changes[1].samples
    assert [(s.global_id, s.old, s.new) for s in changed] == [(DOOR, "EI 30", "EI 60")]
    added = result.property_changes[2].samples
    assert [(s.object_name, s.old, s.new) for s in added] == [("Basic Wall:Generic:1", None, "M2")]


def test_without_an_old_model_the_one_replaced_is_used(
    conn: Connection, versions: tuple[int, int]
) -> None:
    old, new = versions

    assert compare.compare_models(conn, new).old_model_id == old


def test_samples_can_be_left_out(conn: Connection, versions: tuple[int, int]) -> None:
    old, new = versions

    result = compare.compare_models(conn, new, old, samples=0)

    assert all(c.samples == [] for c in result.property_changes + result.added_by_class)
    assert result.property_changes[0].removed == 1  # the counts stay


def test_numbers_and_units_count_as_values(conn: Connection) -> None:
    old = add_model(conn)
    new = add_model(conn)
    for model_id, (number, unit) in ((old, (1.4, None)), (new, (1.4, "W/(m2*K)"))):
        door = _element(conn, model_id, "IfcDoor", DOOR, "Door")
        add_property(
            conn,
            door,
            "Pset_DoorCommon",
            "ThermalTransmittance",
            value_type="IfcThermalTransmittanceMeasure",
            value_num=number,
            unit=unit,
        )

    [change] = compare.compare_models(conn, new, old).property_changes

    assert change.changed == 1
    assert (change.samples[0].old, change.samples[0].new) == (1.4, 1.4)
    assert (change.samples[0].old_unit, change.samples[0].new_unit) == (None, "W/(m2*K)")


def test_two_unrelated_models_say_so(conn: Connection) -> None:
    house = add_model(conn)
    tower = add_model(conn)
    _element(conn, house, "IfcWall", WALL, "Basic Wall:Generic:1")
    _element(conn, tower, "IfcWall", DOOR, "Basic Wall:Generic:1")

    result = compare.compare_models(conn, tower, house)

    assert (result.objects_in_both, result.property_changes) == (0, [])
    assert result.note is not None
    assert result.note.startswith("No object of one model has a GlobalId of the other")


def test_the_lists_stop_at_their_limit_and_say_so(
    conn: Connection, versions: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = versions
    monkeypatch.setattr(compare, "MAX_GROUPS", 1)

    result = compare.compare_models(conn, new, old)

    assert (len(result.removed_by_class), len(result.property_changes), result.truncated) == (
        1,
        1,
        True,
    )
    assert result.objects_removed == 2  # the totals count every class


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"old_model_id": 0}, "no model with id 0"),
        ({"old_model_id": None}, "replaces no model; give old_model_id"),
        ({"same": True}, "old_model_id and new_model_id are the same model"),
    ],
)
def test_comparisons_that_cannot_be_made_are_refused(
    conn: Connection, arguments: dict[str, object], message: str
) -> None:
    model_id = add_model(conn)
    old = model_id if arguments.pop("same", False) else arguments["old_model_id"]

    with pytest.raises(ToolInputError, match=message):
        compare.compare_models(conn, model_id, old)  # type: ignore[arg-type]
