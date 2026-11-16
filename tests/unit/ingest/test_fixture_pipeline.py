"""Extract and transform the synthetic fixture: each Revit quirk ends up where it should.

``tests/fixtures/make_revit_quirks.py`` documents the building. Values are written in
feet there and checked in metres here.
"""

import hashlib
import math
from decimal import Decimal

import make_revit_quirks as fx
import pytest
from record_contract import violations

from ifc_sql_mcp.ingest.raw import RawElement, RawModel, RawObject
from ifc_sql_mcp.ingest.records import ModelRecords, PropertyRecord


def raw_object(raw: RawModel, global_id: str) -> RawObject:
    return next(o for o in (*raw.spatial_elements, *raw.elements) if o.global_id == global_id)


def raw_element(raw: RawModel, global_id: str) -> RawElement:
    obj = raw_object(raw, global_id)
    assert isinstance(obj, RawElement)
    return obj


def props(records: ModelRecords, global_id: str) -> dict[tuple[str, str], PropertyRecord]:
    return {(p.pset_name, p.prop_name): p for p in records.properties if p.global_id == global_id}


# --------------------------------------------------------------------------- extract


def test_file_facts_come_from_the_bytes_and_the_header(fixture_raw: RawModel) -> None:
    data = fx.OUTPUT.read_bytes()
    assert fixture_raw.file.sha256 == hashlib.sha256(data).hexdigest()
    assert fixture_raw.file.size_bytes == len(data)
    assert fixture_raw.file.schema == "IFC2X3"
    assert fixture_raw.file.descriptions == ("ViewDefinition [CoordinationView]",)
    assert fixture_raw.project.application_name == "ifc-sql-mcp fixture generator"
    assert fixture_raw.issues == ()


def test_units_are_read_with_their_si_factor(fixture_raw: RawModel) -> None:
    by_type = {unit.unit_type: unit for unit in fixture_raw.units}
    assert (by_type["LENGTHUNIT"].name, by_type["LENGTHUNIT"].is_si) == ("FOOT", False)
    assert by_type["LENGTHUNIT"].scale_to_si == pytest.approx(0.3048)
    assert by_type["VOLUMEUNIT"].scale_to_si == pytest.approx(0.3048**3)
    assert by_type["PLANEANGLEUNIT"].scale_to_si == pytest.approx(math.pi / 180)
    assert by_type["TIMEUNIT"].is_si


def test_the_class_ancestry_comes_from_the_schema(fixture_raw: RawModel) -> None:
    wall = raw_object(fixture_raw, fx.WALL)
    assert wall.ancestry[:4] == (
        "IfcWallStandardCase",
        "IfcWall",
        "IfcBuildingElement",
        "IfcElement",
    )
    assert wall.ancestry[-1] == "IfcRoot"


def test_type_values_are_flagged_and_the_empty_lining_set_is_skipped(fixture_raw: RawModel) -> None:
    door = raw_object(fixture_raw, fx.DOOR)
    sets = {(v.set_name, v.name, v.from_type) for v in door.values}
    assert sets == {
        ("Pset_DoorCommon", "FireRating", True),
        ("Pset_DoorCommon", "IsExternal", True),
        ("Pset_DoorCommon", "IsExternal", False),
    }


def test_quantity_set_values_are_marked(fixture_raw: RawModel) -> None:
    kitchen = raw_object(fixture_raw, fx.KITCHEN)
    gsa = [v for v in kitchen.values if v.from_quantity_set]
    assert [(v.set_name, v.name, v.value_type) for v in gsa] == [
        ("GSA Space Areas", "GSA BIM Area", "IfcAreaMeasure")
    ]


def test_same_named_sets_stay_apart_until_the_transform(fixture_raw: RawModel) -> None:
    panel = raw_object(fixture_raw, fx.PANEL_1)
    other = [v for v in panel.values if v.set_name == "Other"]
    assert len({v.order[0] for v in other}) == 2  # two IfcPropertySets called "Other"
    assert sorted(v.value for v in other if v.name == "Material") == ["N/A", "Steel - Painted"]


