#!/usr/bin/env python3
"""Generate ``revit_quirks.ifc``: a tiny synthetic IFC2X3 file with the quirks of a Revit export.

Usage::

    uv run python tests/fixtures/make_revit_quirks.py      # rewrites revit_quirks.ifc

The building is made up. Level 1 has a kitchen, a stud wall with a door and two
openings, a beam, a stair, a chair in the kitchen and a slab; Level 2 has a store room,
two panelboards and a generic model; a Foundation level is empty. There is no
geometry, because nothing in the pipeline reads it. It reproduces what the Duplex
sample files do (see ``docs/revit-ifc-quirks.md``) without copying any of their data:

* feet as the length unit (``FOOT``, ``SQUARE FOOT``, ``CUBIC FOOT``) and degrees;
* storey elevations with float noise;
* ``Family:Type:ElementId`` names, ``Family:Type:Label:ElementId`` names (with a label
  that differs from the type), and ``:n`` part suffixes on an opening and stair parts;
* a wall with a layer set, a beam with one material, a panelboard with a material list;
* a door type carrying an empty IfcDoorLiningProperties named after the door instance;
* ``PSet_Revit_Type_*`` sets on instances, and a real property set on two type objects;
* placeholder (``BarCode = 'BarCode'``) and empty values;
* a space with OmniClass Table 13, Revit dimensions and a ``GSA Space Areas`` quantity set;
* furniture contained in a space rather than a storey;
* UniFormat IfcClassificationReferences on types, one of them without a code;
* Revit 2013's two same-named sets on one element, with a conflicting value.

It adds a few deliberate defects so every kind of issue has a case: a negative area, a
name that is not a Revit name, a dimension parameter written as a plain ``IfcReal`` and
a store room whose OmniClass value has no code.

The output is deterministic: GlobalIds are fixed, the header timestamp and owner
history dates are constants, and nothing records the IfcOpenShell version. Running the
script twice gives identical bytes, which ``tests/unit/ingest/test_fixture.py`` checks.
"""

import math
import sys
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.classification
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.material
import ifcopenshell.api.owner
import ifcopenshell.api.owner.settings
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.type
import ifcopenshell.api.unit
import ifcopenshell.guid

OUTPUT = Path(__file__).with_name("revit_quirks.ifc")

FOOT = 0.3048  # metres per foot
TIMESTAMP = "2026-01-01T00:00:00"
EPOCH = 1767225600  # the same instant, in seconds since 1970, for IfcOwnerHistory


_FIXED_IDS: set[str] = set()


def gid(label: str) -> str:
    """Build a readable, valid GlobalId: ``0`` plus the label, padded to 22 characters."""
    value = "0" + label.ljust(21, "_")
    assert len(value) == 22, label
    _FIXED_IDS.add(value)
    return value


# GlobalIds the tests refer to.
PROJECT = gid("project")
SITE = gid("site")
BUILDING = gid("building")
LEVEL_1 = gid("level1")
LEVEL_2 = gid("level2")
FOUNDATION = gid("foundation")
KITCHEN = gid("space_kitchen")
STORE = gid("space_store")
WALL = gid("wall_stud")
DOOR = gid("door")
DOOR_OPENING = gid("opening_door")
UNNAMED_OPENING = gid("opening_unnamed")
BEAM = gid("beam")
STAIR = gid("stair")
STAIR_FLIGHT = gid("stair_flight")
STAIR_STRINGER = gid("stair_stringer")
CHAIR = gid("chair")
SLAB = gid("slab")
PANEL_1 = gid("panel_1")
PANEL_2 = gid("panel_2")
PROXY = gid("proxy")
DOOR_STYLE = gid("type_door")
CHAIR_TYPE = gid("type_chair")
PANEL_TYPE = gid("type_panel")

# What the file says, in metres; the script writes feet, the pipeline converts back.
LEVEL_2_ELEVATION_FT = 9.8425196850394  # 3.000 m plus Revit-style float noise
FOUNDATION_ELEVATION_FT = -6.56167979002625  # -2.000 m
KITCHEN_AREA_M2 = 12.5
KITCHEN_PERIMETER_M = 14.5
KITCHEN_HEIGHT_M = 2.6
KITCHEN_VOLUME_M3 = 32.5
WALL_LENGTH_M = 4.0
WALL_AREA_M2 = 10.4
WALL_VOLUME_M3 = 1.3
PLASTERBOARD_M = 0.0125
STUD_M = 0.075
WALL_WIDTH_M = 2 * PLASTERBOARD_M + STUD_M


