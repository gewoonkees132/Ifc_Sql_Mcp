"""The door schedule of one storey, read with IfcOpenShell only: type, count, sizes.

Usage: python -I door_schedule.py <file.ifc> <storey name>

Never imports ``ifc_sql_mcp``. It groups the storey's IfcDoor occurrences by the first two
parts of their name (Revit's family and type) and counts each type's OverallWidth and
OverallHeight, converted to metres and rounded to 0.1 mm. On file B and level
``XX_AR_L01`` it prints the ground truth of audit finding A11: 159 doors in 6 types.
"""

# ruff: noqa: T201

import sys
from collections import Counter, defaultdict

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.unit


def main() -> None:
    """Print the doors per type, with their widths and heights and how many have each."""
    model = ifcopenshell.open(sys.argv[1])
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for door in model.by_type("IfcDoor"):
        storey = ifcopenshell.util.element.get_container(door)
        if storey is None or storey.Name != sys.argv[2]:
            continue
        family_type = ":".join((door.Name or "").split(":")[:2])
        size = (round(door.OverallWidth * scale, 4), round(door.OverallHeight * scale, 4))
        groups[family_type].append(size)
    print(sum(len(sizes) for sizes in groups.values()), "doors in", len(groups), "types")
    for family_type, sizes in sorted(groups.items(), key=lambda item: -len(item[1])):
        widths = dict(Counter(width for width, _ in sizes))
        heights = dict(Counter(height for _, height in sizes))
        print(len(sizes), repr(family_type), widths, heights)


if __name__ == "__main__":
    main()