def test_hosts_containers_and_types(fixture_raw: RawModel) -> None:
    assert raw_element(fixture_raw, fx.DOOR_OPENING).host_global_id == fx.WALL  # voids
    assert raw_element(fixture_raw, fx.STAIR_FLIGHT).host_global_id == fx.STAIR  # aggregates
    assert raw_element(fixture_raw, fx.STAIR_FLIGHT).container_global_id is None
    assert raw_element(fixture_raw, fx.CHAIR).container_global_id == fx.KITCHEN
    assert raw_element(fixture_raw, fx.DOOR).type_name == "0915 x 2134mm"
    assert raw_element(fixture_raw, fx.SLAB).predefined_type == "FLOOR"
    assert raw_element(fixture_raw, fx.PANEL_1).type_predefined_type == "NOTDEFINED"


def test_references_are_inherited_from_the_type(fixture_raw: RawModel) -> None:
    for panel in (fx.PANEL_1, fx.PANEL_2):
        references = raw_object(fixture_raw, panel).references
        assert [(r.system, r.code, r.title) for r in references] == [
            ("UniFormat", "D5010120", "Panelboards")
        ]


def test_material_definitions_become_ordered_entries(fixture_raw: RawModel) -> None:
    wall = raw_element(fixture_raw, fx.WALL)
    assert [(m.position, m.material_name) for m in wall.materials] == [
        (1, "Plasterboard"),
        (2, "Metal Stud"),
        (3, "Plasterboard"),
    ]
    assert wall.materials[1].thickness == pytest.approx(fx.STUD_M / fx.FOOT)
    beam = raw_element(fixture_raw, fx.BEAM)
    assert [(m.material_name, m.thickness) for m in beam.materials] == [("Timber", None)]
    panel = raw_element(fixture_raw, fx.PANEL_1)
    assert [(m.material_name, m.thickness) for m in panel.materials] == [
        ("Steel", None),
        ("Paint", None),
    ]


# --------------------------------------------------------------------------- transform


def test_the_records_satisfy_every_schema_rule(fixture_records: ModelRecords) -> None:
    assert violations(fixture_records) == []


def test_model_record(fixture_records: ModelRecords) -> None:
    model = fixture_records.model
    assert (model.ifc_schema, model.mvd, model.length_unit) == ("IFC2X3", "CoordinationView", "ft")
    assert (model.project_global_id, model.project_name, model.project_long_name) == (
        fx.PROJECT,
        "0042",
        "Fixture House",
    )
    assert model.authoring_app == "ifc-sql-mcp fixture generator 1"


def test_every_object_is_loaded_once(fixture_records: ModelRecords) -> None:
    kinds = {o.global_id: o.object_kind for o in fixture_records.objects}
    assert len(kinds) == 20
    assert sum(kind == "spatial_element" for kind in kinds.values()) == 7
    assert {o.global_id: o.ifc_class_base for o in fixture_records.objects}[fx.WALL] == "IfcWall"


def test_storey_elevations_are_metres_rounded_to_the_millimetre(
    fixture_records: ModelRecords,
) -> None:
    elevations = {s.global_id: s.elevation_m for s in fixture_records.spatial_elements}
    assert elevations[fx.LEVEL_1] == Decimal("0.000")
    assert elevations[fx.LEVEL_2] == Decimal("3.000")
    assert elevations[fx.FOUNDATION] == Decimal("-2.000")
    assert elevations[fx.KITCHEN] is None


def test_the_spatial_hierarchy(fixture_records: ModelRecords) -> None:
    parents = {s.global_id: s.parent_global_id for s in fixture_records.spatial_elements}
    assert parents[fx.SITE] is None
    assert parents[fx.BUILDING] == fx.SITE
    assert parents[fx.LEVEL_2] == fx.BUILDING
    assert parents[fx.KITCHEN] == fx.LEVEL_1


