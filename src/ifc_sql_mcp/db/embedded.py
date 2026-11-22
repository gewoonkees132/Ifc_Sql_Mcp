"""PostgreSQL 16 in a folder of the user's, so that one user needs no Docker (A18.4).

The audit's finding A18 asked, last, for "an embedded database option, so Docker is not
required for one user". The first round deferred it, because every view is PostgreSQL's
SQL and the MCP server's safety rests on PostgreSQL's read-only role (ADR 6). An embedded
**PostgreSQL** keeps both. ``pixeltable-pgserver`` (the ``embedded`` extra) ships the
PostgreSQL 16.15 binaries as a wheel for Linux, macOS and Windows, the version CI and the
audit use.

With ``IFC_SQL_DATA_DIR`` set, the first command that needs the database starts a server
on that folder, or attaches to the one already running there. ``ingest``, ``dq``,
``watch`` and ``serve`` can therefore run at once. The server stops when the last
process attached to it exits. It listens on a Unix socket in the folder on Linux and
macOS, and on a free port of 127.0.0.1 on Windows, and it accepts its local connections
without a password (ADR 13). The database is ``ifc``, owned by the superuser
``postgres``, and migrations create ``ifc_reader`` in it as on any other server.
"""

import logging
import warnings
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

DATABASE = "ifc"
"""The database the embedded server holds the schema in."""

POSTGRES_VERSION = 16
"""The bundled PostgreSQL to run: the major version CI and the audit use."""

logger = logging.getLogger(__name__)


class EmbeddedError(RuntimeError):
    """The embedded database cannot be used: the extra is missing, or it did not start."""


def embedded_database_url(data_dir: Path) -> str:
    """Start the embedded server on ``data_dir``, or attach to it, and return its URL.

    Args:
        data_dir: The folder that holds the database. It is created, with its parents, if
            it does not exist.

    Returns:
        A ``postgresql+psycopg`` URL of the ``ifc`` database, as the superuser ``postgres``.

    Raises:
        EmbeddedError: if the ``embedded`` extra is not installed, or the server does not
            start.
    """
    server = _server(data_dir)
    info = server.get_postmaster_info()
    with psycopg.connect(info.get_uri(), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)
        ).fetchone()
        if exists is None:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
            logger.info("created the embedded database", extra={"data_dir": str(data_dir)})
    return str(info.get_uri(database=DATABASE, driver="psycopg"))


def _server(data_dir: Path) -> Any:
    """The pgserver handle for ``data_dir``; it stops the server when the last user exits."""
    try:
        with warnings.catch_warnings():
            # Without XDG_RUNTIME_DIR (a container, a service) platformdirs warns, and
            # pgserver keeps its lock file in the temporary directory instead.
            warnings.filterwarnings("ignore", message="XDG_RUNTIME_DIR is not set")
            # An optional extra, so it is imported here, on use.
            from pixeltable_pgserver.postgres_server import get_server
    except ImportError as exc:
        raise EmbeddedError(
            "IFC_SQL_DATA_DIR needs the embedded database, which is not installed: run "
            "uv sync --extra embedded (or pip install 'ifc-sql-mcp[embedded]')"
        ) from exc
    # pgserver tells every step at INFO; ifc-sql's own log says what matters.
    logging.getLogger("pixeltable_pgserver").setLevel(logging.WARNING)
    data_dir = data_dir.expanduser().resolve()
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        server = get_server(data_dir, cleanup_mode="stop", postgres_version=POSTGRES_VERSION)
    except Exception as exc:  # pgserver raises RuntimeError, subprocess and OS errors
        raise EmbeddedError(f"the embedded database in {data_dir} did not start: {exc}") from exc
    logger.info("embedded database", extra={"data_dir": str(data_dir)})
    return server
