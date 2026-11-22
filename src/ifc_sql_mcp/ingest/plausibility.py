"""Physical bounds on the values of one IFC quantity set (audit finding A2).

An ``IfcElementQuantity`` states several measures of one object, and geometry ties them
together: a wall's side area cannot exceed its length times its height, a net area cannot
exceed the gross one, no shape has a perimeter below 2√(π·area). Revit's exporters break
these ties in ways the audit measured on its files:

* Revit 2021 writes a wall's ``NetSideArea`` and ``NetVolume`` twice. The first is the
  gross face in mm² and the volume in mm³, both labelled in m²/m³: 10⁶ and 10⁹ times the
  bound. Kept in file order, they gave 1,157,891,673.6 m² of wall for a 946 m² house.
* Revit 2021 writes a slab's ``NetArea`` as the area of the whole solid (top, bottom and
  edges, so twice its plan area) and its ``GrossVolume`` as area (m²) x width (mm); on a
  sloped roof slab its ``GrossArea`` is a tenth of the roof.
* Revit 2019 and 2021 write a space's ``GrossVolume`` as floor area (m²) x height (mm),
  1,000 times too large, and Revit 2021 its ``GrossPerimeter`` in feet, labelled with the
  file's millimetres.

Two uses, both before any value becomes a quantity:

* :func:`choose` settles a property written twice with two values: the one value that
  keeps every bound wins, whatever the file order. It returns ``None`` when the bounds do
  not single one out, and the caller keeps file order.
* :func:`check_set` flags each value of a resolved set that breaks a bound. A flagged
  value stays in ``ifc.property`` as written, with ``is_implausible``, and becomes no
  canonical quantity; an ``implausible_quantity`` issue says which bound it broke.

Bounds that involve modelling (a face against length x height, a volume against area x
thickness) allow a factor of :data:`FACTOR`: the correct values in the audit's files stay
within 1.1 of them, the wrong ones are 10³ to 10⁹ away. A net quantity against its gross
allows :data:`NET_TOLERANCE`, since net is gross minus openings by definition. Finer
inconsistencies are data quality rules, not reasons to drop a value.
"""

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from ifc_sql_mcp.ingest.issues import Issue, IssueCode

if TYPE_CHECKING:
    # properties.py calls this module, so its type is imported for annotations only.
    from ifc_sql_mcp.ingest.properties import TypedValue

FACTOR = 2.0
"""How far a measure may pass a geometric bound before it is implausible."""
NET_TOLERANCE = 1.05
"""How far a net measure may pass its gross one (rounding, not geometry)."""

type Siblings = Mapping[str, float]
"""The numeric values of one quantity set, by quantity name, in SI units."""


@dataclass(frozen=True, slots=True)
class Bound:
    """One physical bound on some quantities of a set, computed from the others.

    Attributes:
        checked: Names of the quantities the bound limits.
        side: ``at_most`` for an upper bound, ``at_least`` for a lower one.
        allowance: Factor by which a value may pass the bound and still be plausible.
        formula: The bound in words, for messages, e.g. ``Length x Height``.
        limit: Computes the bound from the set's other values; ``None`` when they do not
            give it (a wall set without a Width says nothing about its volume).
    """

    checked: frozenset[str]
    side: Literal["at_most", "at_least"]
    allowance: float
    formula: str
    limit: Callable[[Siblings], float | None]

    def describe(self, limit: float, unit: str | None) -> str:
        """The bound in words with its value, e.g. ``at most 2 x Length x Height = 83 m2``."""
        if self.side == "at_most":
            return (
                f"at most {self.allowance:g} x {self.formula} = {self.allowance * limit:.6g} {unit}"
            )
        return (
            f"at least 1/{self.allowance:g} of {self.formula} = {limit / self.allowance:.6g} {unit}"
        )

    def violated_by(self, value: float, siblings: Siblings) -> float | None:
        """Return the bound if ``value`` breaks it, else ``None`` (also when unknown)."""
        limit = self.limit(siblings)
        if limit is None or limit <= 0:
            return None
        if self.side == "at_most" and value > limit * self.allowance:
            return limit
        if self.side == "at_least" and value < limit / self.allowance:
            return limit
        return None


def _product(*names: str | tuple[str, ...]) -> Callable[[Siblings], float | None]:
    """The product of named siblings; a tuple names alternatives, the first present wins."""

    def limit(siblings: Siblings) -> float | None:
        result = 1.0
        for name in names:
            options = (name,) if isinstance(name, str) else name
            value = next((siblings[o] for o in options if o in siblings), None)
            if value is None:
                return None
            result *= value
        return result

    return limit


def _quotient(numerator: str, denominator: str) -> Callable[[Siblings], float | None]:
    """One sibling divided by another, if both are present and the divisor is not 0."""

    def limit(siblings: Siblings) -> float | None:
        if numerator not in siblings or not siblings.get(denominator):
            return None
        return siblings[numerator] / siblings[denominator]

    return limit


def _shortest_perimeter(siblings: Siblings) -> float | None:
    """The perimeter of a circle of the set's plan area: no shape of that area has less."""
    area = next(
        (siblings[n] for n in ("GrossFloorArea", "NetFloorArea", "GrossArea") if n in siblings),
        None,
    )
    return 2 * math.sqrt(math.pi * area) if area else None


