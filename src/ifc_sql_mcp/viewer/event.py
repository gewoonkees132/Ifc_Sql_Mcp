"""The feed's event, version 1: one tool call of ``ifc-sql serve`` (ADR 14, decision 4).

With ``IFC_SQL_VIEWER_FEED=1``, ``serve`` sends one PostgreSQL notification per tool call
on :data:`CHANNEL`, and every ``ifc-sql view`` on the database receives it. The payload
is the JSON of :class:`Event`, at most :data:`MAX_PAYLOAD_BYTES` bytes of UTF-8:
PostgreSQL refuses a payload of 8,000 bytes or more (ADR 14, P7).

:func:`encode` is ``serve``'s side. It keeps the event within the limit in this order:
each text argument is clipped to :data:`MAX_TEXT_BYTES` (and named in ``clipped``), the
selection's ids are capped at :data:`MAX_IDS` and :data:`MAX_IDS_BYTES`, and the SQL text
of ``run_sql`` gets what is left. The ids come first because they make the selection;
the SQL text is only shown.

:func:`decode` is ``view``'s side, and trusts nothing: any login to the database can send
a notification on the channel. It refuses an unknown version, a field of the wrong type,
a tool that does not exist, an id that is not a positive integer, a GlobalId that is not
one, and more than :data:`MAX_IDS` ids. ``view`` then checks the arguments with the
tools' own limits (``viewer/selection.py``) and never runs SQL that came from an event:
the SQL text is shown, never executed.
"""

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ifc_sql_mcp.db.schema import GLOBAL_ID_PATTERN
from ifc_sql_mcp.server.values import JsonValue

CHANNEL = "ifc_viewer_feed"
"""The notification channel; every ``view`` on the database listens to it."""

VERSION = 1

MAX_PAYLOAD_BYTES = 7_999
"""The longest payload PostgreSQL delivers, in bytes of UTF-8 (ADR 14, P7)."""

MAX_IDS = 500
"""Most ids an event carries, in all its groups together."""

MAX_IDS_BYTES = 4_800
"""Most bytes of JSON the ids take, so that a clipped SQL text still fits beside them."""

MAX_TEXT_BYTES = 200
"""Most bytes of JSON one text argument takes; a longer one is clipped and named."""

MAX_LABEL_BYTES = 60
"""Most bytes of JSON a group's label takes."""

TOOLS = (
    "list_models",
    "list_storeys",
    "find_elements",
    "get_element",
    "quantity_takeoff",
    "space_schedule",
    "schedule",
    "data_quality_report",
    "compare_models",
    "describe_schema",
    "run_sql",
)
"""The eleven tools of ``ifc-sql serve``."""

type ToolName = Literal[
    "list_models",
    "list_storeys",
    "find_elements",
    "get_element",
    "quantity_takeoff",
    "space_schedule",
    "schedule",
    "data_quality_report",
    "compare_models",
    "describe_schema",
    "run_sql",
]

type Outcome = Literal[
    "ok", "invalid", "refused", "timeout", "database_error", "unavailable", "error"
]
"""How a call ended, as ``serve`` logs it (ADR 6, decision 10)."""

IDS_TOOLS = ("run_sql", "get_element")
"""The tools whose result holds the selection itself, and so whose events carry ids."""

_GLOBAL_ID = re.compile(GLOBAL_ID_PATTERN)
_MODEL_KEY = re.compile(r"[1-9][0-9]{0,17}")

type GlobalId = Annotated[str, Field(pattern=GLOBAL_ID_PATTERN)]
type PositiveId = Annotated[int, Field(ge=1)]


class EventRejectedError(ValueError):
    """A notification that is no event ``view`` accepts; the message says why, never what."""


# --------------------------------------------------------------------------- the format


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Source(_Strict):
    """The ``serve`` process that sent the event."""

    pid: PositiveId
    started_at: Annotated[str, Field(max_length=40)]


