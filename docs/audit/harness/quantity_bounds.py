"""Measure every quantity set of an IFC file against the physical bounds of ADR 7.

Usage: python -I quantity_bounds.py <file.ifc>

Reads the file with IfcOpenShell only (never ``ifc_sql_mcp``), converts each quantity to
SI with the project's units, and prints, per set and quantity, the ratio of the value to
its bound: side area / (Length x Height), volume / (Length x Height x Width), volume /
(floor area x Height), volume / (GrossArea x Width), perimeter / 2 sqrt(pi x area). The
distributions this printed for files A, C and E set the allowances in
``ingest/plausibility.py``: correct values stay within 1.1 of their bound, the defective
ones are 10^3 to 10^9 away.
"""

# ruff: noqa: T201

import math
import sys
from collections import defaultdict
from typing import Any

import ifcopenshell
import ifcopenshell.util.unit


def scales(model: Any) -> dict[str, float]:
    """The factor to SI of each kind of quantity, from the project's units."""
    result = {"IfcQuantityLength": ifcopenshell.util.unit.calculate_unit_scale(model)}
    for kind, unit_type in (("IfcQuantityArea", "AREAUNIT"), ("IfcQuantityVolume", "VOLUMEUNIT")):
        unit = ifcopenshell.util.unit.get_project_unit(model, unit_type)
        result[kind] = ifcopenshell.util.unit.get_unit_scale(unit) if unit else 1.0
    return result


def ratios(model: Any) -> dict[tuple[str, str], list[float]]:
    """Value / bound for every quantity that has a bound in its set."""
    factor = scales(model)
    found: dict[tuple[str, str], list[float]] = defaultdict(list)
    for quantity_set in model.by_type("IfcElementQuantity"):
        values: dict[str, list[float]] = defaultdict(list)
        for quantity in quantity_set.Quantities:
            number = quantity[3]
            if quantity.is_a() in factor and isinstance(number, int | float):
                values[quantity.Name].append(number * factor[quantity.is_a()])
        single = {name: numbers[0] for name, numbers in values.items() if len(numbers) == 1}

        def ratio(
            label: str,
            value: float,
            bound: float | None,
            name: str,
            set_name: str = quantity_set.Name,
        ) -> None:
            if bound:
                found[(set_name, f"{name}{label}")].append(value / bound)

        length, height, width = (single.get(n) for n in ("Length", "Height", "Width"))
        floor = single.get("GrossFloorArea") or single.get("NetFloorArea")
        plan = floor or single.get("GrossArea")
        for name in ("NetSideArea", "GrossSideArea"):
            for value in values.get(name, []):
                ratio("", value, length * height if length and height else None, name)
        for name in ("NetVolume", "GrossVolume"):
            for value in values.get(name, []):
                ratio(
                    "/LHW",
                    value,
                    length * height * width if length and height and width else None,
                    name,
                )
                ratio("/AH", value, floor * height if floor and height else None, name)
                gross = single.get("GrossArea")
                ratio("/AW", value, gross * width if gross and width else None, name)
        for name in ("GrossPerimeter", "NetPerimeter", "Perimeter"):
            for value in values.get(name, []):
                ratio("/circle", value, 2 * math.sqrt(math.pi * plan) if plan else None, name)
    return found


def main() -> None:
    """Print the distribution of each ratio, and how many pass the allowances."""
    for (set_name, label), values in sorted(ratios(ifcopenshell.open(sys.argv[1])).items()):
        values.sort()
        print(
            f"{set_name:26} {label:22} n={len(values):5} min={values[0]:.3g} "
            f"median={values[len(values) // 2]:.3g} max={values[-1]:.3g} "
            f">2: {sum(v > 2 for v in values)}  >1.05: {sum(v > 1.05 for v in values)}  "
            f"<0.5: {sum(v < 0.5 for v in values)}"
        )


if __name__ == "__main__":
    main()
