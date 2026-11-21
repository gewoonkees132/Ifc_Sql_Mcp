"""Turn a :class:`RawModel` into :class:`ModelRecords`: pure, deterministic, no I/O.

This module ties the stages together and holds the rules that need more than one
object at a time: which objects are accepted, ``ifc_class_base``, the storey of each
element and its materials. Property typing, quantities, classifications and Revit names
have their own modules.

The same input always gives the same output, in the same order: every choice between
candidates is made by a stated rule, never by dictionary or set iteration order.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from ifc_sql_mcp.db.schema import GLOBAL_ID_PATTERN
from ifc_sql_mcp.ingest.classifications import collect_classifications
from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.properties import resolve_properties
from ifc_sql_mcp.ingest.quantities import derive_quantities
from ifc_sql_mcp.ingest.raw import (
    RawElement,
    RawMaterialEntry,
    RawModel,
    RawObject,
    RawSpatialElement,
    RawUnit,
)
from ifc_sql_mcp.ingest.records import (
    ClassificationRecord,
    ElementMaterialLayerRecord,
    ElementRecord,
    MaterialRecord,
    ModelRecord,
    ModelRecords,
    ObjectKind,
    ObjectRecord,
    PropertyRecord,
    QuantityRecord,
    SpatialElementRecord,
)
from ifc_sql_mcp.ingest.revit_names import parse_revit_name
from ifc_sql_mcp.ingest.units import LENGTH, project_units, round_elevation, to_si, unit_label

_GLOBAL_ID = re.compile(GLOBAL_ID_PATTERN)
_IFC_SCHEMA = re.compile(r"IFC[0-9A-Z_]+")
_PREDEFINED_TYPE = re.compile(r"[A-Z][A-Z0-9_]*")
_VIEW_DEFINITION = re.compile(r"ViewDefinition\s*\[(?P<views>[^\]]*)\]")
_CASE_SUFFIXES = ("StandardCase", "ElementedCase")
STOREY_CLASS = "IfcBuildingStorey"
FEATURE_CLASS = "IfcFeatureElement"
"""Supertype of openings, voids and projections: elements that shape another element."""


class TransformError(ValueError):
    """The file lacks something no record can do without (a recognisable IFC schema)."""


# --------------------------------------------------------------------------- model


def parse_mvd(descriptions: Iterable[str]) -> str | None:
    """Return the Model View Definition from FILE_DESCRIPTION, e.g. ``CoordinationView``.

    The header says ``ViewDefinition [CoordinationView]``; several views are listed as
    ``ViewDefinition [CoordinationView, QuantityTakeOffAddOnView]`` and kept that way.
    """
    for description in descriptions:
        match = _VIEW_DEFINITION.search(description)
        if match and match.group("views").strip():
            return match.group("views").strip()
    return None


def authoring_app(name: str | None, version: str | None) -> str | None:
    """Combine IfcApplication name and version, without repeating the version.

    Revit writes ``Autodesk Revit Architecture 2011`` with version ``2011``.
    """
    name = (name or "").strip()
    version = (version or "").strip()
    if not name:
        return None
    if version and version not in name:
        return f"{name} {version}"
    return name


def _model_record(raw: RawModel, units: Mapping[str, RawUnit], issues: list[Issue]) -> ModelRecord:
    schema = raw.file.schema.strip().upper()
    if not _IFC_SCHEMA.fullmatch(schema):
        raise TransformError(f"{raw.file.file_name}: unrecognised IFC schema {raw.file.schema!r}")

    project_global_id = raw.project.global_id
    if project_global_id is not None and not _GLOBAL_ID.fullmatch(project_global_id):
        message = f"IfcProject GlobalId {project_global_id!r} is malformed"
        issues.append(Issue(IssueCode.INVALID_PROJECT_GLOBAL_ID, None, message))
        project_global_id = None

    return ModelRecord(
        file_name=raw.file.file_name,
        file_sha256=raw.file.sha256,
        file_size_bytes=raw.file.size_bytes,
        ifc_schema=schema,
        mvd=parse_mvd(raw.file.descriptions),
        authoring_app=authoring_app(raw.project.application_name, raw.project.application_version),
        project_global_id=project_global_id,
        project_name=raw.project.name,
        project_long_name=raw.project.long_name,
        length_unit=unit_label(units[LENGTH.unit_type]),
    )


# --------------------------------------------------------------------------- objects


def base_class(ifc_class: str, ancestry: Sequence[str]) -> str:
    """Return the class to filter and group by: ``IfcWallStandardCase`` -> ``IfcWall``.

    IFC specialises some classes into "case" subtypes that only add geometry rules
    (``IfcWallStandardCase``, ``IfcSlabElementedCase``, ``IfcOpeningStandardCase``...).
    They are the same kind of thing as their supertype, so this walks up the
    inheritance chain past them. Every other class is its own base: an
    ``IfcBuildingElementProxy`` stays a proxy rather than becoming an
    ``IfcBuildingElement``.

    Args:
        ifc_class: The exact class.
        ancestry: The class followed by its supertypes, as the schema defines them.
    """
    for name in ancestry or (ifc_class,):
        if not name.endswith(_CASE_SUFFIXES):
            return name
    return ifc_class


def _accept_objects(raw: RawModel, issues: list[Issue]) -> list[RawObject]:
    """Keep objects whose GlobalId is valid and not already taken, in file order."""
    accepted: list[RawObject] = []
    seen: set[str] = set()
    for obj in (*raw.spatial_elements, *raw.elements):
        if not _GLOBAL_ID.fullmatch(obj.global_id):
            message = f"{obj.ifc_class} {obj.name!r} has a malformed GlobalId; skipped"
            issues.append(Issue(IssueCode.INVALID_GLOBAL_ID, obj.global_id, message))
        elif obj.global_id in seen:
            message = f"{obj.ifc_class} {obj.name!r} repeats a GlobalId; skipped"
            issues.append(Issue(IssueCode.DUPLICATE_GLOBAL_ID, obj.global_id, message))
        else:
            seen.add(obj.global_id)
            accepted.append(obj)
    return accepted


def predefined_type(occurrence: str | None, type_value: str | None) -> str | None:
    """Return the PredefinedType to store, or ``None``.

    IFC lets the type object's value apply to all its occurrences, so a type value wins
    unless it is NOTDEFINED. NOTDEFINED itself means "absent", which this schema writes
    as NULL. USERDEFINED is kept: the custom text lives in ObjectType, which is free
    text and would not fit the column's enumeration format.
    """
    for candidate in (type_value, occurrence):
        if candidate and candidate != "NOTDEFINED" and _PREDEFINED_TYPE.fullmatch(candidate):
            return candidate
    return None


def resolve_storey(
    global_id: str,
    containers: Mapping[str, str | None],
    hosts: Mapping[str, str | None],
    spatial_parents: Mapping[str, str | None],
    spatial_classes: Mapping[str, str],
) -> str | None:
    """Find the storey an element is on.

    Start from the element's direct container. An element without one (an opening,
    a stair flight, a roof slab that is part of a roof) takes the container of its
    host, the element it is part of or cuts through. From the container, walk up the
    spatial hierarchy until a storey: furniture in a room is on the room's storey.

    Args:
        global_id: The element.
        containers: Direct container per element (``None`` if not contained).
        hosts: Host element per element (``None`` if none).
        spatial_parents: Parent per spatial element.
        spatial_classes: ``ifc_class_base`` per spatial element.

    Returns:
        The storey's GlobalId, or ``None`` if the chain reaches no storey.
    """
    element: str | None = global_id
    container: str | None = None
    visited: set[str] = set()
    while element is not None and element not in visited:
        visited.add(element)
        container = containers.get(element)
        if container is not None:
            break
        element = hosts.get(element)

    while container is not None and container not in visited:
        visited.add(container)
        if spatial_classes.get(container) == STOREY_CLASS:
            return container
        container = spatial_parents.get(container)
    return None


def is_feature(ancestry: Sequence[str]) -> bool:
    """True for an IfcFeatureElement: an opening, void, projection or surface feature.

    Such an element is not a building part of its own but a change to another one, which
    Revit expresses by naming it after that element (``Basic Wall:Generic 200mm:1044048:1``
    for an opening in that wall). Counting it under the wall's type would count the wall
    twice, so its parsed name goes to ``host_revit_family`` and ``host_revit_type``.
    """
    return FEATURE_CLASS in ancestry


def material_layers(
    global_id: str, entries: Iterable[RawMaterialEntry], length_unit: RawUnit
) -> tuple[list[ElementMaterialLayerRecord], list[Issue]]:
    """Return the element's material rows, in layer or list order.

    An entry without a named material (an air gap in a layer set) has no material row
    to point at, so it is skipped; the other entries keep their original positions, so
    ``layer_index`` still says where in the build-up each material sits.
    """
    records: list[ElementMaterialLayerRecord] = []
    issues: list[Issue] = []
    for entry in entries:
        name = entry.material_name
        if name is None or not name.strip():
            message = f"material position {entry.position} has no named material; skipped"
            issues.append(Issue(IssueCode.UNNAMED_MATERIAL, global_id, message))
            continue
        thickness_m: float | None = None
        if entry.thickness is not None:
            thickness_m = to_si(entry.thickness, length_unit)
            if thickness_m is None or not math.isfinite(thickness_m) or thickness_m < 0:
                message = f"layer {entry.position} ({name}) has thickness {entry.thickness!r}"
                issues.append(Issue(IssueCode.INVALID_LAYER_THICKNESS, global_id, message))
                thickness_m = None
        records.append(
            ElementMaterialLayerRecord(
                element_global_id=global_id,
                layer_index=entry.position,
                material_name=name,
                thickness_m=thickness_m,
            )
        )
    return records, issues


# --------------------------------------------------------------------------- the whole model


def _object_records(
    obj: RawObject, units: Mapping[str, RawUnit]
) -> tuple[
    ObjectRecord,
    list[PropertyRecord],
    list[QuantityRecord],
    list[ClassificationRecord],
    list[Issue],
]:
    """Return the object row and the attribute rows shared by both kinds of object."""
    kind: ObjectKind = "spatial_element" if isinstance(obj, RawSpatialElement) else "element"
    record = ObjectRecord(
        global_id=obj.global_id,
        object_kind=kind,
        ifc_class=obj.ifc_class,
        ifc_class_base=base_class(obj.ifc_class, obj.ancestry),
        name=obj.name,
    )
    values, issues = resolve_properties(obj.global_id, obj.values, units)
    quantities, quantity_issues = derive_quantities(obj.global_id, values)
    classifications, class_issues = collect_classifications(obj.global_id, values, obj.references)
    properties = [value.to_record(obj.global_id) for value in values]
    return record, properties, quantities, classifications, issues + quantity_issues + class_issues


def _spatial_record(
    space: RawSpatialElement, known: set[str], length_unit: RawUnit
) -> tuple[SpatialElementRecord, list[Issue]]:
    issues: list[Issue] = []
    elevation_m: Decimal | None = None
    if space.elevation is not None:
        in_metres = to_si(space.elevation, length_unit)
        elevation_m = round_elevation(in_metres) if in_metres is not None else None
        if elevation_m is None:
            message = f"elevation {space.elevation!r} cannot be stored; left NULL"
            issues.append(Issue(IssueCode.INVALID_ELEVATION, space.global_id, message))
    parent = space.parent_global_id
    # A parent that was skipped would break the foreign key; a self-reference the CHECK.
    if parent not in known or parent == space.global_id:
        parent = None
    record = SpatialElementRecord(
        global_id=space.global_id,
        parent_global_id=parent,
        long_name=space.long_name,
        elevation_m=elevation_m,
    )
    return record, issues


def _element_records(
    elements: Sequence[RawElement],
    spatial: Sequence[SpatialElementRecord],
    spatial_classes: Mapping[str, str],
    length_unit: RawUnit,
) -> tuple[list[ElementRecord], list[ElementMaterialLayerRecord], list[Issue]]:
    element_ids = {element.global_id for element in elements}
    containers = {
        element.global_id: (
            element.container_global_id if element.container_global_id in spatial_classes else None
        )
        for element in elements
    }
    hosts = {
        element.global_id: (
            element.host_global_id if element.host_global_id in element_ids else None
        )
        for element in elements
    }
    spatial_parents = {record.global_id: record.parent_global_id for record in spatial}

    records: list[ElementRecord] = []
    layers: list[ElementMaterialLayerRecord] = []
    issues: list[Issue] = []
    for element in elements:
        revit, name_issues = parse_revit_name(element.global_id, element.name, element.tag)
        feature = is_feature(element.ancestry)
        element_layers, layer_issues = material_layers(
            element.global_id, element.materials, length_unit
        )
        layers.extend(element_layers)
        issues.extend(name_issues + layer_issues)
        records.append(
            ElementRecord(
                global_id=element.global_id,
                container_global_id=containers[element.global_id],
                storey_global_id=resolve_storey(
                    element.global_id, containers, hosts, spatial_parents, spatial_classes
                ),
                tag=element.tag,
                is_feature=feature,
                revit_family=None if feature else revit.family,
                revit_type=None if feature else revit.type,
                host_revit_family=revit.family if feature else None,
                host_revit_type=revit.type if feature else None,
                revit_element_id=revit.element_id,
                type_name=element.type_name,
                predefined_type=predefined_type(
                    element.predefined_type, element.type_predefined_type
                ),
            )
        )
    return records, layers, issues


def transform(raw: RawModel) -> ModelRecords:
    """Turn everything extracted from one file into records for the loader.

    Raises:
        TransformError: if the header names no recognisable IFC schema.
    """
    issues = list(raw.issues)
    units, unit_issues = project_units(raw.units)
    issues.extend(unit_issues)
    length_unit = units[LENGTH.unit_type]
    model = _model_record(raw, units, issues)
    accepted = _accept_objects(raw, issues)

    objects: list[ObjectRecord] = []
    properties: list[PropertyRecord] = []
    quantities: list[QuantityRecord] = []
    classifications: list[ClassificationRecord] = []
    for obj in accepted:
        record, object_properties, object_quantities, object_classes, object_issues = (
            _object_records(obj, units)
        )
        objects.append(record)
        properties.extend(object_properties)
        quantities.extend(object_quantities)
        classifications.extend(object_classes)
        issues.extend(object_issues)

    known = {obj.global_id for obj in accepted}
    spatial: list[SpatialElementRecord] = []
    for space in (obj for obj in accepted if isinstance(obj, RawSpatialElement)):
        spatial_record, spatial_issues = _spatial_record(space, known, length_unit)
        spatial.append(spatial_record)
        issues.extend(spatial_issues)

    spatial_classes = {
        record.global_id: record.ifc_class_base
        for record in objects
        if record.object_kind == "spatial_element"
    }
    elements, layers, element_issues = _element_records(
        [obj for obj in accepted if isinstance(obj, RawElement)],
        spatial,
        spatial_classes,
        length_unit,
    )
    issues.extend(element_issues)

    return ModelRecords(
        model=model,
        objects=tuple(sorted(objects, key=lambda r: r.global_id)),
        spatial_elements=tuple(sorted(spatial, key=lambda r: r.global_id)),
        elements=tuple(sorted(elements, key=lambda r: r.global_id)),
        properties=tuple(sorted(properties, key=lambda r: (r.global_id, r.pset_name, r.prop_name))),
        quantities=tuple(sorted(quantities, key=lambda r: (r.global_id, r.name, r.source))),
        materials=tuple(MaterialRecord(name) for name in sorted({r.material_name for r in layers})),
        material_layers=tuple(sorted(layers, key=lambda r: (r.element_global_id, r.layer_index))),
        classifications=tuple(
            sorted(classifications, key=lambda r: (r.global_id, r.system, r.code))
        ),
        issues=tuple(issues),
    )
