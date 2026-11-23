"""``ifc-sql view``'s listener: one session that LISTENs to the feed, in a thread (ADR 14).

It is a plain psycopg connection, not SQLAlchemy's, with ``autocommit``: notifications
arrive only between transactions, and ``ifc_reader`` ends a session idle in a
transaction after 30 seconds. It is synchronous on purpose: on Windows uvicorn runs a
``ProactorEventLoop``, which psycopg's ``AsyncConnection`` refuses.

The thread waits for notifications a second at a time, so that :meth:`Listener.stop`
takes effect within a second. When the session is lost (the database restarted, an
administrator ended it) it says so in the feed's status, waits a little longer each
time, connects again and listens again. A notification sent while it was away is lost:
PostgreSQL keeps notifications only for the sessions listening when they are sent.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from sqlalchemy import Engine

from ifc_sql_mcp.db.engine import APPLICATION_NAME
from ifc_sql_mcp.viewer.event import CHANNEL
from ifc_sql_mcp.viewer.feed import Feed

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (0.5, 1.0, 2.0, 5.0)
"""Waits before each new attempt to connect; the last one repeats."""

WAIT_SECONDS = 1.0
"""How long one wait for notifications lasts, and so how soon :meth:`Listener.stop` acts."""

type Connect = Callable[[], psycopg.Connection[Any]]


def listen_connection(engine: Engine) -> psycopg.Connection[Any]:
    """A new autocommit session with ``engine``'s credentials: ``ifc_reader``, for view.

    Named ``ifc-sql-mcp-viewer-feed`` in ``pg_stat_activity``, beside the routes'
    ``ifc-sql-mcp-viewer``; it gives up connecting after 5 seconds, as they do.
    """
    _, params = engine.dialect.create_connect_args(engine.url)
    params.pop("context", None)  # SQLAlchemy's type adapters: a LISTEN needs none
    return psycopg.connect(
        **params,
        autocommit=True,
        connect_timeout=5,
        application_name=f"{APPLICATION_NAME}-viewer-feed",
    )


class Listener:
    """The thread that hands each notification on the channel to ``feed``.

    Args:
        connect: Opens a new session; :func:`listen_connection` in ``view``.
        feed: Receives every payload, and the listener's state.
        backoff: Seconds to wait before each new attempt to connect.
    """

    def __init__(
        self, connect: Connect, feed: Feed, *, backoff: Sequence[float] = BACKOFF_SECONDS
    ) -> None:
        self._connect = connect
        self.feed = feed
        self._backoff = tuple(backoff)
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ifc-sql-view-feed", daemon=True)
        self.connections = 0
        """Sessions opened so far: 1, and one more after each interruption."""

    def start(self) -> None:
        """Start listening, in the background."""
        self._thread.start()

    def stop(self) -> None:
        """Stop listening, and wait for the thread to end: about a second at most."""
        self._stopping.set()
        self._thread.join(timeout=WAIT_SECONDS * 5)

    def _run(self) -> None:
        failures = 0
        while not self._stopping.is_set():
            try:
                with self._connect() as connection:
                    self.connections += 1
                    connection.execute(f"LISTEN {CHANNEL}")  # a constant, not caller text
                    self.feed.set_state("live")
                    failures = 0
                    while not self._stopping.is_set():
                        for notification in connection.notifies(timeout=WAIT_SECONDS):
                            self.feed.receive(notification.payload)
            except psycopg.Error as exc:
                message = "the feed's session was lost; connecting again"
                self.feed.set_state("interrupted", message)
                logger.warning(
                    "viewer feed interrupted",
                    extra={"error": f"{type(exc).__name__}: {exc}"[:300]},
                )
                wait = self._backoff[min(failures, len(self._backoff) - 1)]
                failures += 1
                self._stopping.wait(wait)
        self.feed.set_state("off", "ifc-sql view stopped listening")
