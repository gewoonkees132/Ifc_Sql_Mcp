"""Phase 0, item 1: triangulate one reference file for a scene, and measure it.

Usage: uv run python -I phase0_triangulate.py FILE [--threads 4] [--variants]

Prints one JSON object. With --variants (file B), also times --geometry alone and a shared
pass in local coordinates that serves both the measures and the scene.
"""

# ruff: noqa: T201

import argparse
import ctypes
import ctypes.wintypes
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import numpy as np
from glb_sketch import Mesh, Node, json_bytes_of, write_glb

from ifc_sql_mcp.ingest.geometry import (
    MEASURED_CLASSES,
    _measure,
    _triangulated,
    measure_shapes,
    measure_space_plans,
)

CAP_ELEMENTS = 2000
CAP_BYTES = 16 * 1024 * 1024


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_mb() -> float:
    """Return the process's peak working set in MiB (Windows)."""
    counters = _PMC()
    counters.cb = ctypes.sizeof(_PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(_PMC),
        ctypes.wintypes.DWORD,
    ]
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.PeakWorkingSetSize / 2**20


def rgba(material) -> tuple[float, float, float, float]:
    """Return an IfcOpenShell material's colour with alpha."""
    r, g, b = material.diffuse.components
    t = material.transparency
    alpha = 1.0 if t is None or math.isnan(t) else 1.0 - float(t)
    return (float(r), float(g), float(b), alpha)


def to_mesh(entity, geometry, verts: np.ndarray | None = None) -> Mesh:
    """Turn a triangulated shape into a Mesh, with world vertices if given."""
    if verts is None:
        verts = np.frombuffer(geometry.verts_buffer, dtype=np.float64).reshape(-1, 3)
    faces = np.frombuffer(geometry.faces_buffer, dtype=np.int32).reshape(-1, 3)
    mids = np.frombuffer(geometry.material_ids_buffer, dtype=np.int32)
    return Mesh(
        step_id=entity.id(),
        global_id=str(entity.GlobalId),
        ifc_class=entity.is_a(),
        verts=verts,
        faces=faces,
        material_ids=mids,
        materials=[rgba(m) for m in geometry.materials],
    )


def container(entity):
    """Return the element's first spatial container, by STEP id."""
    rels = sorted(entity.ContainedInStructure or (), key=lambda r: r.id())
    return rels[0].RelatingStructure if rels else None


def host(entity):
    """Return the element this one is part of, nested in or cuts, if any."""
    h = (
        ifcopenshell.util.element.get_aggregate(entity)
        or ifcopenshell.util.element.get_nest(entity)
        or ifcopenshell.util.element.get_voided_element(entity)
    )
    return h if h is not None and h.is_a("IfcElement") else None


def storey_of(entity):
    """Return the storey an element or space is on, as transform resolves it."""
    seen = set()
    current = entity
    place = None
    if entity.is_a("IfcSpatialStructureElement"):
        place = ifcopenshell.util.element.get_aggregate(entity)
    else:
        while current is not None and current.id() not in seen:
            seen.add(current.id())
            place = container(current)
            if place is not None:
                break
            current = host(current)
    while place is not None and place.id() not in seen:
        seen.add(place.id())
        if place.is_a("IfcBuildingStorey"):
            return place
        place = ifcopenshell.util.element.get_aggregate(place)
    return None


def parts_of(entity):
    """Return the elements an element aggregates, openings left out."""
    return [
        o
        for rel in sorted(entity.IsDecomposedBy or (), key=lambda r: r.id())
        if rel.is_a("IfcRelAggregates")
        for o in rel.RelatedObjects
        if o.is_a("IfcElement") and not o.is_a("IfcFeatureElement")
    ]


def count_nodes(node: Node) -> int:
    """Return how many nodes of a tree carry a mesh."""
    return (1 if node.mesh is not None else 0) + sum(count_nodes(c) for c in node.children)


def build_tree(entity, meshes, used) -> Node:
    """Return an element's node: its parts as children if any has a body."""
    used.add(entity.id())
    parts = sorted(parts_of(entity), key=lambda e: e.id())
    children = [build_tree(p, meshes, used) for p in parts]
    with_body = [c for c in children if count_nodes(c)]
    own = meshes.get(entity.id())
    if with_body:
        return Node(str(entity.GlobalId), entity.is_a(), None, with_body)
    return Node(str(entity.GlobalId), entity.is_a(), own, [])


def chunks(roots: list[Node], origin) -> list[tuple[int, int]]:
    """Split a storey's nodes at the element cap; return (elements, bytes) each."""
    """Split by the caps; return (elements, bytes) per chunk."""
    out: list[tuple[int, int]] = []
    batch: list[Node] = []
    count = 0
    for node in roots:
        n = count_nodes(node)
        if batch and count + n > CAP_ELEMENTS:
            out.append((count, len(write_glb(batch, origin))))
            batch, count = [], 0
        batch.append(node)
        count += n
    if batch:
        out.append((count, len(write_glb(batch, origin))))
    # The byte cap: a chunk above it would be split in two (not reached in the spike).
    return out


