"""``view``'s side of the feed, against PostgreSQL: the listener, the feed and its routes.

The listener connects as ``ifc_reader``, as ``ifc-sql view`` does, on the view test
database. Events are sent with ``db.engine.notify`` through the MCP server's own engine,
as ``serve`` sends them; forged ones with plain ``pg_notify``, as anyone with a login can.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine, notify
from ifc_sql_mcp.server.app import argument_checks, build_server
from ifc_sql_mcp.viewer.event import CHANNEL, Call, Selected, Source, encode
from ifc_sql_mcp.viewer.feed import Feed, Resolver
from ifc_sql_mcp.viewer.http import create_app
from ifc_sql_mcp.viewer.listener import Listener, listen_connection
from ifc_sql_mcp.viewer.reading import Reader
from ifc_sql_mcp.viewer.security import new_token

SERVE = Source(pid=4242, started_at="2026-11-23T10:00:00+00:00")
PORT = 8766
TOKEN = new_token()


def _payload(tool: str = "list_storeys", n: int = 1, **fields: object) -> str:
    arguments = fields.pop("arguments", {"model_id": 1})
    return encode(
        Call(
            source=SERVE,
            n=n,
            at=datetime.now(UTC),
            tool=tool,
            arguments=arguments,  # type: ignore[arg-type]
            outcome="ok",
            seconds=0.01,
            rows=1,
            **fields,  # type: ignore[arg-type]
        )
    )


def _until(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


@pytest.fixture
def serve(view_settings: Settings) -> Iterator[Engine]:
    """The MCP server's engine on the view database: what serve notifies through."""
    engine = make_reader_engine(view_settings)
    yield engine
    engine.dispose()


@pytest.fixture
def owner(view_settings: Settings) -> Iterator[Engine]:
    engine = create_engine(
        view_settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    yield engine
    engine.dispose()


@pytest.fixture
def listening(view_engine: Engine) -> Iterator[tuple[Feed, Listener]]:
    reader = Reader(view_engine)
    feed = Feed(Resolver(reader, argument_checks(build_server(view_engine))), database="test")
    listener = Listener(lambda: listen_connection(view_engine), feed, backoff=(0.05,))
    listener.start()
    _until(lambda: feed.status().state == "live")
    yield feed, listener
    listener.stop()


def test_the_listener_listens_as_ifc_reader_and_names_its_session(
    listening: tuple[Feed, Listener], owner: Engine
) -> None:
    with owner.connect() as connection:
        sessions = connection.execute(
            text(
                "SELECT usename, application_name FROM pg_stat_activity WHERE "
                "application_name = 'ifc-sql-mcp-viewer-feed' AND datname = current_database()"
            )
        ).all()

    assert [tuple(row) for row in sessions] == [("ifc_reader", "ifc-sql-mcp-viewer-feed")]


def test_events_arrive_in_order(listening: tuple[Feed, Listener], serve: Engine) -> None:
    feed, _ = listening

    for n in range(1, 21):
        notify(serve, CHANNEL, _payload(n=n))
    _until(lambda: len(feed.entries()) == 20)

    assert [(entry.id, entry.n) for entry in feed.entries()] == [(n, n) for n in range(1, 21)]
    assert feed.status().received == 20


def test_a_selection_is_resolved_when_its_event_arrives(
    listening: tuple[Feed, Listener], serve: Engine
) -> None:
    feed, _ = listening

    notify(
        serve, CHANNEL, _payload("find_elements", arguments={"model_id": 1, "ifc_class": "IfcWall"})
    )
    _until(lambda: len(feed.entries()) == 1)

    [entry] = feed.entries()
    assert entry.selection is not None
    assert entry.selection.count == 1
    assert entry.summary == "find_elements: IfcWall, model 1"


@pytest.mark.parametrize(
    ("forged", "reason"),
    [
        (json.dumps({**json.loads(_payload()), "v": 2}), "an unknown version"),
        (
            json.dumps(
                {
                    **json.loads(_payload("get_element", arguments={"object_id": 1})),
                    "ids": {"groups": [{"object_ids": list(range(1, 502))}], "truncated": False},
                }
            ),
            "invalid at ids",
        ),
        (
            json.dumps(
                {
                    **json.loads(_payload("get_element", arguments={"object_id": 1})),
                    "ids": {
                        "groups": [{"global_ids": {"1": ["x' OR '1'='1' --        "]}}],
                        "truncated": False,
                    },
                }
            ),
            "invalid at ids",
        ),
        (
            _payload("run_sql", arguments={"max_rows": 5, "sql": "DELETE FROM ifc.model"}, sql="x"),
            "invalid arguments",
        ),
    ],
    ids=["version", "oversized ids", "global id", "sql"],
)
def test_a_forged_event_is_dropped(
    listening: tuple[Feed, Listener], owner: Engine, forged: str, reason: str
) -> None:
    feed, _ = listening

    with owner.connect() as connection:  # any login can notify: here the owner's
        connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": forged})
        connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": _payload(n=2)})
    _until(lambda: len(feed.entries()) == 1)

    assert [entry.n for entry in feed.entries()] == [2]
    assert feed.status().dropped == {reason: 1}
    with owner.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ifc.model")).scalar_one() == 3


def test_the_listener_reconnects_after_its_session_is_ended(
    listening: tuple[Feed, Listener],
    owner: Engine,
    serve: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    feed, listener = listening

    with owner.connect() as connection:
        ended = (
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
                    "application_name = 'ifc-sql-mcp-viewer-feed' AND datname = current_database()"
                )
            )
            .scalars()
            .all()
        )
    assert ended == [True]
    _until(lambda: listener.connections == 2 and feed.status().state == "live")
    notify(serve, CHANNEL, _payload(n=5))
    _until(lambda: len(feed.entries()) == 1)

    assert feed.entries()[0].n == 5
    [warning] = [r for r in caplog.records if r.getMessage() == "viewer feed interrupted"]
    assert warning.levelname == "WARNING"


