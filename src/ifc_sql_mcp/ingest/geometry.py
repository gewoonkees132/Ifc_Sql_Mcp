"""Measure an export's shapes when it carries no quantities (``ingest --geometry``, A5).

Revit writes IFC base quantities and Revit parameters only when "Export base
quantities" or "Export Revit property sets" is ticked. Neither is needed for
coordination, so the audit's tower (file B) has no quantity at all: no area for any of its
6,095 walls. Its shapes are there, though. This module triangulates them with
IfcOpenShell, in parallel, and measures each one as the audit did
(``docs/audit/harness/ground_truth.py``), in its own local coordinates:

* the side area, seen along the local Y axis: a wall's face, which Revit's wall axis
  runs along X;
* the footprint area, seen from above: a slab's or a space's plan;
* the volume enclosed by the triangulated shape.

It measures the classes in :data:`MEASURED_CLASSES`. These are modelled as solids, so
their volume means something. Doors, windows and furniture are assemblies of open
meshes, and a door has its size as attributes (A4). Which measure becomes which quantity
is decided by ``quantities.geometry_quantities``, and every geometry quantity ranks last
(source ``geometry``), so a quantity the file carries always wins. On the audit's files
the result agrees with them: file A's walls are 964.6 m² measured against 946.0 m² of
``NetSideArea`` (which deducts the openings), and file B's 6,095 walls are 103,057.7 m².

It also gives every space its plan, in world coordinates (:func:`measure_space_plans`).
That runs on every ingest, not only with ``--geometry``: comparing plans is how rooms
exported twice are found (``footprints.py``, re-check finding R1), and spaces are few.

With ``--viewer``, :func:`triangulate_scene` makes one pass over every element and space
instead, for the viewer's scene (ADR 14). IfcOpenShell triangulates each shape in its own
local coordinates, as for the measures, and the vertices are moved to world coordinates
with the shape's placement matrix, in numpy. On the nine reference files that is 1.4 to 4
times faster than IfcOpenShell's ``use-world-coords``, with the same triangles, so the one
pass also gives the space plans and, with ``--geometry``, the measures.

Each pass triangulates only the objects the transform keeps: of two that share a GlobalId,
the first in the transform's order (:func:`kept_by_global_id`). The body of an object it
skips is then never drawn, planned or measured under the GlobalId of the one it keeps.

This module and ``extract`` are the only ones that touch IfcOpenShell.
"""

import math
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.shape
import numpy as np

from ifc_sql_mcp.ingest.footprints import plan_from_triangles
from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.raw import DEFAULT_RGBA, RawFootprint, RawMesh, RawShape

type Entity = Any

IFCOPENSHELL_VERSION: str = ifcopenshell.version
"""The IfcOpenShell release that triangulates; each scene records it."""

MEASURED_CLASSES = (
    "IfcWall",
    "IfcSlab",
    "IfcRoof",
    "IfcCovering",
    "IfcColumn",
    "IfcBeam",
    "IfcMember",
    "IfcPlate",
    "IfcFooting",
    "IfcStairFlight",
    "IfcRampFlight",
    "IfcRailing",
    "IfcSpace",
)
"""The classes whose shapes are measured, with their subclasses (IfcWallStandardCase)."""


def measure_shapes(
    model: Entity, threads: int | None = None
) -> tuple[tuple[RawShape, ...], list[Issue], float]:
    """Triangulate and measure the shapes of the :data:`MEASURED_CLASSES` objects.

    Args:
        model: The open IfcOpenShell file.
        threads: Worker threads; by default one per CPU.

    Returns:
        One measurement per shape, the objects with a body that could not be measured
        (``geometry_not_measured``), and the seconds it took; each by STEP id, as
        :func:`triangulate_scene` gives them, whatever order the threads finish in.
    """
    started = time.perf_counter()
    targets = {
        entity_id: entity
        for entity_id, entity in kept_by_global_id(model).items()
        if entity.Representation is not None
        and any(entity.is_a(ifc_class) for ifc_class in MEASURED_CLASSES)
    }
    shapes: dict[int, RawShape] = {}
    failed: dict[int, Issue] = {}
    if targets:
        for shape, entity in _triangulated(model, targets, threads):
            try:
                shapes[entity.id()] = _measure(entity, shape.geometry)
            except Exception as exc:  # IfcOpenShell raises many kinds; each is reported
                failed[entity.id()] = _not_measured(entity, f"could not be measured ({exc})")
    issues = [failed[entity_id] for entity_id in sorted(failed)]
    issues.extend(
        _not_measured(targets[entity_id], "could not be triangulated")
        for entity_id in sorted(set(targets) - set(shapes) - set(failed))
    )
    ordered = tuple(shapes[entity_id] for entity_id in sorted(shapes))
    return ordered, issues, time.perf_counter() - started


