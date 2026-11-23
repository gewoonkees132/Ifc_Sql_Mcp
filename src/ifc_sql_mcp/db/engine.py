"""SQLAlchemy engine construction.

Kept in one place so every connection the project opens gets the same options. The MCP
server's read-only engine and the transaction each of its calls runs in live here too,
with their own safety settings on top (ADR 6).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.schema import READER_ROLE, SCHEMA

APPLICATION_NAME = "ifc-sql-mcp"


def make_engine(database_url: str) -> Engine:
    """Create an engine for ``database_url``.

    ``pool_pre_ping`` makes a long-running process (the MCP server) survive a database
    restart instead of failing on the first query after it. ``application_name`` labels
    our sessions in ``pg_stat_activity`` so an operator can tell them apart.
    """
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"application_name": APPLICATION_NAME},
    )


def reader_database_url(database_url: str, reader_password: str) -> str:
    """Return ``database_url`` with the credentials of the read-only role swapped in.

    Host, port and database stay those of the owner URL, so there is one place to point
    the project at a database, and one secret per role.
    """
    url = make_url(database_url).set(username=READER_ROLE, password=reader_password)
    return url.render_as_string(hide_password=False)


def make_reader_engine(settings: Settings, *, application: str = "server") -> Engine:
    """Create the MCP server's engine: the read-only role, read-only transactions, no pool.

    * It connects as ``ifc_reader`` (:func:`reader_database_url`), so the server needs no
      other password, and the owner's in ``IFC_SQL_DATABASE_URL`` is never used.
    * Every transaction begins ``REPEATABLE READ READ ONLY``, said by the client in its
      BEGIN, on top of the role's ``default_transaction_read_only``. A tool that runs several
      queries (a count, then a page) sees one snapshot of the data.
    * No pool: every call opens a new session and closes it, so nothing one call leaves
      in its session (a setting, a session advisory lock, a prepared statement) can reach
      the next. A new session costs about 12 ms here, against seconds per LLM tool call.
    * ``connect_timeout`` makes an unreachable host fail in 5 seconds instead of hanging.

    ``ifc-sql view`` reads through the same engine, with ``application="viewer"``: its
    sessions are then ``ifc-sql-mcp-viewer`` in ``pg_stat_activity``.
    """
    return create_engine(
        reader_database_url(settings.database_url, settings.reader_password),
        poolclass=NullPool,
        isolation_level="REPEATABLE READ",
        execution_options={"postgresql_readonly": True},
        connect_args={
            "application_name": f"{APPLICATION_NAME}-{application}",
            "connect_timeout": 5,
        },
    )


@contextmanager
def reader_transaction(
    engine: Engine, *, statement_timeout_ms: int, lock_timeout_ms: int
) -> Iterator[Connection]:
    """Run one MCP call: a new session, one read-only transaction with the call's limits.

    ``SET LOCAL`` lasts until the transaction ends and covers every statement in it, so a
    setting from before cannot carry over, whatever the role's defaults say. The search
    path is pinned too, because ``run_sql`` relies on unqualified names meaning ``ifc``.

    JIT compilation is switched off. These queries run in milliseconds, and compiling one
    takes hundreds: ``ifc.model`` is too small for autovacuum ever to analyze it, so the
    planner assumed 210 models instead of 4, costed ``v_model_summary`` above the JIT
    threshold and compiled it, which made ``list_models`` take 0.6 s instead of 0.06 s on
    the Duplex files.

    The transaction always ends with a rollback: there is nothing to commit, and a
    rollback also undoes a setting a query changed for the rest of the session.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # SET takes no bind parameters; the values are integers from the server's code.
            connection.execute(text(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"))
            connection.execute(text(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"))
            connection.execute(text(f"SET LOCAL search_path = {SCHEMA}"))
            connection.execute(text("SET LOCAL jit = off"))
            yield connection
        finally:
            transaction.rollback()


def notify(engine: Engine, channel: str, payload: str) -> None:
    """Send one notification on ``channel``, from a session and a transaction of its own.

    ``engine`` is the MCP server's (:func:`make_reader_engine`): the transaction begins
    ``READ ONLY``, which PostgreSQL allows ``NOTIFY`` in, and it commits, because a
    notification is delivered only when its transaction commits (ADR 14, P7). It costs a
    session, opened and closed here as every call's is: 15 ms on the development machine,
    the cost of the session itself.

    Raises:
        sqlalchemy.exc.DBAPIError: The database refused it or could not be reached; a
            payload of 8,000 bytes or more is refused.
    """
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload}
        )


def redact(database_url: str) -> str:
    """Return ``database_url`` with the password masked, safe to log or show in errors."""
    return make_url(database_url).render_as_string(hide_password=True)


def describe_error(exc: BaseException) -> str:
    """Return an error as a run records it: its type and message, no traceback.

    Used for ``ingestion_run.error`` and ``dq_run.error``. For a database error this is
    the driver's message. SQLAlchemy's wrapper would add the statement and its
    parameters, which for a bulk insert run to thousands of values.
    """
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        return f"{type(exc.orig).__name__}: {exc.orig}"
    return f"{type(exc).__name__}: {exc}"


def psycopg_connection(connection: Connection) -> psycopg.Connection[Any]:
    """Return the psycopg connection under ``connection``: same session, same transaction.

    For what SQLAlchemy does not wrap: migrations compose DDL with :mod:`psycopg.sql`
    (quoted names and literals, which cannot take bind parameters) and run SQL files as
    scripts, and the loader streams rows with COPY.
    """
    driver_connection = connection.connection.driver_connection
    if not isinstance(driver_connection, psycopg.Connection):
        raise TypeError("this needs the psycopg 3 driver (a postgresql+psycopg:// URL)")
    return driver_connection