def _ft(metres: float) -> float:
    return metres / FOOT


def _ft2(square_metres: float) -> float:
    return square_metres / FOOT**2


def _ft3(cubic_metres: float) -> float:
    return cubic_metres / FOOT**3


class _Builder:
    """Wraps ``ifcopenshell.api`` with the few helpers this building needs."""

    def __init__(self) -> None:
        self.model = ifcopenshell.api.project.create_file(version="IFC2X3")
        self._owner_history()

    def _owner_history(self) -> None:
        # IFC2X3 requires an owner history on every rooted entity. The API creates one
        # per entity from these two settings; their dates are fixed in finish().
        model = self.model
        person = ifcopenshell.api.owner.add_person(
            model, identification="fixture", family_name="Fixture", given_name="Generator"
        )
        organisation = ifcopenshell.api.owner.add_organisation(
            model, identification="ifc-sql-mcp", name="ifc-sql-mcp"
        )
        user = ifcopenshell.api.owner.add_person_and_organisation(
            model, person=person, organisation=organisation
        )
        application = ifcopenshell.api.owner.add_application(
            model,
            application_developer=organisation,
            version="1",
            application_full_name="ifc-sql-mcp fixture generator",
            application_identifier="ifc-sql-mcp",
        )
        ifcopenshell.api.owner.settings.get_user = lambda _: user
        ifcopenshell.api.owner.settings.get_application = lambda _: application

    def entity(self, ifc_class: str, global_id: str, name: str | None, **attributes: Any) -> Any:
        """Create a rooted entity with a fixed GlobalId."""
        entity = ifcopenshell.api.root.create_entity(self.model, ifc_class=ifc_class, name=name)
        entity.GlobalId = global_id
        for attribute, value in attributes.items():
            setattr(entity, attribute, value)
        return entity

    def value(self, ifc_type: str, value: Any) -> Any:
        """Wrap a value in an IFC type, e.g. ``IfcLengthMeasure(2.5)``."""
        return self.model.create_entity(ifc_type, value)

    def pset(self, product: Any, name: str, properties: dict[str, Any]) -> Any:
        """Add a property set to a product, the way the API intends."""
        pset = ifcopenshell.api.pset.add_pset(self.model, product=product, name=name)
        ifcopenshell.api.pset.edit_pset(self.model, pset=pset, properties=properties)
        return pset

    def raw_pset(self, name: str, properties: list[tuple[str, Any]]) -> Any:
        """Build a property set entity by hand, for what the API rightly refuses to make.

        ``add_pset`` reuses a product's existing set of the same name, and a dict cannot
        repeat a property name; Revit 2013 does both.
        """
        return self.model.create_entity(
            "IfcPropertySet",
            GlobalId=ifcopenshell.guid.new(),  # renumbered in finish()
            Name=name,
            HasProperties=[
                self.model.create_entity("IfcPropertySingleValue", Name=prop, NominalValue=value)
                for prop, value in properties
            ],
        )

    def relate_pset(self, products: list[Any], pset: Any) -> None:
        """Attach a property set to several products with one relationship."""
        self.model.create_entity(
            "IfcRelDefinesByProperties",
            GlobalId=ifcopenshell.guid.new(),  # renumbered in finish()
            RelatedObjects=products,
            RelatingPropertyDefinition=pset,
        )

    def finish(self) -> ifcopenshell.file:
        """Make the file reproducible: fixed GlobalIds for the rest, fixed dates and header."""
        model = self.model
        _sort_sets(model)
        roots = sorted(model.by_type("IfcRoot"), key=lambda e: e.id())
        # Psets and relationships got random GlobalIds from the API; number them instead.
        numbered = 0
        for entity in roots:
            if entity.GlobalId not in _FIXED_IDS:
                numbered += 1
                entity.GlobalId = f"1{numbered:021d}"
        # The API makes one owner history per entity, stamped with the current time, and
        # leaves a few relationships without one (IFC2X3 requires it). Like Revit, use
        # one shared history with a fixed date.
        histories = sorted(model.by_type("IfcOwnerHistory"), key=lambda e: e.id())
        shared = histories[0]
        shared.CreationDate = EPOCH
        shared.LastModifiedDate = EPOCH
        for entity in roots:
            entity.OwnerHistory = shared
        for history in histories[1:]:
            model.remove(history)
        header = model.header
        header.file_description.description = ("ViewDefinition [CoordinationView]",)
        header.file_name.name = OUTPUT.name
        header.file_name.time_stamp = TIMESTAMP
        header.file_name.author = ("",)
        header.file_name.organization = ("",)
        header.file_name.preprocessor_version = "IfcOpenShell"
        header.file_name.originating_system = "ifc-sql-mcp fixture generator"
        header.file_name.authorization = ""
        return model