def measure_space_plans(
    model: Entity, threads: int | None = None
) -> tuple[tuple[RawFootprint, ...], list[Issue], float]:
    """Give every space with a body its plan: the body seen from above, in world coordinates.

    Args:
        model: The open IfcOpenShell file.
        threads: Worker threads; by default one per CPU.

    Returns:
        The plans in file order, the spaces whose body gave none (``geometry_not_measured``:
        a copy of such a space is then found by its number, name, storey and area only), and
        the seconds it took.
    """
    started = time.perf_counter()
    targets = {
        entity_id: entity
        for entity_id, entity in kept_by_global_id(model).items()
        if entity.is_a("IfcSpace") and entity.Representation is not None
    }
    plans: dict[int, RawFootprint] = {}
    if targets:
        for shape, entity in _triangulated(model, targets, threads, world=True):
            plan = plan_from_triangles(shape.geometry.verts, shape.geometry.faces)
            if plan is not None:
                plans[entity.id()] = RawFootprint(global_id=str(entity.GlobalId), plan=plan)
    issues = [
        _plan_not_measured(targets[entity_id]) for entity_id in sorted(set(targets) - set(plans))
    ]
    ordered = tuple(plans[entity_id] for entity_id in sorted(plans))
    return ordered, issues, time.perf_counter() - started


@dataclass(frozen=True, slots=True)
class SceneTriangulation:
    """What one pass of :func:`triangulate_scene` gives, each part ordered by STEP id.

    Attributes:
        meshes: The body of every element and space IfcOpenShell triangulated.
        footprints: Every space's plan, as :func:`measure_space_plans` gives it.
        shapes: The measures of :func:`measure_shapes`; empty unless asked for.
        issues: The spaces without a plan, then (when measured) the bodies that could not
            be measured or triangulated, with the messages the two functions give.
        seconds: How long the pass took.
    """

    meshes: tuple[RawMesh, ...]
    footprints: tuple[RawFootprint, ...]
    shapes: tuple[RawShape, ...]
    issues: tuple[Issue, ...]
    seconds: float


def triangulate_scene(
    model: Entity, threads: int | None = None, *, measure: bool = False
) -> SceneTriangulation:
    """Triangulate every element and space once: for the scene, the plans and the measures.

    The targets are every ``IfcElement`` with a body, feature elements (openings) left out,
    and every ``IfcSpace`` with a body, of those the transform keeps
    (:func:`kept_by_global_id`). IfcOpenShell cuts the openings out of their hosts, since
    ``disable-opening-subtractions`` stays off.

    Args:
        model: The open IfcOpenShell file.
        threads: Worker threads; by default one per CPU.
        measure: Also measure the :data:`MEASURED_CLASSES` shapes, as ``--geometry`` does.

    Returns:
        The meshes, plans, measures and issues, ordered by STEP id, so that IfcOpenShell's
        worker threads cannot change them from one run to the next.
    """
    started = time.perf_counter()
    targets = {
        entity_id: entity
        for entity_id, entity in kept_by_global_id(model).items()
        if (entity.is_a("IfcElement") or entity.is_a("IfcSpace"))
        and entity.Representation is not None
        and not entity.is_a("IfcFeatureElement")
    }
    spaces = {entity_id for entity_id, entity in targets.items() if entity.is_a("IfcSpace")}
    measured = {
        entity_id
        for entity_id, entity in targets.items()
        if measure and any(entity.is_a(ifc_class) for ifc_class in MEASURED_CLASSES)
    }
    meshes: dict[int, RawMesh] = {}
    plans: dict[int, RawFootprint] = {}
    shapes: dict[int, RawShape] = {}
    failed: dict[int, Issue] = {}
    seen: set[int] = set()
    if targets:
        for shape, entity in _triangulated(model, targets, threads):
            entity_id = entity.id()
            seen.add(entity_id)
            vertices = _world_vertices(shape)
            meshes[entity_id] = _mesh(entity, shape.geometry, vertices)
            if entity_id in spaces:
                faces = np.frombuffer(shape.geometry.faces_buffer, dtype=np.int32)
                plan = plan_from_triangles(vertices.ravel(), faces)
                if plan is not None:
                    plans[entity_id] = RawFootprint(global_id=str(entity.GlobalId), plan=plan)
            if entity_id in measured:
                try:
                    shapes[entity_id] = _measure(entity, shape.geometry)
                except Exception as exc:  # as measure_shapes: each failure is reported
                    failed[entity_id] = _not_measured(entity, f"could not be measured ({exc})")
    issues = [_plan_not_measured(targets[entity_id]) for entity_id in sorted(spaces - set(plans))]
    issues.extend(failed[entity_id] for entity_id in sorted(failed))
    issues.extend(
        _not_measured(targets[entity_id], "could not be triangulated")
        for entity_id in sorted(measured - seen)
    )
    return SceneTriangulation(
        meshes=tuple(meshes[entity_id] for entity_id in sorted(meshes)),
        footprints=tuple(plans[entity_id] for entity_id in sorted(plans)),
        shapes=tuple(shapes[entity_id] for entity_id in sorted(shapes)),
        issues=tuple(issues),
        seconds=time.perf_counter() - started,
    )


