"""Create ``ifc.v_storey_alignment``: storeys compared across discipline models.

Discipline models of one building are exported separately, and each can define its
levels differently. This view puts each project's storeys side by side, matched on a
normalised name, so a storey missing from a discipline or at a different elevation
stands out. See ``docs/data-model.md`` for the matching rules.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the view (and its comments) from its versioned file."""
    run_sql_file(op.get_bind(), "views/v_storey_alignment.v1.sql")


def downgrade() -> None:
    """Drop the view."""
    op.execute("DROP VIEW ifc.v_storey_alignment")
