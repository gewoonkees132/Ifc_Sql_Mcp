"""Build raw values by hand, so transform tests spell out only what they are about."""

import itertools
from typing import Any

from ifc_sql_mcp.ingest.raw import (
    RawElement,
    RawFile,
    RawModel,
    RawProject,
    RawSpatialElement,
    RawUnit,
    RawValue,
    Scalar,
)

_ids = itertools.count(1)

METRE = RawUnit("LENGTHUNIT", "METRE", None, True, 1.0)
MILLIMETRE = RawUnit("LENGTHUNIT", "METRE", "MILLI", True, 0.001)
FOOT = RawUnit("LENGTHUNIT", "FOOT", None, False, 0.3048)
SQUARE_FOOT = RawUnit("AREAUNIT", "SQUARE FOOT", None, False, 0.09290304)
SQUARE_METRE = RawUnit("AREAUNIT", "SQUARE_METRE", None, True, 1.0)
CUBIC_METRE = RawUnit("VOLUMEUNIT", "CUBIC_METRE", None, True, 1.0)
DEGREE = RawUnit("PLANEANGLEUNIT", "DEGREE", None, False, 0.017453292519943295)
SI_UNITS = {
    "LENGTHUNIT": METRE,
    "AREAUNIT": SQUARE_METRE,
    "VOLUMEUNIT": CUBIC_METRE,
    "PLANEANGLEUNIT": RawUnit("PLANEANGLEUNIT", "RADIAN", None, True, 1.0),
}


def gid(label: str) -> str:
    """A valid GlobalId from a short label."""
    return ("0" + label).ljust(22, "_")


def value(
    set_name: str | None,
    name: str | None,
    raw: Scalar | None,
    value_type: str | None = "IfcLabel",
    *,
    unit: RawUnit | None = None,
    from_type: bool = False,
    from_quantity_set: bool = False,
    order: tuple[int, int] | None = None,
    from_attribute: bool = False,
) -> RawValue:
    """A raw property value; ``order`` defaults to file order of creation."""
    return RawValue(
        set_name=set_name,
        name=name,
        value_type=value_type if raw is not None else None,
        value=raw,
        unit=unit,
        from_type=from_type,
        from_quantity_set=from_quantity_set,
        order=order or (next(_ids), next(_ids)),
        from_attribute=from_attribute,
    )


def spatial(label: str, ifc_class: str = "IfcSpace", **overrides: Any) -> RawSpatialElement:
    """A raw spatial element with no values."""
    fields: dict[str, Any] = {
        "global_id": gid(label),
        "ifc_class": ifc_class,
        "ancestry": (ifc_class, "IfcSpatialStructureElement", "IfcProduct"),
        "name": label,
        "values": (),
        "references": (),
        "long_name": None,
        "parent_global_id": None,
        "elevation": None,
    }
    return RawSpatialElement(**(fields | overrides))


def element(label: str, ifc_class: str = "IfcWall", **overrides: Any) -> RawElement:
    """A raw physical element with no values."""
    fields: dict[str, Any] = {
        "global_id": gid(label),
        "ifc_class": ifc_class,
        "ancestry": (ifc_class, "IfcBuildingElement", "IfcElement", "IfcProduct"),
        "name": None,
        "values": (),
        "references": (),
        "tag": None,
        "container_global_id": None,
        "host_global_id": None,
        "type_name": None,
        "predefined_type": None,
        "type_predefined_type": None,
        "materials": (),
    }
    return RawElement(**(fields | overrides))


def model(
    spatial_elements: tuple[RawSpatialElement, ...] = (),
    elements: tuple[RawElement, ...] = (),
    *,
    units: tuple[RawUnit, ...] = (METRE, SQUARE_METRE, CUBIC_METRE, DEGREE),
    schema: str = "IFC2X3",
    project_global_id: str | None = None,
) -> RawModel:
    """A raw model around the given objects."""
    return RawModel(
        file=RawFile(
            file_name="test.ifc",
            sha256="0" * 64,
            size_bytes=100,
            schema=schema,
            descriptions=("ViewDefinition [CoordinationView]",),
        ),
        project=RawProject(
            global_id=project_global_id or gid("project"),
            name="0001",
            long_name="Test",
            application_name="Autodesk Revit 2013",
            application_version="2013",
        ),
        units=units,
        spatial_elements=spatial_elements,
        elements=elements,
        issues=(),
    )
