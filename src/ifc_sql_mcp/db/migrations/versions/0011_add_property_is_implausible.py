"""Add ``ifc.property.is_implausible``: quantity values that break a physical bound (A2).

Revit's exporters write some quantity-set values that cannot be measurements: Revit 2021
writes a wall's ``NetSideArea`` twice, the first unscaled (10⁶ times too large), a slab's
``NetArea`` as the area of the whole solid, and a space's ``GrossVolume`` 1,000 times too
large. The ingest now keeps such a value as written, so the file can still be read back,
but flags it here and makes no canonical quantity from it (``ingest/plausibility.py``).
A reader summing ``value_num`` over a quantity set should filter on it.

Models loaded before this migration get ``false``: their values were never checked. The
duplicated wall areas they kept in file order are still wrong; load the file again (after
deleting the model) to get the plausible value and the flag.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMENT = (
    "True when a quantity-set value breaks a physical bound set by the other values of its "
    "set (a wall's NetSideArea above twice Length x Height, a space volume 1,000 times its "
    "floor area x height): it is stored as written but is not a measurement, and no "
    "canonical quantity is made from it. Filter it out (NOT is_implausible) before summing "
    "quantity-set values; the implausible_quantity finding says which bound it broke."
)


def upgrade() -> None:
    """Add the column."""
    op.add_column(
        "property",
        sa.Column(
            "is_implausible",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment=COMMENT,
        ),
        schema="ifc",
    )


def downgrade() -> None:
    """Drop the column."""
    op.drop_column("property", "is_implausible", schema="ifc")