class SqlText(_Strict):
    """The query of a ``run_sql`` call: its text, clipped, its length and fingerprint."""

    text: str = Field(description="The query, clipped to what fits in the event.")
    chars: Annotated[int, Field(ge=0)] = Field(description="Characters of the whole query.")
    sha256_16: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")] = Field(
        description="The first 16 hex digits of its SHA-256, as serve's log line has them."
    )


class IdGroup(_Strict):
    """Objects of one group of the selection: by object id, or by model and GlobalId."""

    label: Annotated[str, Field(max_length=200)] | None = None
    object_ids: list[PositiveId] = Field(default_factory=list)
    global_ids: dict[str, list[GlobalId]] = Field(
        default_factory=dict, description="By model id, as text: GlobalIds are unique per model."
    )

    @model_validator(mode="after")
    def _model_ids(self) -> "IdGroup":
        for key in self.global_ids:
            if not _MODEL_KEY.fullmatch(key):
                raise ValueError("a model id is not a positive integer")
        return self

    def size(self) -> int:
        """How many ids the group holds."""
        return len(self.object_ids) + sum(len(ids) for ids in self.global_ids.values())


class Ids(_Strict):
    """The selection of a ``run_sql`` or ``get_element`` call, as its result held it."""

    groups: list[IdGroup]
    truncated: bool = Field(description="True when the result held more ids than fit.")

    @model_validator(mode="after")
    def _at_most(self) -> "Ids":
        if sum(group.size() for group in self.groups) > MAX_IDS:
            raise ValueError(f"more than {MAX_IDS} ids")
        return self


class Event(_Strict):
    """One tool call of ``ifc-sql serve``, as the feed carries it."""

    v: Literal[1]
    src: Source
    n: PositiveId = Field(description="The call's number in its serve process.")
    at: Annotated[str, Field(max_length=40)] = Field(description="When it ended, ISO 8601, UTC.")
    tool: ToolName
    arguments: dict[str, JsonValue]
    clipped: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, description="Arguments whose text was clipped to fit."
    )
    outcome: Outcome
    seconds: Annotated[float, Field(ge=0)]
    rows: Annotated[int, Field(ge=0)] | None = None
    sql: SqlText | None = None
    ids: Ids | None = None

    @model_validator(mode="after")
    def _ids_where_they_belong(self) -> "Event":
        if self.ids is not None and (self.tool not in IDS_TOOLS or self.outcome != "ok"):
            raise ValueError("ids on an event whose result holds no selection")
        if self.sql is not None and self.tool != "run_sql":
            raise ValueError("sql on an event of another tool than run_sql")
        return self


# --------------------------------------------------------------------------- serve's side


@dataclass(frozen=True, slots=True)
class Selected:
    """The ids a tool's result holds, for :func:`encode`, before any cap.

    Attributes:
        label: The group's label, e.g. the first column of a ``run_sql`` row.
        object_ids: Objects by ``object_id``.
        global_ids: Objects by model id and GlobalId.
    """

    label: str | None = None
    object_ids: tuple[int, ...] = ()
    global_ids: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Call:
    """What ``serve`` knows of one call when it publishes it.

    Attributes:
        source: The ``serve`` process.
        n: The call's number in that process, from 1.
        at: When the call ended.
        tool: The tool's name.
        arguments: The arguments as the tool received them, without ``run_sql``'s text.
        outcome: How the call ended.
        seconds: How long it took.
        rows: What the call's log line counts as its rows, for a call that succeeded.
        sql: ``run_sql``'s query.
        selected: The ids the result holds, for ``run_sql`` and ``get_element``; ``None``
            also for a ``run_sql`` whose rows hold no id column.
    """

    source: Source
    n: int
    at: datetime
    tool: str
    arguments: dict[str, JsonValue]
    outcome: str
    seconds: float
    rows: int | None = None
    sql: str | None = None
    selected: Sequence[Selected] | None = field(default=None)


def size(event: Event) -> int:
    """The bytes of UTF-8 an event's payload takes."""
    return len(event.model_dump_json().encode("utf-8"))


