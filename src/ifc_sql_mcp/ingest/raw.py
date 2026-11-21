"""Plain values read from an IFC file: what ``extract`` produces and ``transform`` consumes.

These types hold what the file says, in the file's own units, without interpretation.
Keeping them free of IfcOpenShell objects means the transform can be tested with
hand-written values, and that every decision about the data is made in pure code.

``order`` fields carry STEP instance ids (``#123``). They have no meaning beyond the file,
but they are stable for a given file, so the transform uses them to break ties the same
way on every run.
"""

from dataclasses import dataclass

from ifc_sql_mcp.ingest.issues import Issue

type Scalar = str | float | int | bool
"""A primitive IFC value as IfcOpenShell returns it (``wrappedValue``)."""


@dataclass(frozen=True, slots=True)
class RawUnit:
    """A named unit (IfcSIUnit, IfcConversionBasedUnit or IfcContextDependentUnit).

    Attributes:
        unit_type: IfcUnitEnum value, e.g. ``LENGTHUNIT``.
        name: Unit name as written, e.g. ``METRE``, ``FOOT``, ``SQUARE FOOT``.
        prefix: SI prefix such as ``MILLI``; always ``None`` for non-SI units.
        is_si: True for an IfcSIUnit.
        scale_to_si: Factor that converts a value in this unit to the unprefixed SI unit
            (0.3048 for FOOT, 1e-6 for MILLI SQUARE_METRE); ``None`` when the file gives
            no conversion (a context-dependent unit).
    """

    unit_type: str
    name: str
    prefix: str | None
    is_si: bool
    scale_to_si: float | None


@dataclass(frozen=True, slots=True)
class RawValue:
    """One property (IfcPropertySingleValue) or quantity (IfcPhysicalSimpleQuantity).

    Attributes:
        set_name: Name of the property set or quantity set; may be missing or blank.
        name: Name of the property or quantity; may be missing or blank.
        value_type: IFC type of the value, e.g. ``IfcLengthMeasure``; ``None`` when the
            property has no value.
        value: The value in the file's units; ``None`` when absent.
        unit: Unit stated on the property itself; ``None`` means the project unit applies.
        from_type: True if the set belongs to the element's IfcTypeObject.
        from_quantity_set: True if the set is an IfcElementQuantity.
        order: (set id, value id) in the STEP file, for deterministic tie-breaking.
        from_attribute: True if the value is an attribute of an entity rather than an
            IfcProperty: a door's ``OverallWidth`` (under a set named after the declaring
            entity) or a lining's ``LiningDepth`` (under a set named after its class).
    """

    set_name: str | None
    name: str | None
    value_type: str | None
    value: Scalar | None
    unit: RawUnit | None
    from_type: bool
    from_quantity_set: bool
    order: tuple[int, int]
    from_attribute: bool = False


@dataclass(frozen=True, slots=True)
class RawReference:
    """An IfcClassificationReference that applies to an object (directly or via its type).

    Attributes:
        system: Name of the IfcClassification it belongs to, e.g. ``UniFormat``.
        code: ItemReference (IFC2X3) or Identification (IFC4), e.g. ``D5020220``.
        title: The reference's Name, e.g. ``Lighting - Incandescent``.
    """

    system: str | None
    code: str | None
    title: str | None


@dataclass(frozen=True, slots=True)
class RawMaterialEntry:
    """One position in an element's material definition.

    Attributes:
        position: 1-based layer or list position.
        material_name: Name of the material at this position; ``None`` if there is none
            (an air-gap layer) or it has no name.
        thickness: Layer thickness in project length units; ``None`` outside layer sets.
    """

    position: int
    material_name: str | None
    thickness: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RawObject:
    """Attributes shared by spatial and physical elements.

    Attributes:
        global_id: GlobalId as written in the file (not yet validated).
        ifc_class: Exact entity name, e.g. ``IfcWallStandardCase``.
        ancestry: ``ifc_class`` followed by its supertypes, e.g. ``("IfcWallStandardCase",
            "IfcWall", "IfcBuildingElement", ...)``.
        name: IFC Name, verbatim.
        values: Every property and quantity value, from the object and from its type.
        references: Classification references, including those inherited from the type.
    """

    global_id: str
    ifc_class: str
    ancestry: tuple[str, ...]
    name: str | None
    values: tuple[RawValue, ...]
    references: tuple[RawReference, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RawSpatialElement(RawObject):
    """A site, building, storey or space.

    Attributes:
        long_name: IFC LongName.
        parent_global_id: GlobalId of the spatial element it is aggregated into, if any.
        elevation: Storey elevation in project length units; ``None`` for other classes.
    """

    long_name: str | None
    parent_global_id: str | None
    elevation: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RawElement(RawObject):
    """A physical element: wall, door, opening, duct, chair...

    Attributes:
        tag: IFC Tag, verbatim (Revit writes the ElementId here).
        container_global_id: Direct IfcRelContainedInSpatialStructure target, if any.
        host_global_id: Element this one is part of (IfcRelAggregates, IfcRelNests) or
            cuts (IfcRelVoidsElement, for openings); used to find the storey of elements
            that have no container of their own.
        type_name: Name of the assigned IfcTypeObject.
        predefined_type: The occurrence's PredefinedType attribute, if the class has one.
        type_predefined_type: The type object's PredefinedType, if any.
        materials: The element's material definition; empty if it has none.
    """

    tag: str | None
    container_global_id: str | None
    host_global_id: str | None
    type_name: str | None
    predefined_type: str | None
    type_predefined_type: str | None
    materials: tuple[RawMaterialEntry, ...]


@dataclass(frozen=True, slots=True)
class RawFile:
    """Facts about the file itself and its STEP header.

    Attributes:
        file_name: Base name of the file.
        sha256: Lower-case hex SHA-256 of the file bytes.
        size_bytes: File size in bytes.
        schema: First FILE_SCHEMA identifier, e.g. ``IFC2X3``.
        descriptions: FILE_DESCRIPTION strings, e.g. ``("ViewDefinition [CoordinationView]",)``.
    """

    file_name: str
    sha256: str
    size_bytes: int
    schema: str
    descriptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RawProject:
    """The IfcProject and the application that wrote the file.

    Attributes:
        global_id: IfcProject GlobalId.
        name: IfcProject Name (Revit: Project Number).
        long_name: IfcProject LongName (Revit: Project Name).
        application_name: IfcApplication ApplicationFullName.
        application_version: IfcApplication Version.
    """

    global_id: str | None
    name: str | None
    long_name: str | None
    application_name: str | None
    application_version: str | None


@dataclass(frozen=True, slots=True)
class RawModel:
    """Everything extracted from one IFC file.

    Attributes:
        file: File and header facts.
        project: Project and authoring application.
        units: Named units assigned to the project (IfcUnitAssignment).
        spatial_elements: Sites, buildings, storeys and spaces, in file order.
        elements: Physical elements, in file order.
        issues: Problems found while reading (e.g. unsupported property kinds).
    """

    file: RawFile
    project: RawProject
    units: tuple[RawUnit, ...]
    spatial_elements: tuple[RawSpatialElement, ...]
    elements: tuple[RawElement, ...]
    issues: tuple[Issue, ...]