@pytest.mark.parametrize(
    ("global_id", "family", "revit_type", "element_id"),
    [
        (fx.WALL, "Basic Wall", "Interior - Stud Partition", 200001),
        (fx.DOOR, "M_Single-Flush", "0915 x 2134mm", 200101),
        (fx.DOOR_OPENING, "M_Single-Flush", "0915 x 2134mm", 200101),  # host's id + ":1"
        (fx.STAIR_STRINGER, "Stair", "Residential - 180mm Riser", 200301),  # ":2", not 2
        (fx.PANEL_1, "M_Panelboard", "225 A", 200601),  # label "Unit 1 Panel" is not the type
        (fx.UNNAMED_OPENING, None, None, None),
        (fx.PROXY, None, None, None),  # "Generic Model 1" is no Revit name
    ],
)
def test_revit_names_are_parsed(
    fixture_records: ModelRecords,
    global_id: str,
    family: str | None,
    revit_type: str | None,
    element_id: int | None,
) -> None:
    element = next(e for e in fixture_records.elements if e.global_id == global_id)
    assert (element.revit_family, element.revit_type, element.revit_element_id) == (
        family,
        revit_type,
        element_id,
    )


def test_containers_and_storeys(fixture_records: ModelRecords) -> None:
    elements = {e.global_id: e for e in fixture_records.elements}
    assert (elements[fx.CHAIR].container_global_id, elements[fx.CHAIR].storey_global_id) == (
        fx.KITCHEN,
        fx.LEVEL_1,
    )
    for part in (fx.DOOR_OPENING, fx.UNNAMED_OPENING, fx.STAIR_FLIGHT, fx.STAIR_STRINGER):
        assert (elements[part].container_global_id, elements[part].storey_global_id) == (
            None,
            fx.LEVEL_1,
        )
    assert elements[fx.PANEL_1].storey_global_id == fx.LEVEL_2


def test_predefined_types(fixture_records: ModelRecords) -> None:
    elements = {e.global_id: e for e in fixture_records.elements}
    assert elements[fx.SLAB].predefined_type == "FLOOR"
    assert elements[fx.PANEL_1].predefined_type is None  # the type says NOTDEFINED


def test_door_type_values_are_inherited_and_overridden(fixture_records: ModelRecords) -> None:
    door = props(fixture_records, fx.DOOR)
    assert door[("Pset_DoorCommon", "FireRating")].is_type_property
    is_external = door[("Pset_DoorCommon", "IsExternal")]
    assert (is_external.value_bool, is_external.is_type_property) == (False, False)
    # The lining set named after the door produces nothing, and nothing is called "id".
    assert {pset for pset, _ in door} == {"Pset_DoorCommon"}
    assert all(p.prop_name != "id" for p in fixture_records.properties)


def test_revit_type_sets_on_instances_and_type_object_sets(fixture_records: ModelRecords) -> None:
    width = props(fixture_records, fx.WALL)[("PSet_Revit_Type_Construction", "Width")]
    assert (width.is_type_property, width.unit) == (True, "m")
    assert width.value_num == pytest.approx(fx.WALL_WIDTH_M)
    chair = props(fixture_records, fx.CHAIR)
    assert chair[("Pset_FurnitureTypeCommon", "Style")].is_type_property  # from IfcFurnitureType


def test_placeholders_and_empty_values_are_kept_verbatim(fixture_records: ModelRecords) -> None:
    wall = props(fixture_records, fx.WALL)
    assert wall[("PSet_Revit_Other", "BarCode")].value_text == "BarCode"
    assert wall[("Pset_WallCommon", "Reference")].value_text == ""
    kitchen = props(fixture_records, fx.KITCHEN)
    assert kitchen[("Pset_SpaceCommon", "CeilingCovering")].value_text == "CeilingCovering"
    assert kitchen[("Pset_SpaceCommon", "Reference")].value_type == "IfcIdentifier"


def test_measures_are_converted_to_si(fixture_records: ModelRecords) -> None:
    top_offset = props(fixture_records, fx.WALL)[("PSet_Revit_Constraints", "Top Offset")]
    assert (top_offset.value_num, top_offset.unit) == (pytest.approx(-0.3), "m")
    rotation = props(fixture_records, fx.BEAM)[("PSet_Revit_Constraints", "Cross-Section Rotation")]
    assert (rotation.value_num, rotation.unit) == (pytest.approx(math.pi / 2), "rad")


