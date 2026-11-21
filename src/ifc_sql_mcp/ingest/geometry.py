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

This module and ``extract`` are the only ones that touch IfcOpenShell.
"""

import math
import os
import time
from collections.abc import Iterable
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.shape

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.raw import RawShape

type Entity = Any

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
        (``geometry_not_measured``), and the seconds it took.
    """
    started = time.perf_counter()
    targets = {
        entity.id(): entity
        for ifc_class in MEASURED_CLASSES
        for entity in model.by_type(ifc_class)
        if entity.Representation is not None
    }
    shapes: list[RawShape] = []
    issues: list[Issue] = []
    measured: set[int] = set()
    if targets:
        for shape, entity in _triangulated(model, targets, threads):
            measured.add(entity.id())
            try:
                shapes.append(_measure(entity, shape.geometry))
            except Exception as exc:  # IfcOpenShell raises many kinds; each is reported
                issues.append(_not_measured(entity, f"could not be measured ({exc})"))
    for entity_id in sorted(set(targets) - measured):
        issues.append(_not_measured(targets[entity_id], "could not be triangulated"))
    return tuple(shapes), issues, time.perf_counter() - started


def _triangulated(
    model: Entity, targets: dict[int, Entity], threads: int | None
) -> Iterable[tuple[Any, Entity]]:
    """Each target IfcOpenShell triangulates, with its shape; the others are left out."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", False)
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
