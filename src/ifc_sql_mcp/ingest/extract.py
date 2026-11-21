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
* **Attributes** that are measurements or kinds of operation, not properties: a door's
  or window's ``OverallWidth`` and ``OverallHeight`` and its operation or partitioning
  type, on the occurrence and on its type (:data:`_ATTRIBUTES`). Each is stored as a
  property of a set named after the entity that declares it (``IfcDoor.OverallWidth``),
  typed by the schema's declaration, so it is converted like any measure.
* **Predefined property sets** (``IfcDoorLiningProperties``, ``IfcDoorPanelProperties``,
  ``IfcWindowLiningProperties``...), whose values are attributes too: every attribute
  with a simple value becomes a property of a set named after the class. ``NOTDEFINED``
  is no value, as for ``PredefinedType``.
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

# Attributes read as properties, by the entity that declares them. A door schedule needs
# the overall size and how the door opens; IFC puts them in attributes, not property
# sets. Names an entity's schema does not have (IFC2X3's IfcDoor has no OperationType)
# are skipped.
_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "IfcDoor": ("OverallWidth", "OverallHeight", "OperationType", "UserDefinedOperationType"),
    "IfcWindow": (
        "OverallWidth",
        "OverallHeight",
        "PartitioningType",
        "UserDefinedPartitioningType",
    ),
    "IfcDoorType": ("OperationType", "UserDefinedOperationType"),
    "IfcDoorStyle": ("OperationType", "ConstructionType"),
    "IfcWindowType": ("PartitioningType", "UserDefinedPartitioningType"),
    "IfcWindowStyle": ("OperationType", "ConstructionType"),
}

# IFC's simple types, as value types: the attribute declares "boolean", a property
# would say IfcBoolean.
_SIMPLE_TYPES = {
    "boolean": "IfcBoolean",
    "logical": "IfcLogical",
    "string": "IfcLabel",
    "real": "IfcReal",
    "number": "IfcReal",
    "integer": "IfcInteger",
}
_NOT_DEFINED = "NOTDEFINED"

# An ISO 10303-21 (STEP) file opens and closes with these keywords.
_STEP_START = b"ISO-10303-21;"
_STEP_END = b"END-ISO-10303-21;"
_ENDS_BYTES = 256


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
        ExtractError: if the file is empty or truncated, IfcOpenShell cannot parse it, or
            it has no IfcProject.
    """
    size_bytes = path.stat().st_size
    sha256 = sha256_file(path)
    _check_complete(path, size_bytes)
    try:
        model = ifcopenshell.open(str(path))
    except ifcopenshell.Error as exc:
        raise ExtractError(f"{path.name}: not a readable IFC file ({exc})") from exc
    return _Extractor(model, path.name, sha256, size_bytes).run()


def _check_complete(path: Path, size_bytes: int) -> None:
    """Refuse an empty file, or a STEP file that stops before its closing keyword.

    IfcOpenShell reads as many entities as it can and does not complain when a file
    stops halfway, so a cut-off copy would load as a smaller model that looks valid. A
    STEP file starts with ``ISO-10303-21;`` and ends with ``END-ISO-10303-21;``. A file
    that does not start that way is left to IfcOpenShell to accept or reject.
    """
    if size_bytes == 0:
        raise ExtractError(f"{path.name}: the file is empty")
    with path.open("rb") as handle:
        start = handle.read(_ENDS_BYTES).lstrip()
        handle.seek(max(0, size_bytes - _ENDS_BYTES))
        end = handle.read().rstrip()
    if start.startswith(_STEP_START) and not end.endswith(_STEP_END):
        raise ExtractError(f"{path.name}: the file ends before END-ISO-10303-21; (truncated)")


def _text(value: object) -> str | None:
    """Return ``value`` if it is a string, else ``None`` (IFC leaves optional text unset)."""
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    """Return ``value`` as a float if it is a number (but not a bool), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _is_predefined(definition: Entity) -> bool:
    """True for a property set definition that is neither a property nor a quantity set."""
    return not definition.is_a("IfcPropertySet") and not definition.is_a("IfcElementQuantity")