def test_revit_2013_duplicate_sets_merge_to_one_value_per_key(
    fixture_records: ModelRecords,
) -> None:
    panel = props(fixture_records, fx.PANEL_1)
    assert {name for pset, name in panel if pset == "Other"} == {"Color", "Material", "TagNumber"}
    assert panel[("Other", "Material")].value_text == "Steel - Painted"  # first in the file


def test_quantities(fixture_records: ModelRecords) -> None:
    found = {(q.global_id, q.name, q.source): q for q in fixture_records.quantities}
    expected = {
        (fx.KITCHEN, "area_m2", "ifc_qto"): fx.KITCHEN_AREA_M2,
        (fx.KITCHEN, "area_m2", "revit_pset"): fx.KITCHEN_AREA_M2,
        (fx.KITCHEN, "perimeter_m", "revit_pset"): fx.KITCHEN_PERIMETER_M,
        (fx.KITCHEN, "height_m", "revit_pset"): fx.KITCHEN_HEIGHT_M,
        (fx.KITCHEN, "volume_m3", "revit_pset"): fx.KITCHEN_VOLUME_M3,
        (fx.WALL, "length_m", "revit_pset"): fx.WALL_LENGTH_M,
        (fx.WALL, "area_m2", "revit_pset"): fx.WALL_AREA_M2,
        (fx.WALL, "volume_m3", "revit_pset"): fx.WALL_VOLUME_M3,
    }
    assert found.keys() == expected.keys()  # the slab's negative area and the IfcReal are not
    for key, metres in expected.items():
        assert found[key].value == pytest.approx(metres)
    assert found[(fx.KITCHEN, "area_m2", "ifc_qto")].source_ref == "GSA Space Areas.GSA BIM Area"


def test_every_quantity_points_at_a_property_row(fixture_records: ModelRecords) -> None:
    rows = {(p.global_id, f"{p.pset_name}.{p.prop_name}") for p in fixture_records.properties}
    assert {(q.global_id, q.source_ref) for q in fixture_records.quantities} <= rows


def test_materials_and_layers(fixture_records: ModelRecords) -> None:
    assert [m.name for m in fixture_records.materials] == [
        "Concrete",
        "Metal Stud",
        "Paint",
        "Plasterboard",
        "Steel",
        "Timber",
    ]
    wall = [
        layer for layer in fixture_records.material_layers if layer.element_global_id == fx.WALL
    ]
    assert [(layer.layer_index, layer.material_name) for layer in wall] == [
        (1, "Plasterboard"),
        (2, "Metal Stud"),
        (3, "Plasterboard"),
    ]
    assert [layer.thickness_m for layer in wall] == pytest.approx(
        [fx.PLASTERBOARD_M, fx.STUD_M, fx.PLASTERBOARD_M]
    )
    others = {
        (layer.element_global_id, layer.layer_index): (layer.material_name, layer.thickness_m)
        for layer in fixture_records.material_layers
        if layer.element_global_id != fx.WALL
    }
    assert others[(fx.BEAM, 1)] == ("Timber", None)
    assert others[(fx.PANEL_2, 2)] == ("Paint", None)


def test_classifications(fixture_records: ModelRecords) -> None:
    assert {(c.global_id, c.system, c.code, c.title) for c in fixture_records.classifications} == {
        (fx.KITCHEN, "OmniClass Table 13", "13-51 24 17", "Kitchen Space"),
        (fx.CHAIR, "OmniClass Table 23", "23.40.20.11.17", "Chairs"),
        (fx.PANEL_1, "UniFormat", "D5010120", "Panelboards"),
        (fx.PANEL_2, "UniFormat", "D5010120", "Panelboards"),
    }


def test_issues_name_each_deliberate_defect(fixture_records: ModelRecords) -> None:
    assert sorted((i.code.value, i.global_id) for i in fixture_records.issues) == sorted(
        [
            ("omniclass_unparsed", fx.STORE),
            ("negative_quantity", fx.SLAB),
            ("conflicting_property", fx.PANEL_1),
            ("conflicting_property", fx.PANEL_2),
            ("quantity_not_a_measure", fx.PANEL_1),
            ("quantity_not_a_measure", fx.PANEL_2),
            ("incomplete_classification_reference", fx.PROXY),
            ("revit_name_unparsed", fx.PROXY),
        ]
    )
