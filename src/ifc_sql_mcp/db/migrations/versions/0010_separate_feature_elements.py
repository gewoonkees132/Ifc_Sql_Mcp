"""Separate feature elements (openings) from the elements they cut (audit finding A3).

Revit names an opening after its host: the opening in wall 1044048 is
``Basic Wall:1AR_MUR_CV003 …:1044048:1``. The name parser therefore gave the opening the
wall's family and type, and every count by Revit type counted each wall's openings as
more walls (2,330 "walls" of a type with 1,165 on the audit's Revit 2022 tower).

This migration adds to ``ifc.element``:

* ``is_feature``: true for IfcFeatureElement and its subtypes (openings, voids,
  projections), which shape another element and are no building part of their own;
* ``host_revit_family`` and ``host_revit_type``: the parts of a feature element's name,
  which are its host's. Its ``revit_family`` and ``revit_type`` are NULL, so a filter or a
  grouping on them finds only real elements of the type.

Two CHECK constraints keep the two kinds of name apart. Models already loaded are
backfilled from their stored class: the class names below are every subtype of
IfcFeatureElement in IFC2X3 and IFC4, as ``ifc_class_base`` holds them (the transform maps
IfcOpeningStandardCase to IfcOpeningElement). New loads decide from the class's
inheritance in the file's own schema (``transform.is_feature``).

``v_element`` v3 adds the three columns; nothing reads from v_element, so it is dropped
and recreated, and ALTER DEFAULT PRIVILEGES (0003) gives it to ``ifc_reader`` again.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ifc_sql_mcp.db.migrate import run_sql_file

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FEATURE_CLASSES = (
    "IfcFeatureElement",
    "IfcFeatureElementAddition",
    "IfcFeatureElementSubtraction",
    "IfcOpeningElement",
    "IfcVoidingFeature",
    "IfcProjectionElement",
    "IfcSurfaceFeature",
    "IfcEdgeFeature",
    "IfcChamferEdgeFeature",
    "IfcRoundedEdgeFeature",
)

OLD_FAMILY_COMMENT = "Family part of a Revit Family:Type:ElementId name."
NEW_FAMILY_COMMENT = (
    "Family part of a Revit Family:Type:ElementId name. NULL for feature elements "
    "(openings), whose name is their host's: see host_revit_family."
)
OLD_TYPE_COMMENT = (
    "Type part of a Revit name: its second segment, so never containing ':'. In "
    "Family:Type:Label:ElementId the third segment is an instance label, not part of "
    "the type."
)
NEW_TYPE_COMMENT = (
    "Type part of a Revit name: its second segment, so never containing ':'. In "
    "Family:Type:Label:ElementId the third segment is an instance label, not part of the "
    "type. NULL for feature elements (openings), whose name is their host's."
)


def upgrade() -> None:
    """Add the columns, backfill loaded models, constrain them and replace v_element."""
    op.add_column(
        "element",
        sa.Column(
            "is_feature",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="True for an IfcFeatureElement (opening, void, projection): it shapes "
            "another element and is no building part of its own. Takeoffs leave such "
            "elements out unless asked; their Revit name parts are in host_revit_family and "
            "host_revit_type.",
        ),
        schema="ifc",
    )
    op.add_column(
        "element",
        sa.Column(
            "host_revit_family",
            sa.Text(),
            nullable=True,
            comment="Feature elements only: the family part of their Revit name, which is "
            "that of the element they cut or add to (an opening in a wall is named after the "
            "wall).",
        ),
        schema="ifc",
    )
    op.add_column(
        "element",
        sa.Column(
            "host_revit_type",
            sa.Text(),
            nullable=True,
            comment="Feature elements only: the type part of their Revit name, the host's type.",
        ),
        schema="ifc",
    )
    op.get_bind().execute(
        sa.text(
            """
            UPDATE ifc.element AS e
            SET is_feature = true,
                host_revit_family = e.revit_family,
                host_revit_type = e.revit_type,
                revit_family = NULL,
                revit_type = NULL
            FROM ifc.object AS o
            WHERE o.object_id = e.object_id AND o.ifc_class_base = ANY(:classes)
            """
        ),
        {"classes": list(FEATURE_CLASSES)},
    )
    op.create_check_constraint(
        op.f("ck_element_host_revit_name_only_for_features"),
        "element",
        "is_feature OR (host_revit_family IS NULL AND host_revit_type IS NULL)",
        schema="ifc",
    )
    op.create_check_constraint(
        op.f("ck_element_features_have_no_revit_type"),
        "element",
        "NOT is_feature OR (revit_family IS NULL AND revit_type IS NULL)",
        schema="ifc",
    )
    _comment("revit_family", NEW_FAMILY_COMMENT, OLD_FAMILY_COMMENT)
    _comment("revit_type", NEW_TYPE_COMMENT, OLD_TYPE_COMMENT)
    op.execute("DROP VIEW ifc.v_element")
    run_sql_file(op.get_bind(), "views/v_element.v3.sql")


def downgrade() -> None:
    """Restore v_element v2, give openings their host's name parts back, drop the columns."""
    op.execute("DROP VIEW ifc.v_element")
    run_sql_file(op.get_bind(), "views/v_element.v2.sql")
    _comment("revit_type", OLD_TYPE_COMMENT, NEW_TYPE_COMMENT)
    _comment("revit_family", OLD_FAMILY_COMMENT, NEW_FAMILY_COMMENT)
    for name in (
        "ck_element_features_have_no_revit_type",
        "ck_element_host_revit_name_only_for_features",
    ):
        op.drop_constraint(op.f(name), "element", type_="check", schema="ifc")
    op.execute(
        "UPDATE ifc.element SET revit_family = host_revit_family, "
        "revit_type = host_revit_type WHERE is_feature"
    )
    op.drop_column("element", "host_revit_type", schema="ifc")
    op.drop_column("element", "host_revit_family", schema="ifc")
    op.drop_column("element", "is_feature", schema="ifc")


def _comment(column: str, new: str, old: str) -> None:
    op.alter_column(
        "element",
        column,
        existing_type=sa.Text(),
        comment=new,
        existing_comment=old,
        schema="ifc",
    )
