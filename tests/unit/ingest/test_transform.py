"""The rules in transform.py, and transform() on hand-built raw models."""

from decimal import Decimal

import pytest
from raw_builders import FOOT, METRE, element, gid, model, spatial, value
from record_contract import violations

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.raw import RawMaterialEntry, RawReference
from ifc_sql_mcp.ingest.transform import (
    TransformError,
    authoring_app,
    base_class,
    is_feature,
    material_layers,
    parse_mvd,
    predefined_type,
    resolve_storey,
    transform,
)

# --------------------------------------------------------------------------- model header


@pytest.mark.parametrize(
    ("descriptions", "mvd"),
    [
        (("ViewDefinition [CoordinationView]",), "CoordinationView"),
        (
            ("ViewDefinition [CoordinationView, QuantityTakeOffAddOnView]",),
            "CoordinationView, QuantityTakeOffAddOnView",
        ),
        (("Exported by a tool", "ViewDefinition[ReferenceView_V1.2]"), "ReferenceView_V1.2"),
        (("ViewDefinition []",), None),
        (("",), None),
        ((), None),
    ],
)
def test_parse_mvd(descriptions: tuple[str, ...], mvd: str | None) -> None:
    assert parse_mvd(descriptions) == mvd


@pytest.mark.parametrize(
    ("name", "version", "app"),
    [
        ("Autodesk Revit Architecture 2011", "2011", "Autodesk Revit Architecture 2011"),
        ("IfcOpenShell", "0.9.0", "IfcOpenShell 0.9.0"),
        ("Exporter", None, "Exporter"),
        (None, "2011", None),
        ("  ", "1", None),
    ],
)
def test_authoring_app(name: str | None, version: str | None, app: str | None) -> None:
    assert authoring_app(name, version) == app


# --------------------------------------------------------------------------- objects


@pytest.mark.parametrize(
    ("ancestry", "base"),
    [
        (("IfcWallStandardCase", "IfcWall", "IfcBuildingElement"), "IfcWall"),
        (("IfcSlabElementedCase", "IfcSlab", "IfcBuildingElement"), "IfcSlab"),
        (
            ("IfcOpeningStandardCase", "IfcOpeningElement", "IfcFeatureElementSubtraction"),
            "IfcOpeningElement",
        ),
        (
            ("IfcBuildingElementProxy", "IfcBuildingElement", "IfcElement"),
            "IfcBuildingElementProxy",
        ),
        (("IfcFlowTerminal", "IfcDistributionFlowElement"), "IfcFlowTerminal"),
    ],
)
def test_base_class_skips_case_subtypes_only(ancestry: tuple[str, ...], base: str) -> None:
    assert base_class(ancestry[0], ancestry) == base


@pytest.mark.parametrize(
    ("ancestry", "feature"),
    [
        (("IfcOpeningStandardCase", "IfcOpeningElement", "IfcFeatureElementSubtraction",
          "IfcFeatureElement", "IfcElement"), True),
        (("IfcVoidingFeature", "IfcFeatureElementSubtraction", "IfcFeatureElement"), True),
        (("IfcProjectionElement", "IfcFeatureElementAddition", "IfcFeatureElement"), True),
        (("IfcWallStandardCase", "IfcWall", "IfcBuildingElement", "IfcElement"), False),
        # The decision follows the schema's inheritance, never the class's name.
        (("IfcOpeningElementLookalike", "IfcBuildingElementProxy", "IfcElement"), False),
    ],
)  # fmt: skip
def test_feature_elements_are_found_by_inheritance(
    ancestry: tuple[str, ...], feature: bool
) -> None:
    assert is_feature(ancestry) is feature


def test_a_feature_elements_name_parts_are_its_hosts() -> None:
    level = spatial("level", "IfcBuildingStorey")
    wall = element("wall", name="Basic Wall:Generic 200mm:1044048", tag="1044048")
    opening = element(
        "opening",
        "IfcOpeningElement",
        ancestry=("IfcOpeningElement", "IfcFeatureElementSubtraction", "IfcFeatureElement"),
        name="Basic Wall:Generic 200mm:1044048:1",
        host_global_id=wall.global_id,
    )

    records = {e.global_id: e for e in transform(model((level,), (wall, opening))).elements}

    host, feature = records[wall.global_id], records[opening.global_id]
    assert (host.is_feature, host.revit_family, host.revit_type) == (
        False,
        "Basic Wall",
        "Generic 200mm",
    )
    assert (host.host_revit_family, host.host_revit_type) == (None, None)
    assert (feature.is_feature, feature.revit_family, feature.revit_type) == (True, None, None)
    assert (feature.host_revit_family, feature.host_revit_type) == ("Basic Wall", "Generic 200mm")
    assert feature.revit_element_id == host.revit_element_id == 1044048


