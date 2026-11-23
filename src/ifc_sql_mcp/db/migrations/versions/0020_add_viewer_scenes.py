"""Create schema ``ifc_viewer``: one 3D scene per model, for the viewer (ADR 14).

``ifc-sql ingest --viewer`` triangulates every element and space with IfcOpenShell while
the file is open, and stores the result here as glTF binary (GLB) chunks, storey by
storey. ``ifc-sql view`` serves them. The database keeps no copy of the file itself, so
this is the only way the page can show a model.

The scenes are derived data and can be rebuilt at any time, so they live in a schema of
their own:

* ``run_sql``'s validator reads only schema ``ifc``, so a query cannot pull megabytes of
  geometry through the MCP server, and ``describe_schema`` does not list them;
* ``ifc_reader`` gets explicit ``SELECT`` on the two tables, because ``ifc-sql view``
  reads them as that role. No default privileges: a later table in ``ifc_viewer`` is not
  readable unless a migration says so.

Generated with ``alembic revision --autogenerate`` from ``schema.py``, then given the
schema, the grants, this docstring and a shorter downgrade.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the schema and its two tables, and let ifc_reader read them."""
    op.execute("CREATE SCHEMA ifc_viewer")
    op.execute(
        "COMMENT ON SCHEMA ifc_viewer IS 'The viewer''s 3D scenes, one per model, built by "
        "ifc-sql ingest --viewer. Derived from the files, so they can be rebuilt.'"
    )
    op.create_table(
        "scene",
        sa.Column(
            "model_id",
            sa.BigInteger(),
            nullable=False,
            comment="The model the scene shows. Deleting the model deletes its scene.",
        ),
        sa.Column(
            "format_version",
            sa.Integer(),
            nullable=False,
            comment="Version of the scene's format. ifc-sql ingest --viewer replaces a scene "
            "of an older version and keeps a current one.",
        ),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the scene was committed.",
        ),
        sa.Column(
            "build_seconds",
            sa.Double(),
            nullable=False,
            comment="Seconds spent triangulating the file's bodies and writing the chunks. "
            "With --geometry, the same pass also measured the shapes.",
        ),
        sa.Column(
            "ifcopenshell_version",
            sa.Text(),
            nullable=False,
            comment="The IfcOpenShell release that triangulated the bodies.",
        ),
        sa.Column(
            "origin_x_m",
            sa.Double(),
            nullable=False,
            comment="X of the scene's origin, in the file's world coordinates in metres: the "
            "centre of the drawn elements' bounding box, rounded to whole metres. The chunks' "
            "positions are relative to it.",
        ),
        sa.Column(
            "origin_y_m", sa.Double(), nullable=False, comment="Y of the scene's origin, in metres."
        ),
        sa.Column(
            "origin_z_m",
            sa.Double(),
            nullable=False,
            comment="Z of the scene's origin (up, in IFC), in metres.",
        ),
        sa.Column(
            "element_count",
            sa.Integer(),
            nullable=False,
            comment="Elements drawn: those with a body of their own, and aggregates drawn "
            "through their parts (a stair through its flights). Openings are not counted.",
        ),
        sa.Column(
            "without_shape_count",
            sa.Integer(),
            nullable=False,
            comment="Elements not drawn because they have no body that IfcOpenShell "
            "triangulates, and no part that has one. Openings are not counted.",
        ),
        sa.Column(
            "opening_count",
            sa.Integer(),
            nullable=False,
            comment="Feature elements (openings, voids), never drawn: IfcOpenShell cuts them "
            "out of the elements they belong to. element_count + without_shape_count + "
            "opening_count is the number of elements in ifc.element.",
        ),
        sa.Column(
            "byte_count",
            sa.BigInteger(),
            nullable=False,
            comment="Bytes of all the scene's chunks together.",
        ),
        sa.CheckConstraint(
            "build_seconds >= 0 AND build_seconds < 'Infinity'",
            name=op.f("ck_scene_build_seconds_non_negative"),
        ),
        sa.CheckConstraint("byte_count >= 0", name=op.f("ck_scene_byte_count_non_negative")),
        sa.CheckConstraint("element_count >= 0", name=op.f("ck_scene_element_count_non_negative")),
        sa.CheckConstraint("format_version >= 1", name=op.f("ck_scene_format_version_positive")),
        sa.CheckConstraint(
            "ifcopenshell_version ~ '\\S'", name=op.f("ck_scene_ifcopenshell_version_not_blank")
        ),
        sa.CheckConstraint("opening_count >= 0", name=op.f("ck_scene_opening_count_non_negative")),
        sa.CheckConstraint(
            "origin_x_m > '-Infinity' AND origin_x_m < 'Infinity'",
            name=op.f("ck_scene_origin_x_m_finite"),
        ),
        sa.CheckConstraint(
            "origin_y_m > '-Infinity' AND origin_y_m < 'Infinity'",
            name=op.f("ck_scene_origin_y_m_finite"),
        ),
        sa.CheckConstraint(
            "origin_z_m > '-Infinity' AND origin_z_m < 'Infinity'",
            name=op.f("ck_scene_origin_z_m_finite"),
        ),
        sa.CheckConstraint(
            "without_shape_count >= 0", name=op.f("ck_scene_without_shape_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc.model.model_id"],
            name=op.f("fk_scene_model_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", name=op.f("pk_scene")),
        schema="ifc_viewer",
        comment="One 3D scene per model, built by ifc-sql ingest --viewer and shown by "
        "ifc-sql view: what was drawn, and from where. Derived from the file, so it can be "
        "rebuilt.",
    )
    op.create_table(
        "scene_chunk",
        sa.Column(
            "model_id",
            sa.BigInteger(),
            nullable=False,
            comment="The scene the chunk belongs to.",
        ),
        sa.Column(
            "chunk_no",
            sa.Integer(),
            nullable=False,
            comment="0-based number of the chunk within its scene, in the order the page "
            "loads them: elements, then spaces, copies and Areas, each storey by storey.",
        ),
        sa.Column(
            "storey_id",
            sa.BigInteger(),
            nullable=True,
            comment="The storey whose elements or spaces the chunk holds; NULL for those on "
            "no storey. A storey with more than 2,000 elements or 16 MiB is split into several.",
        ),
        sa.Column(
            "kind",
            sa.Text(),
            nullable=False,
            comment="elements: the drawn elements. spaces: the rooms. space_copies: spaces "
            "that repeat a room (spatial_element.repeats_id). areas: Revit area plans "
            "(category Areas). Spaces are shown only when a selection holds one.",
        ),
        sa.Column(
            "element_count",
            sa.Integer(),
            nullable=False,
            comment="Elements, or spaces, drawn in the chunk, aggregates included.",
        ),
        sa.Column(
            "glb",
            sa.LargeBinary(),
            nullable=False,
            comment="The chunk as glTF 2.0 binary (GLB): Y up, metres, float32 positions "
            "relative to the scene's origin. One node per element, named by its GlobalId, "
            "with its IFC class in extras; an aggregate's parts are its children.",
        ),
        sa.Column(
            "glb_sha256",
            sa.Text(),
            sa.Computed("encode(sha256(glb), 'hex')", persisted=True),
            nullable=False,
            comment="SHA-256 of glb (lower-case hex), computed by the database: the chunk's ETag.",
        ),
        sa.CheckConstraint("chunk_no >= 0", name=op.f("ck_scene_chunk_chunk_no_non_negative")),
        sa.CheckConstraint(
            "element_count >= 1", name=op.f("ck_scene_chunk_element_count_positive")
        ),
        sa.CheckConstraint(
            "substring(glb FROM 1 FOR 4) = '\\x676c5446'::bytea",
            name=op.f("ck_scene_chunk_glb_is_gltf_binary"),
        ),
        sa.CheckConstraint(
            "kind IN ('elements', 'spaces', 'space_copies', 'areas')",
            name=op.f("ck_scene_chunk_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc_viewer.scene.model_id"],
            name=op.f("fk_scene_chunk_model_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["storey_id"],
            ["ifc.spatial_element.object_id"],
            name=op.f("fk_scene_chunk_storey_id"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("model_id", "chunk_no", name=op.f("pk_scene_chunk")),
        schema="ifc_viewer",
        comment="A scene in parts the page loads and toggles one by one: per storey, and per "
        "kind of object.",
    )
    op.create_index(
        op.f("ix_scene_chunk_storey_id"),
        "scene_chunk",
        ["storey_id"],
        unique=False,
        schema="ifc_viewer",
    )
    # Explicit grants, as migration 0003 gives ifc: ifc-sql view reads the scenes as
    # ifc_reader. No ALTER DEFAULT PRIVILEGES here, so a later table stays unreadable
    # unless its own migration grants it.
    op.execute("GRANT USAGE ON SCHEMA ifc_viewer TO ifc_reader")
    op.execute("GRANT SELECT ON ifc_viewer.scene, ifc_viewer.scene_chunk TO ifc_reader")


def downgrade() -> None:
    """Drop the schema, its tables and the grants on them."""
    op.execute("DROP SCHEMA ifc_viewer CASCADE")
