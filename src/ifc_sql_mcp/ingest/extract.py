"""Read an IFC file into plain values (``raw.py``). The only module that uses IfcOpenShell.

IfcOpenShell entities are dynamic: attribute access is resolved against the IFC schema
at run time, and the library's type hints cover only part of its API. So this module
treats entities as ``Any`` (see the mypy override in ``pyproject.toml``) and converts
every value it reads into a checked Python type before it leaves. Everything after this
module is strictly typed.

What it reads, and from where:

* **Products.** ``IfcSpatialStructureElement`` (site, building, storey, space) and
  ``IfcElement`` (everything physical, openings included). Other products, such as the
  970 ``IfcDistributionPort`` connection points in the plumbing model, are not loaded;
  their count is logged.
* **Properties** by walking ``IfcRelDefinesByProperties`` and the type's
  ``HasPropertySets`` directly, rather than through ``ifcopenshell.util.element.get_psets``.
  That helper merges sets that share a name (losing which value came from the type),
  drops value types and units unless asked, mixes in quantity sets, and adds an ``id``
  key to every set, which would read as a property called ``id``.
* **Classifications** through ``ifcopenshell.util.classification.get_references``, which
  applies IFC's inheritance rule: an occurrence inherits its type's references, and its
  own reference in a system replaces the type's.
* **Materials** from ``IfcRelAssociatesMaterial`` on the element, or on its type if the
  element has none (IFC's inheritance rule again).
"""

import hashlib
import logging
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import ifcopenshell
import ifcopenshell.ifcopenshell_wrapper
import ifcopenshell.util.classification
import ifcopenshell.util.element
import ifcopenshell.util.unit

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.raw import (
    RawElement,
    RawFile,
    RawMaterialEntry,
    RawModel,
    RawProject,
    RawReference,
    RawSpatialElement,
    RawUnit,
    RawValue,
    Scalar,
)

logger = logging.getLogger(__name__)

type Entity = Any
"""An ``ifcopenshell.entity_instance``; its attributes are only known at run time."""

_CHUNK_SIZE = 1024 * 1024

# IfcPhysicalSimpleQuantity subtypes and the measure type of their value (attribute 3).
_QUANTITY_VALUE_TYPES = {
    "IfcQuantityLength": "IfcLengthMeasure",
    "IfcQuantityArea": "IfcAreaMeasure",
    "IfcQuantityVolume": "IfcVolumeMeasure",
    "IfcQuantityCount": "IfcCountMeasure",
    "IfcQuantityWeight": "IfcMassMeasure",
    "IfcQuantityTime": "IfcTimeMeasure",
}
_QUANTITY_VALUE_INDEX = 3

# Attributes every property set definition has; any other non-empty one is data.
_PROPERTY_SET_HEADER = frozenset({"type", "GlobalId", "OwnerHistory", "Name", "Description"})


class ExtractError(Exception):
    """The file cannot be read as an IFC model at all."""


def sha256_file(path: Path) -> str:
    """Return the lower-case hex SHA-256 of a file, read in chunks to bound memory use."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def read_model(path: Path) -> RawModel:
    """Read the IFC file at ``path``.

    Raises:
        FileNotFoundError: if there is no such file.
        ExtractError: if IfcOpenShell cannot parse it, or it has no IfcProject.
    """
    size_bytes = path.stat().st_size
    sha256 = sha256_file(path)
    try:
        model = ifcopenshell.open(str(path))
    except ifcopenshell.Error as exc:
        raise ExtractError(f"{path.name}: not a readable IFC file ({exc})") from exc
    return _Extractor(model, path.name, sha256, size_bytes).run()


def _text(value: object) -> str | None:
    """Return ``value`` if it is a string, else ``None`` (IFC leaves optional text unset)."""
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    """Return ``value`` as a float if it is a number (but not a bool), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _by_id(entities: Any) -> list[Entity]:
    """Return entities sorted by STEP id, so iteration never depends on set order."""
    return sorted(entities or (), key=lambda entity: entity.id())


class _Common(NamedTuple):
    """What every object has, read once and passed into RawSpatialElement or RawElement."""

    global_id: str
    ifc_class: str
    ancestry: tuple[str, ...]
    name: str | None
    values: tuple[RawValue, ...]
    references: tuple[RawReference, ...]


