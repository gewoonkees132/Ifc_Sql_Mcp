"""Create the ifc schema and its tables, constraints, indexes and comments.

Generated with ``alembic revision --autogenerate`` from ``ifc_sql_mcp/db/schema.py`` and
then edited (schema creation, a shorter downgrade). Like every migration it is frozen: it
must keep producing the same tables, so it does not import schema.py. The integration
test ``test_schema_py_matches_migrated_database`` proves the two still agree.

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children before parents, so that every foreign key is gone before its target.
TABLES_IN_DROP_ORDER = (
    "element_material_layer",
    "element",
    "spatial_element",
    "quantity",
    "property",
    "dq_result",
    "classification",
    "object",
    "material",
    "ingestion_run",
    "dq_run",
    "model",
    "dq_rule",
)


def upgrade() -> None:
    """Create the schema, then every table with its constraints, indexes and comments."""
    op.execute("CREATE SCHEMA ifc")
    op.execute(
        "COMMENT ON SCHEMA ifc IS "
        "'Building data loaded from IFC files (Revit exports): models, spatial structure, "
        "elements, properties, quantities, materials, classifications and data quality.'"
    )
    op.create_table(
        "dq_rule",
        sa.Column(
            "rule_id", sa.Text(), nullable=False, comment="Stable rule identifier, snake_case."
        ),
        sa.Column(
            "description", sa.Text(), nullable=False, comment="What the rule checks and why."
        ),
        sa.Column(
            "severity",
            sa.Text(),
            nullable=False,
            comment="Default severity of a finding: error | warning | info.",
        ),
        sa.Column(
            "dimension",
            sa.Text(),
            nullable=False,
            comment="Data quality dimension: completeness | validity | consistency | uniqueness.",
        ),
        sa.CheckConstraint("description ~ '\\S'", name=op.f("ck_dq_rule_description_not_blank")),
        sa.CheckConstraint(
            "dimension IN ('completeness', 'validity', 'consistency', 'uniqueness')",
            name=op.f("ck_dq_rule_dimension_valid"),
        ),
        sa.CheckConstraint("rule_id ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_dq_rule_rule_id_format")),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')", name=op.f("ck_dq_rule_severity_valid")
        ),
        sa.PrimaryKeyConstraint("rule_id", name=op.f("pk_dq_rule")),
        schema="ifc",
        comment="Catalogue of data quality rules, synchronised from rules/dq_rules.yaml.",
    )
    op.create_table(
        "model",
        sa.Column(
            "model_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment="Surrogate key of the model.",
        ),
        sa.Column(
            "file_name", sa.Text(), nullable=False, comment="Base name of the ingested IFC file."
        ),
        sa.Column(
            "file_sha256",
            sa.Text(),
            nullable=False,
            comment="SHA-256 of the file bytes (lower-case hex); identifies one file version.",
        ),
        sa.Column(
            "file_size_bytes", sa.BigInteger(), nullable=False, comment="Size of the file in bytes."
        ),
        sa.Column(
            "ifc_schema",
            sa.Text(),
            nullable=False,
            comment="IFC schema from the file header FILE_SCHEMA, e.g. IFC2X3 or IFC4.",
        ),
        sa.Column(
            "mvd",
            sa.Text(),
            nullable=True,
            comment=(
                "Model View Definition from the header FILE_DESCRIPTION, e.g. CoordinationView."
            ),
        ),
        sa.Column(
            "authoring_app",
            sa.Text(),
            nullable=True,
            comment="Exporting application and version (IfcApplication), e.g. Revit Architecture.",
        ),
        sa.Column(
            "discipline",
            sa.Text(),
            nullable=False,
            comment="Discipline code given at ingest, e.g. ARC, STR, MEP, ELE, PLU.",
        ),
        sa.Column(
            "project_global_id",
            sa.Text(),
            nullable=True,
            comment=(
                "IfcProject GlobalId. Not a reliable cross-file key: discipline models of one "
                "building can be exported from different Revit projects."
            ),
        ),
        sa.Column(
            "project_name",
            sa.Text(),
            nullable=True,
            comment="IfcProject.Name (Revit: Project Number).",
        ),
        sa.Column(
            "project_long_name",
            sa.Text(),
            nullable=True,
            comment="IfcProject.LongName (Revit: Project Name).",
        ),
        sa.Column(
            "length_unit",
            sa.Text(),
            nullable=False,
            comment=(
                "Length unit declared by the file (e.g. m, mm, ft). Every *_m column in this "
                "schema has already been converted from it to metres."
            ),
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the model was committed to the database.",
        ),
        sa.CheckConstraint("discipline ~ '^[A-Z]{2,8}$'", name=op.f("ck_model_discipline_format")),
        sa.CheckConstraint("file_name ~ '\\S'", name=op.f("ck_model_file_name_not_blank")),
        sa.CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_model_file_sha256_hex")),
        sa.CheckConstraint(
            "ifc_schema ~ '^IFC[0-9A-Z_]+$'", name=op.f("ck_model_ifc_schema_format")
        ),
        sa.CheckConstraint("length_unit ~ '\\S'", name=op.f("ck_model_length_unit_not_blank")),
        sa.CheckConstraint(
            "project_global_id ~ '^[0-3][0-9A-Za-z_$]{21}$'",
            name=op.f("ck_model_project_global_id_format"),
        ),
        sa.CheckConstraint("file_size_bytes > 0", name=op.f("ck_model_file_size_bytes_positive")),
        sa.PrimaryKeyConstraint("model_id", name=op.f("pk_model")),
        sa.UniqueConstraint("file_sha256", name=op.f("uq_model_file_sha256")),
        schema="ifc",
        comment=(
            "One row per ingested IFC file version. Deleting a model deletes everything loaded"
            " from it."
        ),
    )
    op.create_table(
        "dq_run",
        sa.Column(
            "dq_run_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment="Surrogate key of the run.",
        ),
        sa.Column(
            "model_id",
            sa.BigInteger(),
            nullable=False,
            comment="Model the rules were evaluated against.",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the run started.",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the run ended; NULL while running.",
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'running'"),
            nullable=False,
            comment="running | succeeded | failed.",
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="Error message of a failed run; NULL otherwise.",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)", name=op.f("ck_dq_run_error_iff_failed")
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name=op.f("ck_dq_run_finished_iff_done")
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name=op.f("ck_dq_run_status_valid")
        ),
        sa.CheckConstraint(
            "finished_at >= started_at", name=op.f("ck_dq_run_finished_after_started")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc.model.model_id"],
            name=op.f("fk_dq_run_model_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dq_run_id", name=op.f("pk_dq_run")),
        schema="ifc",
        comment=(
            "One evaluation of the data quality rules against one model. Results are kept per "
            "run so quality can be compared over time."
        ),
    )
    op.create_index(op.f("ix_dq_run_model_id"), "dq_run", ["model_id"], unique=False, schema="ifc")
    op.create_table(
        "ingestion_run",
        sa.Column(
            "ingestion_run_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment="Surrogate key of the run.",
        ),
        sa.Column(
            "model_id",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Model the run produced or found already loaded; NULL if the run failed before"
                " a model was committed, or the model was later deleted."
            ),
        ),
        sa.Column(
            "file_name", sa.Text(), nullable=False, comment="Base name of the file the run read."
        ),
        sa.Column(
            "file_sha256",
            sa.Text(),
            nullable=False,
            comment="SHA-256 of the file the run read, kept even when model_id is NULL.",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the run started.",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the run ended; NULL while running.",
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'running'"),
            nullable=False,
            comment=(
                "running | succeeded | failed | skipped (file already loaded, nothing written)."
            ),
        ),
        sa.Column(
            "row_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                'Rows written per table, e.g. {"element": 268}. An audit record, never '
                "filtered on, so a JSON object is simpler than a column per table."
            ),
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="Error message of a failed run; NULL otherwise.",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)",
            name=op.f("ck_ingestion_run_error_iff_failed"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_ingestion_run_finished_iff_done"),
        ),
        sa.CheckConstraint("file_name ~ '\\S'", name=op.f("ck_ingestion_run_file_name_not_blank")),
        sa.CheckConstraint(
            "file_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_ingestion_run_file_sha256_hex")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(row_counts) = 'object'",
            name=op.f("ck_ingestion_run_row_counts_is_object"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped')",
            name=op.f("ck_ingestion_run_status_valid"),
        ),
        sa.CheckConstraint(
            "finished_at >= started_at", name=op.f("ck_ingestion_run_finished_after_started")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc.model.model_id"],
            name=op.f("fk_ingestion_run_model_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("ingestion_run_id", name=op.f("pk_ingestion_run")),
        schema="ifc",
        comment=(
            "Audit log of ingest attempts, one row per run, including failed and skipped runs."
            " Written outside the load transaction so failures are recorded too."
        ),
    )
    op.create_index(
        op.f("ix_ingestion_run_file_sha256"),
        "ingestion_run",
        ["file_sha256"],
        unique=False,
        schema="ifc",
    )
    op.create_index(
        op.f("ix_ingestion_run_model_id"), "ingestion_run", ["model_id"], unique=False, schema="ifc"
    )
    op.create_table(
        "material",
        sa.Column(
            "material_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment="Surrogate key of the material.",
        ),
        sa.Column(
            "model_id",
            sa.BigInteger(),
            nullable=False,
            comment=(
                "Model the material belongs to. IFC materials have no GlobalId, so they are "
                "scoped to their file."
            ),
        ),
        sa.Column(
            "name", sa.Text(), nullable=False, comment="IfcMaterial.Name, e.g. Masonry - Brick."
        ),
        sa.CheckConstraint("name ~ '\\S'", name=op.f("ck_material_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc.model.model_id"],
            name=op.f("fk_material_model_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("material_id", name=op.f("pk_material")),
        sa.UniqueConstraint("model_id", "name", name=op.f("uq_material_model_id_name")),
        schema="ifc",
        comment="Materials used in a model, one row per distinct name.",
    )
    op.create_table(
        "object",
        sa.Column(
            "object_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment="Surrogate key, shared with the object's spatial_element or element row.",
        ),
        sa.Column(
            "model_id", sa.BigInteger(), nullable=False, comment="Model the object was loaded from."
        ),
        sa.Column(
            "global_id",
            sa.Text(),
            nullable=False,
            comment=(
                "IFC GlobalId (22 characters). Unique within a model; Revit keeps it stable "
                "when the same project is exported again."
            ),
        ),
        sa.Column(
            "object_kind",
            sa.Text(),
            nullable=False,
            comment="Which subtype table holds the object's details: spatial_element or element.",
        ),
        sa.Column(
            "ifc_class",
            sa.Text(),
            nullable=False,
            comment="Exact IFC entity, e.g. IfcWallStandardCase.",
        ),
        sa.Column(
            "ifc_class_base",
            sa.Text(),
            nullable=False,
            comment=(
                "Normalised IFC entity for filtering and grouping, derived from the IFC "
                "inheritance tree, e.g. IfcWall for both IfcWall and IfcWallStandardCase."
            ),
        ),
        sa.Column(
            "name",
            sa.Text(),
            nullable=True,
            comment=(
                "IFC Name attribute, verbatim. Revit elements: Family:Type:ElementId; storeys:"
                " level name; spaces: room number."
            ),
        ),
        sa.CheckConstraint(
            "global_id ~ '^[0-3][0-9A-Za-z_$]{21}$'", name=op.f("ck_object_global_id_format")
        ),
        sa.CheckConstraint(
            "ifc_class ~ '^Ifc[A-Z][A-Za-z0-9]*$'", name=op.f("ck_object_ifc_class_format")
        ),
        sa.CheckConstraint(
            "ifc_class_base ~ '^Ifc[A-Z][A-Za-z0-9]*$'",
            name=op.f("ck_object_ifc_class_base_format"),
        ),
        sa.CheckConstraint(
            "object_kind IN ('spatial_element', 'element')",
            name=op.f("ck_object_object_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ifc.model.model_id"],
            name=op.f("fk_object_model_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("object_id", name=op.f("pk_object")),
        sa.UniqueConstraint("model_id", "global_id", name=op.f("uq_object_model_id_global_id")),
        schema="ifc",
        comment=(
            "Supertype of every IFC product: one row per spatial element or physical element. "
            "Properties, quantities and classifications attach here."
        ),
    )
    op.create_index(
        op.f("ix_object_global_id"), "object", ["global_id"], unique=False, schema="ifc"
    )
    op.create_index(
        op.f("ix_object_ifc_class_base"), "object", ["ifc_class_base"], unique=False, schema="ifc"
    )
    op.create_table(
        "classification",
        sa.Column("object_id", sa.BigInteger(), nullable=False, comment="Classified object."),
        sa.Column(
            "system",
            sa.Text(),
            nullable=False,
            comment="Classification system, e.g. OmniClass Table 13 or UniFormat.",
        ),
        sa.Column(
            "code", sa.Text(), nullable=False, comment="Code within the system, e.g. 13-51 24 11."
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=True,
            comment="Human-readable title of the code, when the file gives one.",
        ),
        sa.CheckConstraint("code ~ '\\S'", name=op.f("ck_classification_code_not_blank")),
        sa.CheckConstraint("system ~ '\\S'", name=op.f("ck_classification_system_not_blank")),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_classification_object_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("object_id", "system", "code", name=op.f("pk_classification")),
        schema="ifc",
        comment=(
            "Classification codes, from IfcClassificationReference or from Revit parameters "
            "such as OmniClass Table 13 Category."
        ),
    )
    op.create_index(
        op.f("ix_classification_system_code"),
        "classification",
        ["system", "code"],
        unique=False,
        schema="ifc",
    )
    op.create_table(
        "dq_result",
        sa.Column(
            "dq_result_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment="Surrogate key of the finding.",
        ),
        sa.Column(
            "dq_run_id",
            sa.BigInteger(),
            nullable=False,
            comment="Run that produced the finding; its model_id says which model it is about.",
        ),
        sa.Column("rule_id", sa.Text(), nullable=False, comment="Rule that was violated."),
        sa.Column(
            "object_id",
            sa.BigInteger(),
            nullable=True,
            comment="Offending object; NULL for a finding about the model as a whole.",
        ),
        sa.Column(
            "severity",
            sa.Text(),
            nullable=False,
            comment="Severity at the time of the run (a rule's default may change later).",
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=False,
            comment="Human-readable description of the finding.",
        ),
        sa.CheckConstraint("message ~ '\\S'", name=op.f("ck_dq_result_message_not_blank")),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')", name=op.f("ck_dq_result_severity_valid")
        ),
        sa.ForeignKeyConstraint(
            ["dq_run_id"],
            ["ifc.dq_run.dq_run_id"],
            name=op.f("fk_dq_result_dq_run_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_dq_result_object_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["ifc.dq_rule.rule_id"], name=op.f("fk_dq_result_rule_id")
        ),
        sa.PrimaryKeyConstraint("dq_result_id", name=op.f("pk_dq_result")),
        schema="ifc",
        comment="Data quality findings: one row per rule violation found by a run.",
    )
    op.create_index(
        op.f("ix_dq_result_dq_run_id"), "dq_result", ["dq_run_id"], unique=False, schema="ifc"
    )
    op.create_index(
        op.f("ix_dq_result_object_id"), "dq_result", ["object_id"], unique=False, schema="ifc"
    )
    op.create_index(
        op.f("ix_dq_result_rule_id"), "dq_result", ["rule_id"], unique=False, schema="ifc"
    )
    op.create_table(
        "property",
        sa.Column(
            "object_id", sa.BigInteger(), nullable=False, comment="Object the property describes."
        ),
        sa.Column(
            "pset_name",
            sa.Text(),
            nullable=False,
            comment="Property set name, e.g. Pset_WallCommon.",
        ),
        sa.Column("prop_name", sa.Text(), nullable=False, comment="Property name within the set."),
        sa.Column(
            "value_type",
            sa.Text(),
            nullable=True,
            comment=(
                "IFC value type, e.g. IfcLabel, IfcLengthMeasure, IfcBoolean; NULL when the "
                "property has no value. Tells which value_* column holds the value."
            ),
        ),
        sa.Column(
            "value_text",
            sa.Text(),
            nullable=True,
            comment="Value of a text-like property, verbatim (may be '').",
        ),
        sa.Column(
            "value_num",
            sa.Double(),
            nullable=True,
            comment="Value of a numeric property; measures are converted to SI units.",
        ),
        sa.Column(
            "value_bool", sa.Boolean(), nullable=True, comment="Value of a boolean property."
        ),
        sa.Column(
            "unit",
            sa.Text(),
            nullable=True,
            comment="SI unit of value_num, e.g. m, m2, m3; NULL if dimensionless.",
        ),
        sa.Column(
            "is_type_property",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "True if the value describes the element's type rather than this instance: "
                "inherited from its IfcTypeObject, or a PSet_Revit_Type_* set copied onto it."
            ),
        ),
        sa.CheckConstraint("prop_name ~ '\\S'", name=op.f("ck_property_prop_name_not_blank")),
        sa.CheckConstraint("pset_name ~ '\\S'", name=op.f("ck_property_pset_name_not_blank")),
        sa.CheckConstraint(
            "value_num > '-Infinity' AND value_num < 'Infinity'",
            name=op.f("ck_property_value_num_finite"),
        ),
        sa.CheckConstraint(
            "value_type ~ '^Ifc[A-Z][A-Za-z0-9]*$'", name=op.f("ck_property_value_type_format")
        ),
        sa.CheckConstraint(
            "(value_type IS NULL) = (num_nonnulls(value_text, value_num, value_bool) = 0)",
            name=op.f("ck_property_value_type_iff_value"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(value_text, value_num, value_bool) <= 1",
            name=op.f("ck_property_at_most_one_value"),
        ),
        sa.CheckConstraint(
            "unit IS NULL OR value_num IS NOT NULL", name=op.f("ck_property_unit_only_for_numbers")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_property_object_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("object_id", "pset_name", "prop_name", name=op.f("pk_property")),
        schema="ifc",
        comment=(
            "Property set values, one row per (object, property set, property): an entity-"
            "attribute-value table with typed value columns (ADR 2)."
        ),
    )
    op.create_index(
        op.f("ix_property_pset_name_prop_name"),
        "property",
        ["pset_name", "prop_name"],
        unique=False,
        schema="ifc",
    )
    op.create_table(
        "quantity",
        sa.Column(
            "object_id", sa.BigInteger(), nullable=False, comment="Object the quantity measures."
        ),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment=(
                "Canonical quantity; the suffix is its unit: length_m, area_m2, volume_m3, "
                "perimeter_m, height_m."
            ),
        ),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            comment=(
                "Where the value came from: ifc_qto (IfcElementQuantity), revit_pset (Revit "
                "parameter property set) or geometry (computed from the shape)."
            ),
        ),
        sa.Column(
            "value", sa.Double(), nullable=False, comment="Value in the unit named by the suffix."
        ),
        sa.Column(
            "source_ref",
            sa.Text(),
            nullable=False,
            comment="Exact origin of the value, e.g. PSet_Revit_Dimensions.Area.",
        ),
        sa.CheckConstraint(
            "name IN ('length_m', 'area_m2', 'volume_m3', 'perimeter_m', 'height_m')",
            name=op.f("ck_quantity_name_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('ifc_qto', 'revit_pset', 'geometry')", name=op.f("ck_quantity_source_valid")
        ),
        sa.CheckConstraint("source_ref ~ '\\S'", name=op.f("ck_quantity_source_ref_not_blank")),
        sa.CheckConstraint(
            "value >= 0 AND value < 'Infinity'", name=op.f("ck_quantity_value_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_quantity_object_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("object_id", "name", "source", name=op.f("pk_quantity")),
        schema="ifc",
        comment=(
            "Canonical quantities. Several sources may hold a value for the same object and "
            "quantity; ifc.v_quantity picks one per object and quantity."
        ),
    )
    op.create_table(
        "spatial_element",
        sa.Column(
            "object_id", sa.BigInteger(), nullable=False, comment="The object this row extends."
        ),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Enclosing spatial element (site > building > storey > space); NULL at the top."
            ),
        ),
        sa.Column(
            "long_name",
            sa.Text(),
            nullable=True,
            comment="IFC LongName. Spaces: room name, e.g. Kitchen.",
        ),
        sa.Column(
            "elevation_m",
            sa.Numeric(precision=9, scale=3),
            nullable=True,
            comment=(
                "Storey elevation in metres, rounded to the millimetre so that storeys compare"
                " equal across discipline models. NULL for other spatial elements."
            ),
        ),
        sa.CheckConstraint(
            "parent_id <> object_id", name=op.f("ck_spatial_element_parent_is_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_spatial_element_object_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["ifc.spatial_element.object_id"],
            name=op.f("fk_spatial_element_parent_id"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("object_id", name=op.f("pk_spatial_element")),
        schema="ifc",
        comment="Site, building, storey and space details. The hierarchy follows IfcRelAggregates.",
    )
    op.create_index(
        op.f("ix_spatial_element_parent_id"),
        "spatial_element",
        ["parent_id"],
        unique=False,
        schema="ifc",
    )
    op.create_table(
        "element",
        sa.Column(
            "object_id", sa.BigInteger(), nullable=False, comment="The object this row extends."
        ),
        sa.Column(
            "container_id",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Spatial element that directly contains the element "
                "(IfcRelContainedInSpatialStructure): usually a storey, sometimes a space."
            ),
        ),
        sa.Column(
            "storey_id",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Storey the element is on: its container, or the container's storey when the "
                "element sits in a space. Resolved at ingest so queries need no recursion."
            ),
        ),
        sa.Column(
            "tag",
            sa.Text(),
            nullable=True,
            comment="IFC Tag attribute; Revit writes the ElementId here.",
        ),
        sa.Column(
            "revit_family",
            sa.Text(),
            nullable=True,
            comment="Family part of a Revit Family:Type:ElementId name.",
        ),
        sa.Column(
            "revit_type",
            sa.Text(),
            nullable=True,
            comment="Type part of a Revit name. It may itself contain ':' characters.",
        ),
        sa.Column(
            "revit_element_id",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Revit ElementId parsed from the name. Not unique: openings and stair parts "
                "repeat the id of the element that hosts them."
            ),
        ),
        sa.Column(
            "type_name",
            sa.Text(),
            nullable=True,
            comment="Name of the IfcTypeObject assigned to the element.",
        ),
        sa.Column(
            "predefined_type",
            sa.Text(),
            nullable=True,
            comment="IFC PredefinedType enumeration value, e.g. FLOOR for a slab.",
        ),
        sa.CheckConstraint(
            "predefined_type ~ '^[A-Z][A-Z0-9_]*$'", name=op.f("ck_element_predefined_type_format")
        ),
        sa.CheckConstraint(
            "revit_element_id > 0", name=op.f("ck_element_revit_element_id_positive")
        ),
        sa.ForeignKeyConstraint(
            ["container_id"],
            ["ifc.spatial_element.object_id"],
            name=op.f("fk_element_container_id"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["ifc.object.object_id"],
            name=op.f("fk_element_object_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["storey_id"],
            ["ifc.spatial_element.object_id"],
            name=op.f("fk_element_storey_id"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("object_id", name=op.f("pk_element")),
        schema="ifc",
        comment=(
            "Physical element details (walls, doors, furniture, ducts...), including the parts"
            " of the Revit name."
        ),
    )
    op.create_index(
        op.f("ix_element_container_id"), "element", ["container_id"], unique=False, schema="ifc"
    )
    op.create_index(
        op.f("ix_element_revit_element_id"),
        "element",
        ["revit_element_id"],
        unique=False,
        schema="ifc",
    )
    op.create_index(
        op.f("ix_element_storey_id"), "element", ["storey_id"], unique=False, schema="ifc"
    )
    op.create_table(
        "element_material_layer",
        sa.Column(
            "element_id", sa.BigInteger(), nullable=False, comment="Element made of the material."
        ),
        sa.Column(
            "layer_index",
            sa.SmallInteger(),
            nullable=False,
            comment=(
                "1-based position in the element's material definition: the layer order of an "
                "IfcMaterialLayerSet, or the list order of an IfcMaterialList."
            ),
        ),
        sa.Column(
            "material_id", sa.BigInteger(), nullable=False, comment="Material of this layer."
        ),
        sa.Column(
            "thickness_m",
            sa.Double(),
            nullable=True,
            comment=(
                "Layer thickness in metres; NULL when the material is not part of a layer set "
                "(a single IfcMaterial or an IfcMaterialList)."
            ),
        ),
        sa.CheckConstraint(
            "thickness_m >= 0 AND thickness_m < 'Infinity'",
            name=op.f("ck_element_material_layer_thickness_m_non_negative"),
        ),
        sa.CheckConstraint(
            "layer_index >= 1", name=op.f("ck_element_material_layer_layer_index_positive")
        ),
        sa.ForeignKeyConstraint(
            ["element_id"],
            ["ifc.element.object_id"],
            name=op.f("fk_element_material_layer_element_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["ifc.material.material_id"],
            name=op.f("fk_element_material_layer_material_id"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "element_id", "layer_index", name=op.f("pk_element_material_layer")
        ),
        schema="ifc",
        comment=(
            "Materials of each element in order. A layered wall has one row per layer; an "
            "element with a single material has one row without thickness."
        ),
    )
    op.create_index(
        op.f("ix_element_material_layer_material_id"),
        "element_material_layer",
        ["material_id"],
        unique=False,
        schema="ifc",
    )


def downgrade() -> None:
    """Drop every table, then the schema.

    No CASCADE: if anything unexpected was added to the schema outside the migrations,
    the downgrade stops with an error instead of silently destroying it.
    """
    for table in TABLES_IN_DROP_ORDER:
        op.drop_table(table, schema="ifc")
    op.execute("DROP SCHEMA ifc")
