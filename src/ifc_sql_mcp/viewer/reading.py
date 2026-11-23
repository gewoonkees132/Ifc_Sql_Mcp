"""How ``ifc-sql view`` reads the database: as ``ifc_reader``, at most three sessions at once.

The routes of ``viewer/http.py`` and the feed's selections (``viewer/feed.py``) read
through one :class:`Reader`, so that together they never hold more than
:data:`SESSIONS` sessions; the feed's listener holds one more (ADR 14, decision 4).
``ifc_reader`` allows 20, shared with every MCP call on the cluster.
"""

import threading
from collections.abc import Callable

from sqlalchemy import Connection, Engine

from ifc_sql_mcp.db.engine import reader_transaction
from ifc_sql_mcp.server.app import Limits

SESSIONS = 3
"""Database sessions ``view`` reads through at most at once, besides the listener's."""


class Reader:
    """Read-only transactions with the MCP server's limits, at most :data:`SESSIONS` at once.

    Args:
        engine: ``make_reader_engine``'s: ``ifc_reader``, no pool, read-only.
        limits: The limits of each transaction; by default the MCP server's.
    """

    def __init__(self, engine: Engine, limits: Limits | None = None) -> None:
        self.engine = engine
        self.limits = limits or Limits()
        self._sessions = threading.BoundedSemaphore(SESSIONS)

    def read[T](self, work: Callable[[Connection], T]) -> T:
        """Run ``work`` in a new read-only transaction, waiting for a free session first."""
        with (
            self._sessions,
            reader_transaction(
                self.engine,
                statement_timeout_ms=self.limits.statement_timeout_ms,
                lock_timeout_ms=self.limits.lock_timeout_ms,
            ) as connection,
        ):
            return work(connection)
