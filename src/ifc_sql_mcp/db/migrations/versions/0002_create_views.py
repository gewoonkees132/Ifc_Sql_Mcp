"""Create the views, version 1 of each.

Each view lives in its own file under ``ifc_sql_mcp/db/sql/views/``, named with a version
(``v_element.v1.sql``). This migration names the exact versions it uses and released files
are never edited, so it creates the same views however far the files have moved on: a
changed view is a new ``.v2.sql`` file plus a new migration that replaces ``v1`` with it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Creation order: v_quantity first, because the others read from it.
VIEW_FILES = (
    ("v_quantity", "views/v_quantity.v1.sql"),
    ("v_element", "views/v_element.v1.sql"),
    ("v_space_schedule", "views/v_space_schedule.v1.sql"),
    ("v_takeoff", "views/v_takeoff.v1.sql"),
    ("v_model_summary", "views/v_model_summary.v1.sql"),
)


def upgrade() -> None:
    """Create each view (and its comments) from its versioned file."""
    connection = op.get_bind()
    for _view, path in VIEW_FILES:
        run_sql_file(connection, path)


def downgrade() -> None:
    """Drop the views in reverse order, dependants before the views they read from."""
    for view, _path in reversed(VIEW_FILES):
        op.execute(f"DROP VIEW ifc.{view}")