def kept_by_global_id(model: Entity) -> dict[int, Entity]:
    """The spatial elements and elements the transform keeps, by STEP id.

    ``extract`` reads the spatial elements, then the elements, each in STEP id order, and
    the transform keeps the first object of each GlobalId (``transform._accept_objects``).
    This takes the first entity of each GlobalId in that same order. A malformed GlobalId
    is kept here and skipped by the transform, so its body is left out later.
    """
    kept: dict[str, Entity] = {}
    for ifc_class in ("IfcSpatialStructureElement", "IfcElement"):
        for entity in sorted(model.by_type(ifc_class), key=lambda entity: entity.id()):
            kept.setdefault(str(entity.GlobalId), entity)
    return {int(entity.id()): entity for entity in kept.values()}


def _world_vertices(shape: Any) -> np.ndarray[Any, np.dtype[np.float64]]:
    """A shape's vertices, triangulated in its local coordinates, in world coordinates."""
    local = np.frombuffer(shape.geometry.verts_buffer, dtype=np.float64).reshape(-1, 3)
    matrix = np.asarray(ifcopenshell.util.shape.get_shape_matrix(shape), dtype=np.float64)
    world: np.ndarray[Any, np.dtype[np.float64]] = local @ matrix[:3, :3].T + matrix[:3, 3]
    return world


def _mesh(entity: Entity, geometry: Any, vertices: np.ndarray[Any, Any]) -> RawMesh:
    return RawMesh(
        global_id=str(entity.GlobalId),
        step_id=int(entity.id()),
        vertices=np.ascontiguousarray(vertices, dtype=np.float64).tobytes(),
        faces=bytes(geometry.faces_buffer),
        material_ids=bytes(geometry.material_ids_buffer),
        materials=tuple(rgba(material) for material in geometry.materials),
    )


def rgba(material: Any) -> tuple[float, float, float, float]:
    """An IfcOpenShell material's red, green, blue and alpha, each from 0 to 1.

    IfcOpenShell gives a transparency of NaN when the file states none: the material is
    then opaque. A material without a usable colour is :data:`DEFAULT_RGBA`.
    """
    components: Any = getattr(getattr(material, "diffuse", None), "components", None)
    transparency = getattr(material, "transparency", None)
    try:
        red, green, blue = (float(component) for component in components)
        alpha = 1.0 if transparency is None or math.isnan(transparency) else 1 - transparency
    except (TypeError, ValueError):
        return DEFAULT_RGBA
    values = (red, green, blue, float(alpha))
    if not all(math.isfinite(value) for value in values):
        return DEFAULT_RGBA
    clamped = tuple(min(1.0, max(0.0, value)) for value in values)
    return clamped[0], clamped[1], clamped[2], clamped[3]


def _triangulated(
    model: Entity, targets: dict[int, Entity], threads: int | None, *, world: bool = False
) -> Iterable[tuple[Any, Entity]]:
    """Each target IfcOpenShell triangulates, with its shape; the others are left out."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", world)
    iterator = ifcopenshell.geom.iterator(
        settings, model, threads or os.cpu_count() or 1, include=list(targets.values())
    )
    if not iterator.initialize():
        return
    while True:
        shape = iterator.get()
        yield shape, targets[shape.id]
        if not iterator.next():
            return


def _measure(entity: Entity, geometry: Any) -> RawShape:
    side = float(ifcopenshell.util.shape.get_side_area(geometry))
    footprint = float(ifcopenshell.util.shape.get_footprint_area(geometry))
    volume = float(ifcopenshell.util.shape.get_volume(geometry))
    if not all(math.isfinite(value) for value in (side, footprint, volume)):
        message = f"gave a non-finite measure (side {side}, footprint {footprint}, volume {volume})"
        raise ValueError(message)
    return RawShape(
        global_id=str(entity.GlobalId),
        side_area_m2=side,
        footprint_area_m2=footprint,
        volume_m3=volume,
    )


def _not_measured(entity: Entity, what: str) -> Issue:
    name = entity.Name if entity.Name is not None else "(no name)"
    message = f"{entity.is_a()} {name!r} has a body that {what}; no geometry quantity"
    return Issue(IssueCode.GEOMETRY_NOT_MEASURED, str(entity.GlobalId), message)


def _plan_not_measured(entity: Entity) -> Issue:
    name = entity.Name if entity.Name is not None else "(no name)"
    message = (
        f"{entity.is_a()} {name!r} has a body that gives no plan; a copy of it is found by "
        "its number, name, storey and area only"
    )
    return Issue(IssueCode.GEOMETRY_NOT_MEASURED, str(entity.GlobalId), message)
