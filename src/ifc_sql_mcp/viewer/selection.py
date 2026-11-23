"""What a call found, for the page to light up (ADR 14, decision 4).

An event names a tool and its arguments; ``view`` turns it into a selection in two steps:

* :func:`request`, pure: checks the arguments with the tool's own check (the SDK's
  argument model, :func:`~ifc_sql_mcp.server.app.argument_checks`) and picks the fixed
  query that resolves the tool's whole filter, not just the page it returned;
* :func:`resolve`: runs that query, one of ``server/tools.py``'s selection functions,
  read-only, with the arguments as bind parameters. No SQL comes from the event.

======================  ===============================================================
Tool                    What is lit
======================  ===============================================================
find_elements           Every match of the filter, on all pages (``find_element_ids``).
schedule                The same elements, one group per schedule group.
quantity_takeoff        By storey, class or Revit type: each group's elements, openings
                        left out unless ``include_openings``. By material: nothing.
get_element             The one object, and the camera goes to it.
space_schedule          Every space: rooms, their copies, and Revit Areas, three groups.
data_quality_report     The objects with a finding of the run: one group with
                        ``rule_id``, else one per worst severity.
compare_models          The newer model's added objects and those whose properties
                        changed; removed ones are counted.
run_sql                 The ids its rows held (``viewer/event.py``); rows with no id
                        column (a count), nothing: the feed only.
others                  Nothing: they appear in the feed only.
======================  ===============================================================

A selection holds at most :data:`~ifc_sql_mcp.server.tools.MAX_SELECTION` objects. It
is resolved when the event arrives, so a data quality run or a load in between changes
what a later look at the same call would light.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Connection

from ifc_sql_mcp.server import compare, tools
from ifc_sql_mcp.server.tools import MAX_SELECTION, Found, ToolInputError
from ifc_sql_mcp.viewer.event import Event, EventRejectedError, IdGroup

type Query = Literal[
    "nothing",
    "find_elements",
    "schedule",
    "quantity_takeoff",
    "space_schedule",
    "data_quality_report",
    "compare_models",
    "ids",
]

type Checks = Mapping[str, Callable[[dict[str, Any]], dict[str, Any]]]

PALETTE = (
    "#ff5722",
    "#1e88e5",
    "#43a047",
    "#8e24aa",
    "#f9a825",
    "#00897b",
    "#6d4c41",
    "#d81b60",
)
"""The groups' colours, in order; a ninth group takes the first again."""

SEVERITY_COLOURS = {"error": "#d32f2f", "warning": "#f9a825", "info": "#1e88e5"}
"""A data quality report's groups, by their worst finding's severity."""

LAYERS = {"space": "spaces", "space_copy": "space_copies", "area": "areas"}
"""The scene's chunk kind that holds each kind of space (ADR 14, decision 1)."""

FAILED = {
    "invalid": "the server refused its arguments",
    "refused": "the query was refused",
    "timeout": "it ran out of time",
    "database_error": "the database refused the query",
    "unavailable": "the database could not be reached",
    "error": "the server failed",
}
"""Why a call that failed lights nothing, by its outcome."""

FEED_ONLY = ("list_models", "list_storeys", "describe_schema")
"""The tools that light nothing by nature: they appear in the feed only."""

CLIPPED = "an argument was too long for the feed, so nothing is lit"
BY_MATERIAL = (
    "a takeoff by material totals materials, not elements (an element of three materials "
    "counts three times), so nothing is lit"
)


@dataclass(frozen=True, slots=True)
class Request:
    """What to resolve for one event: a fixed query, and its checked arguments.

    Attributes:
        query: Which of the selection queries, or ``nothing``.
        arguments: The arguments the query takes, as the tool's check returned them.
        groups: For ``ids``: the ids the event carries.
        zoom: The page moves the camera to the selection (``get_element``).
        note: What the caption says besides the counts: why nothing is lit, or that the
            event carried only some of the ids.
        lights: False for a call that lights nothing by nature: it has no selection,
            and the page leaves what it lit as it is.
    """

    query: Query
    arguments: dict[str, Any] = field(default_factory=dict)
    groups: tuple[IdGroup, ...] = ()
    zoom: bool = False
    note: str | None = None
    lights: bool = True


class ModelObjects(BaseModel):
    """The GlobalIds of one model in a group: the page finds its instances by them."""

    model_id: int
    global_ids: list[str]


class Group(BaseModel):
    """One group of a selection, drawn in one colour."""

    label: str | None = Field(description="e.g. a Revit type, a severity, rooms or copies.")
    colour: str = Field(description="The group's colour, as #rrggbb.")
    count: int
    models: list[ModelObjects]


class Selection(BaseModel):
    """What the page lights for one call."""

    groups: list[Group] = Field(description="In the order of their colours.")
    count: int = Field(description="Objects found, every group together.")
    truncated: bool = Field(description=f"True when more than {MAX_SELECTION:,} were found.")
    openings: int = Field(description="Of them, openings: never drawn.")
    not_drawn: int = Field(description="Of them, storeys, buildings and sites: never drawn.")
    spaces: int = Field(
        description="Of them, spaces: rooms, their copies and area plans. When every object "
        "is one, the page hides the rest, which would hide the rooms inside."
    )
    layers: list[str] = Field(description="Chunk kinds of spaces the page must show.")
    storey: str | None = Field(description="The one storey every object is on, if one.")
    storey_elevation_m: float | None
    zoom: bool
    note: str | None


