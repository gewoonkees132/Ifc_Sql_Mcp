"""Create ``ifc.v_dq_finding``: the current findings of each model, ready to read.

A finding in ``dq_result`` names its run, rule and object by id. To say what is wrong
with a model, a reader (the MCP ``data_quality_report`` tool, or an LLM writing SQL)
wants the latest successful run's findings with the rule's dimension and the object's
GlobalId, class and name. This view joins them once, choosing the run exactly as
``v_model_summary`` does, so its counts and the summary's always agree.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the view (and its comments) from its versioned file."""
    run_sql_file(op.get_bind(), "views/v_dq_finding.v1.sql")


def downgrade() -> None:
    """Drop the view."""
    op.execute("DROP VIEW ifc.v_dq_finding")
