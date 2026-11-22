"""Correct the comments on element.revit_type and v_element.revit_type.

Both said the Revit type "may contain colons". It never does: in a loadable family's name,
``Family:Type:Label:ElementId``, the third segment is an instance label, and the type is
always the second segment (see ``ingest/revit_names.py``). The comments are what an LLM
reads through the ``ifc://schema`` resource, so a wrong one is a wrong instruction.

The view comment lives in the view file, and released view files are never edited, so
``v_element.v2.sql`` replaces ``v1`` (same query, corrected comment). Nothing reads from
v_element, so it can be dropped and recreated; ALTER DEFAULT PRIVILEGES (migration 0003)
gives the new view to ``ifc_reader`` again.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_COMMENT = "Type part of a Revit name. It may itself contain ':' characters."
NEW_COMMENT = (
    "Type part of a Revit name: its second segment, so never containing ':'. In "
    "Family:Type:Label:ElementId the third segment is an instance label, not part of "
    "the type."
)


def upgrade() -> None:
    """Replace v_element v1 with v2 and correct the column comment."""
    op.alter_column(
        "element",
        "revit_type",
        existing_type=sa.Text(),
        comment=NEW_COMMENT,
        existing_comment=OLD_COMMENT,
        schema="ifc",
    )
    op.execute("DROP VIEW ifc.v_element")
    run_sql_file(op.get_bind(), "views/v_element.v2.sql")


def downgrade() -> None:
    """Restore v_element v1 and the old column comment."""
    op.execute("DROP VIEW ifc.v_element")
    run_sql_file(op.get_bind(), "views/v_element.v1.sql")
    op.alter_column(
        "element",
        "revit_type",
        existing_type=sa.Text(),
        comment=OLD_COMMENT,
        existing_comment=NEW_COMMENT,
        schema="ifc",
    )
