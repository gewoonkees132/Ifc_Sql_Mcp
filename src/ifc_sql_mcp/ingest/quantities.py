"""Map property values onto the six canonical quantities.

:data:`QUANTITY_RULES` lists every mapping; nothing else becomes a quantity, and the raw
values stay in ``property`` as well. The rules come in two families (ADR 3, ADR 8):

* **IFC base quantities** (source ``ifc_qto``). Revit 2019 to 2022 write the standard
  ``Qto_*BaseQuantities`` sets in IFC4 (``Qto_WallBaseQuantities``...), and one set
  named ``BaseQuantities`` in IFC2X3, whose quantities carry the same names. A
  ``BaseQuantities`` set is read as the ``Qto_`` set of the object's class
  (:data:`QTO_SET_OF_CLASS`): on a wall, it is ``Qto_WallBaseQuantities``. Spaces of
  Revit 2011 and 2013 add ``GSA Space Areas``, from the GSA spatial validation
  workflow; in all 65 spaces of the Duplex files its area agrees with Revit's own to
  within 1.1e-14 m².
* **Revit parameters** (source ``revit_pset``): ``PSet_Revit_Dimensions`` (Revit 2011)
  or ``Dimensions`` (Revit 2013 and later), when the file was exported with Revit
  property sets.
* **IFC attributes** (source ``ifc_attribute``): a door's and a window's
  ``OverallWidth`` and ``OverallHeight``, which every Revit export writes, even with
  base quantities and Revit property sets switched off (audit finding A4).
* **Geometry** (source ``geometry``), only with ``ifc-sql ingest --geometry``: the shapes
  ``geometry.py`` measured. A wall's area is its side area, a slab's and a space's their
  footprint, and every measured solid gets its volume (:func:`geometry_quantities`,
  audit finding A5). It ranks last, so it stands in only where the file has nothing.

Within a family, a net value is preferred, and the gross one is a **fallback**: it
supplies the quantity only when its set has no net value, or only an implausible one
(``plausibility.py``), and then it never conflicts. Two exceptions come from the
evidence of the audit's files, not from IFC: a slab's area is ``GrossArea`` alone, because
Revit 2021 writes ``NetArea`` as the area of the whole solid (top, bottom and edges); and
a wall's side area falls back to ``GrossSideArea``, which Revit 2019 writes alone and
which equals Revit's own ``Area`` on all 1,041 walls of the audit's file E.

A value only becomes a quantity if it is the right kind of measure. Revit writes some
dimension parameters as unitless ``IfcReal`` (pipe fittings' ``Length``, for one), and
guessing their unit would be worse than leaving them out, so those are reported.
Negative values are reported and left out too, because the column holds magnitudes, and
implausible values are left out silently, since ``implausible_quantity`` reports them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.properties import TypedValue
from ifc_sql_mcp.ingest.raw import RawShape
from ifc_sql_mcp.ingest.records import QuantityName, QuantityRecord, QuantitySource

REVIT_DIMENSION_SETS = ("PSet_Revit_Dimensions", "Dimensions")
"""Revit 2011 prefixes its parameter sets with ``PSet_Revit_``; later versions do not."""

IFC2X3_BASE_QUANTITIES = "BaseQuantities"
"""The one quantity set name Revit's IFC2X3 exports use for every class."""

QTO_SET_OF_CLASS: Mapping[str, str] = {
    "IfcWall": "Qto_WallBaseQuantities",
    "IfcSlab": "Qto_SlabBaseQuantities",
    "IfcSpace": "Qto_SpaceBaseQuantities",
    "IfcDoor": "Qto_DoorBaseQuantities",
    "IfcWindow": "Qto_WindowBaseQuantities",
    "IfcColumn": "Qto_ColumnBaseQuantities",
    "IfcBeam": "Qto_BeamBaseQuantities",
    "IfcMember": "Qto_MemberBaseQuantities",
    "IfcRailing": "Qto_RailingBaseQuantities",
    "IfcStairFlight": "Qto_StairFlightBaseQuantities",
    "IfcCovering": "Qto_CoveringBaseQuantities",
}
"""The IFC4 ``Qto_`` set an IFC2X3 ``BaseQuantities`` set stands for, by ``ifc_class_base``."""


@dataclass(frozen=True, slots=True)
class QuantityRule:
    """One mapping from a property to a canonical quantity.

    Attributes:
        set_names: Sets the property may be in, in order of preference.
        property_name: Name of the property.
        quantity: Canonical quantity it supplies.
        source: ``ifc_qto`` for an IfcElementQuantity, ``revit_pset`` for a Revit parameter.
        unit: SI unit the converted value must carry, which proves it was a measure of
            the right dimension.
        fallback: True for a value used only when no earlier rule supplied the quantity
            from the same source; it is never reported as conflicting.
    """

    set_names: tuple[str, ...]
    property_name: str
    quantity: QuantityName
    source: QuantitySource
    unit: str
    fallback: bool = False