def encode(call: Call) -> str:
    """Return the payload of ``call``'s event, at most :data:`MAX_PAYLOAD_BYTES` bytes."""
    arguments, clipped = _arguments(call.arguments)
    sql = None
    if call.sql is not None:
        fingerprint = hashlib.sha256(call.sql.encode("utf-8", "surrogatepass")).hexdigest()
        sql = {"text": "", "chars": len(call.sql), "sha256_16": fingerprint[:16]}
    fields: dict[str, object] = {
        "v": VERSION,
        "src": call.source,
        "n": call.n,
        "at": call.at.isoformat(timespec="milliseconds"),
        "tool": call.tool,
        "arguments": arguments,
        "clipped": clipped,
        "outcome": call.outcome,
        "seconds": round(call.seconds, 3),
        "rows": call.rows,
        "sql": sql,
    }
    event = Event.model_validate(fields)
    if size(event) > MAX_PAYLOAD_BYTES:
        # Never within the tools' own limits; a guard, so that no payload is refused.
        event = event.model_copy(update={"arguments": {}, "clipped": sorted(call.arguments)})
    if call.selected is not None:
        event = _with_ids(event, call.selected)
    if call.sql is not None and event.sql is not None:
        room = MAX_PAYLOAD_BYTES - size(event)
        whole = _safe(call.sql)
        kept = _largest(len(whole), lambda n: _json_bytes(whole[:n]) - 2 <= room)
        event = event.model_copy(
            update={"sql": event.sql.model_copy(update={"text": whole[:kept]})}
        )
    return event.model_dump_json()


def decode(payload: str) -> Event:
    """Return the event a notification carries.

    Raises:
        EventRejectedError: It is no event of this version, or breaks one of its limits.
            The message names what is wrong, never the payload's text.
    """
    if len(payload.encode("utf-8", "replace")) > MAX_PAYLOAD_BYTES:
        raise EventRejectedError("longer than an event can be")
    try:
        raw = json.loads(payload)
    except ValueError as exc:
        raise EventRejectedError("not JSON") from exc
    if not isinstance(raw, dict):
        raise EventRejectedError("not a JSON object")
    version = raw.get("v")
    if type(version) is not int or version != VERSION:
        raise EventRejectedError("an unknown version")
    try:
        return Event.model_validate_json(payload)
    except ValidationError as exc:
        # Only the field's name, one of the model's own: the rest of the path can hold
        # the payload's own keys.
        where = next((str(part) for part in exc.errors()[0]["loc"]), "the event")
        raise EventRejectedError(f"invalid at {where}") from exc


def selected_ids(
    column_names: Sequence[str], rows: Sequence[Sequence[object]]
) -> list[Selected] | None:
    """The ids a ``run_sql`` result holds, from its columns' names (ADR 14, decision 4).

    In order of precedence:

    * an ``object_ids`` array column: one group per row, labelled by the row's first column;
    * an ``object_id`` column: one group of every row's;
    * a ``global_ids`` array column next to a ``model_id`` column: one group per row;
    * a ``global_id`` column next to a ``model_id`` column: one group of every row's.

    GlobalIds are unique per model only (files A and D share them), hence ``model_id``.
    Values that are not positive integers, or not GlobalIds, are left out; so are rows
    whose array is NULL. No such column: ``None``, since the query is not about objects
    (a count, say), and the page leaves what it lit as it is; an id column that holds
    none lights nothing.
    """
    names = [name.lower() for name in column_names]

    def label(row: Sequence[object], used: int) -> str | None:
        if used == 0 or row[0] is None:
            return None
        return str(row[0])

    if "object_ids" in names:
        at = names.index("object_ids")
        return [
            Selected(label=label(row, at), object_ids=tuple(_object_ids(cell)))
            for row in rows
            if isinstance(cell := row[at], list)
        ]
    if "object_id" in names:
        at = names.index("object_id")
        return [Selected(object_ids=tuple(_object_ids([row[at] for row in rows])))]
    if "model_id" not in names:
        return None
    model = names.index("model_id")
    if "global_ids" in names:
        at = names.index("global_ids")
        return [
            Selected(label=label(row, at), global_ids=tuple(_global_ids(row[model], cell)))
            for row in rows
            if isinstance(cell := row[at], list)
        ]
    if "global_id" in names:
        at = names.index("global_id")
        pairs = [pair for row in rows for pair in _global_ids(row[model], [row[at]])]
        return [Selected(global_ids=tuple(pairs))]
    return None