def request(event: Event, checks: Checks) -> Request:
    """Check ``event``'s arguments with its tool's own check, and say what to resolve.

    Raises:
        EventRejectedError: The arguments are not what a real call of the tool could
            have: an unknown name, a wrong type, a value past a limit.
    """
    arguments = dict(event.arguments)
    if event.tool == "run_sql":
        if "sql" in arguments:
            raise EventRejectedError("invalid arguments")
        arguments["sql"] = "SELECT 1"  # the text is the event's, shown, never run
    try:
        checked = checks[event.tool](arguments)
    except ValueError as exc:  # pydantic's ValidationError is one
        raise EventRejectedError("invalid arguments") from exc
    if event.outcome != "ok":
        return Request("nothing", note=f"nothing is lit: {FAILED[event.outcome]}")
    if event.tool in FEED_ONLY:
        return Request("nothing", lights=False)
    if event.tool in ("run_sql", "get_element"):
        if event.ids is None:  # rows with no id column: a count, not a choice of objects
            return Request("nothing", lights=False)
        note = None
        if event.ids.truncated:
            note = "the rows held more ids than the feed carries: the first of them are lit"
        return Request(
            "ids", groups=tuple(event.ids.groups), zoom=event.tool == "get_element", note=note
        )
    if event.clipped:
        return Request("nothing", note=CLIPPED)
    if event.tool == "quantity_takeoff" and checked["group_by"] == "material":
        return Request("nothing", note=BY_MATERIAL)
    query, names = _QUERIES[event.tool]
    return Request(query, {name: checked[name] for name in names})


_QUERIES: dict[str, tuple[Query, tuple[str, ...]]] = {
    "find_elements": (
        "find_elements",
        ("model_id", "ifc_class", "storey_name", "revit_family", "revit_type", "name_contains"),
    ),
    "schedule": ("schedule", ("model_id", "ifc_class", "group_by", "storey_name")),
    "quantity_takeoff": ("quantity_takeoff", ("model_id", "group_by", "include_openings")),
    "space_schedule": ("space_schedule", ("model_id",)),
    "data_quality_report": ("data_quality_report", ("model_id", "severity", "rule_id", "scope")),
    "compare_models": ("compare_models", ("new_model_id", "old_model_id")),
}
"""Each filtered tool's query, and the arguments that decide its selection; the others
only page or shape its result."""


def nothing(wanted: Request) -> Selection:
    """The selection of a request that lights nothing; it needs no database."""
    return _selection([], [], wanted, note=wanted.note)


def resolve(connection: Connection, wanted: Request) -> Selection:
    """Run the query ``wanted`` names, and return what the page lights."""
    if wanted.query == "nothing":
        return nothing(wanted)
    try:
        found, extra = _found(connection, wanted)
    except ToolInputError as exc:
        return _selection([], [], wanted, note=f"nothing is lit: {exc}")
    # A request with a note of its own (ids) never has an extra one (reports, comparisons).
    note = extra or wanted.note
    truncated = len(found) > MAX_SELECTION
    found = found[:MAX_SELECTION]
    objects = tools.selected_objects(connection, [object_id for _, object_id in found])
    return _selection(found, objects, wanted, note=note, truncated=truncated)


def summary(event: Event) -> str:
    """The call in one line, from its arguments, never from IFC content.

    For example ``find_elements: IfcWall on 'Level 1', model 1``.
    """
    a = event.arguments
    tool = event.tool
    if tool == "find_elements":
        parts = [str(a["ifc_class"]) if a.get("ifc_class") else "elements"]
        for name, words in (
            ("revit_family", "family"),
            ("revit_type", "type"),
            ("name_contains", "named like"),
            ("storey_name", "on"),
        ):
            if a.get(name):
                parts.append(f"{words} {_quoted(a[name])}")
        return f"{tool}: {' '.join(parts)}, model {a.get('model_id')}"
    if tool == "schedule":
        by = " and ".join(str(key) for key in _list(a.get("group_by")))
        where = f" on {_quoted(a['storey_name'])}" if a.get("storey_name") else ""
        return f"{tool}: {a.get('ifc_class')} by {by}{where}, model {a.get('model_id')}"
    if tool == "quantity_takeoff":
        return f"{tool}: {a.get('metric')} by {a.get('group_by')}, model {a.get('model_id')}"
    if tool == "get_element":
        if a.get("object_id") is not None:
            return f"{tool}: object {a['object_id']}"
        return f"{tool}: {a.get('global_id')} in model {a.get('model_id')}"
    if tool == "data_quality_report":
        rule = f", rule {a['rule_id']}" if a.get("rule_id") else ""
        return f"{tool}: model {a.get('model_id')}{rule}"
    if tool == "compare_models":
        old = a.get("old_model_id")
        against = f"model {old}" if old is not None else "the model it replaces"
        return f"{tool}: model {a.get('new_model_id')} against {against}"
    if tool == "run_sql":
        chars = event.sql.chars if event.sql is not None else 0
        return f"{tool}: {chars:,} characters of SQL"
    if tool == "describe_schema" and a.get("relation"):
        return f"{tool}: {a['relation']}"
    if tool in ("list_storeys", "space_schedule"):
        return f"{tool}: model {a.get('model_id')}"
    return tool


