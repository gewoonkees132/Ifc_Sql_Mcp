"""Add the quantity source ``ifc_attribute``: a door's and a window's overall size (A4).

``IfcDoor.OverallWidth`` and ``OverallHeight`` (and the window's) are attributes, not
properties, so a file exported without base quantities and Revit property sets, such as
the audit's Revit 2022 tower, gave no door any size: 0 of 1,458 doors. The ingest now
reads them, and this migration lets ``ifc.quantity.source`` say where they came from.

``v_quantity`` v3 ranks the new source after ``revit_pset`` and before ``geometry``: a
quantity set or a Revit parameter states a size on purpose, an attribute is the overall
size the shape was built with. It is replaced in place, keeping the views that read it.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THREE = "source IN ('ifc_qto', 'revit_pset', 'geometry')"
FOUR = "source IN ('ifc_qto', 'revit_pset', 'ifc_attribute', 'geometry')"
OLD_COMMENT = (
    "Where the value came from: ifc_qto (IfcElementQuantity), revit_pset (Revit "
    "parameter property set) or geometry (computed from the shape)."
)
NEW_COMMENT = (
    "Where the value came from: ifc_qto (IfcElementQuantity), revit_pset (Revit parameter "
    "property set), ifc_attribute (an attribute of the entity, such as a door's "
    "OverallWidth) or geometry (computed from the shape)."
)


def _source_check(condition: str) -> None:
    op.drop_constraint(op.f("ck_quantity_source_valid"), "quantity", type_="check", schema="ifc")
    op.create_check_constraint(
        op.f("ck_quantity_source_valid"), "quantity", condition, schema="ifc"
    )


def _comment(new: str, old: str) -> None:
    op.alter_column(
        "quantity",
        "source",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=new,
        existing_comment=old,
        schema="ifc",
    )


def upgrade() -> None:
    """Allow ifc_attribute and rank it in v_quantity."""
    _source_check(FOUR)
    _comment(NEW_COMMENT, OLD_COMMENT)
    run_sql_file(op.get_bind(), "views/v_quantity.v3.sql")


def downgrade() -> None:
    """Restore v_quantity v2, remove ifc_attribute rows and the source."""
    run_sql_file(op.get_bind(), "views/v_quantity.v2.sql")
    op.execute("DELETE FROM ifc.quantity WHERE source = 'ifc_attribute'")
    _comment(OLD_COMMENT, NEW_COMMENT)
    _source_check(THREE)