def _object_ids(values: list[object]) -> list[int]:
    return [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v > 0]


def _global_ids(model_id: object, values: list[object]) -> list[tuple[int, str]]:
    if not isinstance(model_id, int) or isinstance(model_id, bool) or model_id < 1:
        return []
    return [(model_id, v) for v in values if isinstance(v, str) and _GLOBAL_ID.fullmatch(v)]


# --------------------------------------------------------------------------- fitting


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _safe(text: str) -> str:
    """``text`` as UTF-8 can carry it: a lone surrogate, which JSON allows, becomes '?'."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _largest(high: int, fits: Callable[[int], bool]) -> int:
    """The largest ``n`` from 0 to ``high`` that ``fits``, which must hold for 0 and shrink."""
    low = 0
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def _clip(text: str, limit: int) -> tuple[str, bool]:
    """``text`` within ``limit`` bytes of JSON, and whether it was cut."""
    whole = _safe(text)
    kept = whole[: _largest(len(whole), lambda n: _json_bytes(whole[:n]) <= limit)]
    return kept, kept != text


def _arguments(arguments: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], list[str]]:
    """The arguments with every text clipped to :data:`MAX_TEXT_BYTES`; the names clipped."""
    kept: dict[str, JsonValue] = {}
    clipped: list[str] = []
    for name, value in arguments.items():
        if isinstance(value, str):
            kept[name], cut = _clip(value, MAX_TEXT_BYTES)
        elif isinstance(value, list):
            items = [
                _clip(item, MAX_TEXT_BYTES) if isinstance(item, str) else (item, False)
                for item in value
            ]
            kept[name] = [item for item, _ in items]
            cut = any(was_cut for _, was_cut in items)
        else:
            kept[name], cut = value, False
        if cut:
            clipped.append(name)
    return kept, clipped


def _with_ids(base: Event, selected: Sequence[Selected]) -> Event:
    """``base`` with as many of ``selected``'s ids as fit: MAX_IDS, and MAX_IDS_BYTES."""
    budget = min(MAX_IDS_BYTES, MAX_PAYLOAD_BYTES - size(base))
    flat: list[tuple[int, int | tuple[int, str]]] = []
    for index, group in enumerate(selected):
        flat.extend((index, object_id) for object_id in group.object_ids)
        flat.extend((index, pair) for pair in group.global_ids)

    def ids(count: int) -> Ids:
        groups: dict[int, tuple[list[int], dict[str, list[str]]]] = {}
        for index, item in flat[:count]:
            object_ids, global_ids = groups.setdefault(index, ([], {}))
            if isinstance(item, int):
                object_ids.append(item)
            else:
                global_ids.setdefault(str(item[0]), []).append(item[1])
        return Ids(
            groups=[
                IdGroup(
                    label=_label(selected[index].label),
                    object_ids=object_ids,
                    global_ids=global_ids,
                )
                for index, (object_ids, global_ids) in groups.items()
            ],
            truncated=count < len(flat),
        )

    empty = size(base)

    def fits(count: int) -> bool:
        return size(base.model_copy(update={"ids": ids(count)})) - empty <= budget

    return base.model_copy(update={"ids": ids(_largest(min(len(flat), MAX_IDS), fits))})


def _label(label: str | None) -> str | None:
    return None if label is None else _clip(label, MAX_LABEL_BYTES)[0]
