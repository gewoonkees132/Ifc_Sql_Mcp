"""SQLAlchemy Core definition of the ``ifc`` schema: the single source of truth for its tables.

The Alembic migrations create these tables; ``tests/integration/test_migrations.py`` runs an
autogenerate comparison against a migrated database and fails on any difference, so the
two cannot drift apart. Views, the read-only role and its privileges live only in the
migrations (and ``sql/views/``) because SQLAlchemy metadata cannot describe them.

Modelling conventions, explained in ``docs/data-model.md``:

* Entity tables (model, object, material, runs, results) get a ``bigint`` identity key
  named ``<table>_id``. Dependent attribute tables (property, quantity, classification,
  element_material_layer) use their natural composite key instead: nothing references
  their rows individually, so a surrogate key would only add an index.
* Every physical object (site, building, storey, space, wall, door...) has one row in
  ``object``; ``spatial_element`` and ``element`` add the columns specific to each kind
  (class-table inheritance). Properties, quantities and classifications reference
  ``object``, which gives them real foreign keys and one id space (ADR 2).
* Lengths, areas and volumes are converted to metres, m² and m³ at ingest; column names
  carry the unit (``elevation_m``, ``thickness_m``).
* An absent value is ``NULL``, never ``''``: identifier-like text columns reject blank
  strings. Property values are the exception, they keep exactly what the file contains.
* Every table and column has a comment. The MCP ``ifc://schema`` resource is generated
  from the catalogue, so these comments are what an LLM reads to understand the data.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

SCHEMA = "ifc"
"""PostgreSQL schema holding every project table and view."""

READER_ROLE = "ifc_reader"
"""Login role with SELECT-only access to ``SCHEMA``, created by migration 0003."""

# Deterministic constraint names: migrations can refer to them, and tests can assert that
# a violation was caught by the intended constraint rather than by some other one.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)

# IFC GlobalId: 128 bits written as 22 characters of IFC's base-64 alphabet. 22 x 6 bits
# is 132 bits, so the first character only carries the top two bits and is 0-3.
GLOBAL_ID_PATTERN = r"^[0-3][0-9A-Za-z_$]{21}$"
IFC_CLASS_PATTERN = r"^Ifc[A-Z][A-Za-z0-9]*$"

QUANTITY_NAMES = ("length_m", "area_m2", "volume_m3", "perimeter_m", "height_m")
QUANTITY_SOURCES = ("ifc_qto", "revit_pset", "geometry")
SEVERITIES = ("error", "warning", "info")
OBJECT_KINDS = ("spatial_element", "element")


def _in(column: str, values: tuple[str, ...]) -> str:
    """Render ``column IN ('a', 'b')`` for a CHECK constraint from a Python tuple."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _not_blank(column: str) -> CheckConstraint:
    """Require at least one non-whitespace character, so 'no value' is always NULL."""
    return CheckConstraint(rf"{column} ~ '\S'", name=f"{column}_not_blank")


def _finite(column: str, *, non_negative: bool = False) -> CheckConstraint:
    """Reject NaN and ±Infinity, which float8 accepts and which poison SUM() and AVG().

    PostgreSQL sorts NaN above every other value, so ``x < 'Infinity'`` is false for NaN as
    well as for +Infinity; the lower bound handles -Infinity (or negatives, if requested).
    """
    lower = f"{column} >= 0" if non_negative else f"{column} > '-Infinity'"
    suffix = "non_negative" if non_negative else "finite"
    return CheckConstraint(f"{lower} AND {column} < 'Infinity'", name=f"{column}_{suffix}")


def _run_status_checks(statuses: tuple[str, ...]) -> list[CheckConstraint]:
    """Constraints shared by ``ingestion_run`` and ``dq_run``: a consistent run lifecycle."""
    return [
        CheckConstraint(_in("status", statuses), name="status_valid"),
        # A run is finished exactly when it has left the 'running' state...
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_iff_done"),
        # ...cannot finish before it started, and failed runs say why.
        CheckConstraint("finished_at >= started_at", name="finished_after_started"),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="error_iff_failed"),
    ]


def _within_model(target: str) -> ForeignKey:
    """Foreign key between two rows of the same model, checked at commit instead of at once.

    PostgreSQL runs every ON DELETE CASCADE step as a separate internal statement. When a
    whole model is deleted, an immediate check can therefore see a half-deleted model (a
    material already gone, the layer using it not yet) and fail. Checked at commit, the
    reference still protects a storey or material that is in use from being deleted on its
    own, while one DELETE on ifc.model removes everything loaded from the file.
    """
    return ForeignKey(target, deferrable=True, initially="DEFERRED")