def main() -> None:
    """Triangulate one file and print its figures as JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--variants", action="store_true")
    parser.add_argument("--local", action="store_true", help="local coordinates + placement matrix")
    parser.add_argument("--export", type=Path, help="write the largest storey's chunks here")
    parser.add_argument("--export-all", type=Path, help="write every chunk here")
    args = parser.parse_args()

    started = time.perf_counter()
    model = ifcopenshell.open(str(args.file))
    open_seconds = time.perf_counter() - started

    elements = model.by_type("IfcElement")
    features = [e for e in elements if e.is_a("IfcFeatureElement")]
    solid = [e for e in elements if not e.is_a("IfcFeatureElement")]
    targets = {e.id(): e for e in solid if e.Representation is not None}
    spaces = list(model.by_type("IfcSpace"))
    space_targets = {s.id(): s for s in spaces if s.Representation is not None}

    t0 = time.perf_counter()
    meshes: dict[int, Mesh] = {}
    geometry_ids = set()
    for shape, entity in _triangulated(
        model, targets | space_targets, args.threads, world=not args.local
    ):
        if args.local:
            local = np.frombuffer(shape.geometry.verts_buffer, dtype=np.float64).reshape(-1, 3)
            matrix = ifcopenshell.util.shape.get_shape_matrix(shape)
            mesh = to_mesh(entity, shape.geometry, local @ matrix[:3, :3].T + matrix[:3, 3])
        else:
            mesh = to_mesh(entity, shape.geometry)
        geometry_ids.add(shape.geometry.id)
        if len(mesh.faces):
            meshes[entity.id()] = mesh
    scene_seconds = time.perf_counter() - t0

    # Aggregates
    agg_both = Counter()
    agg_own_only = Counter()
    agg_parts_only = Counter()
    for e in solid:
        parts = parts_of(e)
        if not parts:
            continue
        own = e.id() in meshes
        parts_body = any(p.id() in meshes for p in parts)
        if own and parts_body:
            agg_both[e.is_a()] += 1
        elif own:
            agg_own_only[e.is_a()] += 1
        elif parts_body:
            agg_parts_only[e.is_a()] += 1

    # Roots: elements that are not parts of another element with parts with bodies.
    used: set[int] = set()
    is_part = set()
    for e in solid:
        for p in parts_of(e):
            is_part.add(p.id())
    roots_by_storey: dict[str, list[Node]] = defaultdict(list)
    storey_names = {}
    for e in sorted(solid, key=lambda x: x.id()):
        if e.id() in is_part:
            continue
        node = build_tree(e, meshes, used)
        if not count_nodes(node):
            continue
        storey = storey_of(e)
        key = storey.GlobalId if storey is not None else "(none)"
        storey_names[key] = storey.Name if storey is not None else "(no storey)"
        roots_by_storey[key].append(node)
    drawn = sum(count_nodes(n) for nodes in roots_by_storey.values() for n in nodes)
    through_parts = sum(agg_parts_only.values())
    without_shape = len(solid) - sum(1 for e in solid if e.id() in meshes) - through_parts

    # Origin: centre of the drawn elements' bounding box, rounded to whole metres.
    all_verts = [m.verts for i, m in meshes.items() if i in targets]
    if all_verts:
        stacked = np.concatenate(all_verts)
        lo, hi = stacked.min(axis=0), stacked.max(axis=0)
    else:
        lo = hi = np.zeros(3)
    origin = tuple(float(round(c)) for c in (lo + hi) / 2)

    per_storey = []
    chunk_list = []
    total_bytes = 0
    json_total = 0
    for key, nodes in roots_by_storey.items():
        whole = write_glb(nodes, origin)
        n = sum(count_nodes(x) for x in nodes)
        per_storey.append((storey_names[key], n, len(whole)))
        json_total += json_bytes_of(whole)
        split = chunks(nodes, origin)
        chunk_list.extend(split)
        total_bytes += sum(b for _, b in split)

    space_nodes_by_storey: dict[str, list[Node]] = defaultdict(list)
    for s in sorted(spaces, key=lambda x: x.id()):
        if s.id() in meshes:
            st = storey_of(s)
            space_nodes_by_storey[st.GlobalId if st else "(none)"].append(
                Node(str(s.GlobalId), s.is_a(), meshes[s.id()])
            )
    space_bytes = sum(len(write_glb(nodes, origin)) for nodes in space_nodes_by_storey.values())

    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        key = max(roots_by_storey, key=lambda k: sum(count_nodes(x) for x in roots_by_storey[k]))
        batch, count, n = [], 0, 0
        for node in roots_by_storey[key] + [None]:
            c = count_nodes(node) if node is not None else 0
            if batch and (node is None or count + c > CAP_ELEMENTS):
                (args.export / f"chunk_{n}.glb").write_bytes(write_glb(batch, origin))
                n += 1
                batch, count = [], 0
            if node is not None:
                batch.append(node)
                count += c
        print(json.dumps({"exported_storey": storey_names[key], "chunks": n}), file=sys.stderr)
    if args.export_all:
        args.export_all.mkdir(parents=True, exist_ok=True)
        n = 0
        for nodes in roots_by_storey.values():
            batch, count = [], 0
            for node in [*nodes, None]:
                c = count_nodes(node) if node is not None else 0
                if batch and (node is None or count + c > CAP_ELEMENTS):
                    (args.export_all / f"chunk_{n}.glb").write_bytes(write_glb(batch, origin))
                    n += 1
                    batch, count = [], 0
                if node is not None:
                    batch.append(node)
                    count += c
    tri = sum(len(m.faces) for i, m in meshes.items() if i in targets)
    verts = sum(len(m.verts) for i, m in meshes.items() if i in targets)
    largest = max(per_storey, key=lambda x: x[1]) if per_storey else None
    largest_bytes = max(per_storey, key=lambda x: x[2]) if per_storey else None
    result = {
        "file": args.file.name,
        "threads": args.threads,
        "mode": "local+matrix" if args.local else "world",
        "without_shape_ids": [
            f"{e.is_a()} {e.GlobalId} rep={e.Representation is not None}"
            for e in solid
            if e.id() not in meshes and not parts_of(e)
        ][:5],
        "open_seconds": round(open_seconds, 2),
        "scene_seconds": round(scene_seconds, 2),
        "ifc_elements": len(elements),
        "features_openings": len(features),
        "solid_elements": len(solid),
        "drawn_own_body": drawn,
        "drawn_through_parts": through_parts,
        "without_shape": without_shape,
        "spaces": len(spaces),
        "spaces_drawn": sum(1 for s in spaces if s.id() in meshes),
        "triangles": tri,
        "vertices": verts,
        "space_triangles": sum(len(m.faces) for i, m in meshes.items() if i in space_targets),
        "unique_geometry_ids": len(geometry_ids),
        "shapes": len(meshes),
        "glb_bytes_elements": total_bytes,
        "glb_json_bytes_elements_unsplit": json_total,
        "glb_bytes_spaces": space_bytes,
        "chunks": len(chunk_list),
        "max_chunk": max(chunk_list, key=lambda c: c[1]) if chunk_list else None,
        "storeys": len(per_storey),
        "largest_storey_by_elements": largest,
        "largest_storey_by_bytes": largest_bytes,
        "aggregates_own_and_parts": dict(agg_both),
        "aggregates_own_only": dict(agg_own_only),
        "aggregates_parts_only": dict(agg_parts_only),
        "bbox_min": [round(float(x), 3) for x in lo],
        "bbox_max": [round(float(x), 3) for x in hi],
        "origin": origin,
        "peak_mb_after_scene": round(peak_mb(), 1),
    }

    if args.variants:
        t = time.perf_counter()
        measure_space_plans(model, args.threads)
        result["space_plans_seconds"] = round(time.perf_counter() - t, 2)
        t = time.perf_counter()
        shapes, _, _ = measure_shapes(model, args.threads)
        result["geometry_seconds"] = round(time.perf_counter() - t, 2)
        result["geometry_shapes"] = len(shapes)
        # One shared pass: local coordinates; measure the measured classes, move the
        # rest to world coordinates with the placement matrix.
        measured_ids = {
            e.id()
            for c in MEASURED_CLASSES
            for e in model.by_type(c)
            if e.Representation is not None
        }
        t = time.perf_counter()
        shared_meshes = 0
        shared_measures = 0
        max_dev = 0.0
        for shape, entity in _triangulated(
            model, targets | space_targets, args.threads, world=False
        ):
            g = shape.geometry
            if entity.id() in measured_ids:
                try:
                    _measure(entity, g)
                    shared_measures += 1
                except Exception:  # noqa: S110 - as measure_shapes: a shape it cannot measure
                    pass
            local = np.frombuffer(g.verts_buffer, dtype=np.float64).reshape(-1, 3)
            m = ifcopenshell.util.shape.get_shape_matrix(shape)
            world = local @ m[:3, :3].T + m[:3, 3]
            shared_meshes += 1
            ref = meshes.get(entity.id())
            if ref is not None and len(ref.verts) == len(world):
                max_dev = max(
                    max_dev,
                    float(np.abs(np.sort(ref.verts, axis=0) - np.sort(world, axis=0)).max()),
                )
        result["shared_pass_seconds"] = round(time.perf_counter() - t, 2)
        result["shared_pass_shapes"] = shared_meshes
        result["shared_pass_measures"] = shared_measures
        result["shared_pass_max_world_deviation_m"] = max_dev
        result["peak_mb_end"] = round(peak_mb(), 1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
