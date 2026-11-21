"""Turn raw property and quantity values into typed property rows, one per key.

Three things happen here, in this order:

1. **Typing.** Each value goes into exactly one of text, number or boolean, chosen by
   its Python type, and keeps its IFC type name. Lengths, areas, volumes and plane
   angles are converted to SI (``units.py``). Empty strings and placeholders such as
   ``BarCode = 'BarCode'`` are kept verbatim: they are data quality findings, not load
   errors.
2. **Type properties.** A value is a type property if it came from the element's
   IfcTypeObject, or if it sits in a ``PSet_Revit_Type_*`` set. Revit 2011 copies type
   parameters onto every instance under that prefix because walls have no type object.
3. **One value per key.** The table's key is (object, set, property), but files hold
   duplicates. Revit 2013 attaches two sets of the same name to one element (instance
   and type parameters, both called ``Other``), and one set can repeat a property name.
   The rules, in order:

   * a value on the occurrence overrides one from its type (IFC's own rule), silently;
   * in a quantity set, the one value that keeps the set's physical bounds wins
     (``plausibility.choose``): Revit 2021 writes a wall's ``NetSideArea`` twice, first
     unscaled, and file order would keep the value 10⁶ times too large;
   * otherwise the value written first in the file wins (lowest STEP ids);
   * a losing value that differs from the winner is reported, identical ones are not.
4. **Plausibility.** A quantity-set value that still breaks a physical bound set by the
   other values of its set (a space volume of 1,000 x area x height) is kept as written
   but flagged ``implausible``, so it becomes no quantity (``plausibility.check_set``).

Quantity sets (IfcElementQuantity) are stored here too, under their set name, so every
value in the file is queryable and ``quantity.source_ref`` always names a property row.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ifc_sql_mcp.ingest import plausibility
from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.raw import RawUnit, RawValue
from ifc_sql_mcp.ingest.records import PropertyRecord
from ifc_sql_mcp.ingest.units import (
    DIMENSIONLESS_MEASURES,
    MEASURE_DIMENSIONS,
    Dimension,
    to_si,
)

REVIT_TYPE_SET_PREFIX = "PSet_Revit_Type_"


@dataclass(frozen=True, slots=True)
class TypedValue:
    """A property after typing and conversion, before it becomes a record.

    It keeps ``from_quantity_set`` and ``order``, which the quantity mapping and the
    duplicate rules need but the property table does not.
    """

    set_name: str
    name: str
    value_type: str | None
    text: str | None
    number: float | None
    boolean: bool | None
    unit: str | None
    is_type_property: bool
    from_type: bool
    from_quantity_set: bool
    order: tuple[int, int]
    implausible: bool = False
    from_attribute: bool = False

    @property
    def payload(self) -> tuple[object, ...]:
        """What makes two values for one key the same value."""
        return (self.value_type, self.text, self.number, self.boolean, self.unit)

    @property
    def ref(self) -> str:
        """``Set.Property``, the form used in messages and ``quantity.source_ref``."""
        return f"{self.set_name}.{self.name}"

    def to_record(self, global_id: str) -> PropertyRecord:
        """Return the ``ifc.property`` row for this value."""
        return PropertyRecord(
            global_id=global_id,
            pset_name=self.set_name,
            prop_name=self.name,
            value_type=self.value_type,
            value_text=self.text,
            value_num=self.number,
            value_bool=self.boolean,
            unit=self.unit,
            is_type_property=self.is_type_property,
            is_implausible=self.implausible,
        )


def type_value(
    global_id: str, raw: RawValue, units: Mapping[str, RawUnit]
) -> tuple[TypedValue | None, list[Issue]]:
    """Type and convert one raw value.

    Args:
        global_id: The object the value belongs to, for issue messages.
        raw: The value as extracted.
        units: Project units by IfcUnitEnum value (see ``units.project_units``).

    Returns:
        The typed value, or ``None`` if it cannot be represented, plus any issues.
    """
    set_name = (raw.set_name or "").strip()
    name = (raw.name or "").strip()
    if not set_name or not name:
        where = f"{raw.set_name!r}.{raw.name!r}"
        return None, [Issue(IssueCode.UNNAMED_PROPERTY, global_id, f"{where} has no name")]
    # Names are kept as written; only the check above ignores surrounding blanks.
    set_name, name = raw.set_name or "", raw.name or ""
    ref = f"{set_name}.{name}"
    issues: list[Issue] = []

    text: str | None = None
    number: float | None = None
    boolean: bool | None = None
    unit_symbol: str | None = None
    value = raw.value
    # bool before int: in Python, True is also an int.
    if isinstance(value, bool):
        boolean = value
    elif isinstance(value, str):
        text = value
    elif isinstance(value, int | float):
        number = float(value)
        dimension = MEASURE_DIMENSIONS.get(raw.value_type or "")
        if dimension is not None and dimension.required:
            unit = raw.unit if raw.unit and raw.unit.unit_type == dimension.unit_type else None
            unit = unit or units[dimension.unit_type]
            converted = to_si(number, unit)
            if converted is None:
                message = f"{ref} = {value!r} is in {unit.name}, which has no SI conversion"
                return None, [Issue(IssueCode.UNIT_NOT_CONVERTIBLE, global_id, message)]
            number, unit_symbol = converted, dimension.si_symbol
        elif (derived := _derived_si(number, dimension, raw, units)) is not None:
            number, unit_symbol = derived
        elif raw.unit is not None or _has_physical_unit(raw.value_type):
            message = f"{ref} = {value!r} ({raw.value_type}) is stored as written, without a unit"
            issues.append(Issue(IssueCode.MEASURE_NOT_CONVERTED, global_id, message))
        if not math.isfinite(number):
            message = f"{ref} = {value!r} is not a finite number"
            return None, [Issue(IssueCode.NON_FINITE_VALUE, global_id, message)]

    typed = TypedValue(
        set_name=set_name,
        name=name,
        # value_type is present exactly when a value is (a CHECK constraint).
        value_type=raw.value_type if value is not None else None,
        text=text,
        number=number,
        boolean=boolean,
        unit=unit_symbol,
        is_type_property=raw.from_type or set_name.startswith(REVIT_TYPE_SET_PREFIX),
        from_type=raw.from_type,
        from_quantity_set=raw.from_quantity_set,
        order=raw.order,
        from_attribute=raw.from_attribute,
    )
    return typed, issues


def _derived_si(
    number: float, dimension: Dimension | None, raw: RawValue, units: Mapping[str, RawUnit]
) -> tuple[float, str] | None:
    """A derived measure in SI with its unit, when its own or the project's unit says how.

    ``None`` when the file declares no unit for it, or one without a known SI factor:
    the value is then stored as written and reported (audit finding A13).
    """
    if dimension is None:
        return None
    own = raw.unit if raw.unit and raw.unit.unit_type == dimension.unit_type else None
    unit = own or units.get(dimension.unit_type)
    converted = to_si(number, unit) if unit is not None else None
    return None if converted is None else (converted, dimension.si_symbol)


def _has_physical_unit(value_type: str | None) -> bool:
    """True for measure types such as IfcPowerMeasure, whose numbers depend on a unit."""
    return (
        value_type is not None
        and value_type.endswith("Measure")
        and value_type not in DIMENSIONLESS_MEASURES
    )


def resolve_properties(
    global_id: str, raw_values: Iterable[RawValue], units: Mapping[str, RawUnit]
) -> tuple[list[TypedValue], list[Issue]]:
    """Type every value of one object and keep one value per (set, property).

    Returns:
        The kept values sorted by set and property name, and the issues found.
    """
    issues: list[Issue] = []
    candidates: dict[tuple[str, str], list[TypedValue]] = {}
    for raw in raw_values:
        typed, value_issues = type_value(global_id, raw, units)
        issues.extend(value_issues)
        if typed is not None:
            candidates.setdefault((typed.set_name, typed.name), []).append(typed)

    # Occurrence values (from_type False) sort before type values, then file order.
    ranked = {
        key: sorted(values, key=lambda value: (value.from_type, value.order))
        for key, values in candidates.items()
    }
    singles = _single_quantities(ranked)

    kept: list[TypedValue] = []
    for key in sorted(ranked):
        winner, reason = ranked[key][0], ""
        rivals = _rivals(ranked[key])
        if rivals and winner.from_quantity_set:
            siblings = singles.get(key[0], {})
            chosen = plausibility.choose([winner, *rivals], siblings)
            if chosen is not None:
                winner, reason = chosen, f" ({plausibility.why_chosen(chosen, siblings)})"
        kept.append(winner)
        for loser in ranked[key]:
            if (
                loser is not winner
                and loser.from_type == winner.from_type
                and loser.payload != winner.payload
            ):
                message = (
                    f"{winner.ref} has several values; kept {_shown(winner)}{reason}, "
                    f"dropped {_shown(loser)}"
                )
                issues.append(Issue(IssueCode.CONFLICTING_PROPERTY, global_id, message))
    checked, check_issues = _check_quantity_sets(global_id, kept)
    return checked, issues + check_issues


def _rivals(ranked: list[TypedValue]) -> list[TypedValue]:
    """The values that compete with the first: same level, a different value."""
    first = ranked[0]
    return [v for v in ranked[1:] if v.from_type == first.from_type and v.payload != first.payload]


def _single_quantities(
    ranked: Mapping[tuple[str, str], list[TypedValue]],
) -> dict[str, dict[str, float]]:
    """Per quantity set, the numbers of its quantities that have exactly one value."""
    singles: dict[str, dict[str, float]] = {}
    for (set_name, name), values in ranked.items():
        first = values[0]
        if first.from_quantity_set and first.number is not None and not _rivals(values):
            singles.setdefault(set_name, {})[name] = first.number
    return singles


def _check_quantity_sets(
    global_id: str, values: list[TypedValue]
) -> tuple[list[TypedValue], list[Issue]]:
    """Flag the quantity-set values that break a physical bound of their set."""
    by_set: dict[str, list[TypedValue]] = {}
    for value in values:
        if value.from_quantity_set:
            by_set.setdefault(value.set_name, []).append(value)
    checked: dict[tuple[str, str], TypedValue] = {}
    issues: list[Issue] = []
    for set_values in by_set.values():
        flagged, set_issues = plausibility.check_set(global_id, set_values)
        checked.update({(v.set_name, v.name): v for v in flagged})
        issues.extend(set_issues)
    return [checked.get((v.set_name, v.name), v) for v in values], issues


def _shown(value: TypedValue) -> str:
    """Render a value for a message, e.g. ``'N/A'`` or ``2.5 m``."""
    if value.text is not None:
        return repr(value.text)
    if value.number is not None:
        return f"{value.number!r} {value.unit}" if value.unit else repr(value.number)
    if value.boolean is not None:
        return repr(value.boolean)
    return "no value"
