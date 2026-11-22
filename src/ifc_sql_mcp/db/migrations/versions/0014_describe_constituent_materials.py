"""Describe IFC4 constituent and profile sets in ``element_material_layer`` (audit A6).

Revit's IFC4 exporter gives doors, windows and families an ``IfcMaterialConstituentSet``
(frame, panel, glazing), which the ingest did not read: 104 elements of the audit's file A
and 295 of file C had no materials. It now reads constituents and profile sets in the
order the file lists them, without thickness, like a material list. The rows fit the
table as it is; this migration only corrects the two column comments an LLM reads to
understand them.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_INDEX = (
    "1-based position in the element's material definition: the layer order of an "
    "IfcMaterialLayerSet, or the list order of an IfcMaterialList."
)
NEW_INDEX = (
    "1-based position in the element's material definition: the layer order of an "
    "IfcMaterialLayerSet, or the order an IfcMaterialList, an IFC4 IfcMaterialConstituentSet "
    "(a door's frame and panel) or an IfcMaterialProfileSet lists its materials in."
)
OLD_THICKNESS = (
    "Layer thickness in metres; NULL when the material is not part of a layer set (a "
    "single IfcMaterial or an IfcMaterialList)."
)
NEW_THICKNESS = (
    "Layer thickness in metres; NULL when the material is not part of a layer set (a "
    "single IfcMaterial, a list, a constituent or a profile set)."
)


def _comments(index: tuple[str, str], thickness: tuple[str, str]) -> None:
    for column, kind, (new, old), nullable in (
        ("layer_index", sa.SmallInteger(), index, False),
        ("thickness_m", sa.Double(), thickness, True),
    ):
        op.alter_column(
            "element_material_layer",
            column,
            existing_type=kind,
            existing_nullable=nullable,
            comment=new,
            existing_comment=old,
            schema="ifc",
        )


def upgrade() -> None:
    """Name constituent and profile sets in the comments."""
    _comments((NEW_INDEX, OLD_INDEX), (NEW_THICKNESS, OLD_THICKNESS))


def downgrade() -> None:
    """Restore the comments."""
    _comments((OLD_INDEX, NEW_INDEX), (OLD_THICKNESS, NEW_THICKNESS))
