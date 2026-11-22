"""Tell the model's gaps from the loader's: ``dq_rule.scope`` (audit finding A9).

On the audit's Revit 2022 tower, ``ifc-sql dq`` reported 1,458 errors, and none was
something the architect could fix: they were door panel sets this tool did not read.
Each rule now says what its findings are about:

* ``model_content``: what the file contains, as its author modelled it or Revit's
  exporter wrote it (a placeholder value, a missing fire rating, a room exported twice);
* ``loader_coverage``: something in the file that this tool does not read or convert
  (an unsupported property kind, a unit it has no factor for).

The catalogue states the scope of every rule and ``ifc-sql dq`` synchronises it. This
migration fills it in for the rules a database may already hold: the seven
``loader_coverage`` ones below, as the catalogue classifies them, and ``model_content``
for every other (including retired rules, which no run evaluates any more).
``v_dq_finding`` v2 adds the scope to each finding.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOADER_COVERAGE = (
    "unit_not_convertible",
    "measure_not_converted",
    "revit_name_unparsed",
    "unsupported_property",
    "unsupported_property_set",
    "unsupported_classification",
    "unsupported_material",
)
COMMENT = (
    "model_content: the finding is about what the file contains, which its author or "
    "Revit's exporter decided, so a re-export can change it. loader_coverage: something "
    "in the file that this tool does not read or convert; the model is not at fault."
)


def upgrade() -> None:
    """Add the scope, fill it in, constrain it, and show it in v_dq_finding."""
    op.add_column(
        "dq_rule", sa.Column("scope", sa.Text(), nullable=True, comment=COMMENT), schema="ifc"
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE ifc.dq_rule SET scope = CASE WHEN rule_id = ANY(:loader) "
            "THEN 'loader_coverage' ELSE 'model_content' END"
        ),
        {"loader": list(LOADER_COVERAGE)},
    )
    op.alter_column("dq_rule", "scope", existing_type=sa.Text(), nullable=False, schema="ifc")
    op.create_check_constraint(
        op.f("ck_dq_rule_scope_valid"),
        "dq_rule",
        "scope IN ('model_content', 'loader_coverage')",
        schema="ifc",
    )
    op.execute("DROP VIEW ifc.v_dq_finding")
    run_sql_file(op.get_bind(), "views/v_dq_finding.v2.sql")


def downgrade() -> None:
    """Restore v_dq_finding v1 and drop the scope."""
    op.execute("DROP VIEW ifc.v_dq_finding")
    run_sql_file(op.get_bind(), "views/v_dq_finding.v1.sql")
    op.drop_column("dq_rule", "scope", schema="ifc")