def _is_value(value: object) -> bool:
    """True for a simple attribute value worth storing: not absent, not NOTDEFINED."""
    return isinstance(value, str | int | float | bool) and value != _NOT_DEFINED


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
        definitions: list[tuple[Entity, bool]] = []
        for relation in _by_id(entity.IsDefinedBy):
            if not relation.is_a("IfcRelDefinesByProperties"):
                continue
            definition = relation.RelatingPropertyDefinition
            # IFC4 can wrap several sets in one IfcPropertySetDefinitionSet.
            if definition.is_a("IfcPropertySetDefinitionSet"):
                definitions.extend((each, False) for each in definition.wrappedValue)
            else:
                definitions.append((definition, False))
        yield from self._attribute_values(entity, global_id, from_type=False)

        element_type = ifcopenshell.util.element.get_type(entity)
        if element_type is not None:
            definitions.extend((d, True) for d in _by_id(element_type.HasPropertySets))
            yield from self._attribute_values(element_type, global_id, from_type=True)

        # A predefined set is named after its class; a second one of the same class (a
        # double door's two panels) gets a number, in file order.
        seen: Counter[str] = Counter()
        for definition, from_type in definitions:
            if _is_predefined(definition):
                seen[definition.is_a()] += 1
                count = seen[definition.is_a()]
                name = definition.is_a() if count == 1 else f"{definition.is_a()} {count}"
                yield from self._predefined_values(definition, name, global_id, from_type)
            else:
                yield from self._definition_values(definition, global_id, from_type=from_type)

    def _definition_values(
        self, definition: Entity, global_id: str, *, from_type: bool
    ) -> Iterator[RawValue]:
        set_name = _text(definition.Name)
        if definition.is_a("IfcPropertySet"):
            members, is_quantity_set = definition.HasProperties, False
        else:
            members, is_quantity_set = definition.Quantities, True
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

    def _attribute_values(
        self, entity: Entity, global_id: str, *, from_type: bool
    ) -> Iterator[RawValue]:
        """The attributes of :data:`_ATTRIBUTES` that ``entity`` has, as properties."""
        for declaring, names in _ATTRIBUTES.items():
            if not entity.is_a(declaring):
                continue
            declared = self._declared_types(entity)
            for index, name in enumerate(names):
                value = getattr(entity, name, None) if name in declared else None
                if _is_value(value):
                    yield RawValue(
                        set_name=declaring,
                        name=name,
                        value_type=declared[name],
                        value=value,
                        unit=None,
                        from_type=from_type,
                        from_quantity_set=False,
                        order=(int(entity.id()), index),
                        from_attribute=True,
                    )

    def _predefined_values(
        self, definition: Entity, set_name: str, global_id: str, from_type: bool
    ) -> Iterator[RawValue]:
        """Every simple attribute of a predefined property set, as a property.

        Revit 2011 gives door and window styles such sets with every attribute empty, and
        Revit 2022 with only NOTDEFINED enumerations: they yield nothing. An attribute
        that refers to another entity (``ShapeAspectStyle``) is not read, and reported.
        """
        declared = self._declared_types(definition)
        skipped: list[str] = []
        for index, (name, value_type) in enumerate(declared.items()):
            if name in _PROPERTY_SET_HEADER:
                continue
            value = getattr(definition, name, None)
            if isinstance(value, ifcopenshell.entity_instance):
                skipped.append(name)
            elif _is_value(value):
                yield RawValue(
                    set_name=set_name,
                    name=name,
                    value_type=value_type,
                    value=value,
                    unit=None,
                    from_type=from_type,
                    from_quantity_set=False,
                    order=(int(definition.id()), index),
                    from_attribute=True,
                )
        if skipped:
            message = f"{set_name}.{skipped[0]} refers to another entity, which is not read"
            self.issues.append(Issue(IssueCode.UNSUPPORTED_PROPERTY_SET, global_id, message))

    def _declared_types(self, entity: Entity) -> dict[str, str]:
        """Each attribute of the entity's class, with the IFC type it is declared as."""
        declaration = self.schema.declaration_by_name(entity.is_a()).as_entity()
        types: dict[str, str] = {}
        for attribute in declaration.all_attributes():
            kind = attribute.type_of_attribute()
            named = kind.as_named_type()
            if named is not None:
                types[str(attribute.name())] = str(named.declared_type().name())
            elif kind.as_simple_type() is not None:
                simple = str(kind.as_simple_type().declared_type())
                types[str(attribute.name())] = _SIMPLE_TYPES.get(simple, "IfcLabel")
        return types

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
