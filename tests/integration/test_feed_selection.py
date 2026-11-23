"""What view lights for each call: the tools' whole filters, resolved by their own SQL.

Each event is encoded as ``serve`` sends it and decoded as ``view`` reads it; its
arguments are checked with the tools' own checks. On the MCP test database (the Revit
fixture as model 1, checked; one byte longer as model 2, never checked), the selections
must agree with what the tools themselves return. Spaces, two exports and the 20,000
cap are built row by row in a transaction that is rolled back.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from rows import add_element, add_model, add_object, add_property, add_spatial
from sqlalchemy import Connection, Engine, insert, text

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.server import compare, tools
from ifc_sql_mcp.server.app import argument_checks, build_server
from ifc_sql_mcp.server.tools import MAX_SELECTION
from ifc_sql_mcp.viewer import selection
from ifc_sql_mcp.viewer.event import (
    Call,
    Event,
    EventRejectedError,
    Selected,
    Source,
    decode,
    encode,
    selected_ids,
)
from ifc_sql_mcp.viewer.selection import Request, Selection

SOURCE = Source(pid=7, started_at="2026-11-23T10:00:00+00:00")


@pytest.fixture(scope="module")
def checks(mcp_engine: Engine) -> selection.Checks:
    return argument_checks(build_server(mcp_engine))


@pytest.fixture
def reader(mcp_engine: Engine) -> Iterator[Connection]:
    with mcp_engine.connect() as connection, connection.begin():
        yield connection


def _event(
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    outcome: str = "ok",
    selected: list[Selected] | None = None,
    sql: str | None = None,
) -> Event:
    call = Call(
        source=SOURCE,
        n=1,
        at=datetime.now(UTC),
        tool=tool,
        arguments=arguments or {},
        outcome=outcome,
        seconds=0.01,
        rows=1,
        sql=sql,
        selected=selected,
    )
    return decode(encode(call))


def _lit(
    connection: Connection, checks: selection.Checks, tool: str, **arguments: Any
) -> Selection:
    wanted = selection.request(_event(tool, arguments), checks)
    return selection.resolve(connection, wanted)


def _groups(lit: Selection) -> list[tuple[str | None, int]]:
    return [(group.label, group.count) for group in lit.groups]


def _global_ids(lit: Selection) -> list[str]:
    return [gid for group in lit.groups for model in group.models for gid in model.global_ids]


# --------------------------------------------------------------------------- the tools


def test_find_elements_lights_every_match_not_just_its_page(
    reader: Connection, checks: selection.Checks
) -> None:
    pages = [tools.find_elements(reader, 1, {}, limit=5, offset=offset) for offset in (0, 5, 10)]
    found = [element.global_id for page in pages for element in page.elements]

    lit = _lit(reader, checks, "find_elements", model_id=1, limit=5, offset=10)

    assert pages[0].total == 13
    assert pages[2].next_offset is None
    assert lit.count == 13
    assert _global_ids(lit) == found
    assert _groups(lit) == [(None, 13)]
    assert (lit.truncated, lit.zoom, lit.note) == (False, False, None)
    assert lit.openings == 2  # find_elements finds openings too, which are never drawn


def test_find_elements_on_one_storey_names_it(reader: Connection, checks: selection.Checks) -> None:
    lit = _lit(reader, checks, "find_elements", model_id=1, storey_name="level 1")
    page = tools.find_elements(reader, 1, {"storey_name": "level 1"}, limit=100, offset=0)

    assert lit.count == page.total > 1
    assert (lit.storey, lit.storey_elevation_m) == ("Level 1", 0.0)
    assert _lit(reader, checks, "find_elements", model_id=1).storey is None


def test_schedule_lights_what_it_schedules(reader: Connection, checks: selection.Checks) -> None:
    schedule = tools.schedule(reader, 1, "IfcFlowTerminal", ["storey", "revit_type"], ["width_m"])

    lit = _lit(
        reader,
        checks,
        "schedule",
        model_id=1,
        ifc_class="ifcflowterminal",
        group_by=["storey", "revit_type"],
        fields=["width_m"],
    )

    assert _groups(lit) == [("Level 2 / M_Panelboard:225 A", 2)]
    assert lit.count == schedule.elements == 2


@pytest.mark.parametrize("group_by", ["storey", "ifc_class", "revit_type"])
@pytest.mark.parametrize("include_openings", [False, True])
def test_a_takeoff_lights_the_elements_of_each_group(
    reader: Connection, checks: selection.Checks, group_by: str, include_openings: bool
) -> None:
    takeoff = tools.quantity_takeoff(
        reader,
        1,
        group_by,
        "count",
        include_openings=include_openings,  # type: ignore[arg-type]
    )

    lit = _lit(
        reader,
        checks,
        "quantity_takeoff",
        model_id=1,
        group_by=group_by,
        metric="area_m2",
        include_openings=include_openings,
    )

    expected: dict[str | None, int] = {}
    for group in takeoff.groups:  # two Revit types without a family both read as null
        expected[group.group] = expected.get(group.group, 0) + group.elements
    assert dict(_groups(lit)) == expected
    assert lit.count == takeoff.coverage.elements
    assert lit.openings == (2 if include_openings else 0)


def test_a_takeoff_by_material_lights_nothing_and_says_why(
    reader: Connection, checks: selection.Checks
) -> None:
    lit = _lit(reader, checks, "quantity_takeoff", model_id=1, group_by="material", metric="count")

    assert (lit.count, lit.groups) == (0, [])
    assert lit.note == selection.BY_MATERIAL


def test_a_rule_lights_the_objects_it_found(reader: Connection, checks: selection.Checks) -> None:
    report = tools.data_quality_report(
        reader, 1, severity=None, rule_id="missing_fire_rating", samples_per_rule=20
    )
    [rule] = report.rules
    expected = [s.global_id for s in rule.samples if s.object_id is not None]

    lit = _lit(reader, checks, "data_quality_report", model_id=1, rule_id="missing_fire_rating")

    assert _groups(lit) == [("missing_fire_rating", rule.findings)]
    assert sorted(_global_ids(lit)) == sorted(expected)


def test_a_report_lights_every_object_with_a_finding_by_its_worst_severity(
    reader: Connection, checks: selection.Checks
) -> None:
    worst = dict(
        reader.execute(
            text(
                "SELECT object_id, min(CASE severity WHEN 'error' THEN 1 WHEN 'warning' THEN 2 "
                "ELSE 3 END) FROM ifc.v_dq_finding WHERE model_id = 1 AND object_id IS NOT NULL "
                "GROUP BY object_id"
            )
        ).all()
    )

    lit = _lit(reader, checks, "data_quality_report", model_id=1)
    scoped = _lit(reader, checks, "data_quality_report", model_id=1, severity="info")

    by_severity = {"error": 1, "warning": 2, "info": 3}
    assert _groups(lit) == [
        (name, sum(1 for level in worst.values() if level == rank))
        for name, rank in by_severity.items()
        if rank in worst.values()
    ]
    assert lit.count == len(worst)
    assert {group.label for group in scoped.groups} == {"info"}
    loader = _lit(reader, checks, "data_quality_report", model_id=1, scope="loader_coverage")
    content = _lit(reader, checks, "data_quality_report", model_id=1, scope="model_content")
    assert loader.count + content.count >= lit.count
    assert content.count > 0


def test_a_model_never_checked_says_so(reader: Connection, checks: selection.Checks) -> None:
    lit = _lit(reader, checks, "data_quality_report", model_id=2)

    assert lit.count == 0
    assert lit.note == "model 2 was never checked (ifc-sql dq)"


def test_an_unknown_rule_lights_nothing_and_says_why(
    reader: Connection, checks: selection.Checks
) -> None:
    lit = _lit(reader, checks, "data_quality_report", model_id=1, rule_id="no_such_rule")

    assert lit.note == "nothing is lit: no rule 'no_such_rule'"


def test_get_element_lights_its_object_and_the_camera_goes_there(
    reader: Connection, checks: selection.Checks
) -> None:
    detail = tools.get_element(reader, object_id=3, model_id=None, global_id=None)
    event = _event("get_element", {"object_id": 3}, selected=[Selected(object_ids=(3,))])

    lit = selection.resolve(reader, selection.request(event, checks))

    assert _global_ids(lit) == [detail.global_id]
    assert lit.zoom is True


def test_run_sql_lights_its_rows_ids_by_group(reader: Connection, checks: selection.Checks) -> None:
    rows = reader.execute(
        text(
            "SELECT ifc_class_base, array_agg(object_id ORDER BY object_id) AS object_ids "
            "FROM ifc.v_element WHERE model_id = 1 GROUP BY 1 ORDER BY 1"
        )
    ).all()
    found = selected_ids(["ifc_class_base", "object_ids"], [tuple(row) for row in rows])
    found.append(Selected(label="gone", object_ids=(99_999,)))  # deleted since: not lit
    found.append(Selected(label="again", object_ids=(rows[0][1][0],)))  # lit once, in its group
    event = _event("run_sql", {"max_rows": 100}, selected=found, sql="SELECT …")

    lit = selection.resolve(reader, selection.request(event, checks))

    assert _groups(lit) == [(row[0], len(row[1])) for row in rows]


def test_run_sql_lights_global_ids_beside_a_model_id(
    reader: Connection, checks: selection.Checks
) -> None:
    pairs = reader.execute(
        text("SELECT model_id, global_id FROM ifc.object WHERE object_id IN (3, 4) ORDER BY 1")
    ).all()
    event = _event(
        "run_sql",
        {"max_rows": 5},
        selected=selected_ids(["model_id", "global_id"], [tuple(pair) for pair in pairs]),
        sql="SELECT …",
    )

    lit = selection.resolve(reader, selection.request(event, checks))

    assert _global_ids(lit) == [pair[1] for pair in pairs]


def test_run_sql_without_ids_lights_nothing_and_says_how(checks: selection.Checks) -> None:
    wanted = selection.request(_event("run_sql", {"max_rows": 5}, sql="SELECT 1"), checks)

    assert wanted == Request("nothing", note=selection.NO_COLUMNS)


def test_ids_the_event_cut_say_so(checks: selection.Checks) -> None:
    selected = [Selected(object_ids=tuple(range(1, 700)))]

    wanted = selection.request(_event("run_sql", sql="SELECT 1", selected=selected), checks)

    assert wanted.query == "ids"
    assert wanted.note is not None
    assert wanted.note.startswith("the rows held more ids than the feed carries")


@pytest.mark.parametrize(
    ("tool", "arguments", "outcome", "note"),
    [
        ("list_models", {}, "ok", None),
        ("list_storeys", {"model_id": 1}, "ok", None),
        ("describe_schema", {"relation": "v_element"}, "ok", None),
        ("find_elements", {"model_id": 99}, "invalid", "the server refused its arguments"),
        ("run_sql", {}, "refused", "the query was refused"),
        ("space_schedule", {"model_id": 1}, "timeout", "it ran out of time"),
        ("run_sql", {}, "database_error", "the database refused the query"),
        ("list_models", {}, "unavailable", "the database could not be reached"),
        ("get_element", {"object_id": 1}, "error", "the server failed"),
    ],
)
def test_some_calls_light_nothing(
    checks: selection.Checks, tool: str, arguments: dict[str, Any], outcome: str, note: str | None
) -> None:
    sql = "SELECT 1" if tool == "run_sql" else None

    wanted = selection.request(_event(tool, arguments, outcome=outcome, sql=sql), checks)

    assert wanted.query == "nothing"
    assert wanted.note == (None if note is None else f"nothing is lit: {note}")
    assert selection.nothing(wanted).count == 0


def test_a_clipped_argument_lights_nothing(checks: selection.Checks) -> None:
    event = _event("find_elements", {"model_id": 1, "name_contains": "ü" * 150})

    assert event.clipped == ["name_contains"]
    assert selection.request(event, checks) == Request("nothing", note=selection.CLIPPED)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("find_elements", {"model_id": 0}),
        ("find_elements", {"model_id": "1; DROP TABLE ifc.model"}),
        ("find_elements", {"model_id": 1, "storey_name": ""}),
        ("find_elements", {"model_id": 1, "limit": 101}),
        ("find_elements", {"model_id": 1, "sql": "SELECT 1"}),
        (
            "schedule",
            {"model_id": 1, "ifc_class": "IfcDoor", "group_by": ["colour"], "fields": ["x.y"]},
        ),
        ("quantity_takeoff", {"model_id": 1, "group_by": "storey"}),
        ("run_sql", {"sql": "DELETE FROM ifc.model"}),
        ("get_element", {"global_id": "short"}),
    ],
)
def test_arguments_no_real_call_could_have_are_refused(
    checks: selection.Checks, tool: str, arguments: dict[str, Any]
) -> None:
    event = _event(tool, arguments, sql="SELECT 1" if tool == "run_sql" else None)

    with pytest.raises(EventRejectedError, match=r"^invalid arguments$"):
        selection.request(event, checks)


# --------------------------------------------------------------------------- rows built here


@pytest.fixture
def owner(conn: Connection) -> Connection:
    return conn


def test_schedule_lights_one_group_per_schedule_group_in_its_order(
    owner: Connection, checks: selection.Checks
) -> None:
    model = add_model(owner)
    level_1 = add_spatial(owner, model, "IfcBuildingStorey", "Level 1", elevation_m=0.0)
    level_2 = add_spatial(owner, model, "IfcBuildingStorey", "Level 2", elevation_m=3.1)
    for storey, door_type in [
        (level_2, "0864 x 2032mm"),
        (level_1, "0915 x 2134mm"),
        (level_1, "0864 x 2032mm"),
        (level_2, "0864 x 2032mm"),
        (None, None),
    ]:
        add_element(
            owner,
            model,
            "IfcDoor",
            "door",
            storey_id=storey,
            revit_family="M_Single-Flush" if door_type else None,
            revit_type=door_type,
        )
    schedule = tools.schedule(owner, model, "IfcDoor", ["storey", "revit_type"], ["width_m"])

    lit = _lit(
        owner,
        checks,
        "schedule",
        model_id=model,
        ifc_class="IfcDoor",
        group_by=["storey", "revit_type"],
        fields=["width_m"],
    )
    by_type = _lit(
        owner,
        checks,
        "schedule",
        model_id=model,
        ifc_class="IfcDoor",
        group_by=["revit_type", "revit_type"],
        fields=["width_m"],
        storey_name="Level 2",
    )

    assert _groups(lit) == [
        ("Level 1 / M_Single-Flush:0864 x 2032mm", 1),
        ("Level 1 / M_Single-Flush:0915 x 2134mm", 1),
        ("Level 2 / M_Single-Flush:0864 x 2032mm", 2),
        ("(none) / (none)", 1),
    ]
    assert [(" / ".join("(none)" if v is None else v for v in row.group), row.elements)
            for row in schedule.groups] == _groups(lit)  # fmt: skip
    assert _groups(by_type) == [("M_Single-Flush:0864 x 2032mm", 2)]
    assert by_type.storey == "Level 2"


def test_space_schedule_lights_rooms_copies_and_areas_apart(
    owner: Connection, checks: selection.Checks
) -> None:
    model = add_model(owner)
    level = add_spatial(owner, model, "IfcBuildingStorey", "Level 1", elevation_m=0.0)
    room = add_spatial(owner, model, "IfcSpace", "A101", parent_id=level, footprint_m2=12.0)
    copy = add_spatial(
        owner, model, "IfcSpace", "A101 Space", parent_id=level, footprint_m2=12.0, repeats_id=room
    )
    area = add_spatial(owner, model, "IfcSpace", "1", parent_id=level, revit_category="Areas")

    lit = _lit(owner, checks, "space_schedule", model_id=model, limit=1)

    names = {room: "A101", copy: "A101 Space", area: "1"}
    gids = dict(
        owner.execute(
            text("SELECT object_id, global_id FROM ifc.object WHERE object_id = ANY(:ids)"),
            {"ids": list(names)},
        ).all()
    )
    assert _groups(lit) == [(label, 1) for label in tools.SPACE_GROUPS]
    assert _global_ids(lit) == [gids[room], gids[copy], gids[area]]
    assert lit.layers == ["areas", "space_copies", "spaces"]
    assert lit.storey == "Level 1"


def test_compare_models_lights_what_changed_in_the_newer_model(
    owner: Connection, checks: selection.Checks
) -> None:
    old = add_model(owner, project_name="001-00")
    new = add_model(owner, project_name="001-00", replaces_model_id=old)
    for model in (old, new):
        wall = add_element(owner, model, "IfcWall", "Basic Wall:Generic:1")
        owner.execute(
            text("UPDATE ifc.object SET global_id = '0wall_________________' WHERE object_id = :o"),
            {"o": wall},
        )
        if model == new:
            add_property(
                owner, wall, "Pset_WallCommon", "FireRating", value_type="IfcLabel", value_text="M2"
            )
    owner.execute(
        insert(schema.element).values(
            object_id=add_object(owner, new, "IfcDoor", global_id="0door_new_____________")
        )
    )
    add_element(owner, old, "IfcSlab", "Floor:Generic:4")

    lit = _lit(owner, checks, "compare_models", new_model_id=new)
    comparison = compare.compare_models(owner, new, old)

    assert _groups(lit) == [("added", 1), ("properties changed", 1)]
    assert {model.model_id for group in lit.groups for model in group.models} == {new}
    assert lit.note == f"1 objects of model {old} are not in model {new}: counted, not drawn"
    assert comparison.objects_removed == 1


def test_compare_models_without_an_older_model_lights_nothing(
    owner: Connection, checks: selection.Checks
) -> None:
    model = add_model(owner)

    lit = _lit(owner, checks, "compare_models", new_model_id=model)

    assert lit.note == f"nothing is lit: model {model} replaces no model; give old_model_id"


@pytest.mark.parametrize("old_model_id", ["same", 999_999])
def test_compare_models_with_a_wrong_older_model_lights_nothing(
    owner: Connection, checks: selection.Checks, old_model_id: object
) -> None:
    model = add_model(owner)
    old = model if old_model_id == "same" else old_model_id

    lit = _lit(owner, checks, "compare_models", new_model_id=model, old_model_id=old)

    assert lit.count == 0
    assert lit.note is not None
    assert lit.note.startswith("nothing is lit: ")


def test_a_selection_stops_at_20000_objects(owner: Connection, checks: selection.Checks) -> None:
    model = add_model(owner)
    owner.execute(
        text(
            "WITH o AS (INSERT INTO ifc.object (model_id, global_id, object_kind, ifc_class, "
            "ifc_class_base) SELECT :m, '0' || lpad(to_hex(n), 21, '0'), 'element', 'IfcWall', "
            "'IfcWall' FROM generate_series(1, :n) AS n RETURNING object_id) "
            "INSERT INTO ifc.element (object_id) SELECT object_id FROM o"
        ),
        {"m": model, "n": MAX_SELECTION + 1},
    )

    lit = _lit(owner, checks, "find_elements", model_id=model, ifc_class="IfcWall")

    assert (lit.count, lit.truncated) == (MAX_SELECTION, True)
    assert len(_global_ids(lit)) == MAX_SELECTION
