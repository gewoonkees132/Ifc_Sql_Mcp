"""Find the spaces that cover the same floor as another (re-check finding R1).

Revit writes the same floor more than once when a model holds both Rooms and MEP Spaces,
or when it exports its area plans. Each is an ``IfcSpace``, and summing the room schedule
then counts that floor twice. The audit's key for a copy (number, name, storey and floor
area, finding A7) catches only the copies whose names agree. In the audit's file C it
found 47 of 75, missing pairs such as ``41 WC`` / ``41 WC H`` and the corridor one set
splits into 49a-d.

The plans settle it. Here a space's plan is its body seen from above, in world
coordinates. On every audit file, whenever two rooms overlap on a storey, one lies at
least 90 % inside the other (``docs/audit/harness/space_truth.py``). So, storey by storey,
the largest plan is taken first, and a space whose plan lies at least :data:`INSIDE` of
the way inside those taken before it **repeats** them: it is the same floor again. It
points at the one it overlaps most. The rule is the ground truth's own, so it gives the
same totals: each room once, 4,721.4 m² on C and 386.3 m² on the Duplex MEP model.

Revit Areas (``Category`` ``Areas``) are area plans, not rooms: they never repeat a room
and no room repeats them, so they are left out of the comparison.

This module only compares shapes, with shapely. ``geometry.measure_space_plans`` makes
them with IfcOpenShell.
"""

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely

INSIDE = 0.9
"""Share of a space's plan inside larger spaces from which it repeats them."""

MIN_TRIANGLE_M2 = 1e-9
"""Triangles smaller than this, seen from above, are walls of the body: left out."""

REVIT_AREAS = "Areas"
"""The ``Category`` Revit writes on the spaces of an area plan."""


@dataclass(frozen=True, slots=True)
class SpacePlan:
    """What the comparison found for one space.

    Attributes:
        global_id: The space.
        footprint_m2: Area of its plan, in square metres.
        repeats_global_id: The larger space whose floor it covers again, if any.
    """

    global_id: str
    footprint_m2: float
    repeats_global_id: str | None


def plan_from_triangles(verts: Sequence[float], faces: Sequence[int]) -> Any | None:
    """The plan of a triangulated body: the union of its triangles seen from above.

    Args:
        verts: Flat x, y, z coordinates, in metres (IfcOpenShell's ``geometry.verts``).
        faces: Flat vertex indices, three per triangle (``geometry.faces``).

    Returns:
        A shapely (multi)polygon, or ``None`` when the body covers no area from above.
    """
    points = np.asarray(verts, dtype=float).reshape(-1, 3)[:, :2]
    triangles = shapely.polygons(points[np.asarray(faces, dtype=int).reshape(-1, 3)])
    triangles = triangles[shapely.area(triangles) > MIN_TRIANGLE_M2]
    if triangles.size == 0:
        return None
    return shapely.make_valid(shapely.union_all(triangles))


def space_plans(
    plans: Sequence[tuple[str, Any]],
    storeys: Mapping[str, str | None],
    revit_areas: Collection[str] = (),
) -> list[SpacePlan]:
    """Compare the plans of a model's spaces, storey by storey.

    Args:
        plans: Each space's GlobalId and plan, in file order.
        storeys: The storey (or other parent) of each space, by GlobalId.
        revit_areas: GlobalIds of the spaces Revit marks as ``Areas``.

    Returns:
        One result per space, in the order of ``plans``.
    """
    by_storey: dict[str | None, list[tuple[int, str, Any]]] = defaultdict(list)
    for order, (global_id, plan) in enumerate(plans):
        if global_id not in revit_areas:
            by_storey[storeys.get(global_id)].append((order, global_id, plan))
    repeats: dict[str, str] = {}
    for spaces in by_storey.values():
        repeats.update(_repeats_on_one_storey(spaces))
    return [
        SpacePlan(global_id, float(plan.area), repeats.get(global_id)) for global_id, plan in plans
    ]


def _repeats_on_one_storey(spaces: list[tuple[int, str, Any]]) -> dict[str, str]:
    """The spaces of one storey that repeat another, largest plan first, then file order."""
    taken: list[tuple[str, Any]] = []
    repeats: dict[str, str] = {}
    for _, global_id, plan in sorted(spaces, key=lambda s: (-s[2].area, s[0])):
        overlaps = [
            (other, plan.intersection(other_plan).area, other_plan)
            for other, other_plan in taken
            if plan.intersects(other_plan)
        ]
        overlaps = [overlap for overlap in overlaps if overlap[1] > 0]
        if overlaps:
            covered = plan.intersection(shapely.union_all([o[2] for o in overlaps])).area
            if covered >= INSIDE * plan.area:
                repeats[global_id] = max(overlaps, key=lambda o: o[1])[0]
                continue
        taken.append((global_id, plan))
    return repeats
