"""Ground truth for a door schedule: one storey's doors by type, overall size and fire rating.

Usage: python -I doors_on_level.py <file.ifc> <storey name>
"""

# ruff: noqa: T201

import sys
from collections import Counter

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uu


def main() -> None:
    """Print the door schedule of one storey, straight from the IFC file."""
    f = ifcopenshell.open(sys.argv[1])
    level = sys.argv[2]
    scale = uu.calculate_unit_scale(f)
    rows: Counter[tuple[str, float, float, object]] = Counter()
    for door in f.by_type("IfcDoor"):
        container = ue.get_container(door)
        if container is None or container.Name != level:
            continue
        door_type = ue.get_type(door)
        fire = ue.get_psets(door).get("Pset_DoorCommon", {}).get("FireRating")
        name = door_type.Name if door_type else door.Name.rsplit(":", 1)[0]
        width = round(door.OverallWidth * scale, 3)
        height = round(door.OverallHeight * scale, 3)
        rows[(name, width, height, fire)] += 1
    print("total", sum(rows.values()))
    for key, count in rows.most_common():
        print(count, key)


if __name__ == "__main__":
    main()
