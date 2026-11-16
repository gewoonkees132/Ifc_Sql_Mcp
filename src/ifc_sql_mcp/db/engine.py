"""SQLAlchemy engine construction.

Kept in one place so every connection the project opens gets the same options. The
read-only engine used by the MCP server will live next to this one and add its own
safety settings on top.
"""

from typing import Any

import psycopg
from sqlalchemy import Connection, Engine, create_engine, make_url
from sqlalchemy.exc import DBAPIError

from ifc_sql_mcp.db.schema import READER_ROLE

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