# --------------------------------------------------------------------------- model, runs

model = Table(
    "model",
    metadata,
    Column(
        "model_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        comment="Surrogate key of the model.",
    ),
    Column("file_name", Text, nullable=False, comment="Base name of the ingested IFC file."),
    Column(
        "file_sha256",
        Text,
        nullable=False,
        unique=True,
        comment="SHA-256 of the file bytes (lower-case hex); identifies one file version.",
    ),
    Column("file_size_bytes", BigInteger, nullable=False, comment="Size of the file in bytes."),
    Column(
        "ifc_schema",
        Text,
        nullable=False,
        comment="IFC schema from the file header FILE_SCHEMA, e.g. IFC2X3 or IFC4.",
    ),
    Column(
        "mvd",
        Text,
        comment="Model View Definition from the header FILE_DESCRIPTION, e.g. CoordinationView.",
    ),
    Column(
        "authoring_app",
        Text,
        comment="Exporting application and version (IfcApplication), e.g. Revit Architecture.",
    ),
    Column(
        "discipline",
        Text,
        nullable=False,
        comment="Discipline code given at ingest, e.g. ARC, STR, MEP, ELE, PLU.",
    ),
    Column(
        "project_global_id",
        Text,
        comment="IfcProject GlobalId. Not a reliable cross-file key: discipline models of "
        "one building can be exported from different Revit projects.",
    ),
    Column("project_name", Text, comment="IfcProject.Name (Revit: Project Number)."),
    Column("project_long_name", Text, comment="IfcProject.LongName (Revit: Project Name)."),
    Column(
        "length_unit",
        Text,
        nullable=False,
        comment="Length unit declared by the file (e.g. m, mm, ft). Every *_m column in "
        "this schema has already been converted from it to metres.",
    ),
    Column(
        "ingested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the model was committed to the database.",
    ),
    _not_blank("file_name"),
    CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="file_sha256_hex"),
    CheckConstraint("file_size_bytes > 0", name="file_size_bytes_positive"),
    CheckConstraint("ifc_schema ~ '^IFC[0-9A-Z_]+$'", name="ifc_schema_format"),
    CheckConstraint("discipline ~ '^[A-Z]{2,8}$'", name="discipline_format"),
    CheckConstraint(f"project_global_id ~ '{GLOBAL_ID_PATTERN}'", name="project_global_id_format"),
    _not_blank("length_unit"),
    comment="One row per ingested IFC file version. Deleting a model deletes everything "
    "loaded from it.",
)

ingestion_run = Table(
    "ingestion_run",
    metadata,
    Column(
        "ingestion_run_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        comment="Surrogate key of the run.",
    ),
    Column(
        "model_id",
        BigInteger,
        ForeignKey(model.c.model_id, ondelete="SET NULL"),
        index=True,
        comment="Model the run produced or found already loaded; NULL if the run failed "
        "before a model was committed, or the model was later deleted.",
    ),
    Column("file_name", Text, nullable=False, comment="Base name of the file the run read."),
    Column(
        "file_sha256",
        Text,
        nullable=False,
        index=True,
        comment="SHA-256 of the file the run read, kept even when model_id is NULL.",
    ),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the run started.",
    ),
    Column(
        "finished_at",
        DateTime(timezone=True),
        comment="When the run ended; NULL while running.",
    ),
    Column(
        "status",
        Text,
        nullable=False,
        server_default=text("'running'"),
        comment="running | succeeded | failed | skipped (file already loaded, nothing written).",
    ),
    Column(
        "row_counts",
        JSONB,
        comment='Rows written per table, e.g. {"element": 268}. An audit record, never '
        "filtered on, so a JSON object is simpler than a column per table.",
    ),
    Column("error", Text, comment="Error message of a failed run; NULL otherwise."),
    _not_blank("file_name"),
    CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="file_sha256_hex"),
    CheckConstraint("jsonb_typeof(row_counts) = 'object'", name="row_counts_is_object"),
    *_run_status_checks(("running", "succeeded", "failed", "skipped")),
    comment="Audit log of ingest attempts, one row per run, including failed and skipped "
    "runs. Written outside the load transaction so failures are recorded too.",
)

# --------------------------------------------------------------------------- objects