def test_base_class_without_ancestry_is_the_class_itself() -> None:
    assert base_class("IfcWallStandardCase", ()) == "IfcWallStandardCase"
    assert base_class("IfcFooStandardCase", ("IfcFooStandardCase",)) == "IfcFooStandardCase"


@pytest.mark.parametrize(
    ("occurrence", "type_value", "stored"),
    [
        ("FLOOR", None, "FLOOR"),
        ("FLOOR", "ROOF", "ROOF"),  # the type's value applies to its occurrences
        ("FLOOR", "NOTDEFINED", "FLOOR"),
        ("NOTDEFINED", None, None),  # NOTDEFINED means absent
        ("USERDEFINED", None, "USERDEFINED"),
        ("stringer", None, None),  # free text would break the column's format
        (None, None, None),
    ],
)
def test_predefined_type(
    occurrence: str | None, type_value: str | None, stored: str | None
) -> None:
    assert predefined_type(occurrence, type_value) == stored


def test_resolve_storey_walks_from_containers_and_hosts_to_a_storey() -> None:
    containers = {
        "wall": "level",
        "chair": "room",
        "opening": None,
        "flight": None,
        "proxy": "site",
    }
    hosts = {"opening": "wall", "flight": "stair", "stair": "wall"}
    parents = {"room": "level", "level": "building", "building": "site", "site": None}
    classes = {
        "room": "IfcSpace",
        "level": "IfcBuildingStorey",
        "building": "IfcBuilding",
        "site": "IfcSite",
    }

    def storey(global_id: str) -> str | None:
        return resolve_storey(global_id, containers, hosts, parents, classes)

    assert storey("wall") == "level"
    assert storey("chair") == "level"  # furniture in a room is on the room's storey
    assert storey("opening") == "level"  # an opening is on its host's storey
    assert storey("flight") == "level"  # part of a stair, which is hosted by the wall here
    assert storey("proxy") is None  # contained in the site: no storey above it


def test_resolve_storey_survives_cycles() -> None:
    hosts = {"a": "b", "b": "a"}
    assert resolve_storey("a", {}, hosts, {}, {}) is None
    parents = {"x": "y", "y": "x"}
    assert resolve_storey("e", {"e": "x"}, {}, parents, {"x": "IfcSpace"}) is None


def test_material_layers_convert_thickness_and_keep_positions() -> None:
    entries = (
        RawMaterialEntry(1, "Plasterboard", 0.0525),
        RawMaterialEntry(2, None, 0.1),  # an air gap: no material
        RawMaterialEntry(3, "Plasterboard", 0.0525),
    )
    records, issues = material_layers(gid("wall"), entries, FOOT)
    assert [(r.layer_index, r.material_name) for r in records] == [
        (1, "Plasterboard"),
        (3, "Plasterboard"),
    ]
    assert records[0].thickness_m == pytest.approx(0.016002)
    assert [issue.code for issue in issues] == [IssueCode.UNNAMED_MATERIAL]


@pytest.mark.parametrize("thickness", [-0.01, float("nan")])
def test_an_impossible_thickness_is_left_null(thickness: float) -> None:
    records, issues = material_layers(
        gid("wall"), (RawMaterialEntry(1, "Brick", thickness),), METRE
    )
    assert [r.thickness_m for r in records] == [None]
    assert [issue.code for issue in issues] == [IssueCode.INVALID_LAYER_THICKNESS]


def test_single_materials_and_lists_have_no_thickness() -> None:
    records, issues = material_layers(
        gid("beam"), (RawMaterialEntry(1, "Steel", None), RawMaterialEntry(2, " ", None)), METRE
    )
    assert [(r.material_name, r.thickness_m) for r in records] == [("Steel", None)]
    assert [issue.code for issue in issues] == [IssueCode.UNNAMED_MATERIAL]


# --------------------------------------------------------------------------- whole model


def codes(issues: tuple[Issue, ...]) -> list[str]:
    return [issue.code.value for issue in issues]


def test_transform_rejects_a_file_without_an_ifc_schema() -> None:
    with pytest.raises(TransformError, match="unrecognised IFC schema"):
        transform(model(schema="STEP42"))


def test_transform_normalises_the_schema_name_and_reads_the_header() -> None:
    records = transform(model(schema="ifc2x3"))
    assert records.model.ifc_schema == "IFC2X3"
    assert records.model.mvd == "CoordinationView"
    assert records.model.authoring_app == "Autodesk Revit 2013"
    assert records.model.length_unit == "m"


