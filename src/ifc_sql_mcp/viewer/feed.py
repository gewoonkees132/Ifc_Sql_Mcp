"""The calls ``ifc-sql view`` received, validated and resolved: the last 200 (ADR 14, decision 4).

The listener (``viewer/listener.py``) hands every notification's payload to
:meth:`Feed.receive`, from its own thread. Every payload is untrusted, since any login to
the database can send one, so the feed:

* rate-limits: at most :data:`BURST` events at once, :data:`PER_SECOND` a second after;
* decodes it (``viewer/event.py``), dropping an unknown version, a field of the wrong
  type, too many ids, a GlobalId that is none;
* checks its arguments with the tool's own check, and resolves the selection with a
  fixed query (``viewer/selection.py``), never with SQL the event brought;
* numbers it itself, for the page's ``Last-Event-ID``, and labels it with its source;
* keeps the last :data:`KEEP` in memory, the selections of the last
  :data:`SELECTIONS_KEPT`.

What it dropped is counted per reason in :class:`Status`, and logged without the payload.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from ifc_sql_mcp.db.engine import describe_error
from ifc_sql_mcp.server.values import JsonValue
from ifc_sql_mcp.viewer import selection
from ifc_sql_mcp.viewer.event import Event, EventRejectedError, Source, SqlText, decode
from ifc_sql_mcp.viewer.reading import Reader
from ifc_sql_mcp.viewer.selection import Checks, Request, Selection

logger = logging.getLogger(__name__)

KEEP = 200
"""Events kept in memory, the newest."""

SELECTIONS_KEPT = 50
"""Events whose selection is kept; an older one keeps its line and counts, not its ids."""

BURST = 30
PER_SECOND = 10.0

DEMO = "scripted demo, no LLM"

FEED_ONLY = ("list_models", "list_storeys", "describe_schema")
"""Tools whose calls light nothing: they are listed, and the view stays as it is."""

type State = Literal["off", "connecting", "live", "interrupted"]


class Status(BaseModel):
    """Whether events arrive, and what was dropped."""

    state: State = Field(
        description="off: no feed. connecting, live, or interrupted (the listener "
        "reconnects by itself)."
    )
    database: str | None = Field(description="Where the listener listens: host, port, database.")
    since: str | None = Field(description="When the state began, ISO 8601, UTC.")
    message: str | None = Field(description="Why it was interrupted.")
    received: int = Field(description="Events accepted since view started.")
    dropped: dict[str, int] = Field(description="Notifications refused, by reason.")
    demo: bool = Field(description="This view plays the scripted demo.")


class DemoStep(BaseModel):
    """The step of the scripted demo a call answers, for the page's caption."""

    index: int = Field(description="Its number, from 1.")
    steps: int = Field(description="How many steps the script has.")
    title: str = Field(description="What it asks, e.g. 'What lacks a fire rating?'.")
    figure: str = Field(description="What the answer shows, as the docs cite it.")


class Entry(BaseModel):
    """One call, as the page lists it."""

    id: int = Field(description="view's own number, from 1: the SSE event's id.")
    received_at: str
    src: Source = Field(description="The serve process that sent it.")
    source: str = Field(description='e.g. "serve 4242, started 10:00:00".')
    demo: bool = Field(description="The call came from the scripted demo.")
    n: int = Field(description="The call's number in its serve process.")
    tool: str
    summary: str = Field(description="The call in one line, from its arguments.")
    arguments: dict[str, JsonValue]
    clipped: list[str]
    outcome: str
    seconds: float
    rows: int | None
    sql: SqlText | None
    selection: Selection | None = Field(
        description="What it lights. Null for a call that lights nothing by nature "
        "(list_models, list_storeys, describe_schema), and for an old one whose "
        "selection is no longer kept (selection_dropped)."
    )
    selection_dropped: bool = False
    step: DemoStep | None = Field(default=None, description="The demo's step, if one.")


class Resolver:
    """Turns events into selections: the tools' own checks, and reads through ``reader``."""

    def __init__(self, reader: Reader, checks: Checks) -> None:
        self.reader = reader
        self.checks = checks

    def request(self, event: Event) -> Request:
        """See :func:`~ifc_sql_mcp.viewer.selection.request`."""
        return selection.request(event, self.checks)

    def resolve(self, wanted: Request) -> Selection:
        """Resolve ``wanted`` in a read-only transaction of its own, sessions permitting."""
        if wanted.query == "nothing":
            return selection.nothing(wanted)
        return self.reader.read(lambda connection: selection.resolve(connection, wanted))


