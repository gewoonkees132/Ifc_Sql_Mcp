"""The feed's event (``viewer/event.py``): what serve sends, and what view accepts."""

import json
from datetime import UTC, datetime

import pytest

from ifc_sql_mcp.viewer.event import (
    MAX_IDS,
    MAX_IDS_BYTES,
    MAX_PAYLOAD_BYTES,
    MAX_TEXT_BYTES,
    Call,
    Event,
    EventRejectedError,
    Selected,
    Source,
    decode,
    encode,
    selected_ids,
    size,
)

SOURCE = Source(pid=4242, started_at="2026-11-23T10:00:00+00:00")
AT = datetime(2026, 11, 23, 10, 1, 2, 345000, tzinfo=UTC)
GLOBAL_ID = "0iEHWY1$XA8eQeeULq4jDb"


def _call(**fields: object) -> Call:
    defaults: dict[str, object] = {
        "source": SOURCE,
        "n": 1,
        "at": AT,
        "tool": "list_models",
        "arguments": {},
        "outcome": "ok",
        "seconds": 0.0123456,
        "rows": 4,
    }
    return Call(**{**defaults, **fields})  # type: ignore[arg-type]


def _bytes(payload: str) -> int:
    return len(payload.encode("utf-8"))


# --------------------------------------------------------------------------- the limit


def test_the_worst_case_fits_in_one_notification() -> None:
    """20,000 non-ASCII characters of SQL and 500 object ids of seven digits."""
    ids = tuple(range(9_999_500, 10_000_000))
    payload = encode(
        _call(
            tool="run_sql",
            arguments={"max_rows": 500},
            rows=500,
            sql="é" * 20_000,
            selected=[Selected(object_ids=ids)],
        )
    )

    assert _bytes(payload) <= MAX_PAYLOAD_BYTES == 7_999
    event = decode(payload)
    assert event.ids is not None
    assert event.ids.groups[0].object_ids == list(ids)
    assert event.ids.truncated is False
    assert event.sql is not None
    assert (event.sql.chars, len(event.sql.text)) == (20_000, 1_821)
    assert event.sql.text == "é" * 1_821
    # The figure docs/viewer.md gives: 500 seven-digit ids take 4,076 bytes of the event.
    assert _bytes(event.ids.model_dump_json()) == 4_076


@pytest.mark.parametrize(
    "sql",
    ["😀" * 20_000, '"\\' * 10_000, "\n\x01" * 10_000, "a" * 20_000],
    ids=["astral", "escaped", "control", "ascii"],
)
def test_any_text_is_clipped_to_what_fits(sql: str) -> None:
    payload = encode(_call(tool="run_sql", arguments={"max_rows": 5}, sql=sql))

    # Within one character of the limit: an escaped control character takes 6 bytes.
    assert MAX_PAYLOAD_BYTES - 6 < _bytes(payload) <= MAX_PAYLOAD_BYTES
    event = decode(payload)
    assert event.sql is not None
    assert sql.startswith(event.sql.text)
    assert event.sql.chars == 20_000


def test_a_short_query_is_kept_whole_with_its_fingerprint() -> None:
    event = decode(encode(_call(tool="run_sql", arguments={"max_rows": 5}, sql="SELECT 1")))

    assert event.sql is not None
    assert event.sql.model_dump() == {
        "text": "SELECT 1",
        "chars": 8,
        "sha256_16": "e004ebd5b5532a4b",
    }


def test_a_lone_surrogate_becomes_a_question_mark() -> None:
    event = decode(encode(_call(tool="run_sql", arguments={"max_rows": 5}, sql="SELECT '\ud800'")))

    assert event.sql is not None
    assert event.sql.text == "SELECT '?'"


def test_the_event_names_the_call_and_how_it_ended() -> None:
    payload = encode(
        _call(tool="list_storeys", arguments={"model_id": 3}, outcome="invalid", rows=None)
    )

    assert json.loads(payload) == {
        "v": 1,
        "src": {"pid": 4242, "started_at": "2026-11-23T10:00:00+00:00"},
        "n": 1,
        "at": "2026-11-23T10:01:02.345+00:00",
        "tool": "list_storeys",
        "arguments": {"model_id": 3},
        "clipped": [],
        "outcome": "invalid",
        "seconds": 0.012,
        "rows": None,
        "sql": None,
        "ids": None,
    }


# --------------------------------------------------------------------------- arguments