def _qto(
    set_name: str, *mappings: tuple[str, QuantityName, str] | tuple[str, QuantityName, str, bool]
) -> tuple[QuantityRule, ...]:
    """Rules for one IFC base quantity set: (property, quantity, unit[, fallback])."""
    return tuple(
        QuantityRule((set_name,), m[0], m[1], "ifc_qto", m[2], fallback=len(m) == 4 and m[3])
        for m in mappings
    )


GROSS = True
"""Marks a gross value that is only a fallback for the net one."""

QUANTITY_RULES: tuple[QuantityRule, ...] = (
    *_qto(
        "Qto_WallBaseQuantities",
        ("Length", "length_m", "m"),
        ("Height", "height_m", "m"),
        ("Width", "width_m", "m"),
        ("NetSideArea", "area_m2", "m2"),
        ("GrossSideArea", "area_m2", "m2", GROSS),
        ("NetVolume", "volume_m3", "m3"),
        ("GrossVolume", "volume_m3", "m3", GROSS),
    ),
    *_qto(
        "Qto_SlabBaseQuantities",
        ("Width", "width_m", "m"),
        ("Perimeter", "perimeter_m", "m"),
        ("GrossArea", "area_m2", "m2"),
        ("NetVolume", "volume_m3", "m3"),
        ("GrossVolume", "volume_m3", "m3", GROSS),
    ),
    *_qto(
        "Qto_SpaceBaseQuantities",
        ("Height", "height_m", "m"),
        ("NetFloorArea", "area_m2", "m2"),
        ("GrossFloorArea", "area_m2", "m2", GROSS),
        ("NetPerimeter", "perimeter_m", "m"),
        ("GrossPerimeter", "perimeter_m", "m", GROSS),
        ("NetVolume", "volume_m3", "m3"),
        ("GrossVolume", "volume_m3", "m3", GROSS),
    ),
    *(
        rule
        for set_name in ("Qto_DoorBaseQuantities", "Qto_WindowBaseQuantities")
        for rule in _qto(
            set_name,
            ("Width", "width_m", "m"),
            ("Height", "height_m", "m"),
            ("Area", "area_m2", "m2"),
        )
    ),
    *(
        rule
        for set_name in (
            "Qto_ColumnBaseQuantities",
            "Qto_BeamBaseQuantities",
            "Qto_MemberBaseQuantities",
            "Qto_StairFlightBaseQuantities",
        )
        for rule in _qto(
            set_name,
            ("Length", "length_m", "m"),
            ("NetVolume", "volume_m3", "m3"),
            ("GrossVolume", "volume_m3", "m3", GROSS),
        )
    ),
    *_qto("Qto_RailingBaseQuantities", ("Length", "length_m", "m")),
    # A ceiling or a floor finish (re-check finding R2). Width is its thickness. Revit
    # 2019 writes a ceiling's area into IFC2X3's BaseQuantities as GrossCeilingArea, a name
    # no IFC set has; on all 259 coverings of the audit's file E it equals Revit's Area.
    *_qto(
        "Qto_CoveringBaseQuantities",
        ("Width", "width_m", "m"),
        ("NetArea", "area_m2", "m2"),
        ("GrossArea", "area_m2", "m2", GROSS),
        ("GrossCeilingArea", "area_m2", "m2", GROSS),
    ),
    QuantityRule(("GSA Space Areas",), "GSA BIM Area", "area_m2", "ifc_qto", "m2", fallback=True),
    # A door's and a window's overall size are attributes of the occurrence (A4).
    QuantityRule(("IfcDoor", "IfcWindow"), "OverallWidth", "width_m", "ifc_attribute", "m"),
    QuantityRule(("IfcDoor", "IfcWindow"), "OverallHeight", "height_m", "ifc_attribute", "m"),
    QuantityRule(REVIT_DIMENSION_SETS, "Length", "length_m", "revit_pset", "m"),
    QuantityRule(REVIT_DIMENSION_SETS, "Area", "area_m2", "revit_pset", "m2"),
    QuantityRule(REVIT_DIMENSION_SETS, "Volume", "volume_m3", "revit_pset", "m3"),
    QuantityRule(REVIT_DIMENSION_SETS, "Perimeter", "perimeter_m", "revit_pset", "m"),
    QuantityRule(REVIT_DIMENSION_SETS, "Unbounded Height", "height_m", "revit_pset", "m"),
)


def _set_rank(rule: QuantityRule, value: TypedValue, ifc_class_base: str | None) -> int | None:
    """Where the value's set stands in the rule's preference, or ``None`` if it is not one.

    An IFC2X3 ``BaseQuantities`` set counts as the ``Qto_`` set of the object's class.
    """
    set_name = value.set_name
    if set_name == IFC2X3_BASE_QUANTITIES and ifc_class_base is not None:
        set_name = QTO_SET_OF_CLASS.get(ifc_class_base, set_name)
    if set_name not in rule.set_names:
        return None
    return rule.set_names.index(set_name)


