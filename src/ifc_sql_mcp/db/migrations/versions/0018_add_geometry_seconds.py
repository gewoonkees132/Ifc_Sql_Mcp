"""Record how long measuring an export's shapes took (``ingest --geometry``, A5).

``ifc-sql ingest --geometry`` triangulates and measures the shapes of a file that carries
no quantities. On the audit's tower that took 54 s on 4 cores, much longer than reading
the file, so each run records its own time in ``ingestion_run.geometry_seconds``. It is
NULL for a run that did not measure.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ingestion_run.geometry_seconds."""
    op.add_column(
        "ingestion_run",
        sa.Column(
            "geometry_seconds",
            sa.Double(),
            nullable=True,
            comment="Seconds spent triangulating and measuring shapes (ifc-sql ingest "
            "--geometry); NULL when the run did not measure geometry.",
        ),
        schema="ifc",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_run_geometry_seconds_non_negative"),
        "ingestion_run",
        "geometry_seconds >= 0",
        schema="ifc",
    )


def downgrade() -> None:
    """Drop ingestion_run.geometry_seconds."""
    op.drop_column("ingestion_run", "geometry_seconds", schema="ifc")