def _found(connection: Connection, wanted: Request) -> tuple[Found, str | None]:
    """The objects ``wanted`` finds, and a note to add, if any."""
    a = wanted.arguments
    if wanted.query == "find_elements":
        filters = {name: value for name, value in a.items() if name != "model_id"}
        return tools.find_element_ids(connection, a["model_id"], filters), None
    if wanted.query == "schedule":
        found = tools.schedule_element_ids(
            connection, a["model_id"], a["ifc_class"], a["group_by"], storey_name=a["storey_name"]
        )
        return found, None
    if wanted.query == "quantity_takeoff":
        found = tools.takeoff_element_ids(
            connection, a["model_id"], a["group_by"], include_openings=a["include_openings"]
        )
        return found, None
    if wanted.query == "space_schedule":
        return tools.space_ids(connection, a["model_id"]), None
    if wanted.query == "data_quality_report":
        found = tools.finding_object_ids(
            connection,
            a["model_id"],
            severity=a["severity"],
            rule_id=a["rule_id"],
            scope=a["scope"],
        )
        checked = tools.model_summary(connection, a["model_id"]).data_quality.status
        never = (
            f"model {a['model_id']} was never checked (ifc-sql dq)"
            if checked != "checked"
            else None
        )
        return found, never
    if wanted.query == "compare_models":
        found, old, removed = compare.changed_object_ids(
            connection, a["new_model_id"], a["old_model_id"]
        )
        new = a["new_model_id"]
        gone = f"{removed:,} objects of model {old} are not in model {new}: counted, not drawn"
        return found, gone if removed else None
    return _from_ids(connection, wanted.groups), None  # ids


def _from_ids(connection: Connection, groups: tuple[IdGroup, ...]) -> Found:
    """The loaded objects among the ids an event carries, group by group."""
    found: Found = []
    seen: set[int] = set()
    for group in groups:
        pairs = [(int(model), gid) for model, gids in group.global_ids.items() for gid in gids]
        ids = tools.existing_object_ids(connection, group.object_ids)
        ids += tools.object_ids_by_global_id(connection, pairs)
        for object_id in ids:
            if object_id not in seen:
                seen.add(object_id)
                found.append((group.label, object_id))
    return found


def _selection(
    found: Found,
    objects: list[tools.SelectedObject],
    wanted: Request,
    *,
    note: str | None,
    truncated: bool = False,
) -> Selection:
    by_id = {obj.object_id: obj for obj in objects}
    groups: dict[str | None, dict[int, list[str]]] = {}
    counts: dict[str | None, int] = {}
    for label, object_id in found:
        obj = by_id[object_id]  # read in the same transaction as ``found``
        groups.setdefault(label, {}).setdefault(obj.model_id, []).append(obj.global_id)
        counts[label] = counts.get(label, 0) + 1
    storeys = {obj.storey_id for obj in objects if obj.kind != "spatial"}
    one = next(iter(storeys)) if len(storeys) == 1 else None
    storey = next((obj for obj in objects if one is not None and obj.storey_id == one), None)
    return Selection(
        groups=[
            Group(
                label=label,
                colour=_colour(wanted, label, index),
                count=counts[label],
                models=[
                    ModelObjects(model_id=model_id, global_ids=global_ids)
                    for model_id, global_ids in models.items()
                ],
            )
            for index, (label, models) in enumerate(groups.items())
        ],
        count=len(objects),
        truncated=truncated,
        openings=sum(1 for obj in objects if obj.kind == "opening"),
        not_drawn=sum(1 for obj in objects if obj.kind == "spatial"),
        spaces=sum(1 for obj in objects if obj.kind in LAYERS),
        layers=sorted({LAYERS[obj.kind] for obj in objects if obj.kind in LAYERS}),
        storey=None if storey is None else storey.storey_name,
        storey_elevation_m=None if storey is None else storey.storey_elevation_m,
        zoom=wanted.zoom,
        note=note,
    )


FIXED_GROUPS: dict[Query, tuple[str, ...]] = {
    "space_schedule": tools.SPACE_GROUPS,
    "compare_models": compare.CHANGE_GROUPS,
}
"""The tools whose groups are fixed kinds: each kind keeps its colour, so that Revit
Areas are the third colour on a model with no copies of rooms too (file E)."""


def _colour(wanted: Request, label: str | None, index: int) -> str:
    if wanted.query == "data_quality_report" and label in SEVERITY_COLOURS:
        return SEVERITY_COLOURS[label]
    kinds = FIXED_GROUPS.get(wanted.query, ())
    if label in kinds:
        index = kinds.index(label)
    return PALETTE[index % len(PALETTE)]


def _quoted(value: object) -> str:
    return f"'{value}'"


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []
