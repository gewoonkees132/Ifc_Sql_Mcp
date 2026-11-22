"""Group models by a project they are given, and mark a re-export as current (A12).

``v_storey_alignment`` v1 grouped models by ``IfcProject.Name``. The audit's Italian tower
is numbered ``0001``, Revit's default, and so is the Duplex; the tower was aligned as the
Duplex's fifth model. A weekly re-export was a second model of the same project, so a
project-wide count held both versions. This migration adds to ``ifc.model``:

* ``project_key``: the project given at ingest (``ifc-sql ingest --project``);
* ``project_group``, generated: ``project_key``, else the IFC project number, else its
  name, leaving out Revit's placeholders (``0001``, ``Project Number``,
  ``Project Name``), so a placeholder never makes two models one project;
* ``replaces_model_id``: the earlier version this model replaces
  (``ifc-sql ingest --replaces``), at most one later model each.

``v_model_current`` lists the models no later one replaces, and ``v_storey_alignment``
v2 compares those, grouped by ``project_group``.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file
from ifc_sql_mcp.db.schema import PROJECT_GROUP

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the project and version columns, v_model_current, and v_storey_alignment v2."""
    op.add_column(
        "model",
        sa.Column(
            "project_key",
            sa.Text(),
            nullable=True,
            comment="Project the model belongs to, as given at ingest (ifc-sql ingest "
            "--project), or inherited from the model it replaces. NULL when none was given.",
        ),
        schema="ifc",
    )
    op.add_column(
        "model",
        sa.Column(
            "project_group",
            sa.Text(),
            sa.Computed(PROJECT_GROUP, persisted=True),
            nullable=True,
            comment="What project-wide views group models by: project_key, else project_name, "
            "else project_long_name, leaving out Revit's placeholders (0001, Project Number, "
            "Project Name). NULL: the model joins no project.",
        ),
        schema="ifc",
    )
    op.add_column(
        "model",
        sa.Column(
            "replaces_model_id",
            sa.BigInteger(),
            nullable=True,
            comment="The earlier version of the same model that this one replaces (ifc-sql "
            "ingest --replaces). A replaced model stays loaded but is not current: see "
            "v_model_current.",
        ),
        schema="ifc",
    )
    op.create_foreign_key(
        op.f("fk_model_replaces_model_id"),
        "model",
        "model",
        ["replaces_model_id"],
        ["model_id"],
        source_schema="ifc",
        referent_schema="ifc",
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_model_replaces_model_id"), "model", ["replaces_model_id"], schema="ifc"
    )
    op.create_check_constraint(
        op.f("ck_model_project_key_not_blank"), "model", r"project_key ~ '\S'", schema="ifc"
    )
    op.create_check_constraint(
        op.f("ck_model_replaces_model_id_not_itself"),
        "model",
        "replaces_model_id <> model_id",
        schema="ifc",
    )
    run_sql_file(op.get_bind(), "views/v_model_current.v1.sql")
    op.execute("DROP VIEW ifc.v_storey_alignment")
    run_sql_file(op.get_bind(), "views/v_storey_alignment.v2.sql")


def downgrade() -> None:
    """Restore v_storey_alignment v1 and drop the view and the columns."""
    op.execute("DROP VIEW ifc.v_storey_alignment")
    run_sql_file(op.get_bind(), "views/v_storey_alignment.v1.sql")
    op.execute("DROP VIEW ifc.v_model_current")
    op.drop_column("model", "replaces_model_id", schema="ifc")
    op.drop_column("model", "project_group", schema="ifc")
    op.drop_column("model", "project_key", schema="ifc")
