"""Run the Alembic migrations from Python, for ``ifc-sql migrate`` and the tests.

The Alembic configuration is built in code instead of read from ``alembic.ini`` for two
reasons. The database URL always comes from :class:`~ifc_sql_mcp.config.Settings`, so
there is no config file where credentials could be committed; and an installed package
can migrate a database without any project files on disk. ``[tool.alembic]`` in
``pyproject.toml`` points the plain ``alembic`` CLI at the same scripts for development
tasks such as ``alembic history`` or writing a new revision.

``psycopg_connection`` is re-exported (``import ... as ...``) because migration 0003
imports it from this module, and released migrations are not edited.
"""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.pool import NullPool

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import APPLICATION_NAME
from ifc_sql_mcp.db.engine import psycopg_connection as psycopg_connection
from ifc_sql_mcp.db.schema import SCHEMA

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SQL_DIR = Path(__file__).parent / "sql"

# Key under which the Settings travel from here to env.py and the migration scripts.
SETTINGS_ATTRIBUTE = "settings"


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Limit autogenerate to the ``ifc`` schema.

    Without this filter, comparing the metadata with the database would also report every
    table in ``public`` (and any other schema) as "removed".
    """
    if type_ == "schema":
        return name == SCHEMA
    return True


# Shared by env.py (``alembic revision --autogenerate``) and the test that asserts the
# migrations and schema.py agree, so both compare the database in exactly the same way.
AUTOGENERATE_OPTIONS: dict[str, Any] = {
    "include_schemas": True,
    "include_name": include_name,
    "compare_server_default": True,
}


def alembic_config(settings: Settings) -> Config:
    """Return an Alembic config that migrates the database named in ``settings``."""
    config = Config()
    # Alembic reads options through configparser, which treats '%' as interpolation.
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    config.attributes[SETTINGS_ATTRIBUTE] = settings
    return config


def upgrade(settings: Settings, revision: str = "head") -> None:
    """Upgrade the database in ``settings`` to ``revision`` (default: the latest)."""
    command.upgrade(alembic_config(settings), revision)


def head_revision() -> str:
    """Return the newest revision id in the migration scripts."""
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    if head is None:  # pragma: no cover - only possible with an empty versions directory
        raise RuntimeError(f"no migrations found in {MIGRATIONS_DIR}")
    return head


def migration_engine(database_url: str) -> Engine:
    """Create the engine that runs (and inspects) migrations, with ``search_path`` pinned.

    PostgreSQL's default search_path is ``"$user", public``. For a role that shares the
    schema's name (the development role is called ``ifc``) the default schema would be
    ``ifc``: Alembic's version table could land inside the schema it manages, and an
    autogenerate comparison would skip ``ifc`` as "the default schema". Pinning the path
    to ``public`` makes migrations behave the same whatever the owning role is called.
    One-off connections, so no pool.
    """
    return create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"application_name": APPLICATION_NAME, "options": "-c search_path=public"},
    )


def current_revision(settings: Settings) -> str | None:
    """Return the revision the database is at, or None if it was never migrated."""
    engine = migration_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def run_sql_file(connection: Connection, relative_path: str) -> None:
    """Execute one SQL file from ``sql/`` on ``connection``, verbatim, in its transaction.

    Migrations name files by version (``views/v_element.v1.sql``) and a file is never
    edited once a migration uses it, so an old migration keeps creating the same objects
    after a view has moved on to ``v2``.

    The script goes straight to psycopg instead of through ``sqlalchemy.text()``: text()
    reads ``:word`` as a bind parameter even inside string literals (the comments quote
    names like ``Family:Type:ElementId``), and psycopg leaves ``%`` alone only when no
    parameters are passed. Both are fine for trusted files shipped with the package.
    """
    script = (SQL_DIR / relative_path).read_text(encoding="utf-8")
    psycopg_connection(connection).execute(script)
