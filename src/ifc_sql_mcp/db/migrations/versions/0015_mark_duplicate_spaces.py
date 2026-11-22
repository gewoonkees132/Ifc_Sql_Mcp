"""Mark rooms exported twice in ``v_space_schedule`` (audit finding A7).

A Revit model that holds both Rooms and MEP Spaces can export both: the audit's file C
has 163 IfcSpaces for 116 rooms, 47 of them twice, with the same number, name, storey and
floor area (but heights of 3.8 m and 2.75 m). Summing gives 8,090.7 m²; one row per room gives
6,124.8 m², and nothing told the two apart. ``v_space_schedule`` v2 adds
``duplicate_of``, the first space with the same number, name, storey and floor area, so a
total can leave the repeats out. The area is part of the key: in the audit's file E, five
spaces share a number with another (two shafts, Revit Areas) but not an area, and are
different spaces. Nothing reads from the view, so it is dropped and recreated, and
ALTER DEFAULT PRIVILEGES (0003) gives it to ``ifc_reader`` again.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace v_space_schedule v1 with v2."""
    op.execute("DROP VIEW ifc.v_space_schedule")
    run_sql_file(op.get_bind(), "views/v_space_schedule.v2.sql")


def downgrade() -> None:
    """Restore v1."""
    op.execute("DROP VIEW ifc.v_space_schedule")
    run_sql_file(op.get_bind(), "views/v_space_schedule.v1.sql")