class Feed:
    """The events ``view`` accepted, newest last, and the state of its listener.

    Thread-safe: the listener's thread receives, the web server's tasks and the demo read.

    Args:
        resolver: Resolves each event's selection.
        database: Where the listener listens, for the page.
        clock: Seconds, for the rate limit; ``time.monotonic`` by default.
    """

    def __init__(
        self,
        resolver: Resolver,
        *,
        database: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.resolver = resolver
        self._clock = clock
        self._changed = threading.Condition()
        self._entries: deque[Entry] = deque(maxlen=KEEP)
        self._numbered = 0
        self._tokens = float(BURST)
        self._filled = clock()
        self._demo_source: Source | None = None
        self._expected: list[tuple[Callable[[Entry], bool], DemoStep]] = []
        self._closed = False
        self._status = Status(
            state="connecting",
            database=database,
            since=_now(),
            message=None,
            received=0,
            dropped={},
            demo=False,
        )

    # ----------------------------------------------------------------- the listener's side

    def receive(self, payload: str) -> Entry | None:
        """Accept one notification's payload, or drop it; never raises."""
        try:
            if not self._take_token():
                raise EventRejectedError("more than 10 a second")
            event = decode(payload)
            wanted = self.resolver.request(event)
        except EventRejectedError as exc:
            self._drop(str(exc))
            return None
        try:
            found = None if event.tool in FEED_ONLY else self.resolver.resolve(wanted)
        except Exception as exc:  # the database, after the event was accepted
            logger.warning("viewer feed: selection not read", extra={"error": describe_error(exc)})
            found = selection.nothing(
                Request("nothing", note="the selection could not be read; see view's log")
            )
        with self._changed:
            self._numbered += 1
            demo = self._demo_source is not None and event.src == self._demo_source
            entry = Entry(
                id=self._numbered,
                received_at=_now(),
                src=event.src,
                source=_label(event.src, demo=demo),
                demo=demo,
                n=event.n,
                tool=event.tool,
                summary=selection.summary(event),
                arguments=event.arguments,
                clipped=event.clipped,
                outcome=event.outcome,
                seconds=event.seconds,
                rows=event.rows,
                sql=event.sql,
                selection=found,
            )
            for index, (match, step) in enumerate(self._expected):
                if match(entry):
                    entry = entry.model_copy(update={"step": step})
                    del self._expected[index]
                    break
            self._entries.append(entry)
            if len(self._entries) > SELECTIONS_KEPT:
                old = self._entries[-SELECTIONS_KEPT - 1]
                if old.selection is not None:
                    self._entries[-SELECTIONS_KEPT - 1] = old.model_copy(
                        update={"selection": None, "selection_dropped": True}
                    )
            self._status = self._status.model_copy(update={"received": self._numbered})
            self._changed.notify_all()
        return entry

    def set_state(self, state: State, message: str | None = None) -> None:
        """Say whether events arrive: ``live``, or ``interrupted`` with why."""
        with self._changed:
            if (state, message) != (self._status.state, self._status.message):
                self._status = self._status.model_copy(
                    update={"state": state, "message": message, "since": _now()}
                )
                self._changed.notify_all()

    # ----------------------------------------------------------------- the readers' side

    def status(self) -> Status:
        """The listener's state and the counts."""
        with self._changed:
            return self._status

    def entries(self, after: int = 0) -> list[Entry]:
        """The kept events numbered above ``after``, oldest first."""
        with self._changed:
            return [entry for entry in self._entries if entry.id > after]

    def wait(self, match: Callable[[Entry], bool], after: int, seconds: float) -> Entry | None:
        """Wait up to ``seconds`` for an event above ``after`` that ``match``es."""
        deadline = time.monotonic() + seconds
        with self._changed:
            while True:
                found = next((e for e in self._entries if e.id > after and match(e)), None)
                left = deadline - time.monotonic()
                if found is not None or left <= 0:
                    return found
                self._changed.wait(left)

    def expect(self, match: Callable[[Entry], bool], step: DemoStep) -> None:
        """Attach ``step`` to the next call that ``match``es, when it arrives: the demo's.

        It replaces an earlier expectation not met, so a step whose call never arrived
        cannot label the next one.
        """
        with self._changed:
            self._expected = [(match, step)]

    def mark_demo(self, source: Source) -> None:
        """Label the events of ``source``, the demo's own serve, as the scripted demo's.

        Those already received are labelled too.
        """
        with self._changed:
            self._demo_source = source
            self._status = self._status.model_copy(update={"demo": True})
            for index, entry in enumerate(self._entries):
                if entry.src == source and not entry.demo:
                    self._entries[index] = entry.model_copy(
                        update={"demo": True, "source": f"{DEMO} ({entry.source})"}
                    )
            self._changed.notify_all()

    def close(self) -> None:
        """No more events: the page's streams end."""
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    @property
    def closed(self) -> bool:
        """True once :meth:`close` was called."""
        return self._closed

    # ----------------------------------------------------------------- inside

    def _take_token(self) -> bool:
        with self._changed:
            now = self._clock()
            self._tokens = min(float(BURST), self._tokens + (now - self._filled) * PER_SECOND)
            self._filled = now
            if self._tokens < 1:
                return False
            self._tokens -= 1
            return True

    def _drop(self, reason: str) -> None:
        with self._changed:
            dropped = dict(self._status.dropped)
            first = reason not in dropped
            dropped[reason] = dropped.get(reason, 0) + 1
            self._status = self._status.model_copy(update={"dropped": dropped})
        # The payload is never logged: anyone can write it.
        (logger.warning if first else logger.debug)(
            "viewer feed: notification dropped", extra={"reason": reason}
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _label(source: Source, *, demo: bool) -> str:
    started = source.started_at[11:19] or source.started_at  # the time of an ISO instant
    serve = f"serve {source.pid}, started {started}"
    return f"{DEMO} ({serve})" if demo else serve