def _sort_sets(model: ifcopenshell.file) -> None:
    """Write every SET-typed attribute in STEP id order.

    The API gathers related objects in Python sets, so their order follows object hashes
    and can change from one run to the next. In IFC a SET has no order, so sorting
    changes nothing but the bytes. LISTs (such as material layers) keep their order.
    """
    for entity in model:
        for index, attribute in enumerate(entity.declaration.all_attributes()):
            kind = attribute.type_of_attribute()
            if type(kind).__name__ != "aggregation_type":
                continue
            if kind.type_of_aggregation_string() != "set":
                continue
            members = entity[index]
            if members and all(isinstance(m, ifcopenshell.entity_instance) for m in members):
                entity[index] = sorted(members, key=lambda m: m.id())


def _units(b: _Builder) -> None:
    model = b.model
    units = []
    # Revit spells conversion-based units in capitals and writes the factor as a measure.
    # IfcOpenShell writes lower case, and its cubic foot (0.02831684671168849) is not
    # quite 0.3048 ** 3, so both are set here.
    for api_name, revit_name, factor_type, factor in (
        ("foot", "FOOT", "IfcLengthMeasure", FOOT),
        ("square foot", "SQUARE FOOT", "IfcAreaMeasure", FOOT**2),
        ("cubic foot", "CUBIC FOOT", "IfcVolumeMeasure", FOOT**3),
        ("degree", "DEGREE", "IfcPlaneAngleMeasure", math.pi / 180),
    ):
        unit = ifcopenshell.api.unit.add_conversion_based_unit(model, name=api_name)
        unit.Name = revit_name
        unit.ConversionFactor.ValueComponent = b.value(factor_type, factor)
        units.append(unit)
    units.append(ifcopenshell.api.unit.add_si_unit(model, unit_type="TIMEUNIT"))
    ifcopenshell.api.unit.assign_unit(model, units=units)