class _Extractor:
    """Reads one opened file; holds the issue list and a cache of class ancestries."""

    def __init__(self, model: Entity, file_name: str, sha256: str, size_bytes: int) -> None:
        self.model = model
        self.file_name = file_name
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(model.schema_identifier)
        self.issues: list[Issue] = []
        self._ancestries: dict[str, tuple[str, ...]] = {}

    def run(self) -> RawModel:
        """Extract the whole model."""
        project = self._project()
        spatial = [
            self._spatial(entity) for entity in _by_id(self._by_type("IfcSpatialStructureElement"))
        ]
        elements = [self._element(entity) for entity in _by_id(self._by_type("IfcElement"))]
        self._log_skipped_products()
        return RawModel(
            file=self._file(),
            project=self._raw_project(project),
            units=self._project_units(project),
            spatial_elements=tuple(spatial),
            elements=tuple(elements),
            issues=tuple(self.issues),
        )

    def _by_type(self, ifc_class: str) -> list[Entity]:
        return list(self.model.by_type(ifc_class))

    # ----------------------------------------------------------------- file and project

    def _file(self) -> RawFile:
        header = self.model.header
        schemas = [s for s in header.file_schema.schema_identifiers if isinstance(s, str)]
        descriptions = tuple(d for d in header.file_description.description if isinstance(d, str))
        return RawFile(
            file_name=self.file_name,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            schema=schemas[0] if schemas else "",
            descriptions=descriptions,
        )

    def _project(self) -> Entity:
        projects = _by_id(self._by_type("IfcProject"))
        if not projects:
            raise ExtractError(f"{self.file_name}: the file has no IfcProject")
        if len(projects) > 1:
            message = f"the file has {len(projects)} IfcProject entities; using the first"
            self.issues.append(Issue(IssueCode.MULTIPLE_PROJECTS, None, message))
        return projects[0]

    def _raw_project(self, project: Entity) -> RawProject:
        # The project's owner history names the application that created it (mandatory
        # in IFC2X3); otherwise fall back to the first application in the file.
        history = project.OwnerHistory
        application = history.OwningApplication if history is not None else None
        if application is None:
            applications = _by_id(self._by_type("IfcApplication"))
            application = applications[0] if applications else None
        return RawProject(
            global_id=_text(project.GlobalId),
            name=_text(project.Name),
            long_name=_text(project.LongName),
            application_name=_text(application.ApplicationFullName) if application else None,
            application_version=_text(application.Version) if application else None,
        )

    def _project_units(self, project: Entity) -> tuple[RawUnit, ...]:
        assignment = project.UnitsInContext
        if assignment is None:
            return ()
        units = (self._unit(unit) for unit in assignment.Units or ())
        return tuple(unit for unit in units if unit is not None)

    def _unit(self, unit: Entity) -> RawUnit | None:
        """Describe a named unit; derived and monetary units are not needed here."""
        if unit is None or not unit.is_a("IfcNamedUnit"):
            return None
        is_si = bool(unit.is_a("IfcSIUnit"))
        convertible = is_si or unit.is_a("IfcConversionBasedUnit")
        return RawUnit(
            unit_type=_text(unit.UnitType) or "",
            name=_text(unit.Name) or "",
            prefix=_text(unit.Prefix) if is_si else None,
            is_si=is_si,
            scale_to_si=float(ifcopenshell.util.unit.get_unit_scale(unit)) if convertible else None,
        )

    def _log_skipped_products(self) -> None:
        skipped = Counter(
            product.is_a()
            for product in self._by_type("IfcProduct")
            if not product.is_a("IfcSpatialStructureElement") and not product.is_a("IfcElement")
        )
        if skipped:
            logger.info(
                "skipped products that are neither spatial nor physical elements",
                extra={"file": self.file_name, "skipped": dict(sorted(skipped.items()))},
            )

    # ----------------------------------------------------------------- objects

    def _ancestry(self, ifc_class: str) -> tuple[str, ...]:
        """Return the class and its supertypes, nearest first, from the file's schema."""
        if ifc_class not in self._ancestries:
            names: list[str] = []
            declaration = self.schema.declaration_by_name(ifc_class)
            while declaration is not None:
                names.append(str(declaration.name()))
                declaration = declaration.supertype()
            self._ancestries[ifc_class] = tuple(names)
        return self._ancestries[ifc_class]

    def _common(self, entity: Entity) -> _Common:
        global_id = _text(entity.GlobalId) or ""
        ifc_class = str(entity.is_a())
        return _Common(
            global_id=global_id,
            ifc_class=ifc_class,
            ancestry=self._ancestry(ifc_class),
            name=_text(entity.Name),
            values=tuple(self._values(entity, global_id)),
            references=tuple(self._references(entity, global_id)),
        )

    def _spatial(self, entity: Entity) -> RawSpatialElement:
        common = self._common(entity)
        parent = ifcopenshell.util.element.get_aggregate(entity)
        is_spatial_parent = parent is not None and parent.is_a("IfcSpatialStructureElement")
        is_storey = entity.is_a("IfcBuildingStorey")
        return RawSpatialElement(
            global_id=common.global_id,
            ifc_class=common.ifc_class,
            ancestry=common.ancestry,
            name=common.name,
            values=common.values,
            references=common.references,
            long_name=_text(entity.LongName),
            parent_global_id=_text(parent.GlobalId) if is_spatial_parent else None,
            elevation=_number(entity.Elevation) if is_storey else None,
        )

    def _element(self, entity: Entity) -> RawElement:
        common = self._common(entity)
        relations = _by_id(entity.ContainedInStructure)
        container = relations[0].RelatingStructure if relations else None
        host = (
            ifcopenshell.util.element.get_aggregate(entity)
            or ifcopenshell.util.element.get_nest(entity)
            or ifcopenshell.util.element.get_voided_element(entity)
        )
        element_type = ifcopenshell.util.element.get_type(entity)
        return RawElement(
            global_id=common.global_id,
            ifc_class=common.ifc_class,
            ancestry=common.ancestry,
            name=common.name,
            values=common.values,
            references=common.references,
            tag=_text(entity.Tag),
            container_global_id=_text(container.GlobalId) if container is not None else None,
            host_global_id=(
                _text(host.GlobalId) if host is not None and host.is_a("IfcElement") else None
            ),
            type_name=_text(element_type.Name) if element_type is not None else None,
            predefined_type=_text(getattr(entity, "PredefinedType", None)),
            type_predefined_type=(
                _text(getattr(element_type, "PredefinedType", None))
                if element_type is not None
                else None
            ),
            materials=self._materials(entity, common.global_id),
        )

    # ----------------------------------------------------------------- property values

    def _values(self, entity: Entity, global_id: str) -> Iterator[RawValue]:
        for relation in _by_id(entity.IsDefinedBy):
            if not relation.is_a("IfcRelDefinesByProperties"):
                continue
            definition = relation.RelatingPropertyDefinition
            # IFC4 can wrap several sets in one IfcPropertySetDefinitionSet.
            if definition.is_a("IfcPropertySetDefinitionSet"):
                definitions = list(definition.wrappedValue)
            else:
                definitions = [definition]
            for each in definitions:
                yield from self._definition_values(each, global_id, from_type=False)

        element_type = ifcopenshell.util.element.get_type(entity)
        if element_type is not None:
            for definition in _by_id(element_type.HasPropertySets):
                yield from self._definition_values(definition, global_id, from_type=True)

    def _definition_values(
        self, definition: Entity, global_id: str, *, from_type: bool
    ) -> Iterator[RawValue]:
        set_name = _text(definition.Name)
        if definition.is_a("IfcPropertySet"):
            members, is_quantity_set = definition.HasProperties, False
        elif definition.is_a("IfcElementQuantity"):
            members, is_quantity_set = definition.Quantities, True
        else:
            # Revit 2011 gives door and window styles an IfcDoorLiningProperties (or
            # window equivalent) named after one of their instances, with every attribute
            # empty. Such predefined sets are skipped; one that holds data is reported.
            info = definition.get_info(include_identifier=False, recursive=False)
            if any(v is not None for k, v in info.items() if k not in _PROPERTY_SET_HEADER):
                message = f"{definition.is_a()} {set_name!r} holds values that are not read"
                self.issues.append(Issue(IssueCode.UNSUPPORTED_PROPERTY_SET, global_id, message))
            return
        for member in members or ():
            value = self._member_value(member, set_name, global_id)
            if value is None:
                continue
            value_type, scalar, unit = value
            yield RawValue(
                set_name=set_name,
                name=_text(member.Name),
                value_type=value_type,
                value=scalar,
                unit=unit,
                from_type=from_type,
                from_quantity_set=is_quantity_set,
                order=(int(definition.id()), int(member.id())),
            )

    def _member_value(
        self, member: Entity, set_name: str | None, global_id: str
    ) -> tuple[str | None, Scalar | None, RawUnit | None] | None:
        """Return (value type, value, explicit unit) of a property or quantity.

        ``None`` means the member cannot be represented and has been reported.
        """
        ifc_class = str(member.is_a())
        what = f"an {ifc_class}"
        if member.is_a("IfcPropertySingleValue"):
            nominal = member.NominalValue
            if nominal is None:
                return None, None, None
            value = nominal.wrappedValue
            if isinstance(value, str | int | float | bool):
                return str(nominal.is_a()), value, self._unit(member.Unit)
            what = f"an {nominal.is_a()} value"  # e.g. IfcComplexNumber, a pair of numbers
        elif ifc_class in _QUANTITY_VALUE_TYPES:
            value = member[_QUANTITY_VALUE_INDEX]
            if isinstance(value, int | float) and not isinstance(value, bool):
                return _QUANTITY_VALUE_TYPES[ifc_class], value, self._unit(member.Unit)
            what = f"an {ifc_class} without a number"
        message = f"{set_name!r}.{_text(member.Name)!r} is {what}, which is not read"
        self.issues.append(Issue(IssueCode.UNSUPPORTED_PROPERTY, global_id, message))
        return None

    # ----------------------------------------------------------------- classifications

    def _references(self, entity: Entity, global_id: str) -> Iterator[RawReference]:
        for reference in _by_id(ifcopenshell.util.classification.get_references(entity)):
            if not reference.is_a("IfcClassificationReference"):
                message = f"{reference.is_a()} is not a classification reference; not read"
                self.issues.append(Issue(IssueCode.UNSUPPORTED_CLASSIFICATION, global_id, message))
                continue
            # IFC2X3 calls the code ItemReference; IFC4 renamed it Identification.
            code = (
                reference.ItemReference
                if self.schema.name() == "IFC2X3"
                else reference.Identification
            )
            system = ifcopenshell.util.classification.get_classification(reference)
            yield RawReference(
                system=_text(system.Name) if system is not None else None,
                code=_text(code),
                title=_text(reference.Name),
            )

    # ----------------------------------------------------------------- materials

    def _materials(self, entity: Entity, global_id: str) -> tuple[RawMaterialEntry, ...]:
        relations = self._material_relations(entity)
        if not relations:
            element_type = ifcopenshell.util.element.get_type(entity)
            if element_type is not None:
                relations = self._material_relations(element_type)
        if not relations:
            return ()
        if len(relations) > 1:
            message = f"{len(relations)} material associations; using the first"
            self.issues.append(Issue(IssueCode.MULTIPLE_MATERIAL_ASSOCIATIONS, global_id, message))

        definition = relations[0].RelatingMaterial
        if definition.is_a("IfcMaterial"):
            return (RawMaterialEntry(1, _text(definition.Name), None),)
        if definition.is_a("IfcMaterialList"):
            return tuple(
                RawMaterialEntry(position, _text(material.Name), None)
                for position, material in enumerate(definition.Materials or (), start=1)
            )
        if definition.is_a("IfcMaterialLayerSetUsage"):
            definition = definition.ForLayerSet
        if definition.is_a("IfcMaterialLayerSet"):
            layers = list(definition.MaterialLayers or ())
        elif definition.is_a("IfcMaterialLayer"):
            layers = [definition]
        else:
            message = f"{definition.is_a()} is not read; the element gets no materials"
            self.issues.append(Issue(IssueCode.UNSUPPORTED_MATERIAL, global_id, message))
            return ()
        return tuple(
            RawMaterialEntry(
                position,
                _text(layer.Material.Name) if layer.Material is not None else None,
                _number(layer.LayerThickness),
            )
            for position, layer in enumerate(layers, start=1)
        )

    @staticmethod
    def _material_relations(entity: Entity) -> list[Entity]:
        return _by_id(
            relation
            for relation in entity.HasAssociations or ()
            if relation.is_a("IfcRelAssociatesMaterial")
        )