object_ = Table(
    "object",
    metadata,
    Column(
        "object_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        comment="Surrogate key, shared with the object's spatial_element or element row.",
    ),
    Column(
        "model_id",
        BigInteger,
        ForeignKey(model.c.model_id, ondelete="CASCADE"),
        nullable=False,
        comment="Model the object was loaded from.",
    ),
    Column(
        "global_id",
        Text,
        nullable=False,
        index=True,
        comment="IFC GlobalId (22 characters). Unique within a model; Revit keeps it "
        "stable when the same project is exported again.",
    ),
    Column(
        "object_kind",
        Text,
        nullable=False,
        comment="Which subtype table holds the object's details: spatial_element or element.",
    ),
    Column(
        "ifc_class",
        Text,
        nullable=False,
        comment="Exact IFC entity, e.g. IfcWallStandardCase.",
    ),
    Column(
        "ifc_class_base",
        Text,
        nullable=False,
        index=True,
        comment="Normalised IFC entity for filtering and grouping, derived from the IFC "
        "inheritance tree, e.g. IfcWall for both IfcWall and IfcWallStandardCase.",
    ),
    Column(
        "name",
        Text,
        comment="IFC Name attribute, verbatim. Revit elements: Family:Type:ElementId; "
        "storeys: level name; spaces: room number.",
    ),
    UniqueConstraint("model_id", "global_id"),
    CheckConstraint(f"global_id ~ '{GLOBAL_ID_PATTERN}'", name="global_id_format"),
    CheckConstraint(_in("object_kind", OBJECT_KINDS), name="object_kind_valid"),
    CheckConstraint(f"ifc_class ~ '{IFC_CLASS_PATTERN}'", name="ifc_class_format"),
    CheckConstraint(f"ifc_class_base ~ '{IFC_CLASS_PATTERN}'", name="ifc_class_base_format"),
    comment="Supertype of every IFC product: one row per spatial element or physical "
    "element. Properties, quantities and classifications attach here.",
)

spatial_element = Table(
    "spatial_element",
    metadata,
    Column(
        "object_id",
        BigInteger,
        ForeignKey(object_.c.object_id, ondelete="CASCADE"),
        primary_key=True,
        comment="The object this row extends.",
    ),
    Column(
        "parent_id",
        BigInteger,
        _within_model("spatial_element.object_id"),
        index=True,
        comment="Enclosing spatial element (site > building > storey > space); NULL at the top.",
    ),
    Column("long_name", Text, comment="IFC LongName. Spaces: room name, e.g. Kitchen."),
    Column(
        "elevation_m",
        Numeric(9, 3),
        comment="Storey elevation in metres, rounded to the millimetre so that storeys "
        "compare equal across discipline models. NULL for other spatial elements.",
    ),
    CheckConstraint("parent_id <> object_id", name="parent_is_not_self"),
    comment="Site, building, storey and space details. The hierarchy follows IfcRelAggregates.",
)

element = Table(
    "element",
    metadata,
    Column(
        "object_id",
        BigInteger,
        ForeignKey(object_.c.object_id, ondelete="CASCADE"),
        primary_key=True,
        comment="The object this row extends.",
    ),
    Column(
        "container_id",
        BigInteger,
        _within_model("spatial_element.object_id"),
        index=True,
        comment="Spatial element that directly contains the element "
        "(IfcRelContainedInSpatialStructure): usually a storey, sometimes a space.",
    ),
    Column(
        "storey_id",
        BigInteger,
        _within_model("spatial_element.object_id"),
        index=True,
        comment="Storey the element is on: its container, or the container's storey when "
        "the element sits in a space. Resolved at ingest so queries need no recursion.",
    ),
    Column("tag", Text, comment="IFC Tag attribute; Revit writes the ElementId here."),
    Column("revit_family", Text, comment="Family part of a Revit Family:Type:ElementId name."),
    Column(
        "revit_type",
        Text,
        comment="Type part of a Revit name. It may itself contain ':' characters.",
    ),
    Column(
        "revit_element_id",
        BigInteger,
        index=True,
        comment="Revit ElementId parsed from the name. Not unique: openings and stair parts "
        "repeat the id of the element that hosts them.",
    ),
    Column("type_name", Text, comment="Name of the IfcTypeObject assigned to the element."),
    Column(
        "predefined_type",
        Text,
        comment="IFC PredefinedType enumeration value, e.g. FLOOR for a slab.",
    ),
    CheckConstraint("revit_element_id > 0", name="revit_element_id_positive"),
    CheckConstraint("predefined_type ~ '^[A-Z][A-Z0-9_]*$'", name="predefined_type_format"),
    comment="Physical element details (walls, doors, furniture, ducts...), including the "
    "parts of the Revit name.",
)

# --------------------------------------------------------------------------- attributes

