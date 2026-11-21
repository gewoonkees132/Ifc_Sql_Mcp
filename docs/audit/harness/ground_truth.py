"""Independent ground truth for the audit, read straight from an IFC file with IfcOpenShell.

Usage: python -I ground_truth.py <file.ifc> <out.json> [--geometry]

Nothing here imports ifc_sql_mcp: the point is to have answers the tool's output can be
checked against. ``--geometry`` also triangulates walls, slabs, doors and windows and
measures walls (one side face) and slabs (footprint), timing it, to show what a geometry
quantity source would deliver on files that carry no quantities.
"""

# ruff: noqa: T201

import json
import multiprocessing
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.shape as us
import ifcopenshell.util.unit as uu

# Of several values for one quantity, the plausible one fits within Length x Height.
PLAUSIBLE_SIDE_AREA_MARGIN = 1.05


def pset_value(entity: Any, pset: str, prop: str) -> Any:
    """Return one property value, or None."""
    return ue.get_psets(entity).get(pset, {}).get(prop)


def storey_of(entity: Any) -> Any:
    """The storey that contains an element, following aggregates and voids to the host."""
    container = ue.get_container(entity)
    hops = 0
    while container is not None and not container.is_a("IfcBuildingStorey") and hops < 10:
        container = ue.get_aggregate(container)
        hops += 1
    if container is None:
        host = ue.get_aggregate(entity) or ue.get_voided_element(entity)
        if host is not None and host is not entity:
            return storey_of(host)
    return container


def doors_and_windows(f: Any, scale: float) -> dict[str, Any]:
    """Count, overall sizes, fire rating, IsExternal and storeys of doors and windows."""
    result: dict[str, Any] = {}
    for cls, pset in (("IfcDoor", "Pset_DoorCommon"), ("IfcWindow", "Pset_WindowCommon")):
        items = f.by_type(cls)
        sizes: Counter[str] = Counter()
        fire: Counter[str] = Counter()
        external: Counter[str] = Counter()
        per_level: Counter[str | None] = Counter()
        with_size = 0
        for item in items:
            width, height = item.OverallWidth, item.OverallHeight
            if width and height:
                with_size += 1
                sizes[f"{round(width * scale, 3)} x {round(height * scale, 3)}"] += 1
            fire[str(pset_value(item, pset, "FireRating"))] += 1
            external[str(pset_value(item, pset, "IsExternal"))] += 1
            storey = storey_of(item)
            per_level[storey.Name if storey else None] += 1
        result[cls] = {
            "count": len(items),
            "with_overall_width_and_height": with_size,
            "top_sizes_m": sizes.most_common(8),
            "fire_rating_values": fire.most_common(8),
            "is_external_values": external.most_common(),
            "per_storey_top": per_level.most_common(6),
        }
    return result


def walls(f: Any) -> dict[str, Any]:
    """Wall flags from Pset_WallCommon and the kind of material definition."""
    items = f.by_type("IfcWall")
    materials: Counter[str | None] = Counter()
    for wall in items:
        material = ue.get_material(wall, should_skip_usage=True)
        materials[material.is_a() if material else None] += 1

    def values(prop: str) -> list[tuple[str, int]]:
        return Counter(str(pset_value(w, "Pset_WallCommon", prop)) for w in items).most_common(8)

    return {
        "count": len(items),
        "is_external_values": values("IsExternal"),
        "fire_rating_values": values("FireRating"),
        "load_bearing_values": values("LoadBearing"),
        "material_kinds": materials.most_common(),
    }


def quantity_sets(f: Any, scale: float) -> dict[str, Any]:
    """Totals from IFC quantity sets, with Revit 2021's duplicated values resolved."""
    space_totals: defaultdict[str, float] = defaultdict(float)
    duplicates = 0
    by_wall_type: defaultdict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for rel in f.by_type("IfcRelDefinesByProperties"):
        definition = rel.RelatingPropertyDefinition
        if not definition.is_a("IfcElementQuantity"):
            continue
        names = Counter(q.Name for q in definition.Quantities)
        duplicates += sum(n - 1 for n in names.values() if n > 1)
        values: defaultdict[str, list[float]] = defaultdict(list)
        for quantity in definition.Quantities:
            if quantity.is_a("IfcPhysicalSimpleQuantity"):
                values[quantity.Name].append(quantity[3])
        for obj in rel.RelatedObjects:
            if definition.Name == "Qto_WallBaseQuantities" and obj.is_a("IfcWall"):
                length = (values.get("Length") or [0])[0] * scale
                height = (values.get("Height") or [0])[0] * scale
                limit = length * height * PLAUSIBLE_SIDE_AREA_MARGIN + 1e-9
                plausible = [v for v in values.get("NetSideArea", []) if v <= limit]
                if plausible:
                    element_type = ue.get_type(obj)
                    key = element_type.Name if element_type else obj.Name
                    by_wall_type[key][0] += 1
                    by_wall_type[key][1] += max(plausible)
            if definition.Name == "Qto_SpaceBaseQuantities" and obj.is_a("IfcSpace"):
                for name in ("NetFloorArea", "GrossFloorArea"):
                    if values.get(name):
                        space_totals[f"space.{name}_m2"] += values[name][0]
    rows = [[k, int(n), round(a, 2)] for k, (n, a) in by_wall_type.items()]
    return {
        "qto_duplicate_quantity_names": duplicates,
        "qto_space_totals": {k: round(v, 2) for k, v in space_totals.items()},
        "qto_wall_net_side_area_by_type_m2": sorted(rows, key=lambda r: -float(r[2]))[:12],
        "qto_wall_net_side_area_total_m2": round(sum(a for _, a in by_wall_type.values()), 2),
    }


