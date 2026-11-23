"""Listening to the viewer's feed in tests: a real PostgreSQL session that LISTENs.

``listening(settings)`` opens a psycopg connection in autocommit, as ``ifc-sql view``'s
listener does, runs ``LISTEN ifc_viewer_feed``, and yields a function that collects the
payloads that arrive. Nothing is mocked: the notifications go through the database.
"""

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from sqlalchemy import make_url

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.viewer.event import CHANNEL

type Received = Callable[..., list[dict[str, Any]]]


def conninfo(database_url: str) -> str:
    """A SQLAlchemy URL as psycopg's own connection string."""
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@contextmanager
def listening(settings: Settings) -> Iterator[Received]:
    """Listen on the feed's channel until the block ends.

    The function it yields, ``received(count=None, seconds=3.0)``, returns the events
    (parsed JSON) that arrived so far, waiting until ``count`` have, or ``seconds`` passed.
    """
    with psycopg.connect(conninfo(settings.database_url), autocommit=True) as connection:
        connection.execute(f"LISTEN {CHANNEL}")
        arrived: list[dict[str, Any]] = []

        def received(count: int | None = None, seconds: float = 3.0) -> list[dict[str, Any]]:
            deadline = time.monotonic() + seconds
            while count is None or len(arrived) < count:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                for notification in connection.notifies(timeout=min(left, 0.2)):
                    arrived.append(json.loads(notification.payload))
            return list(arrived)

        yield received
