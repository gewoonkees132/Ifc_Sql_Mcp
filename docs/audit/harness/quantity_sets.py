"""Which quantity sets a file carries, class by class, read with IfcOpenShell only.

Usage: python -I quantity_sets.py <file.ifc>

Prints, for every IfcElement class, how many elements it has and how many values of each
``set.quantity`` they carry (IfcElementQuantity only, the eight most common), and for every
quantity of an ``IfcCovering`` the sum of its values in the file's own unit.
"""

# ruff: noqa: T201

import collections
import sys

import ifcopenshell


def main(path: str) -> None:
    """Print the table for one file."""
    model = ifcopenshell.open(path)
    elements: collections.Counter[str] = collections.Counter()
    quantities: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    covering_totals: collections.Counter[str] = collections.Counter()
    for element in model.by_type("IfcElement"):
        elements[element.is_a()] += 1
        for relation in element.IsDefinedBy or ():
            if not relation.is_a("IfcRelDefinesByProperties"):
                continue
            definition = relation.RelatingPropertyDefinition
            if not definition.is_a("IfcElementQuantity"):
                continue
            for quantity in definition.Quantities:
                key = f"{definition.Name}.{quantity.Name}"
                quantities[element.is_a()][key] += 1
                if element.is_a("IfcCovering") and quantity.is_a("IfcPhysicalSimpleQuantity"):
                    covering_totals[key] += quantity[3]
    for name in sorted(elements):
        found = dict(quantities[name].most_common(8)) if quantities[name] else "-"
        print(f"{name:30} {elements[name]:6}  {found}")
    for key, total in sorted(covering_totals.items()):
        print(f"IfcCovering total {key}: {total:.1f}")


if __name__ == "__main__":
    main(sys.argv[1])
