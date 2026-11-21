"""Add the canonical quantity ``width_m`` (audit finding A1).

The IFC base quantities that Revit 2019 to 2022 write carry a ``Width`` in the door,
window, wall and slab sets: a door's or window's width, a wall's or slab's thickness. A
door schedule needs it, and so does checking a slab's volume against area x thickness.
This migration lets ``ifc.quantity.name`` hold it, documents it in the column and view
comments, and adds it to ``v_element`` (v4) as a column.

``v_quantity`` v2 has the same query as v1 with comments that name ``width_m``; it is
replaced in place (CREATE OR REPLACE), so the views reading from it stay. ``v_element``
is dropped and recreated, as in 0004 and 0010.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIVE = "name IN ('length_m', 'area_m2', 'volume_m3', 'perimeter_m', 'height_m')"
SIX = "name IN ('length_m', 'area_m2', 'volume_m3', 'perimeter_m', 'height_m', 'width_m')"
OLD_COMMENT = (
    "Canonical quantity; the suffix is its unit: length_m, area_m2, volume_m3, "
    "perimeter_m, height_m."
)
NEW_COMMENT = (
    "Canonical quantity; the suffix is its unit: length_m, area_m2, volume_m3, perimeter_m, "
    "height_m, width_m. width_m is IFC's Width: a door's or window's width, a wall's or "
    "slab's thickness."
)


def _name_check(condition: str) -> None:
    op.drop_constraint(op.f("ck_quantity_name_valid"), "quantity", type_="check", schema="ifc")
    op.create_check_constraint(op.f("ck_quantity_name_valid"), "quantity", condition, schema="ifc")


def _comment(new: str, old: str) -> None:
    op.alter_column(
        "quantity",
        "name",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=new,
        existing_comment=old,
        schema="ifc",
    )


def upgrade() -> None:
    """Allow width_m, document it, and expose it in v_element."""
    _name_check(SIX)
    _comment(NEW_COMMENT, OLD_COMMENT)
    run_sql_file(op.get_bind(), "views/v_quantity.v2.sql")
    op.execute("DROP VIEW ifc.v_element")
    run_sql_file(op.get_bind(), "views/v_element.v4.sql")


def downgrade() -> None:
    """Remove width_m rows and the column, and restore the earlier views and check."""
    op.execute("DROP VIEW ifc.v_element")
    run_sql_file(op.get_bind(), "views/v_element.v3.sql")
    # v1 has the same query, so only its two comments change back.
    op.execute(
        "COMMENT ON COLUMN ifc.v_quantity.name IS "
        "'Canonical quantity: length_m, area_m2, volume_m3, perimeter_m or height_m.'"
    )
    op.execute(
        "COMMENT ON COLUMN ifc.v_quantity.source_ref IS "
        "'Exact origin of the chosen value, e.g. PSet_Revit_Dimensions.Area.'"
    )
    op.execute("DELETE FROM ifc.quantity WHERE name = 'width_m'")
    _comment(OLD_COMMENT, NEW_COMMENT)
    _name_check(FIVE)