property_ = Table(
    "property",
    metadata,
    Column(
        "object_id",
        BigInteger,
        ForeignKey(object_.c.object_id, ondelete="CASCADE"),
        nullable=False,
        comment="Object the property describes.",
    ),
    Column("pset_name", Text, nullable=False, comment="Property set name, e.g. Pset_WallCommon."),
    Column("prop_name", Text, nullable=False, comment="Property name within the set."),
    Column(
        "value_type",
        Text,
        comment="IFC value type, e.g. IfcLabel, IfcLengthMeasure, IfcBoolean; NULL when the "
        "property has no value. Tells which value_* column holds the value.",
    ),
    Column("value_text", Text, comment="Value of a text-like property, verbatim (may be '')."),
    Column(
        "value_num",
        Double,
        comment="Value of a numeric property; measures are converted to SI units.",
    ),
    Column("value_bool", Boolean, comment="Value of a boolean property."),
    Column("unit", Text, comment="SI unit of value_num, e.g. m, m2, m3; NULL if dimensionless."),
    Column(
        "is_type_property",
        Boolean,
        nullable=False,
        server_default=false(),
        comment="True if the value describes the element's type rather than this instance: "
        "inherited from its IfcTypeObject, or a PSet_Revit_Type_* set copied onto it.",
    ),
    PrimaryKeyConstraint("object_id", "pset_name", "prop_name"),
    Index(None, "pset_name", "prop_name"),
    _not_blank("pset_name"),
    _not_blank("prop_name"),
    CheckConstraint(f"value_type ~ '{IFC_CLASS_PATTERN}'", name="value_type_format"),
    CheckConstraint(
        "num_nonnulls(value_text, value_num, value_bool) <= 1", name="at_most_one_value"
    ),
    CheckConstraint(
        "(value_type IS NULL) = (num_nonnulls(value_text, value_num, value_bool) = 0)",
        name="value_type_iff_value",
    ),
    CheckConstraint("unit IS NULL OR value_num IS NOT NULL", name="unit_only_for_numbers"),
    _finite("value_num"),
    comment="Property set values, one row per (object, property set, property): an "
    "entity-attribute-value table with typed value columns (ADR 2).",
)

quantity = Table(
    "quantity",
    metadata,
    Column(
        "object_id",
        BigInteger,
        ForeignKey(object_.c.object_id, ondelete="CASCADE"),
        nullable=False,
        comment="Object the quantity measures.",
    ),
    Column(
        "name",
        Text,
        nullable=False,
        comment="Canonical quantity; the suffix is its unit: length_m, area_m2, volume_m3, "
        "perimeter_m, height_m.",
    ),
    Column(
        "source",
        Text,
        nullable=False,
        comment="Where the value came from: ifc_qto (IfcElementQuantity), revit_pset (Revit "
        "parameter property set) or geometry (computed from the shape).",
    ),
    Column("value", Double, nullable=False, comment="Value in the unit named by the suffix."),
    Column(
        "source_ref",
        Text,
        nullable=False,
        comment="Exact origin of the value, e.g. PSet_Revit_Dimensions.Area.",
    ),
    PrimaryKeyConstraint("object_id", "name", "source"),
    CheckConstraint(_in("name", QUANTITY_NAMES), name="name_valid"),
    CheckConstraint(_in("source", QUANTITY_SOURCES), name="source_valid"),
    _finite("value", non_negative=True),
    _not_blank("source_ref"),
    comment="Canonical quantities. Several sources may hold a value for the same object and "
    "quantity; ifc.v_quantity picks one per object and quantity.",
)

material = Table(
    "material",
    metadata,
    Column(
        "material_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        comment="Surrogate key of the material.",
    ),
    Column(
        "model_id",
        BigInteger,
        ForeignKey(model.c.model_id, ondelete="CASCADE"),
        nullable=False,
        comment="Model the material belongs to. IFC materials have no GlobalId, so they are "
        "scoped to their file.",
    ),
    Column("name", Text, nullable=False, comment="IfcMaterial.Name, e.g. Masonry - Brick."),
    UniqueConstraint("model_id", "name"),
    _not_blank("name"),
    comment="Materials used in a model, one row per distinct name.",
)