def _matches(rule: QuantityRule, value: TypedValue, ifc_class_base: str | None) -> bool:
    return (
        value.name == rule.property_name
        and _set_rank(rule, value, ifc_class_base) is not None
        # A property set that happens to be called "GSA Space Areas" is not an IFC
        # quantity set, a quantity set is not a Revit parameter, and a set named IfcDoor
        # is not the door's attributes.
        and value.from_quantity_set == (rule.source == "ifc_qto")
        and value.from_attribute == (rule.source == "ifc_attribute")
    )


def derive_quantities(
    global_id: str, values: Sequence[TypedValue], ifc_class_base: str | None = None
) -> tuple[list[QuantityRecord], list[Issue]]:
    """Return the canonical quantities of one object, from its resolved property values.

    Args:
        global_id: The object, for issue messages.
        values: Its resolved property values.
        ifc_class_base: Its normalised class, which says what an IFC2X3
            ``BaseQuantities`` set stands for.

    Where two values supply the same quantity and source, the rule's first set wins, and
    an earlier rule wins over a later fallback without a word.
    """
    issues: list[Issue] = []
    kept: dict[tuple[str, str], tuple[QuantityRecord, TypedValue]] = {}
    for rule in QUANTITY_RULES:
        matching = [value for value in values if _matches(rule, value, ifc_class_base)]
        matching.sort(key=lambda value: _set_rank(rule, value, ifc_class_base) or 0)
        for value in matching:
            if value.text is None and value.number is None and value.boolean is None:
                continue  # an empty parameter: nothing to measure, and nothing wrong here
            if value.implausible:
                continue  # breaks a bound of its set; implausible_quantity says so already
            key = (rule.quantity, rule.source)
            if rule.fallback and key in kept:
                continue  # the preferred value is there; the fallback is not needed
            if value.number is None or value.unit != rule.unit:
                message = (
                    f"{value.ref} ({value.value_type}) is not a measure in {rule.unit}; "
                    f"no {rule.quantity}"
                )
                issues.append(Issue(IssueCode.QUANTITY_NOT_A_MEASURE, global_id, message))
                continue
            if value.number < 0:
                message = f"{value.ref} = {value.number!r} {value.unit} is negative"
                issues.append(Issue(IssueCode.NEGATIVE_QUANTITY, global_id, message))
                continue
            record = QuantityRecord(
                global_id=global_id,
                name=rule.quantity,
                source=rule.source,
                value=value.number,
                source_ref=value.ref,
            )
            if key not in kept:
                kept[key] = (record, value)
            elif kept[key][0].value != record.value:
                message = (
                    f"{rule.quantity} ({rule.source}) is {kept[key][0].value!r} from "
                    f"{kept[key][1].ref} and {record.value!r} from {value.ref}; kept the first"
                )
                issues.append(Issue(IssueCode.CONFLICTING_QUANTITY, global_id, message))
    records = sorted((record for record, _ in kept.values()), key=lambda r: (r.name, r.source))
    return records, issues


GEOMETRY_AREAS: Mapping[str, tuple[str, str]] = {
    "IfcWall": ("side_area_m2", "geometry:side_area"),
    "IfcSlab": ("footprint_area_m2", "geometry:footprint_area"),
    "IfcSpace": ("footprint_area_m2", "geometry:footprint_area"),
}
"""Which measure is an object's area, by ``ifc_class_base``; other classes get no area."""


def geometry_quantities(
    shapes: Sequence[RawShape], classes: Mapping[str, str]
) -> list[QuantityRecord]:
    """The quantities of the measured shapes (source ``geometry``).

    Args:
        shapes: What ``geometry.measure_shapes`` measured.
        classes: ``ifc_class_base`` by GlobalId of the objects that are loaded; a shape of
            any other object is left out.

    A measure that is not positive (an open mesh's volume, a vertical slab's footprint)
    makes no quantity: zero would claim the element was measured empty.
    """
    records: list[QuantityRecord] = []
    for shape in shapes:
        ifc_class = classes.get(shape.global_id)
        if ifc_class is None:
            continue
        measures: list[tuple[QuantityName, float, str]] = [
            ("volume_m3", shape.volume_m3, "geometry:volume")
        ]
        if ifc_class in GEOMETRY_AREAS:
            attribute, ref = GEOMETRY_AREAS[ifc_class]
            measures.append(("area_m2", getattr(shape, attribute), ref))
        records.extend(
            QuantityRecord(
                global_id=shape.global_id,
                name=name,
                source="geometry",
                value=value,
                source_ref=ref,
            )
            for name, value, ref in measures
            if value > 0
        )
    return records