def test_a_long_text_argument_is_clipped_and_named() -> None:
    event = decode(
        encode(
            _call(
                tool="schedule",
                arguments={
                    "model_id": 1,
                    "ifc_class": "IfcDoor",
                    "fields": ["width_m", "ü" * 300],
                    "storey_name": "x" * 250,
                    "group_by": ["revit_type"],
                },
            )
        )
    )

    assert event.clipped == ["fields", "storey_name"]
    assert event.arguments["ifc_class"] == "IfcDoor"
    assert event.arguments["storey_name"] == "x" * (MAX_TEXT_BYTES - 2)
    fields = event.arguments["fields"]
    assert isinstance(fields, list)
    assert fields == ["width_m", "ü" * ((MAX_TEXT_BYTES - 2) // 2)]


def test_arguments_beyond_any_tool_are_dropped_rather_than_the_event() -> None:
    arguments: dict[str, object] = {f"a{n}": "x" * 190 for n in range(60)}

    payload = encode(_call(tool="find_elements", arguments=arguments))  # type: ignore[arg-type]

    assert _bytes(payload) <= MAX_PAYLOAD_BYTES
    event = decode(payload)
    assert event.arguments == {}
    assert event.clipped == sorted(arguments)


# --------------------------------------------------------------------------- ids


def test_ids_are_capped_at_500() -> None:
    selected = [
        Selected(label="a", object_ids=(1, 2, 3)),
        Selected(object_ids=tuple(range(4, 700))),
    ]

    event = decode(encode(_call(tool="run_sql", sql="SELECT 1", selected=selected)))

    assert event.ids is not None
    assert [group.label for group in event.ids.groups] == ["a", None]
    assert sum(group.size() for group in event.ids.groups) == MAX_IDS
    assert event.ids.groups[1].object_ids[-1] == 500
    assert event.ids.truncated is True


def test_global_ids_are_capped_by_their_bytes() -> None:
    pairs = tuple((2, GLOBAL_ID[:-3] + f"{n:03d}") for n in range(400))

    event = decode(
        encode(_call(tool="run_sql", sql="SELECT 1", selected=[Selected(global_ids=pairs)]))
    )

    assert event.ids is not None
    [group] = event.ids.groups
    kept = group.global_ids["2"]
    assert 150 < len(kept) < 400
    assert kept == [gid for _, gid in pairs[: len(kept)]]
    assert _bytes(event.ids.model_dump_json()) <= MAX_IDS_BYTES
    assert event.ids.truncated is True


def test_a_long_label_is_clipped() -> None:
    event = decode(
        encode(
            _call(
                tool="run_sql", sql="SELECT 1", selected=[Selected(label="L" * 90, object_ids=(7,))]
            )
        )
    )

    assert event.ids is not None
    assert event.ids.groups[0].label == "L" * 58


def test_get_element_carries_its_object() -> None:
    event = decode(encode(_call(tool="get_element", selected=[Selected(object_ids=(23,))])))

    assert event.ids is not None
    assert event.ids.model_dump() == {
        "groups": [{"label": None, "object_ids": [23], "global_ids": {}}],
        "truncated": False,
    }


# --------------------------------------------------------------------------- run_sql's columns


def test_an_object_id_column_is_one_group() -> None:
    assert selected_ids(["revit_type", "object_id"], [("A", 5), ("B", 6), ("C", None)]) == [
        Selected(object_ids=(5, 6))
    ]


def test_an_object_ids_column_is_a_group_per_row_labelled_by_the_first_column() -> None:
    rows = [("Exterior", 12, [1, 2]), (None, 4, [3]), ("Party", 4, None)]

    assert selected_ids(["revit_type", "walls", "OBJECT_IDS"], rows) == [
        Selected(label="Exterior", object_ids=(1, 2)),
        Selected(label=None, object_ids=(3,)),
    ]


def test_an_object_ids_column_first_has_no_label() -> None:
    assert selected_ids(["object_ids", "n"], [([1], 1)]) == [Selected(object_ids=(1,))]


def test_object_ids_win_over_object_id() -> None:
    assert selected_ids(["object_id", "object_ids"], [(1, [2])]) == [
        Selected(label="1", object_ids=(2,))
    ]


def test_only_positive_integers_are_ids() -> None:
    rows = [(1,), (0,), (-3,), (True,), ("7",), (2.0,), (8,)]

    assert selected_ids(["object_id"], rows) == [Selected(object_ids=(1, 8))]


def test_global_ids_need_a_model_id_beside_them() -> None:
    assert selected_ids(["global_id"], [(GLOBAL_ID,)]) is None
    assert selected_ids(["model_id", "global_id"], [(1, GLOBAL_ID), (2, GLOBAL_ID)]) == [
        Selected(global_ids=((1, GLOBAL_ID), (2, GLOBAL_ID)))
    ]


def test_a_global_ids_column_is_a_group_per_row() -> None:
    rows = [("walls", 1, [GLOBAL_ID, "not a GlobalId"]), ("doors", 0, [GLOBAL_ID]), ("x", 1, None)]

    assert selected_ids(["label", "model_id", "global_ids"], rows) == [
        Selected(label="walls", global_ids=((1, GLOBAL_ID),)),
        Selected(label="doors", global_ids=()),
    ]


@pytest.mark.parametrize("model_id", [None, True, "1", -1])
def test_a_model_id_that_is_none_selects_nothing(model_id: object) -> None:
    assert selected_ids(["model_id", "global_id"], [(model_id, GLOBAL_ID)]) == [Selected()]


def test_rows_without_an_id_column_hold_no_selection_at_all() -> None:
    """An aggregate is not about objects: the page leaves what it lit as it is."""
    assert selected_ids(["model_id", "n"], [(1, 2)]) is None
    assert selected_ids(["a"], [(1,)]) is None


def test_an_id_column_without_rows_selects_nothing() -> None:
    """A query about objects that found none: the page lights nothing."""
    assert selected_ids(["object_id"], []) == [Selected()]
    assert selected_ids(["revit_type", "object_ids"], []) == []
    assert selected_ids(["model_id", "global_id"], []) == [Selected()]


def test_a_cell_that_is_no_array_holds_no_ids() -> None:
    assert selected_ids(["model_id", "global_ids"], [(1, GLOBAL_ID)]) == []


# --------------------------------------------------------------------------- view's side


def _event(**changes: object) -> str:
    event = json.loads(
        encode(
            _call(
                tool="get_element",
                arguments={"object_id": 23},
                selected=[Selected(object_ids=(23,))],
            )
        )
    )
    for key, value in changes.items():
        if value is ...:
            del event[key]
        else:
            event[key] = value
    return json.dumps(event, separators=(",", ":"))


def test_an_event_round_trips() -> None:
    event = decode(_event())

    assert isinstance(event, Event)
    assert (event.tool, event.arguments, event.src.pid, event.n) == (
        "get_element",
        {"object_id": 23},
        4242,
        1,
    )
    assert size(event) == _bytes(_event())


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("x" * 8_000, "longer than an event can be"),
        ("{", "not JSON"),
        ("[1]", "not a JSON object"),
        (_event(v=2), "an unknown version"),
        (_event(v=True), "an unknown version"),
        (_event(v="1"), "an unknown version"),
        (_event(v=...), "an unknown version"),
        (_event(tool="drop_table"), "invalid at tool"),
        (_event(src={"pid": 0, "started_at": "x"}), "invalid at src"),
        (_event(n="1"), "invalid at n"),
        (_event(outcome="maybe"), "invalid at outcome"),
        (_event(seconds=-1), "invalid at seconds"),
        (_event(extra=1), "invalid at extra"),
        (_event(tool="list_models"), "invalid at the event"),
        (_event(outcome="error"), "invalid at the event"),
        (
            _event(sql={"text": "SELECT 1", "chars": 8, "sha256_16": "e004ebd5b5532a4b"}),
            "invalid at the event",
        ),
        (
            _event(ids={"groups": [{"object_ids": [-1]}], "truncated": False}),
            "invalid at ids",
        ),
        (
            _event(ids={"groups": [{"object_ids": [True]}], "truncated": False}),
            "invalid at ids",
        ),
        (
            _event(
                ids={
                    "groups": [{"global_ids": {"1": ["'; DROP TABLE x; --  "]}}],
                    "truncated": False,
                }
            ),
            "invalid at ids",
        ),
        (
            _event(ids={"groups": [{"global_ids": {"0": [GLOBAL_ID]}}], "truncated": False}),
            "invalid at ids",
        ),
        (
            _event(ids={"groups": [{"object_ids": list(range(1, 300))}] * 2, "truncated": False}),
            "invalid at ids",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and len(value) < 40 else None,
)
def test_view_refuses_what_is_no_event(payload: str, reason: str) -> None:
    with pytest.raises(EventRejectedError) as raised:
        decode(payload)

    assert str(raised.value) == reason
    assert "DROP" not in str(raised.value)
