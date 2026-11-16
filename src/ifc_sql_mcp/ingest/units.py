"""Unit conversion to SI, unit labels and storey elevation rounding.

The four Duplex files declare metres, millimetres and feet, so a value means nothing
until its unit is known. Each IFC measure type names its dimension (an
``IfcAreaMeasure`` is an area), and the file declares one project unit per dimension
(``AREAUNIT``) unless the property states its own. Converting with that unit makes the
same room 145.72 m² whether the file says ``145.72``, ``1568.5`` (ft²) or
``145721689`` (mm²).

Only four dimensions are converted: length, area, volume and plane angle. They cover
every measure type in the sample files and every ``*_m`` column. Plane angles become
radians, the SI unit, so that the rule "a number with a unit is in SI" has no
exception; degrees are ``value * 180 / pi`` away. Other measures with a physical unit
are stored as written, without a unit, and reported (see ``properties.py``).
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.raw import RawUnit


@dataclass(frozen=True, slots=True)
class Dimension:
    """A physical dimension this pipeline converts.

    Attributes:
        unit_type: The IfcUnitEnum value a project unit for it carries.
        si_symbol: Symbol of the SI unit converted values are in.
        si_name: Name of that SI unit in IFC (used when the file declares none).
    """

    unit_type: str
    si_symbol: str
    si_name: str


LENGTH = Dimension("LENGTHUNIT", "m", "METRE")
AREA = Dimension("AREAUNIT", "m2", "SQUARE_METRE")
VOLUME = Dimension("VOLUMEUNIT", "m3", "CUBIC_METRE")
PLANE_ANGLE = Dimension("PLANEANGLEUNIT", "rad", "RADIAN")
CONVERTED_DIMENSIONS = (LENGTH, AREA, VOLUME, PLANE_ANGLE)

MEASURE_DIMENSIONS: Mapping[str, Dimension] = {
    "IfcLengthMeasure": LENGTH,
    "IfcPositiveLengthMeasure": LENGTH,
    "IfcNonNegativeLengthMeasure": LENGTH,  # IFC4
    "IfcAreaMeasure": AREA,
    "IfcVolumeMeasure": VOLUME,
    "IfcPlaneAngleMeasure": PLANE_ANGLE,
    "IfcPositivePlaneAngleMeasure": PLANE_ANGLE,
}
"""IFC measure types whose values are converted, and the dimension of each."""

DIMENSIONLESS_MEASURES = frozenset(
    {
        "IfcCountMeasure",
        "IfcNumericMeasure",
        "IfcRatioMeasure",
        "IfcPositiveRatioMeasure",
        "IfcNormalisedRatioMeasure",
    }
)
"""Measure types that are pure numbers, so storing them without a unit loses nothing."""

_SI_PREFIX_SYMBOLS: Mapping[str, str] = {
    "EXA": "E",
    "PETA": "P",
    "TERA": "T",
    "GIGA": "G",
    "MEGA": "M",
    "KILO": "k",
    "HECTO": "h",
    "DECA": "da",
    "DECI": "d",
    "CENTI": "c",
    "MILLI": "m",
    "MICRO": "u",
    "NANO": "n",
    "PICO": "p",
    "FEMTO": "f",
    "ATTO": "a",
}
_SI_UNIT_SYMBOLS: Mapping[str, str] = {
    "METRE": "m",
    "SQUARE_METRE": "m2",
    "CUBIC_METRE": "m3",
    "RADIAN": "rad",
}
# Conversion-based units are free text in IFC; these are the spellings Revit and
# IfcOpenShell write for the common imperial units.
_CONVERSION_UNIT_SYMBOLS: Mapping[str, str] = {
    "INCH": "in",
    "FOOT": "ft",
    "YARD": "yd",
    "MILE": "mi",
    "SQUARE INCH": "in2",
    "SQUARE FOOT": "ft2",
    "SQUARE YARD": "yd2",
    "CUBIC INCH": "in3",
    "CUBIC FOOT": "ft3",
    "CUBIC YARD": "yd3",
    "DEGREE": "deg",
}

# spatial_element.elevation_m is numeric(9,3): at most 6 digits before the point.
_ELEVATION_LIMIT = Decimal("999999.999")
_MILLIMETRE = Decimal("0.001")


def unit_label(unit: RawUnit) -> str:
    """Return a short, readable label for a unit: ``m``, ``mm``, ``ft``, ``deg``.

    Used for ``model.length_unit``, which records what the file declared. Unknown
    conversion-based units fall back to their own name, which IFC requires to be set.
    """
    name = unit.name.strip().upper().replace("METER", "METRE")
    if unit.is_si:
        prefix = _SI_PREFIX_SYMBOLS.get(unit.prefix or "", "")
        return prefix + _SI_UNIT_SYMBOLS.get(name, unit.name.strip().lower())
    return _CONVERSION_UNIT_SYMBOLS.get(name, unit.name.strip() or unit.unit_type)


def project_units(declared: Iterable[RawUnit]) -> tuple[dict[str, RawUnit], list[Issue]]:
    """Pick the project unit for each converted dimension.

    A file should declare one unit per type; if it declares several, the first counts.
    If it declares none for a converted dimension, IFC's default (the SI unit) is
    assumed and reported, so a missing declaration never silently changes values.

    Returns:
        The unit per IfcUnitEnum value, and the issues for dimensions without a unit.
    """
    units: dict[str, RawUnit] = {}
    for unit in declared:
        units.setdefault(unit.unit_type, unit)

    issues: list[Issue] = []
    for dimension in CONVERTED_DIMENSIONS:
        if dimension.unit_type not in units:
            units[dimension.unit_type] = RawUnit(
                unit_type=dimension.unit_type,
                name=dimension.si_name,
                prefix=None,
                is_si=True,
                scale_to_si=1.0,
            )
            issues.append(
                Issue(
                    IssueCode.UNIT_ASSUMED_SI,
                    None,
                    f"the file declares no {dimension.unit_type}; assuming {dimension.si_symbol}",
                )
            )
    return units, issues


def to_si(value: float, unit: RawUnit) -> float | None:
    """Convert ``value`` from ``unit`` to SI; ``None`` if the unit has no known factor."""
    if unit.scale_to_si is None:
        return None
    return value * unit.scale_to_si


def round_elevation(metres: float) -> Decimal | None:
    """Round a storey elevation in metres to the millimetre for ``numeric(9,3)``.

    Revit writes elevations with float noise (``3.100000000000378``). Rounding here, to
    a ``Decimal``, rather than leaving it to the column means the records already hold
    the stored value, so tests and the loader see the same number. Ties round away from
    zero, as PostgreSQL rounds numerics.

    Returns:
        The rounded elevation, or ``None`` if it is not finite or does not fit the column.
    """
    if not math.isfinite(metres):
        return None
    rounded = Decimal(metres).quantize(_MILLIMETRE, rounding=ROUND_HALF_UP)
    if abs(rounded) > _ELEVATION_LIMIT:
        return None
    # Normalise -0.000 to 0.000: both are stored as 0, so the record should say so too.
    return rounded + 0