element_material_layer = Table(
    "element_material_layer",
    metadata,
    Column(
        "element_id",
        BigInteger,
        ForeignKey(element.c.object_id, ondelete="CASCADE"),
        nullable=False,
        comment="Element made of the material.",
    ),
    Column(
        "layer_index",
        SmallInteger,
        nullable=False,
        comment="1-based position in the element's material definition: the layer order of "
        "an IfcMaterialLayerSet, or the list order of an IfcMaterialList.",
    ),
    Column(
        "material_id",
        BigInteger,
        _within_model("material.material_id"),
        nullable=False,
        index=True,
        comment="Material of this layer.",
    ),
    Column(
        "thickness_m",
        Double,
        comment="Layer thickness in metres; NULL when the material is not part of a layer "
        "set (a single IfcMaterial or an IfcMaterialList).",
    ),
    PrimaryKeyConstraint("element_id", "layer_index"),
    CheckConstraint("layer_index >= 1", name="layer_index_positive"),
    _finite("thickness_m", non_negative=True),
    comment="Materials of each element in order. A layered wall has one row per layer; an "
    "element with a single material has one row without thickness.",
)

classification = Table(
    "classification",
    metadata,
    Column(
        "object_id",
        BigInteger,
        ForeignKey(object_.c.object_id, ondelete="CASCADE"),
        nullable=False,
        comment="Classified object.",
    ),
    Column(
        "system",
        Text,
        nullable=False,
        comment="Classification system, e.g. OmniClass Table 13 or UniFormat.",
    ),
    Column("code", Text, nullable=False, comment="Code within the system, e.g. 13-51 24 11."),
    Column("title", Text, comment="Human-readable title of the code, when the file gives one."),
    PrimaryKeyConstraint("object_id", "system", "code"),
    Index(None, "system", "code"),
    _not_blank("system"),
    _not_blank("code"),
    comment="Classification codes, from IfcClassificationReference or from Revit "
    "parameters such as OmniClass Table 13 Category.",
)

# --------------------------------------------------------------------------- data quality

dq_rule = Table(
    "dq_rule",
    metadata,
    Column("rule_id", Text, primary_key=True, comment="Stable rule identifier, snake_case."),
    Column("description", Text, nullable=False, comment="What the rule checks and why."),
    Column(
        "severity",
        Text,
        nullable=False,
        comment="Default severity of a finding: error | warning | info.",
    ),
    Column(
        "dimension",
        Text,
        nullable=False,
        comment="Data quality dimension: completeness | validity | consistency | uniqueness.",
    ),
    CheckConstraint("rule_id ~ '^[a-z][a-z0-9_]*$'", name="rule_id_format"),
    _not_blank("description"),
    CheckConstraint(_in("severity", SEVERITIES), name="severity_valid"),
    CheckConstraint(
        _in("dimension", ("completeness", "validity", "consistency", "uniqueness")),
        name="dimension_valid",
    ),
    comment="Catalogue of data quality rules, synchronised from rules/dq_rules.yaml.",
)

dq_run = Table(
    "dq_run",
    metadata,
    Column(
        "dq_run_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        comment="Surrogate key of the run.",
    ),
    Column(
        "model_id",
        BigInteger,
        ForeignKey(model.c.model_id, ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Model the rules were evaluated against.",
    ),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the run started.",
    ),
    Column(
        "finished_at",
        DateTime(timezone=True),
        comment="When the run ended; NULL while running.",
    ),
    Column(
        "status",
        Text,
        nullable=False,
        server_default=text("'running'"),
        comment="running | succeeded | failed.",
    ),
    Column("error", Text, comment="Error message of a failed run; NULL otherwise."),
    *_run_status_checks(("running", "succeeded", "failed")),
    comment="One evaluation of the data quality rules against one model. Results are kept "
    "per run so quality can be compared over time.",
)

dq_result = Table(
    "dq_result",
    metadata,
    Column(
        "dq_result_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        comment="Surrogate key of the finding.",
    ),
    Column(
        "dq_run_id",
        BigInteger,
        ForeignKey(dq_run.c.dq_run_id, ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Run that produced the finding; its model_id says which model it is about.",
    ),
    Column(
        "rule_id",
        Text,
        ForeignKey(dq_rule.c.rule_id),
        nullable=False,
        index=True,
        comment="Rule that was violated.",
    ),
    Column(
        "object_id",
        BigInteger,
        ForeignKey(object_.c.object_id, ondelete="CASCADE"),
        index=True,
        comment="Offending object; NULL for a finding about the model as a whole.",
    ),
    Column(
        "severity",
        Text,
        nullable=False,
        comment="Severity at the time of the run (a rule's default may change later).",
    ),
    Column("message", Text, nullable=False, comment="Human-readable description of the finding."),
    CheckConstraint(_in("severity", SEVERITIES), name="severity_valid"),
    _not_blank("message"),
    comment="Data quality findings: one row per rule violation found by a run.",
)