def test_a_malformed_project_global_id_becomes_null() -> None:
    records = transform(model(project_global_id="not-a-guid"))
    assert records.model.project_global_id is None
    assert codes(records.issues) == ["invalid_project_global_id"]


def test_missing_units_are_assumed_si() -> None:
    records = transform(model(units=()))
    assert records.model.length_unit == "m"
    assert codes(records.issues) == ["unit_assumed_si"] * 4


def test_objects_with_bad_or_repeated_global_ids_are_skipped_with_their_references() -> None:
    level = spatial("level", "IfcBuildingStorey", elevation=0.0)
    bad_room = spatial("room", global_id="9-not-valid", parent_global_id=level.global_id)
    room_copy = spatial("level", "IfcSpace", parent_global_id=level.global_id)  # same id
    wall = element("wall", container_global_id="9-not-valid")
    opening = element("opening", "IfcOpeningElement", host_global_id="9-not-valid")

    records = transform(model((level, bad_room, room_copy), (wall, opening)))

    assert [o.global_id for o in records.objects] == sorted(
        [level.global_id, wall.global_id, opening.global_id]
    )
    assert codes(records.issues) == ["invalid_global_id", "duplicate_global_id"]
    assert {e.container_global_id for e in records.elements} == {None}
    assert violations(records) == []


def test_a_self_parent_is_dropped() -> None:
    room = spatial("room", parent_global_id=gid("room"))
    assert transform(model((room,))).spatial_elements[0].parent_global_id is None


def test_storey_elevations_are_converted_and_rounded() -> None:
    level = spatial("level", "IfcBuildingStorey", elevation=10.1706036745419)
    too_high = spatial("sky", "IfcBuildingStorey", elevation=1e9)

    records = transform(model((level, too_high), units=(FOOT,)))

    elevations = {s.global_id: s.elevation_m for s in records.spatial_elements}
    assert elevations == {level.global_id: Decimal("3.100"), too_high.global_id: None}
    assert "invalid_elevation" in codes(records.issues)


def test_a_small_building_end_to_end() -> None:
    level = spatial("level", "IfcBuildingStorey", name="Level 1", elevation=0.0)
    room = spatial(
        "room",
        long_name="Kitchen",
        parent_global_id=level.global_id,
        values=(
            value(
                "PSet_Revit_Identity Data", "OmniClass Table 13 Category", "13-51 24 11: Kitchen"
            ),
            value("PSet_Revit_Dimensions", "Area", 12.0, "IfcAreaMeasure"),
        ),
    )
    wall = element(
        "wall",
        "IfcWallStandardCase",
        ancestry=("IfcWallStandardCase", "IfcWall", "IfcBuildingElement"),
        name="Basic Wall:Generic 200mm:1001",
        tag="1001",
        container_global_id=level.global_id,
        materials=(RawMaterialEntry(1, "Concrete", 0.2),),
        values=(value("PSet_Revit_Dimensions", "Length", 4.0, "IfcLengthMeasure"),),
    )
    chair = element(
        "chair",
        "IfcFurnishingElement",
        name="M_Chair:Chair:Chair:1002",
        tag="1002",
        container_global_id=room.global_id,
        type_name="Chair",
        references=(RawReference("UniFormat", "E2020", "Furnishings"),),
    )

    records = transform(model((level, room), (wall, chair)))

    assert violations(records) == []
    assert records.issues == ()
    by_id = {e.global_id: e for e in records.elements}
    assert by_id[wall.global_id].revit_family == "Basic Wall"
    assert by_id[chair.global_id].storey_global_id == level.global_id
    assert by_id[chair.global_id].container_global_id == room.global_id
    assert {o.global_id: o.ifc_class_base for o in records.objects}[wall.global_id] == "IfcWall"
    assert [(q.global_id, q.name) for q in records.quantities] == sorted(
        [(room.global_id, "area_m2"), (wall.global_id, "length_m")]
    )
    assert {(c.global_id, c.system, c.code) for c in records.classifications} == {
        (room.global_id, "OmniClass Table 13", "13-51 24 11"),
        (chair.global_id, "UniFormat", "E2020"),
    }
    assert [m.name for m in records.materials] == ["Concrete"]
    assert records.material_layers[0].thickness_m == pytest.approx(0.2)


def test_transform_is_deterministic() -> None:
    raw = model(
        (spatial("level", "IfcBuildingStorey"),),
        (
            element("b", values=(value("Other", "Material", "A"), value("Other", "Material", "B"))),
            element("a", name="Family:Type:7", tag="8"),
        ),
    )
    assert transform(raw) == transform(raw)
