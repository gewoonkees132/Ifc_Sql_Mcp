"""A small IFC file with real shapes, for ``ingest --geometry`` (audit finding A5).

Built with ``ifcopenshell.api`` in metres: a 5 x 3 x 0.2 m wall, a 4 x 4 x 0.25 m slab, a
3 x 2 m space 2.5 m high, a 0.4 x 0.4 x 3 m column, an opening in the wall (never
measured), and a beam whose body is a single point, which IfcOpenShell cannot
triangulate. The measures each should give follow from those sizes. With
``only_unmeasurable``, the file holds just the beam and a space whose body is a point.

With ``scene``, for the viewer's scene (ADR 14), it also holds two stairs of two flights
each, 1 x 3 x 0.2 m: one stair with a body of its own (a 1 x 1 x 3 m block, which the
scene leaves out), one without, whose flights are turned and moved (:data:`PLACED`). And a
copy of the space, the same plan exported again, as Revit writes an MEP Space over a Room.
"""

from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.geometry
import numpy as np

WALL = "Basic Wall:Generic 200mm:101"
SLAB = "Floor:Generic 250mm:102"
SPACE = "101"
COLUMN = "M_Concrete-Square-Column:400 x 400mm:103"
BEAM = "M_W-Wide Flange:W310X60:104"
UNPLANNED_SPACE = "102"
STAIR = "Assembled Stair:Stair:105"
BARE_STAIR = "Assembled Stair:Stair:106"
FLIGHTS = {
    STAIR: ("Run:Monolithic Run:107", "Run:Monolithic Run:108"),
    BARE_STAIR: ("Run:Monolithic Run:109", "Run:Monolithic Run:110"),
}
SPACE_COPY = "101 Space"
PLACED = np.array(
    [[0.0, -1.0, 0.0, 10.0], [1.0, 0.0, 0.0, 20.0], [0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 0.0, 1.0]]
)
"""The bare stair's flights: a quarter turn about Z, then 10, 20 and 1 m along X, Y, Z."""


def write_measured_model(
    path: Path, *, only_unmeasurable: bool = False, scene: bool = False
) -> Path:
    """Write the model to ``path`` and return it; with ``only_unmeasurable``, just the beam."""
    model = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="G-1")
    ifcopenshell.api.run("unit.assign_unit", model)
    context = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    body = ifcopenshell.api.run(
        "context.add_context",
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=context,
    )

    def entity(ifc_class: str, name: str) -> Any:
        return ifcopenshell.api.run("root.create_entity", model, ifc_class=ifc_class, name=name)

    site, building, storey = (
        entity("IfcSite", "Site"),
        entity("IfcBuilding", "B"),
        entity("IfcBuildingStorey", "Level 1"),
    )
    for parent, child in ((project, site), (site, building), (building, storey)):
        ifcopenshell.api.run(
            "aggregate.assign_object", model, products=[child], relating_object=parent
        )

    def shaped(ifc_class: str, name: str, representation: Any) -> Any:
        product = entity(ifc_class, name)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=product)
        ifcopenshell.api.run(
            "geometry.assign_representation", model, product=product, representation=representation
        )
        return product

    geometry = ifcopenshell.api.geometry
    point = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    broken = model.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body,
        RepresentationIdentifier="Body",
        RepresentationType="Point",
        Items=[point],
    )
    beam = shaped("IfcBeam", BEAM, broken)
    if only_unmeasurable:
        ifcopenshell.api.run(
            "spatial.assign_container", model, products=[beam], relating_structure=storey
        )
        # A space whose body is a point too: it gives no plan (re-check finding R1).
        unplanned = shaped(
            "IfcSpace",
            UNPLANNED_SPACE,
            model.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=body,
                RepresentationIdentifier="Body",
                RepresentationType="Point",
                Items=[model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))],
            ),
        )
        ifcopenshell.api.run(
            "aggregate.assign_object", model, products=[unplanned], relating_object=storey
        )
        model.write(str(path))
        return path
    wall = shaped(
        "IfcWall",
        WALL,
        geometry.add_wall_representation(
            model, context=body, length=5.0, height=3.0, thickness=0.2
        ),
    )
    slab = shaped(
        "IfcSlab",
        SLAB,
        geometry.add_slab_representation(
            model,
            context=body,
            depth=0.25,
            polyline=[(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)],
        ),
    )
    column = shaped(
        "IfcColumn",
        COLUMN,
        geometry.add_wall_representation(
            model, context=body, length=0.4, height=3.0, thickness=0.4
        ),
    )
    opening = shaped(
        "IfcOpeningElement",
        f"{WALL}:1",
        geometry.add_wall_representation(
            model, context=body, length=1.0, height=2.0, thickness=0.2
        ),
    )
    ifcopenshell.api.run(
        "spatial.assign_container",
        model,
        products=[wall, slab, column, beam],
        relating_structure=storey,
    )
    ifcopenshell.api.run("feature.add_feature", model, feature=opening, element=wall)
    # A space 3 x 2 m and 2.5 m high: a slab-like extrusion, aggregated under the storey.
    space = shaped(
        "IfcSpace",
        SPACE,
        geometry.add_slab_representation(
            model,
            context=body,
            depth=2.5,
            polyline=[(0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0)],
        ),
    )
    ifcopenshell.api.run("aggregate.assign_object", model, products=[space], relating_object=storey)
    if scene:
        for name, bodied in ((STAIR, True), (BARE_STAIR, False)):
            stair = (
                shaped(
                    "IfcStair",
                    name,
                    geometry.add_wall_representation(
                        model, context=body, length=1.0, height=3.0, thickness=1.0
                    ),
                )
                if bodied
                else entity("IfcStair", name)
            )
            flights = [
                shaped(
                    "IfcStairFlight",
                    flight,
                    geometry.add_slab_representation(
                        model,
                        context=body,
                        depth=0.2,
                        polyline=[(0.0, 0.0), (1.0, 0.0), (1.0, 3.0), (0.0, 3.0)],
                    ),
                )
                for flight in FLIGHTS[name]
            ]
            if not bodied:
                # Turned a quarter about Z and moved, so that world coordinates differ
                # from local ones.
                for flight in flights:
                    ifcopenshell.api.run(
                        "geometry.edit_object_placement",
                        model,
                        product=flight,
                        matrix=PLACED,
                    )
            ifcopenshell.api.run(
                "spatial.assign_container", model, products=[stair], relating_structure=storey
            )
            ifcopenshell.api.run(
                "aggregate.assign_object", model, products=flights, relating_object=stair
            )
        copy = shaped(
            "IfcSpace",
            SPACE_COPY,
            geometry.add_slab_representation(
                model,
                context=body,
                depth=2.5,
                polyline=[(0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0)],
            ),
        )
        ifcopenshell.api.run(
            "aggregate.assign_object", model, products=[copy], relating_object=storey
        )
    model.write(str(path))
    return path
