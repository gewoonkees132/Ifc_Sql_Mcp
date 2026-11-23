"""``serve``'s side of the viewer's feed: one notification per call (ADR 14, decision 4).

The server runs in-process on the MCP test database, as ``ifc_reader``; a session of the
test LISTENs on the channel, so every event below went through PostgreSQL.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from mcp_client import call, call_error, connect
from notifications import listening
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine, notify
from ifc_sql_mcp.server.app import RUN_SQL_VIEWER, Limits, _Calls, build_server
from ifc_sql_mcp.server.tools import list_storeys
from ifc_sql_mcp.viewer.event import (
    CHANNEL,
    MAX_PAYLOAD_BYTES,
    Call,
    Selected,
    Source,
    decode,
    encode,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def sessions(mcp_settings: Settings) -> Iterator[tuple[Engine, list[int]]]:
    """The server's own engine, counting each session it opens."""
    engine = make_reader_engine(mcp_settings)
    opened: list[int] = []
    event.listen(engine, "connect", lambda *_: opened.append(1))
    yield engine, opened
    engine.dispose()


async def test_with_the_feed_off_a_call_sends_nothing_and_opens_one_session(
    mcp_settings: Settings, sessions: tuple[Engine, list[int]]
) -> None:
    engine, opened = sessions
    with listening(mcp_settings) as received:
        async with connect(build_server(engine)) as client:
            await call(client, "list_storeys", model_id=1)
        assert received(seconds=1.0) == []
    assert len(opened) == 1


async def test_with_the_feed_on_a_call_is_published_from_a_second_session(
    mcp_settings: Settings, sessions: tuple[Engine, list[int]]
) -> None:
    engine, opened = sessions
    with listening(mcp_settings) as received:
        async with connect(build_server(engine, feed=True)) as client:
            await call(client, "list_storeys", model_id=1)
            await call(client, "find_elements", model_id=1, ifc_class="IfcWall", limit=2)
        first, second = received(count=2)

    assert len(opened) == 4  # each call's session, and one for its notification
    assert first["tool"] == "list_storeys"
    assert (first["arguments"], first["outcome"], first["rows"], first["ids"]) == (
        {"model_id": 1},
        "ok",
        3,
        None,
    )
    assert (second["n"], second["src"]) == (first["n"] + 1, first["src"])
    assert second["arguments"] == {
        "model_id": 1,
        "ifc_class": "IfcWall",
        "storey_name": None,
        "revit_family": None,
        "revit_type": None,
        "name_contains": None,
        "limit": 2,
        "offset": 0,
    }
    # What view accepts.
    assert [decode(json.dumps(event)).tool for event in (first, second)] == [
        "list_storeys",
        "find_elements",
    ]


@pytest.mark.parametrize(
    ("tool", "arguments", "outcome"),
    [
        ("run_sql", {"sql": "DELETE FROM ifc.property"}, "refused"),
        ("list_storeys", {"model_id": 99}, "invalid"),
        ("run_sql", {"sql": "SELECT 1 / 0"}, "database_error"),
        ("get_element", {"model_id": 1, "global_id": "0000000000000000000000"}, "invalid"),
    ],
)
async def test_a_call_that_fails_is_published_with_its_outcome_and_no_ids(
    mcp_engine: Engine,
    mcp_settings: Settings,
    tool: str,
    arguments: dict[str, object],
    outcome: str,
) -> None:
    with listening(mcp_settings) as received:
        async with connect(build_server(mcp_engine, feed=True)) as client:
            await call_error(client, tool, **arguments)
        [published] = received(count=1)

    assert (published["tool"], published["outcome"], published["rows"]) == (tool, outcome, None)
    assert published["ids"] is None
    if tool == "run_sql":
        assert published["arguments"] == {"max_rows": 100}
        assert published["sql"]["text"] == arguments["sql"]
        assert published["sql"]["chars"] == len(str(arguments["sql"]))


