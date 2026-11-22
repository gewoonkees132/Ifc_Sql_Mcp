"""Ground truth for room totals: Revit Areas apart, and each square metre of floor once.

Usage: python -I space_truth.py <file.ifc> [<file.ifc> ...]

Reads each file with IfcOpenShell and shapely only (never ``ifc_sql_mcp``) and prints one
JSON line per file:

- ``revit_areas`` / ``revit_areas_m2``: spaces whose ``Category`` (in ``Pset_SpaceCommon``
  or Revit's ``Other`` set) is ``Areas``. Revit writes its area plans (an area scheme such
  as gross building area) as IfcSpace too; they are not rooms.
- ``rooms`` / ``rooms_sum_m2``: every other space, and the sum of their areas. A space's
  area is its IFC quantity ``NetFloorArea`` (``Qto_SpaceBaseQuantities``, or IFC2X3's
  ``BaseQuantities``), else Revit's ``Dimensions.Area``, else ``GSA Space Areas``.
- ``rooms_floor_covered_m2``: the union of the rooms' footprints, storey by storey: the
  floor the rooms cover, each square metre once.
- ``rooms_repeating_covered_floor`` / ``rooms_each_once_m2``: rooms are taken largest
  footprint first; a room whose footprint lies at least 90 % inside the rooms taken before
  it on its storey repeats them (the same room exported twice, as a Revit Room and an MEP
  Space). The sum of the other rooms' areas is the total with each room once.
"""

# ruff: noqa: T201

import json
import sys
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import numpy as np
import shapely
from shapely.ops import unary_union

INSIDE = 0.9
"""Share of a footprint inside earlier rooms from which a room repeats them."""


def footprints(model: Any) -> dict[int, Any]:
    """Each space's footprint: its triangulated body projected on the plan, in metres."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    shapes: dict[int, Any] = {}
    iterator = ifcopenshell.geom.iterator(settings, model, include=model.by_type("IfcSpace"))
    if iterator.initialize():
        while True:
            shape = iterator.get()
            verts = np.array(shape.geometry.verts).reshape(-1, 3)
            faces = np.array(shape.geometry.faces).reshape(-1, 3)
            triangles = (shapely.Polygon(verts[face, :2]) for face in faces)
            shapes[shape.id] = unary_union([t for t in triangles if t.area > 1e-9]).buffer(0)
            if not iterator.next():
                break
    return shapes


def describe(space: Any) -> dict[str, Any]:
    """A space's number, name, storey, area and Revit category, as the file states them."""
    psets = ifcopenshell.util.element.get_psets(space)
    quantities = psets.get("Qto_SpaceBaseQuantities") or psets.get("BaseQuantities") or {}
    area = (
        quantities.get("NetFloorArea")
        or (psets.get("Dimensions") or {}).get("Area")
        or (psets.get("GSA Space Areas") or {}).get("GSA BIM Area")
    )
    category = (psets.get("Pset_SpaceCommon") or {}).get("Category") or (
        psets.get("Other") or {}
    ).get("Category")
    storey = space.Decomposes[0].RelatingObject.Name if space.Decomposes else None
    return {
        "id": space.id(),
        "number": space.Name,
        "name": space.LongName,
        "storey": storey,
        "area": area or 0.0,
        "category": category,
    }


def truth(path: str) -> dict[str, Any]:
    """The figures of one file."""
    model = ifcopenshell.open(path)
    shapes = footprints(model)
    spaces = [describe(space) for space in model.by_type("IfcSpace")]
    areas = [s for s in spaces if s["category"] == "Areas"]
    rooms = [s for s in spaces if s["category"] != "Areas"]
    covered = each_once = 0.0
    repeats: list[str] = []
    for storey in sorted({str(r["storey"]) for r in rooms}):
        on_storey = [r for r in rooms if str(r["storey"]) == storey and r["id"] in shapes]
        on_storey.sort(key=lambda r: (-shapes[r["id"]].area, r["id"]))
        covered += unary_union([shapes[r["id"]] for r in on_storey]).area
        taken: list[Any] = []
        for room in on_storey:
            shape = shapes[room["id"]]
            inside = shape.intersection(unary_union(taken)).area / shape.area if taken else 0.0
            if inside >= INSIDE:
                repeats.append(f"{room['number']} {room['name']}")
            else:
                taken.append(shape)
                each_once += room["area"]
    return {
        "file": path.rsplit("/", 1)[-1],
        "spaces": len(spaces),
        "footprints_measured": len(shapes),
        "revit_areas": len(areas),
        "revit_areas_m2": round(sum(a["area"] for a in areas), 1),
        "rooms": len(rooms),
        "rooms_sum_m2": round(sum(r["area"] for r in rooms), 1),
        "rooms_floor_covered_m2": round(covered, 1),
        "rooms_repeating_covered_floor": len(repeats),
        "rooms_each_once_m2": round(each_once, 1),
        "repeating_examples": repeats[:8],
    }


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        print(json.dumps(truth(argument), ensure_ascii=False))