def _spatial(b: _Builder) -> dict[str, Any]:
    model = b.model
    project = b.entity("IfcProject", PROJECT, "0042", LongName="Fixture House")
    _units(b)
    ifcopenshell.api.context.add_context(model, context_type="Model")

    site = b.entity("IfcSite", SITE, "Default")
    building = b.entity("IfcBuilding", BUILDING, None)  # Revit 2011 leaves it unnamed
    level_1 = b.entity("IfcBuildingStorey", LEVEL_1, "Level 1", Elevation=0.0)
    level_2 = b.entity("IfcBuildingStorey", LEVEL_2, "Level 2", Elevation=LEVEL_2_ELEVATION_FT)
    foundation = b.entity(
        "IfcBuildingStorey", FOUNDATION, "Foundation", Elevation=FOUNDATION_ELEVATION_FT
    )
    kitchen = b.entity(
        "IfcSpace", KITCHEN, "F101", LongName="Kitchen", InteriorOrExteriorSpace="INTERNAL"
    )
    store = b.entity(
        "IfcSpace", STORE, "F201", LongName="Store", InteriorOrExteriorSpace="INTERNAL"
    )

    ifcopenshell.api.aggregate.assign_object(model, products=[site], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(model, products=[building], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(
        model, products=[foundation, level_1, level_2], relating_object=building
    )
    ifcopenshell.api.aggregate.assign_object(model, products=[kitchen], relating_object=level_1)
    ifcopenshell.api.aggregate.assign_object(model, products=[store], relating_object=level_2)
    return {"level_1": level_1, "level_2": level_2, "kitchen": kitchen, "store": store}


def _spaces(b: _Builder, spaces: dict[str, Any]) -> None:
    kitchen, store = spaces["kitchen"], spaces["store"]
    b.pset(
        kitchen,
        "PSet_Revit_Identity Data",
        {
            "Number": b.value("IfcLabel", "F101"),
            "Name": b.value("IfcLabel", "Kitchen"),
            # Made up, in the OmniClass Table 13 format Revit writes.
            "OmniClass Table 13 Category": b.value("IfcLabel", "13-51 24 17: Kitchen Space"),
        },
    )
    b.pset(
        kitchen,
        "PSet_Revit_Dimensions",
        {
            "Area": b.value("IfcAreaMeasure", _ft2(KITCHEN_AREA_M2)),
            "Perimeter": b.value("IfcLengthMeasure", _ft(KITCHEN_PERIMETER_M)),
            "Unbounded Height": b.value("IfcLengthMeasure", _ft(KITCHEN_HEIGHT_M)),
            "Volume": b.value("IfcVolumeMeasure", _ft3(KITCHEN_VOLUME_M3)),
        },
    )
    b.pset(
        kitchen,
        "Pset_SpaceCommon",
        {
            "Reference": b.value("IfcIdentifier", ""),  # empty
            "CeilingCovering": b.value("IfcLabel", "CeilingCovering"),  # placeholder
        },
    )
    gsa = ifcopenshell.api.pset.add_qto(b.model, product=kitchen, name="GSA Space Areas")
    ifcopenshell.api.pset.edit_qto(
        b.model, qto=gsa, properties={"GSA BIM Area": _ft2(KITCHEN_AREA_M2)}
    )
    # Revit 2013 style: a title without a code.
    b.pset(
        store,
        "Identity Data",
        {"OmniClass Table 13 Category": b.value("IfcLabel", "Storage Spaces")},
    )


def _materials(b: _Builder) -> dict[str, Any]:
    model = b.model
    names = ("Plasterboard", "Metal Stud", "Timber", "Steel", "Paint", "Concrete")
    materials = {name: ifcopenshell.api.material.add_material(model, name=name) for name in names}

    partition = ifcopenshell.api.material.add_material_set(
        model, name="Interior - Stud Partition", set_type="IfcMaterialLayerSet"
    )
    for material, thickness_m in (
        ("Plasterboard", PLASTERBOARD_M),
        ("Metal Stud", STUD_M),
        ("Plasterboard", PLASTERBOARD_M),
    ):
        layer = ifcopenshell.api.material.add_layer(
            model, layer_set=partition, material=materials[material]
        )
        layer.LayerThickness = _ft(thickness_m)

    panel_list = ifcopenshell.api.material.add_material_set(
        model, name="Panelboard", set_type="IfcMaterialList"
    )
    for material in ("Steel", "Paint"):
        ifcopenshell.api.material.add_list_item(
            model, material_list=panel_list, material=materials[material]
        )
    return {"partition": partition, "panel_list": panel_list, **materials}


def _wall_door_and_openings(b: _Builder, level_1: Any, materials: dict[str, Any]) -> None:
    model = b.model
    wall = b.entity(
        "IfcWallStandardCase",
        WALL,
        "Basic Wall:Interior - Stud Partition:200001",
        Tag="200001",
        ObjectType="Basic Wall:Interior - Stud Partition",
    )
    ifcopenshell.api.material.assign_material(
        model, products=[wall], type="IfcMaterialLayerSetUsage", material=materials["partition"]
    )
    b.pset(
        wall,
        "Pset_WallCommon",
        {
            "IsExternal": b.value("IfcBoolean", False),
            "LoadBearing": b.value("IfcBoolean", False),
            "Reference": b.value("IfcLabel", ""),
        },
    )
    b.pset(
        wall,
        "PSet_Revit_Dimensions",
        {
            "Length": b.value("IfcLengthMeasure", _ft(WALL_LENGTH_M)),
            "Area": b.value("IfcAreaMeasure", _ft2(WALL_AREA_M2)),
            "Volume": b.value("IfcVolumeMeasure", _ft3(WALL_VOLUME_M3)),
        },
    )
    b.pset(
        wall,
        "PSet_Revit_Type_Construction",
        {"Width": b.value("IfcLengthMeasure", _ft(WALL_WIDTH_M))},
    )
    b.pset(
        wall,
        "PSet_Revit_Other",
        {"BarCode": b.value("IfcLabel", "BarCode"), "Manufacturer": b.value("IfcLabel", "")},
    )
    b.pset(
        wall,
        "PSet_Revit_Constraints",
        {"Top Offset": b.value("IfcLengthMeasure", _ft(-0.3))},  # negative, and that is fine
    )

    door_name = "M_Single-Flush:0915 x 2134mm:0915 x 2134mm:200101"
    door = b.entity("IfcDoor", DOOR, door_name, Tag="200101", ObjectType="0915 x 2134mm")
    door_style = b.entity("IfcDoorStyle", DOOR_STYLE, "0915 x 2134mm", Tag="200100")
    ifcopenshell.api.type.assign_type(model, related_objects=[door], relating_type=door_style)
    # Revit 2011: an empty lining-properties set on the style, named after one instance.
    lining = model.create_entity(
        "IfcDoorLiningProperties", GlobalId=gid("door_lining"), Name=door_name
    )
    # A real property set on the type; the instance overrides one of its values.
    door_type_common = b.raw_pset(
        "Pset_DoorCommon",
        [("FireRating", b.value("IfcLabel", "FD30")), ("IsExternal", b.value("IfcBoolean", True))],
    )
    door_style.HasPropertySets = [lining, door_type_common]
    b.pset(door, "Pset_DoorCommon", {"IsExternal": b.value("IfcBoolean", False)})

    door_opening = b.entity(
        "IfcOpeningElement", DOOR_OPENING, door_name + ":1", ObjectType="Opening"
    )
    unnamed_opening = b.entity("IfcOpeningElement", UNNAMED_OPENING, None, ObjectType="Opening")
    for opening in (door_opening, unnamed_opening):
        ifcopenshell.api.feature.add_feature(model, feature=opening, element=wall)
    ifcopenshell.api.feature.add_filling(model, opening=door_opening, element=door)

    ifcopenshell.api.spatial.assign_container(
        model, products=[wall, door], relating_structure=level_1
    )


def _beam_stair_slab_chair(b: _Builder, spaces: dict[str, Any], materials: dict[str, Any]) -> None:
    model = b.model
    level_1, kitchen = spaces["level_1"], spaces["kitchen"]

    beam = b.entity(
        "IfcBeam", BEAM, "M_Timber:200x400mm:200x400mm:200201", Tag="200201", ObjectType="200x400mm"
    )
    ifcopenshell.api.material.assign_material(
        model, products=[beam], type="IfcMaterial", material=materials["Timber"]
    )
    b.pset(
        beam,
        "PSet_Revit_Constraints",
        {"Cross-Section Rotation": b.value("IfcPlaneAngleMeasure", 90.0)},
    )

    stair_name = "Stair:Residential - 180mm Riser:200301"
    stair = b.entity("IfcStair", STAIR, stair_name, Tag="200301", ShapeType="STRAIGHT_RUN_STAIR")
    flight = b.entity("IfcStairFlight", STAIR_FLIGHT, stair_name + ":1", Tag="200301")
    stringer = b.entity(
        "IfcMember", STAIR_STRINGER, stair_name + ":2", Tag="200301", ObjectType="stringer"
    )
    ifcopenshell.api.aggregate.assign_object(
        model, products=[flight, stringer], relating_object=stair
    )

    slab = b.entity(
        "IfcSlab", SLAB, "Floor:Generic 150mm:200401", Tag="200401", PredefinedType="FLOOR"
    )
    ifcopenshell.api.material.assign_material(
        model, products=[slab], type="IfcMaterial", material=materials["Concrete"]
    )
    # A deliberate defect: a negative area.
    b.pset(slab, "PSet_Revit_Dimensions", {"Area": b.value("IfcAreaMeasure", _ft2(-12.0))})

    ifcopenshell.api.spatial.assign_container(
        model, products=[beam, stair, slab], relating_structure=level_1
    )

    chair_type = b.entity("IfcFurnitureType", CHAIR_TYPE, "Chair-Breuer", Tag="200500")
    # Made up, in the OmniClass Table 23 format Revit writes.
    chair_type.HasPropertySets = [
        b.raw_pset(
            "Pset_FurnitureTypeCommon",
            [("Style", b.value("IfcLabel", "Cantilever"))],
        )
    ]
    chair = b.entity(
        "IfcFurnishingElement",
        CHAIR,
        "M_Chair-Breuer:Chair-Breuer:Chair-Breuer:200501",
        Tag="200501",
        ObjectType="Chair-Breuer",
    )
    ifcopenshell.api.type.assign_type(model, related_objects=[chair], relating_type=chair_type)
    b.pset(
        chair,
        "PSet_Revit_Type_Identity Data",
        {
            "OmniClass Number": b.value("IfcLabel", "23.40.20.11.17"),
            "OmniClass Title": b.value("IfcLabel", "Chairs"),
        },
    )
    ifcopenshell.api.spatial.assign_container(model, products=[chair], relating_structure=kitchen)


def _level_2(b: _Builder, spaces: dict[str, Any], materials: dict[str, Any]) -> None:
    model = b.model
    panel_type = b.entity(
        "IfcElectricApplianceType", PANEL_TYPE, "225 A", PredefinedType="NOTDEFINED"
    )
    panels = [
        b.entity(
            "IfcFlowTerminal",
            global_id,
            f"M_Panelboard:225 A:Unit {number} Panel:20060{number}",
            Tag=f"20060{number}",
            ObjectType="225 A",
        )
        for number, global_id in ((1, PANEL_1), (2, PANEL_2))
    ]
    ifcopenshell.api.type.assign_type(model, related_objects=panels, relating_type=panel_type)
    ifcopenshell.api.material.assign_material(
        model, products=panels, type="IfcMaterialList", material=materials["panel_list"]
    )

    uniformat = ifcopenshell.api.classification.add_classification(
        model, classification="UniFormat"
    )
    uniformat.Source = "http://www.csiorg.net/uniformat"
    uniformat.Edition = "1998"
    # The API also relates the system itself to the project, which IFC4 allows and
    # IFC2X3 does not (only references and notations may be associated). Undo that.
    for relation in model.by_type("IfcRelAssociatesClassification"):
        if relation.RelatingClassification == uniformat:
            model.remove(relation)
    ifcopenshell.api.classification.add_reference(
        model,
        products=[panel_type],
        identification="D5010120",
        name="Panelboards",
        classification=uniformat,
    )

    # Revit 2013: each panel has its own "Other" set for instance parameters, and both
    # share a second "Other" set for type parameters, which repeats a property name.
    for panel in panels:
        b.pset(panel, "Other", {"TagNumber": b.value("IfcLabel", "N/A")})
    shared_other = b.raw_pset(
        "Other",
        [
            ("Color", b.value("IfcLabel", "Grey")),
            ("Material", b.value("IfcLabel", "Steel - Painted")),
            ("Material", b.value("IfcLabel", "N/A")),
        ],
    )
    b.relate_pset(panels, shared_other)
    # Revit 2013 writes some dimension parameters as plain numbers.
    b.relate_pset(panels, b.raw_pset("Dimensions", [("Length", b.value("IfcReal", 2.0))]))

    proxy = b.entity("IfcBuildingElementProxy", PROXY, "Generic Model 1", Tag="200701")
    proxy_type = b.entity("IfcBuildingElementProxyType", gid("type_proxy"), "Generic")
    ifcopenshell.api.type.assign_type(model, related_objects=[proxy], relating_type=proxy_type)
    # A reference without a code, like most UniFormat references in the Revit 2013 files.
    ifcopenshell.api.classification.add_reference(
        model, products=[proxy_type], identification="", name="", classification=uniformat
    )

    ifcopenshell.api.spatial.assign_container(
        model, products=[*panels, proxy], relating_structure=spaces["level_2"]
    )


def build() -> ifcopenshell.file:
    """Build the fixture model in memory.

    The owner settings are process-wide, so they are restored afterwards; otherwise any
    later IFC2X3 file built with the API in the same process would get this file's user.
    """
    settings = ifcopenshell.api.owner.settings
    saved = (settings.get_user, settings.get_application)
    try:
        b = _Builder()
        spaces = _spatial(b)
        _spaces(b, spaces)
        materials = _materials(b)
        _wall_door_and_openings(b, spaces["level_1"], materials)
        _beam_stair_slab_chair(b, spaces, materials)
        _level_2(b, spaces, materials)
        return b.finish()
    finally:
        settings.get_user, settings.get_application = saved


def write(path: Path) -> None:
    """Build the fixture and write it to ``path``."""
    build().write(str(path))


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT
    write(target)
    print(f"wrote {target} ({target.stat().st_size:,} bytes)")  # noqa: T201
