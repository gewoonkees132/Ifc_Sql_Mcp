"""Let ``ifc_reader`` read the migration revision; take temporary tables away from PUBLIC.

Two changes to the read-only role's boundary, both for the MCP server (ADR 6):

* **SELECT on ``public.alembic_version``.** ``ifc-sql serve`` connects only as
  ``ifc_reader`` and refuses a database that is not at the latest revision, as ingest
  and dq do. Without this grant it would need the owner's credentials just to read one
  version string. The table holds that string and nothing else.
* **REVOKE TEMPORARY ON DATABASE … FROM PUBLIC.** PostgreSQL grants every role the
  right to create temporary tables in every database. ``ifc_reader`` could therefore
  create one and fill it in a read-write transaction, which breaks the promise that the
  role can only read (``tests/integration/test_privileges.py`` now checks it). The owner
  keeps the privilege, as owners do, and nothing in the project uses temporary tables
  as another role.

Downgrade revokes the grant and gives TEMPORARY back to PUBLIC, PostgreSQL's default.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from psycopg import sql

from ifc_sql_mcp.db.migrate import psycopg_connection

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _on_this_database(statement: str) -> None:
    """Run ``statement`` with ``{database}`` replaced by this database's quoted name."""
    database = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    psycopg_connection(op.get_bind()).execute(
        sql.SQL(statement).format(database=sql.Identifier(database))
    )


def upgrade() -> None:
    """Grant the version table, revoke temporary tables."""
    # migration_engine pins search_path to public, so Alembic's table is always there.
    op.execute("GRANT SELECT ON public.alembic_version TO ifc_reader")
    _on_this_database("REVOKE TEMPORARY ON DATABASE {database} FROM PUBLIC")


def downgrade() -> None:
    """Restore PostgreSQL's default for temporary tables and revoke the version table."""
    _on_this_database("GRANT TEMPORARY ON DATABASE {database} TO PUBLIC")
    op.execute("REVOKE SELECT ON public.alembic_version FROM ifc_reader")