def test_a_listener_that_cannot_connect_keeps_trying_until_stopped(view_engine: Engine) -> None:
    unreachable = make_reader_engine(
        Settings.from_env({"IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc@127.0.0.1:1/ifc"})
    )
    feed = Feed(Resolver(Reader(unreachable), argument_checks(build_server(unreachable))))
    listener = Listener(lambda: listen_connection(unreachable), feed, backoff=(0.01, 0.02))
    listener.start()
    _until(lambda: feed.status().state == "interrupted", seconds=30)
    listener.stop()
    unreachable.dispose()

    status = feed.status()
    assert (status.state, status.message) == ("off", "ifc-sql view stopped listening")
    assert listener.connections == 0


# --------------------------------------------------------------------------- the routes


def _client(view_engine: Engine, feed: Feed) -> TestClient:
    return TestClient(
        create_app(view_engine, TOKEN, port=PORT, feed=feed, reader=feed.resolver.reader),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def _stream(body: str) -> list[tuple[str, str | None, dict[str, object]]]:
    """The events of an SSE body: (event, id, data)."""
    found = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        found.append((fields["event"], fields.get("id"), json.loads(fields["data"])))
    return found


def test_the_feed_route_lists_the_status_and_the_calls(
    listening: tuple[Feed, Listener], serve: Engine, view_engine: Engine
) -> None:
    feed, _ = listening
    notify(serve, CHANNEL, _payload(n=1))
    notify(
        serve,
        CHANNEL,
        _payload(
            "get_element", n=2, arguments={"object_id": 3}, selected=[Selected(object_ids=(3,))]
        ),
    )
    _until(lambda: len(feed.entries()) == 2)

    with _client(view_engine, feed) as client:
        response = client.get("/api/feed")

    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"]["state"] == "live"
    assert [entry["tool"] for entry in body["entries"]] == ["list_storeys", "get_element"]
    selection = body["entries"][1]["selection"]
    assert (selection["count"], selection["zoom"]) == (1, True)


def test_the_stream_sends_the_status_then_every_call_and_resumes_after_the_last_seen(
    listening: tuple[Feed, Listener], serve: Engine, view_engine: Engine
) -> None:
    feed, _ = listening
    for n in range(1, 5):
        notify(serve, CHANNEL, _payload(n=n))
    _until(lambda: len(feed.entries()) == 4)
    feed.close()  # the streams end once they have sent what there is

    with _client(view_engine, feed) as client:
        whole = _stream(client.get("/api/events").text)
        resumed = _stream(client.get("/api/events", headers={"Last-Event-ID": "2"}).text)
        nonsense = _stream(client.get("/api/events", headers={"Last-Event-ID": "x"}).text)

    assert [(event, id_) for event, id_, _ in whole] == [
        ("status", None),
        ("call", "1"),
        ("call", "2"),
        ("call", "3"),
        ("call", "4"),
    ]
    assert whole[0][2]["state"] == "live"
    assert [(event, id_) for event, id_, _ in resumed] == [
        ("status", None),
        ("call", "3"),
        ("call", "4"),
    ]
    assert len(nonsense) == 5


def test_an_open_stream_waits_for_calls_and_ends_with_the_feed(
    listening: tuple[Feed, Listener], serve: Engine, view_engine: Engine
) -> None:
    feed, _ = listening
    later = threading.Timer(0.6, lambda: (notify(serve, CHANNEL, _payload(n=1)), _closing(feed)))
    later.start()

    with _client(view_engine, feed) as client:
        events = _stream(client.get("/api/events").text)
    later.join()

    assert [event for event, _, _ in events][:2] == ["status", "call"]


def _closing(feed: Feed) -> None:
    _until(lambda: len(feed.entries()) == 1)
    feed.close()
