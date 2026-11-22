"""Create ``ifc.ingest_issue``: the transform's issues, kept with the model they describe.

Issues exist only while a file is being ingested: they record what the pipeline could
not represent as written (a value dropped, a reference left NULL). The data quality
stage turns them into findings later, when the file may no longer be at hand, so they
are stored now, in the same transaction as the model (ADR 4).

Generated with ``alembic revision --autogenerate`` from ``schema.py``, then given this
docstring and a shorter downgrade (dropping the table drops its index).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table, its constraints, its object_id index and comments."""
    op.create_table(
        "ingest_issue",
        sa.Column(
            "model_id",
            sa.BigInteger(),
            nullable=False,
            comment="Model whose ingest reported the issue.",
        ),
        sa.Column(
            "issue_index",
            sa.Integer(),
            nullable=False,
            comment="1-based position in the order the pipeline reported its issues. The "
            "pipeline is deterministic, so the same file always gives the same numbering.",
        ),
        sa.Column(
            "code",
            sa.Text(),
            nullable=False,
            comment="Stable issue code (IssueCode in ingest/issues.py), e.g. "
            "quantity_not_a_measure. The data quality stage maps codes to rules.",
        ),
        sa.Column(
            "object_id",
            sa.BigInteger(),
            nullable=True,
            comment="Object the issue is about; NULL when it concerns the model as a whole or "
            "no loaded object has its GlobalId (a malformed one). For a repeated GlobalId it "
            "is the object that kept it.",
        ),
        sa.Column(
            "global_id",
            sa.Text(),
            nullable=True,
            comment="GlobalId of the object as written in the file, kept even when the "
            "object was not loaded; NULL when the issue concerns the model as a whole.",
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=False,
            comment="What could not be represented, the offending value, and what was done "
            "instead (dropped, left NULL, first value kept...).",
        ),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_ingest_issue_code_format")),
        sa.CheckConstraint("message ~ '\\S'", name=op.f("ck_ingest_issue_message_not_blank")),
        sa.CheckConstraint("issue_index >= 1", name=op.f("ck_ingest_issue_issue_index_positive")),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc.model.model_id"],
            name=op.f("fk_ingest_issue_model_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_ingest_issue_object_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", "issue_index", name=op.f("pk_ingest_issue")),
        schema="ifc",
        comment="What the ingest pipeline could not represent as written in the file, one "
        "row per issue. Loaded with the model in one transaction; the raw input for data "
        "quality findings.",
    )
    op.create_index(
        op.f("ix_ingest_issue_object_id"),
        "ingest_issue",
        ["object_id"],
        unique=False,
        schema="ifc",
    )


def downgrade() -> None:
    """Drop the table with its index."""
    op.drop_table("ingest_issue", schema="ifc")
