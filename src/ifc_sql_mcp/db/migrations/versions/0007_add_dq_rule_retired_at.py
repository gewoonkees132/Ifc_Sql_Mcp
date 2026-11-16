"""Add ``ifc.dq_rule.retired_at``: a rule removed from the catalogue stays while it is cited.

``dq_result.rule_id`` references ``dq_rule`` without ON DELETE CASCADE, on purpose:
results are kept per run so quality can be compared over time, and a finding must keep
saying which rule it broke. So when ``rules/dq_rules.yaml`` drops a rule that earlier
runs found violations of, the rule row cannot go. The sync keeps it and sets
``retired_at``, which tells a reader that later runs no longer evaluate it (ADR 5). A
rule nothing refers to is simply deleted.

The table comment now says where the catalogue lives and what retiring means.

Generated with ``alembic revision --autogenerate`` from ``schema.py``, then given this
docstring.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TABLE_COMMENT = "Catalogue of data quality rules, synchronised from rules/dq_rules.yaml."
NEW_TABLE_COMMENT = (
    "Catalogue of data quality rules, synchronised from the package file "
    "rules/dq_rules.yaml at the start of every `ifc-sql dq`. Rules removed from the file "
    "are kept, with retired_at set, while findings refer to them."
)


def upgrade() -> None:
    """Add the column and update the table comment."""
    op.add_column(
        "dq_rule",
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the rule was removed from the catalogue; NULL while it is in it and "
            "every run evaluates it. A retired rule is kept only while findings of earlier "
            "runs refer to it.",
        ),
        schema="ifc",
    )
    op.create_table_comment(
        "dq_rule", NEW_TABLE_COMMENT, existing_comment=OLD_TABLE_COMMENT, schema="ifc"
    )


def downgrade() -> None:
    """Restore the old comment and drop the column."""
    op.create_table_comment(
        "dq_rule", OLD_TABLE_COMMENT, existing_comment=NEW_TABLE_COMMENT, schema="ifc"
    )
    op.drop_column("dq_rule", "retired_at", schema="ifc")
