"""Map property values onto the five canonical quantities.

Revit 2011 and 2013 exports contain no ``Qto_*BaseQuantities``. Their quantities are
Revit parameters in a dimensions set, and spaces add one IFC quantity set from the GSA
spatial validation workflow. :data:`QUANTITY_RULES` lists every mapping; nothing else
becomes a quantity. The raw values stay in ``property`` as well.

A value only becomes a quantity if it is the right kind of measure. Revit writes some
dimension parameters as unitless ``IfcReal`` (pipe fittings' ``Length``, for one), and
guessing their unit would be worse than leaving them out, so those are reported.
Negative values are reported and left out too, because the column holds magnitudes.

``GSA Space Areas.GSA BIM Area`` counts as ``area_m2`` with source ``ifc_qto``. It is an
IfcQuantityArea that the exporter writes for every space, and in all 65 spaces of the
four sample files it agrees with the Revit ``Area`` parameter of the same space to within
1.1e-14 m². Treating it as an area therefore changes no value, and it keeps IFC's own
quantity first in ``v_quantity``'s precedence.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.properties import TypedValue
from ifc_sql_mcp.ingest.records import QuantityName, QuantityRecord, QuantitySource

REVIT_DIMENSION_SETS = ("PSet_Revit_Dimensions", "Dimensions")
"""Revit 2011 prefixes its parameter sets with ``PSet_Revit_``; Revit 2013 does not."""


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
    """

    set_names: tuple[str, ...]
    property_name: str
    quantity: QuantityName
    source: QuantitySource
    unit: str


QUANTITY_RULES: tuple[QuantityRule, ...] = (
    QuantityRule(("GSA Space Areas",), "GSA BIM Area", "area_m2", "ifc_qto", "m2"),
    QuantityRule(REVIT_DIMENSION_SETS, "Length", "length_m", "revit_pset", "m"),
    QuantityRule(REVIT_DIMENSION_SETS, "Area", "area_m2", "revit_pset", "m2"),
    QuantityRule(REVIT_DIMENSION_SETS, "Volume", "volume_m3", "revit_pset", "m3"),
    QuantityRule(REVIT_DIMENSION_SETS, "Perimeter", "perimeter_m", "revit_pset", "m"),
    QuantityRule(REVIT_DIMENSION_SETS, "Unbounded Height", "height_m", "revit_pset", "m"),
)


def _matches(rule: QuantityRule, value: TypedValue) -> bool:
    is_ifc_quantity = rule.source == "ifc_qto"
    return (
        value.name == rule.property_name
        and value.set_name in rule.set_names
        # A property set that happens to be called "GSA Space Areas" is not an IFC
        # quantity set, and a quantity set is not a Revit parameter.
        and value.from_quantity_set == is_ifc_quantity
    )


def derive_quantities(
    global_id: str, values: Sequence[TypedValue]
) -> tuple[list[QuantityRecord], list[Issue]]:
    """Return the canonical quantities of one object, from its resolved property values.

    Where two values supply the same quantity and source, the rule's first set wins.
    """
    issues: list[Issue] = []
    kept: dict[tuple[str, str], tuple[QuantityRecord, TypedValue]] = {}
    for rule in QUANTITY_RULES:
        matching = [value for value in values if _matches(rule, value)]
        matching.sort(key=lambda value: rule.set_names.index(value.set_name))
        for value in matching:
            if value.text is None and value.number is None and value.boolean is None:
                continue  # an empty parameter: nothing to measure, and nothing wrong here
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
            key = (rule.quantity, rule.source)
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
