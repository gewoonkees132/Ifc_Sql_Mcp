"""Phase 0, item 2: do the Duplex models line up in world coordinates? Do A and D?

Usage: uv run python -I phase0_alignment.py
"""

# ruff: noqa: T201

import json
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.placement
import numpy as np

from ifc_sql_mcp.ingest.geometry import _triangulated, measure_space_plans

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path()


def plans(path: Path):
    """Open a file and return it with every space's plan in world coordinates."""
    model = ifcopenshell.open(str(path))
    footprints, _, _ = measure_space_plans(model, 4)
    names = {s.GlobalId: (s.Name, s.LongName) for s in model.by_type("IfcSpace")}
    return model, [(f.global_id, names[f.global_id], f.plan) for f in footprints]


def site_placement(model):
    """Return where each IfcSite is placed, and any IfcMapConversion."""
    out = []
    for site in model.by_type("IfcSite"):
        m = ifcopenshell.util.placement.get_local_placement(site.ObjectPlacement)
        out.append([round(float(x), 3) for x in m[:3, 3]])
    conv = [
        (c.Eastings, c.Northings, c.OrthogonalHeight)
        for c in (model.by_type("IfcMapConversion") if model.schema != "IFC2X3" else [])
    ]
    return out, conv


def best_iou(plan, others):
    """Return the best intersection over union of a plan with the others."""
    best = (0.0, None)
    for _, name, other in others:
        inter = plan.intersection(other).area
        union = plan.union(other).area
        if union and inter / union > best[0]:
            best = (inter / union, name)
    return best


def boxes(path: Path):
    """Return each element's world bounding box, by GlobalId."""
    model = ifcopenshell.open(str(path))
    targets = {
        e.id(): e
        for e in model.by_type("IfcElement")
        if not e.is_a("IfcFeatureElement") and e.Representation is not None
    }
    out = {}
    for shape, entity in _triangulated(model, targets, 4, world=True):
        v = np.frombuffer(shape.geometry.verts_buffer, dtype=np.float64).reshape(-1, 3)
        if len(v):
            out[str(entity.GlobalId)] = (v.min(axis=0), v.max(axis=0))
    return out


def main() -> None:
    """Print the alignment checks as JSON."""
    samples = ROOT / "data" / "samples"
    arc, arc_plans = plans(samples / "Duplex_A_20110907.ifc")
    result = {"site": {}}
    result["site"]["ARC"] = site_placement(arc)
    for label, name in (
        ("MEP", "Duplex_MEP_20110907.ifc"),
        ("ELE", "Duplex_Electrical_20121207.ifc"),
        ("PLU", "Duplex_Plumbing_20121113.ifc"),
    ):
        model, other = plans(samples / name)
        result["site"][label] = site_placement(model)
        ious = [best_iou(p, other)[0] for _, _, p in arc_plans]
        result[f"ARC rooms vs {label} spaces"] = {
            "arc_rooms": len(arc_plans),
            f"{label}_spaces_with_plan": len(other),
            "best_iou_min": round(min(ious), 4) if ious else None,
            "best_iou_median": round(float(np.median(ious)), 4) if ious else None,
            "rooms_iou_over_0.95": sum(1 for x in ious if x > 0.95),
            f"{label}_space_names": [n for _, n, _ in other][:3],
            f"{label}_space_bounds": [[round(c, 3) for c in p.bounds] for _, _, p in other][:2],
        }
    result["ARC room bounds (union)"] = [
        round(c, 3) for c in __import__("shapely").union_all([p for _, _, p in arc_plans]).bounds
    ]

    audit = ROOT / "data" / "audit"
    a = boxes(audit / "Ifc4_Revit_ARC.ifc")
    d = boxes(audit / "Ifc4_Revit_ARC_FireRatingAdded.ifc")
    shared = sorted(set(a) & set(d))
    deviation = max(
        float(max(np.abs(a[g][0] - d[g][0]).max(), np.abs(a[g][1] - d[g][1]).max())) for g in shared
    )
    result["A vs D"] = {
        "a_shapes": len(a),
        "d_shapes": len(d),
        "shared_global_ids": len(shared),
        "max_bbox_deviation_m": deviation,
    }
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
