"""Structured warnings: what the pipeline could not represent, and what it did instead.

An IFC export is untrusted input with many small defects. Failing a whole file over one
negative area would make the pipeline useless, and dropping the value silently would
hide the defect. So each such value becomes an :class:`Issue` with a stable code, the
GlobalId of the object it concerns and a readable message. The data quality stage maps
codes to rules and stores the issues as findings.
"""

from dataclasses import dataclass
from enum import StrEnum


class IssueCode(StrEnum):
    """Stable identifiers for every kind of issue; the data quality rules key on these."""

    # Model and units
    MULTIPLE_PROJECTS = "multiple_projects"
    """The file has more than one IfcProject; the first one is used."""
    INVALID_PROJECT_GLOBAL_ID = "invalid_project_global_id"
    """The IfcProject GlobalId is malformed; project_global_id is left NULL."""
    UNIT_ASSUMED_SI = "unit_assumed_si"
    """The file declares no unit for a converted dimension; the SI unit is assumed."""
    UNIT_NOT_CONVERTIBLE = "unit_not_convertible"
    """A value's unit has no known SI factor; the value is dropped."""
    MEASURE_NOT_CONVERTED = "measure_not_converted"
    """A measure outside length, area, volume and plane angle is stored as written."""

    # Objects
    INVALID_GLOBAL_ID = "invalid_global_id"
    """An object's GlobalId is malformed; the object is skipped."""
    DUPLICATE_GLOBAL_ID = "duplicate_global_id"
    """Two objects share a GlobalId; the later one is skipped."""
    INVALID_ELEVATION = "invalid_elevation"
    """A storey elevation is not finite or does not fit numeric(9,3); left NULL."""
    REVIT_NAME_UNPARSED = "revit_name_unparsed"
    """An element name does not follow Family:Type:ElementId; its parts are left NULL."""
    REVIT_TAG_MISMATCH = "revit_tag_mismatch"
    """The ElementId parsed from the name differs from the element's Tag."""

    # Properties and quantities
    UNNAMED_PROPERTY = "unnamed_property"
    """A property or its set has no name; the value is dropped."""
    UNSUPPORTED_PROPERTY = "unsupported_property"
    """A property kind other than a single value (list, table, ...); it is dropped."""
    UNSUPPORTED_PROPERTY_SET = "unsupported_property_set"
    """A property set definition other than IfcPropertySet/IfcElementQuantity holds data."""
    NON_FINITE_VALUE = "non_finite_value"
    """A number is NaN or infinite (after conversion); the value is dropped."""
    CONFLICTING_PROPERTY = "conflicting_property"
    """Two different values for one set and property at the same level; the first is kept."""
    QUANTITY_NOT_A_MEASURE = "quantity_not_a_measure"
    """A value mapped to a quantity is not the expected measure; no quantity is made."""
    NEGATIVE_QUANTITY = "negative_quantity"
    """A value mapped to a quantity is negative; no quantity is made."""
    CONFLICTING_QUANTITY = "conflicting_quantity"
    """Two different values for one quantity and source; the first is kept."""

    # Classifications
    OMNICLASS_UNPARSED = "omniclass_unparsed"
    """An OmniClass parameter holds no recognisable code; no classification is made."""
    INCOMPLETE_CLASSIFICATION_REFERENCE = "incomplete_classification_reference"
    """An IfcClassificationReference lacks a code or system; it is dropped."""
    UNSUPPORTED_CLASSIFICATION = "unsupported_classification"
    """A classification association that is not an IfcClassificationReference."""
    CONFLICTING_CLASSIFICATION = "conflicting_classification"
    """One system and code with two different titles; the first is kept."""

    # Materials
    MULTIPLE_MATERIAL_ASSOCIATIONS = "multiple_material_associations"
    """An element has several material associations; the first is used."""
    UNSUPPORTED_MATERIAL = "unsupported_material"
    """A material definition kind this pipeline does not read; the element has no layers."""
    UNNAMED_MATERIAL = "unnamed_material"
    """A layer or list entry without a named material; that position is skipped."""
    INVALID_LAYER_THICKNESS = "invalid_layer_thickness"
    """A layer thickness is negative or not finite; thickness_m is left NULL."""


@dataclass(frozen=True, slots=True)
class Issue:
    """One thing the pipeline could not represent as-is.

    Attributes:
        code: What kind of issue it is.
        global_id: GlobalId of the object concerned, as written in the file; ``None`` when
            the issue is about the model as a whole.
        message: Human-readable details, including the offending value.
    """

    code: IssueCode
    global_id: str | None
    message: str
