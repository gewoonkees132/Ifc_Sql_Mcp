"""The feed ``view`` keeps (``viewer/feed.py``): rate limit, numbering, what it keeps and drops.

The events here light nothing that needs a database (``list_models``, calls that failed),
except where a test wants the database to fail: its engine points at port 1, which
refuses at once.
"""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.server.app import argument_checks, build_server
from ifc_sql_mcp.viewer.event import Call, Source, encode
from ifc_sql_mcp.viewer.feed import (
    BURST,
    KEEP,
    PER_SECOND,
    SELECTIONS_KEPT,
    DemoStep,
    Feed,
    Resolver,
)
from ifc_sql_mcp.viewer.reading import Reader

SERVE = Source(pid=4242, started_at="2026-11-23T10:00:00+00:00")
OTHER = Source(pid=99, started_at="2026-11-23T09:00:00+00:00")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    settings = Settings.from_env(
        {"IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc@127.0.0.1:1/ifc"}
    )
    engine = make_reader_engine(settings, application="viewer")
    yield engine
    engine.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def feed(engine: Engine, clock: Clock) -> Feed:
    resolver = Resolver(Reader(engine), argument_checks(build_server(engine)))
    return Feed(resolver, database="127.0.0.1:1/ifc", clock=clock)


def _payload(
    tool: str = "list_models",
    arguments: dict[str, object] | None = None,
    *,
    n: int = 1,
    outcome: str = "ok",
    source: Source = SERVE,
) -> str:
    return encode(
        Call(
            source=source,
            n=n,
            at=datetime(2026, 11, 23, 10, 1, tzinfo=UTC),
            tool=tool,
            arguments=arguments if arguments is not None else {"include_replaced": False},  # type: ignore[arg-type]
            outcome=outcome,
            seconds=0.01,
            rows=None if outcome != "ok" else 4,
        )
    )


def test_an_event_is_numbered_labelled_and_summarised(feed: Feed) -> None:
    entry = feed.receive(_payload(n=7))

    assert entry is not None
    assert (entry.id, entry.n, entry.tool, entry.summary) == (1, 7, "list_models", "list_models")
    assert (entry.source, entry.demo) == ("serve 4242, started 10:00:00", False)
    assert entry.selection is None  # list_models lights nothing: the view stays as it is
    assert feed.entries() == [entry]
    assert feed.status().received == 1


def test_a_call_that_failed_clears_the_view_and_says_why(feed: Feed) -> None:
    entry = feed.receive(_payload("find_elements", {"model_id": 99}, outcome="invalid"))

    assert entry is not None
    assert entry.selection is not None
    assert (entry.selection.count, entry.selection.note) == (
        0,
        "nothing is lit: the server refused its arguments",
    )
    assert entry.summary == "find_elements: elements, model 99"


def test_a_selection_the_database_cannot_give_says_so(
    feed: Feed, caplog: pytest.LogCaptureFixture
) -> None:
    entry = feed.receive(_payload("space_schedule", {"model_id": 1}))

    assert entry is not None
    assert entry.selection is not None
    assert entry.selection.note == "the selection could not be read; see view's log"
    [warning] = caplog.records
    assert warning.getMessage() == "viewer feed: selection not read"


def test_more_than_the_burst_at_once_is_dropped_and_the_rate_refills(
    feed: Feed, clock: Clock
) -> None:
    accepted = [feed.receive(_payload(n=n)) for n in range(1, BURST + 2)]

    assert sum(entry is not None for entry in accepted) == BURST
    assert accepted[-1] is None
    assert feed.status().dropped == {"more than 10 a second": 1}
    clock.now += 1 / PER_SECOND
    assert feed.receive(_payload(n=99)) is not None