async def test_a_notification_that_fails_changes_no_result(
    mcp_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _Calls(mcp_engine, Limits(), feed=True)
    assert calls.feed is not None
    unreachable = create_engine(
        "postgresql+psycopg://ifc_reader:x@127.0.0.1:1/ifc",
        poolclass=NullPool,
        connect_args={"connect_timeout": 2},
    )
    calls.feed.engine = unreachable
    try:
        result = calls.tool(
            "list_storeys",
            lambda connection: list_storeys(connection, 1),
            arguments={"model_id": 1},
        )
    finally:
        unreachable.dispose()

    assert not result.is_error
    assert result.structured_content is not None
    assert len(result.structured_content["storeys"]) == 3
    [warning] = [record for record in caplog.records if record.levelname == "WARNING"]
    assert warning.getMessage() == "viewer feed: call not published"
    assert warning.tool == "list_storeys"  # type: ignore[attr-defined]
    # Why, without the payload: refused on Linux, a timeout on Windows.
    error: str = warning.error  # type: ignore[attr-defined]
    assert error.startswith(("OperationalError: ", "ConnectionTimeout: "))
    assert "list_storeys" not in error


async def test_get_element_carries_the_object(mcp_engine: Engine, mcp_settings: Settings) -> None:
    with listening(mcp_settings) as received:
        async with connect(build_server(mcp_engine, feed=True)) as client:
            detail = await call(client, "get_element", object_id=3)
        [published] = received(count=1)

    assert published["ids"] == {
        "groups": [{"label": None, "object_ids": [detail["object_id"]], "global_ids": {}}],
        "truncated": False,
    }


@pytest.mark.parametrize(
    ("sql", "groups"),
    [
        (
            "SELECT object_id, name FROM object WHERE model_id = 1 AND object_id <= 3 "
            "ORDER BY object_id",
            [{"label": None, "object_ids": [1, 2, 3], "global_ids": {}}],
        ),
        (
            "SELECT model_id, array_agg(object_id ORDER BY object_id) AS object_ids FROM object "
            "WHERE object_id <= 3 GROUP BY model_id",
            [{"label": "1", "object_ids": [1, 2, 3], "global_ids": {}}],
        ),
        (
            "SELECT model_id, global_id FROM object WHERE object_id IN (1, 2) ORDER BY object_id",
            [{"label": None, "object_ids": [], "global_ids": {"1": ["<1>", "<2>"]}}],
        ),
    ],
    ids=["object_id", "object_ids", "global_id"],
)
async def test_run_sql_carries_the_ids_its_columns_name(
    mcp_engine: Engine, mcp_settings: Settings, sql: str, groups: list[dict[str, object]]
) -> None:
    with listening(mcp_settings) as received:
        async with connect(build_server(mcp_engine, feed=True)) as client:
            result = await call(client, "run_sql", sql=sql)
        [published] = received(count=1)

    if "global_id FROM" in sql:
        first, second = (row[1] for row in result["rows"])
        groups = [{"label": None, "object_ids": [], "global_ids": {"1": [first, second]}}]
    assert published["ids"] == {"groups": groups, "truncated": False}


@pytest.mark.parametrize(
    ("sql", "ids"),
    [
        # An aggregate holds no id column: no ids at all, and view leaves the view as it is.
        ("SELECT ifc_class, count(*) AS n FROM object GROUP BY 1", None),
        # An id column that found nothing: ids, none of them, and view lights nothing.
        ("SELECT object_id FROM object WHERE false", {"groups": [], "truncated": False}),
    ],
)
async def test_run_sql_without_an_id_column_carries_no_ids(
    mcp_engine: Engine, mcp_settings: Settings, sql: str, ids: object
) -> None:
    with listening(mcp_settings) as received:
        async with connect(build_server(mcp_engine, feed=True)) as client:
            await call(client, "run_sql", sql=sql)
        [published] = received(count=1)

    assert published["ids"] == ids


async def test_run_sql_carries_at_most_500_ids(mcp_engine: Engine, mcp_settings: Settings) -> None:
    sql = (
        "SELECT array_agg(a.object_id ORDER BY a.object_id, b.object_id) AS object_ids "
        "FROM object AS a CROSS JOIN object AS b"
    )
    with listening(mcp_settings) as received:
        async with connect(build_server(mcp_engine, feed=True)) as client:
            result = await call(client, "run_sql", sql=sql)
        [published] = received(count=1)

    # The client got the array as clipped text; the event's ids come from the whole array.
    assert isinstance(result["rows"][0][0], str)
    [group] = published["ids"]["groups"]
    assert len(group["object_ids"]) == 500
    assert published["ids"]["truncated"] is True


async def test_the_description_of_run_sql_says_how_to_light_rows(mcp_engine: Engine) -> None:
    async with connect(build_server(mcp_engine)) as client:
        off = {tool.name: tool for tool in (await client.list_tools()).tools}
    async with connect(build_server(mcp_engine, feed=True)) as client:
        on = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert len(on) == len(off) == 11
    assert RUN_SQL_VIEWER not in (off["run_sql"].description or "")
    assert (on["run_sql"].description or "").count(RUN_SQL_VIEWER) == 1


def test_postgresql_delivers_the_worst_case_as_ifc_reader(
    mcp_engine: Engine, mcp_settings: Settings
) -> None:
    payload = encode(
        Call(
            source=Source(pid=1, started_at="2026-11-23T10:00:00+00:00"),
            n=1,
            at=datetime.now(UTC),
            tool="run_sql",
            arguments={"max_rows": 500},
            outcome="ok",
            seconds=0.1,
            rows=500,
            sql="é" * 20_000,
            selected=[Selected(object_ids=tuple(range(9_999_500, 10_000_000)))],
        )
    )
    # A two-byte character cannot fill an odd last byte.
    assert MAX_PAYLOAD_BYTES - 1 <= len(payload.encode("utf-8")) <= MAX_PAYLOAD_BYTES

    with listening(mcp_settings) as received:
        notify(mcp_engine, CHANNEL, payload)
        [arrived] = received(count=1)

    assert len(arrived["ids"]["groups"][0]["object_ids"]) == 500