VOLUMES = frozenset({"NetVolume", "GrossVolume"})

BOUNDS: tuple[Bound, ...] = (
    # A net measure is the gross one minus openings. Checked first: a net value flagged
    # here is no longer used to bound anything else.
    Bound(frozenset({"NetArea"}), "at_most", NET_TOLERANCE, "GrossArea", _product("GrossArea")),
    Bound(
        frozenset({"NetSideArea"}),
        "at_most",
        NET_TOLERANCE,
        "GrossSideArea",
        _product("GrossSideArea"),
    ),
    Bound(
        frozenset({"NetFloorArea"}),
        "at_most",
        NET_TOLERANCE,
        "GrossFloorArea",
        _product("GrossFloorArea"),
    ),
    # Walls: one face, and the solid, against length x height (x width, the thickness).
    Bound(
        frozenset({"NetSideArea", "GrossSideArea"}),
        "at_most",
        FACTOR,
        "Length x Height",
        _product("Length", "Height"),
    ),
    Bound(
        VOLUMES, "at_most", FACTOR, "Length x Height x Width", _product("Length", "Height", "Width")
    ),
    # Slabs: Revit computes GrossVolume as GrossArea x Width, so it is bounded by them;
    # NetVolume is not, because GrossArea is itself wrong on some roofs (below).
    Bound(
        frozenset({"GrossVolume"}),
        "at_most",
        FACTOR,
        "GrossArea x Width",
        _product("GrossArea", "Width"),
    ),
    # ...and a slab's plan area cannot be far below its volume over its thickness. Revit
    # 2021 gives a sloped roof slab a GrossArea of 6.6 m2 for 23.9 m3 at 0.338 m.
    Bound(
        frozenset({"GrossArea"}),
        "at_least",
        FACTOR,
        "NetVolume / Width",
        _quotient("NetVolume", "Width"),
    ),
    # Spaces: the volume against floor area x height.
    Bound(
        VOLUMES,
        "at_most",
        FACTOR,
        "floor area x Height",
        _product(("GrossFloorArea", "NetFloorArea"), "Height"),
    ),
    # Any plan: no shape of area A has a perimeter below 2√(πA), a circle's.
    Bound(
        frozenset({"GrossPerimeter", "NetPerimeter", "Perimeter"}),
        "at_least",
        FACTOR,
        "2√(π x plan area)",
        _shortest_perimeter,
    ),
)


def _siblings(values: Iterable["TypedValue"]) -> dict[str, float]:
    return {value.name: value.number for value in values if value.number is not None}


def _broken(value: "TypedValue", siblings: Siblings) -> tuple[Bound, float] | None:
    """The first bound ``value`` breaks, with the bound's value; ``None`` if it keeps all."""
    if value.number is None:
        return None
    others = {name: number for name, number in siblings.items() if name != value.name}
    for bound in BOUNDS:
        if value.name in bound.checked:
            limit = bound.violated_by(value.number, others)
            if limit is not None:
                return bound, limit
    return None


def choose(candidates: Sequence["TypedValue"], siblings: Siblings) -> "TypedValue | None":
    """Pick the one candidate that keeps every bound, or ``None`` if not exactly one does.

    Args:
        candidates: Values written for one quantity of one set, at the same level.
        siblings: The set's other quantities that have a single value.
    """
    kept = [candidate for candidate in candidates if _broken(candidate, siblings) is None]
    return kept[0] if len(kept) == 1 and len(kept) < len(candidates) else None


def why_chosen(value: "TypedValue", siblings: Siblings) -> str:
    """Say which bounds the chosen value keeps, for the conflicting_property message."""
    formulas = [
        bound.formula
        for bound in BOUNDS
        if value.name in bound.checked and bound.limit(siblings) is not None
    ]
    return f"the only value within {' and '.join(formulas)}"


def check_set(
    global_id: str, values: Sequence["TypedValue"]
) -> tuple[list["TypedValue"], list[Issue]]:
    """Flag the values of one quantity set that break a bound.

    Values are checked in :data:`BOUNDS` order, and a flagged value is no longer used to
    bound the others.

    Returns:
        The values, flagged ones replaced by a copy with ``implausible`` set, and one
        ``implausible_quantity`` issue per flagged value.
    """
    siblings = _siblings(values)
    flagged: dict[str, tuple[Bound, float]] = {}
    for bound in BOUNDS:
        for value in values:
            if value.name not in bound.checked or value.name in flagged or value.number is None:
                continue
            others = {n: v for n, v in siblings.items() if n != value.name and n not in flagged}
            limit = bound.violated_by(value.number, others)
            if limit is not None:
                flagged[value.name] = (bound, limit)
    issues = []
    result = []
    for value in values:
        if value.name not in flagged:
            result.append(value)
            continue
        bound, limit = flagged[value.name]
        message = (
            f"{value.ref} = {value.number:.6g} {value.unit} breaks the bound "
            f"{bound.describe(limit, value.unit)}: kept in ifc.property as written "
            "(is_implausible), not used as a quantity"
        )
        issues.append(Issue(IssueCode.IMPLAUSIBLE_QUANTITY, global_id, message))
        result.append(replace(value, implausible=True))
    return result, issues
