"""A small IFC file with real shapes, for ``ingest --geometry`` (audit finding A5).

Built with ``ifcopenshell.api`` in metres: a 5 x 3 x 0.2 m wall, a 4 x 4 x 0.25 m slab, a
3 x 2 m space 2.5 m high, a 0.4 x 0.4 x 3 m column, an opening in the wall (never
measured), and a beam whose body is a single point, which IfcOpenShell cannot
triangulate. The measures each should give follow from those sizes.
"""

from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.geometry

WALL = "Basic Wall:Generic 200mm:101"
SLAB = "Floor:Generic 250mm:102"
SPACE = "101"
COLUMN = "M_Concrete-Square-Column:400 x 400mm:103"
BEAM = "M_W-Wide Flange:W310X60:104"


def write_measured_model(path: Path, *, only_unmeasurable: bool = False) -> Path:
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
    model.write(str(path))
    return path