def test_the_last_200_are_kept_and_the_selections_of_the_last_50(feed: Feed, clock: Clock) -> None:
    for n in range(1, KEEP + 6):
        clock.now += 1
        if n % 2:
            feed.receive(_payload("space_schedule", {"model_id": 1}, n=n, outcome="invalid"))
        else:  # lights nothing by nature: there is no selection to drop
            feed.receive(_payload(n=n))

    entries = feed.entries()
    assert [entry.id for entry in entries] == list(range(6, KEEP + 6))
    kept = [entry.selection is not None for entry in entries]
    lit = [entry.id % 2 == 1 for entry in entries]
    window = KEEP - SELECTIONS_KEPT
    assert kept == [False] * window + lit[window:]
    assert [entry.selection_dropped for entry in entries] == lit[:window] + [False] * (
        SELECTIONS_KEPT
    )
    assert [entry.id for entry in feed.entries(after=KEEP + 3)] == [KEEP + 4, KEEP + 5]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("not json", "not JSON"),
        (json.dumps({**json.loads(_payload()), "v": 2}), "an unknown version"),
        (json.dumps({**json.loads(_payload()), "tool": "drop"}), "invalid at tool"),
        (_payload("find_elements", {"model_id": 1, "where": "1=1"}), "invalid arguments"),
    ],
)
def test_what_is_no_event_is_dropped_counted_and_logged_once_without_its_text(
    feed: Feed, caplog: pytest.LogCaptureFixture, payload: str, reason: str
) -> None:
    caplog.set_level(logging.DEBUG, logger="ifc_sql_mcp.viewer.feed")

    assert feed.receive(payload) is None
    assert feed.receive(payload) is None

    assert feed.status().dropped == {reason: 2}
    assert feed.entries() == []
    first, second = caplog.records
    assert (first.levelno, second.levelno) == (logging.WARNING, logging.DEBUG)
    assert first.reason == reason  # type: ignore[attr-defined]
    assert "1=1" not in caplog.text


def test_the_demos_events_are_labelled_as_its_own(feed: Feed) -> None:
    before = feed.receive(_payload(n=1))
    feed.receive(_payload(n=1, source=OTHER))
    feed.mark_demo(SERVE)
    after = feed.receive(_payload(n=2))

    assert before is not None
    assert after is not None
    first, other, second = feed.entries()
    assert (first.demo, other.demo, second.demo) == (True, False, True)
    assert first.source == "scripted demo, no LLM (serve 4242, started 10:00:00)"
    assert second.source == first.source
    assert other.source == "serve 99, started 09:00:00"
    assert feed.status().demo is True
    feed.mark_demo(SERVE)  # again: labelled once
    assert feed.entries()[0].source == first.source


def test_wait_returns_the_first_match_or_nothing(feed: Feed) -> None:
    feed.receive(_payload(n=1))
    feed.receive(_payload(n=2))

    found = feed.wait(lambda entry: entry.n == 2, after=0, seconds=1)
    assert found is not None
    assert found.id == 2
    assert feed.wait(lambda entry: entry.n == 2, after=2, seconds=0.05) is None


def test_the_state_says_whether_events_arrive(feed: Feed) -> None:
    assert feed.status().state == "connecting"
    feed.set_state("live")
    live = feed.status()
    feed.set_state("live")
    assert feed.status() == live
    feed.set_state("interrupted", "lost")

    status = feed.status()
    assert (status.state, status.message, status.database) == (
        "interrupted",
        "lost",
        "127.0.0.1:1/ifc",
    )
    assert status.since is not None


def test_a_closed_feed_says_so(feed: Feed) -> None:
    assert feed.closed is False
    feed.close()
    assert feed.closed is True


def test_a_step_labels_only_the_call_it_expects(feed: Feed) -> None:
    feed.expect(lambda entry: entry.n == 2, DemoStep(index=1, steps=1, title="t", figure="f"))

    other = feed.receive(_payload(n=1))
    mine = feed.receive(_payload(n=2))
    again = feed.receive(_payload(n=2))

    assert other is not None
    assert other.step is None
    assert mine is not None
    assert mine.step is not None
    assert again is not None
    assert again.step is None


def test_a_process_with_an_odd_start_is_labelled_by_it(feed: Feed) -> None:
    odd = Source(pid=7, started_at="today")

    entry = feed.receive(_payload(source=odd))

    assert entry is not None
    assert entry.source == "serve 7, started today"
