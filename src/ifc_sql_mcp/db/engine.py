"""SQLAlchemy engine construction.

Kept in one place so every connection the project opens gets the same options. The
read-only engine used by the MCP server will live next to this one and add its own
safety settings on top.
"""

from sqlalchemy import Engine, create_engine, make_url

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


def redact(database_url: str) -> str:
    """Return ``database_url`` with the password masked, safe to log or show in errors."""
    return make_url(database_url).render_as_string(hide_password=True)
