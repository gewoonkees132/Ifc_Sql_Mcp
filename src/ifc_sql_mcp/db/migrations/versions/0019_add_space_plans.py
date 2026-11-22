"""Find rooms exported twice by their plans, and tell Revit Areas from rooms (R1).

The re-check (docs/audit/2026-11-recheck.md) found room totals counting the same floor two
to four times. A7's key for a copy (number, name, storey and area) misses copies whose
names differ: 28 of file C's 75. And file E's 25 Revit Areas, its area plans, were added
to its rooms. Ingest now measures each space's plan and compares them
(ingest/footprints.py). Three columns of ``ifc.spatial_element`` keep the result:

* ``revit_category``: Rooms, Spaces or Areas, as the file states it;
* ``footprint_m2``: the area of the plan;
* ``repeats_id``: the larger space whose floor this one covers again.

``v_space_schedule`` v3 takes ``duplicate_of`` from ``repeats_id``, keeps A7's key for a
space without a plan, and adds ``category`` and ``footprint_m2``. Models loaded before this
revision have no plans until they are loaded again, so their schedule keeps A7's key.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the three columns and replace v_space_schedule v2 with v3."""
    op.add_column(
        "spatial_element",
        sa.Column(
            "revit_category",
            sa.Text(),
            nullable=True,
            comment="Spaces: the Revit category the file states (Pset_SpaceCommon or Revit's "
            "Other set): Rooms, Spaces or Areas. Areas are Revit's area plans, not rooms. NULL "
            "when the file states none.",
        ),
        schema="ifc",
    )
    op.add_column(
        "spatial_element",
        sa.Column(
            "footprint_m2",
            sa.Double(),
            nullable=True,
            comment="Spaces: area of the plan, the body seen from above, measured at ingest in "
            "square metres. NULL when the space has no body that gives one.",
        ),
        schema="ifc",
    )
    op.add_column(
        "spatial_element",
        sa.Column(
            "repeats_id",
            sa.BigInteger(),
            nullable=True,
            comment="Spaces: the larger space on the same storey whose floor this one covers "
            "again (its plan lies at least 90 % inside larger ones): a room exported twice, as "
            "a Revit Room and an MEP Space. NULL when it repeats none.",
        ),
        schema="ifc",
    )
    op.create_foreign_key(
        op.f("fk_spatial_element_repeats_id"),
        "spatial_element",
        "spatial_element",
        ["repeats_id"],
        ["object_id"],
        source_schema="ifc",
        referent_schema="ifc",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        op.f("ix_spatial_element_repeats_id"), "spatial_element", ["repeats_id"], schema="ifc"
    )
    op.create_check_constraint(
        op.f("ck_spatial_element_footprint_m2_positive"),
        "spatial_element",
        "footprint_m2 > 0",
        schema="ifc",
    )
    op.create_check_constraint(
        op.f("ck_spatial_element_repeats_is_not_self"),
        "spatial_element",
        "repeats_id <> object_id",
        schema="ifc",
    )
    op.execute("DROP VIEW ifc.v_space_schedule")
    run_sql_file(op.get_bind(), "views/v_space_schedule.v3.sql")


def downgrade() -> None:
    """Restore v_space_schedule v2 and drop the three columns."""
    op.execute("DROP VIEW ifc.v_space_schedule")
    run_sql_file(op.get_bind(), "views/v_space_schedule.v2.sql")
    op.drop_column("spatial_element", "repeats_id", schema="ifc")
    op.drop_column("spatial_element", "footprint_m2", schema="ifc")
    op.drop_column("spatial_element", "revit_category", schema="ifc")