def measure_geometry(f: Any) -> dict[str, Any]:
    """Triangulate walls, slabs, doors and windows; measure wall faces and slab footprints."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", False)
    targets = [e for c in ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow") for e in f.by_type(c)]
    start = time.perf_counter()
    iterator = ifcopenshell.geom.iterator(settings, f, multiprocessing.cpu_count(), include=targets)
    wall_area: defaultdict[str, float] = defaultdict(float)
    wall_count: Counter[str] = Counter()
    slab_area = 0.0
    shapes = failed = 0
    if iterator.initialize():
        while True:
            shape = iterator.get()
            shapes += 1
            element = f.by_id(shape.id)
            try:
                if element.is_a("IfcWall"):
                    element_type = ue.get_type(element)
                    key = element_type.Name if element_type else element.Name
                    wall_area[key] += us.get_side_area(shape.geometry)
                    wall_count[key] += 1
                elif element.is_a("IfcSlab"):
                    slab_area += us.get_footprint_area(shape.geometry)
            except Exception:
                failed += 1
            if not iterator.next():
                break
    rows = [[k, wall_count[k], round(v, 1)] for k, v in wall_area.items()]
    return {
        "targets": len(targets),
        "shapes": shapes,
        "measure_failures": failed,
        "seconds": round(time.perf_counter() - start, 1),
        "wall_side_area_total_m2": round(sum(wall_area.values()), 1),
        "wall_side_area_by_type_m2": sorted(rows, key=lambda r: -float(r[2]))[:12],
        "slab_footprint_area_total_m2": round(slab_area, 1),
    }


def main() -> None:
    """Write the ground truth of one file as JSON."""
    path, out = Path(sys.argv[1]), Path(sys.argv[2])
    start = time.perf_counter()
    f = ifcopenshell.open(str(path))
    scale = uu.calculate_unit_scale(f)  # file length unit -> metre
    elements = f.by_type("IfcElement")
    per_storey: Counter[str] = Counter()
    without_storey = 0
    for element in elements:
        storey = storey_of(element)
        if storey is None:
            without_storey += 1
        else:
            per_storey[storey.GlobalId] += 1
    storeys = sorted(
        f.by_type("IfcBuildingStorey"), key=lambda s: (s.Elevation is None, s.Elevation or 0)
    )
    truth: dict[str, Any] = {
        "file": path.name,
        "schema": f.schema,
        "open_seconds": round(time.perf_counter() - start, 1),
        "application": [
            f"{a.ApplicationFullName} {a.Version}" for a in f.by_type("IfcApplication")
        ],
        "length_unit_scale_to_m": scale,
        "elements_by_class": dict(Counter(e.is_a() for e in elements).most_common()),
        "elements_total": len(elements),
        "spaces": len(f.by_type("IfcSpace")),
        "zones": len(f.by_type("IfcZone")),
        "storeys": [
            {
                "name": s.Name,
                "elevation_m": round((s.Elevation or 0) * scale, 3),
                "elements": per_storey[s.GlobalId],
            }
            for s in storeys
        ],
        "elements_without_storey": without_storey,
        **doors_and_windows(f, scale),
        "IfcWall": walls(f),
    }
    kinds: Counter[str | None] = Counter()
    for element in elements:
        material = ue.get_material(element, should_skip_usage=True)
        kinds[material.is_a() if material else None] += 1
    truth["material_kinds_all_elements"] = kinds.most_common()
    truth.update(quantity_sets(f, scale))
    truth["space_examples"] = [[s.Name, s.LongName] for s in f.by_type("IfcSpace")[:5]]
    if "--geometry" in sys.argv:
        truth["geometry"] = measure_geometry(f)
    truth["total_seconds"] = round(time.perf_counter() - start, 1)
    out.write_text(json.dumps(truth, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: truth[k] for k in ("file", "elements_total", "total_seconds")}))


if __name__ == "__main__":
    main()
