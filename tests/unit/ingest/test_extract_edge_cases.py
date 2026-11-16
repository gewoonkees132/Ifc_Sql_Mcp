"""Extraction paths the Revit fixture does not take, on tiny files built in each test.

The files are IFC4 unless stated, which also covers the IFC4 spellings (Identification
instead of ItemReference, IfcPropertySetDefinitionSet, IfcRelNests).
"""

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.guid
import pytest

from ifc_sql_mcp.ingest.extract import ExtractError, read_model, sha256_file
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.raw import RawElement, RawModel


class Tiny:
    """A minimal valid-enough model: a project with metre units and one storey."""

    def __init__(self, schema: str = "IFC4") -> None:
        self.model = ifcopenshell.file(schema=schema)
        metre = self.model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
        self.units = self.model.create_entity("IfcUnitAssignment", Units=[metre])
        self.project = self.root("IfcProject", Name="P", UnitsInContext=self.units)
        self.storey = self.root("IfcBuildingStorey", Name="Level 1", Elevation=0.0)

    def root(self, ifc_class: str, **attributes: Any) -> Any:
        return self.model.create_entity(ifc_class, GlobalId=ifcopenshell.guid.new(), **attributes)

    def wall(self, **attributes: Any) -> Any:
        wall = self.root("IfcWall", Name="Basic Wall:Generic:1", Tag="1", **attributes)
        self.root(
            "IfcRelContainedInSpatialStructure",
            RelatedElements=[wall],
            RelatingStructure=self.storey,
        )
        return wall

    def pset(self, product: Any, name: str, *properties: Any) -> Any:
        pset = self.root("IfcPropertySet", Name=name, HasProperties=list(properties))
        self.root(
            "IfcRelDefinesByProperties", RelatedObjects=[product], RelatingPropertyDefinition=pset
        )
        return pset

    def single(self, name: str, value: Any) -> Any:
        return self.model.create_entity("IfcPropertySingleValue", Name=name, NominalValue=value)

    def read(self, tmp_path: Path) -> RawModel:
        path = tmp_path / "tiny.ifc"
        self.model.write(str(path))
        return read_model(path)


def only_element(raw: RawModel) -> RawElement:
    (element,) = raw.elements
    return element


def codes(raw: RawModel) -> list[IssueCode]:
    return [issue.code for issue in raw.issues]


# --------------------------------------------------------------------------- files


def test_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_model(tmp_path / "nope.ifc")


def test_a_file_that_is_not_ifc_raises_extract_error(tmp_path: Path) -> None:
    path = tmp_path / "notes.ifc"
    path.write_text("not an IFC file\n")
    with pytest.raises(ExtractError, match="not a readable IFC file"):
        read_model(path)


def test_an_empty_file_raises_extract_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.ifc"
    path.write_bytes(b"")

    with pytest.raises(ExtractError, match="empty"):
        read_model(path)


def test_a_truncated_file_raises_extract_error_instead_of_loading_part_of_it(
    tmp_path: Path,
) -> None:
    """IfcOpenShell itself reads the first half of a file without complaint."""
    data = Tiny().model.to_string().encode()
    path = tmp_path / "truncated.ifc"
    path.write_bytes(data[: len(data) // 2])

    with pytest.raises(ExtractError, match="truncated"):
        read_model(path)


def test_whitespace_after_the_closing_keyword_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "trailing.ifc"
    path.write_bytes(Tiny().model.to_string().encode() + b"\r\n\r\n")

    assert read_model(path).project.name == "P"


def test_a_file_without_a_project_raises_extract_error(tmp_path: Path) -> None:
    model = ifcopenshell.file(schema="IFC4")
    model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new())
    path = tmp_path / "no_project.ifc"
    model.write(str(path))
    with pytest.raises(ExtractError, match="no IfcProject"):
        read_model(path)


def test_sha256_file_matches_hashlib_across_read_chunks(tmp_path: Path) -> None:
    data = bytes(range(256)) * 12_000  # about 3 MB: several 1 MiB chunks
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- project and units


def test_several_projects_are_reported_and_the_first_is_used(tmp_path: Path) -> None:
    tiny = Tiny()
    tiny.root("IfcProject", Name="Second")
    raw = tiny.read(tmp_path)
    assert raw.project.name == "P"
    assert codes(raw) == [IssueCode.MULTIPLE_PROJECTS]


def test_the_application_falls_back_to_the_first_in_the_file(tmp_path: Path) -> None:
    tiny = Tiny()
    developer = tiny.model.create_entity("IfcOrganization", Name="Dev")
    tiny.model.create_entity(
        "IfcApplication",
        ApplicationDeveloper=developer,
        Version="7",
        ApplicationFullName="Exporter",
        ApplicationIdentifier="x",
    )
    raw = tiny.read(tmp_path)
    assert (raw.project.application_name, raw.project.application_version) == ("Exporter", "7")


def test_no_application_and_no_units(tmp_path: Path) -> None:
    tiny = Tiny()
    tiny.project.UnitsInContext = None
    raw = tiny.read(tmp_path)
    assert raw.project.application_name is None
    assert raw.units == ()


def test_a_storey_without_elevation(tmp_path: Path) -> None:
    tiny = Tiny()
    tiny.storey.Elevation = None
    (storey,) = tiny.read(tmp_path).spatial_elements
    assert storey.elevation is None


def test_derived_and_context_dependent_units(tmp_path: Path) -> None:
    tiny = Tiny()
    exponents = tiny.model.create_entity("IfcDimensionalExponents", 1, 0, 0, 0, 0, 0, 0)
    step = tiny.model.create_entity(
        "IfcContextDependentUnit", Dimensions=exponents, UnitType="LENGTHUNIT", Name="STEP"
    )
    derived = tiny.model.create_entity("IfcDerivedUnit", Elements=[], UnitType="USERDEFINED")
    tiny.units.Units = [step, derived]
    raw = tiny.read(tmp_path)
    assert [(u.name, u.scale_to_si) for u in raw.units] == [("STEP", None)]


# --------------------------------------------------------------------------- property values


def test_property_kinds_other_than_single_values_are_reported(tmp_path: Path) -> None:
    tiny = Tiny()
    wall = tiny.wall()
    label = tiny.model.create_entity("IfcLabel", "A")
    tiny.pset(
        wall,
        "Pset_Test",
        tiny.single("Kept", tiny.model.create_entity("IfcLabel", "yes")),
        tiny.single("Empty", None),
        tiny.model.create_entity(
            "IfcPropertyEnumeratedValue", Name="Choice", EnumerationValues=[label]
        ),
        tiny.single("Complex", tiny.model.create_entity("IfcComplexNumber", (1.0, 2.0))),
    )
    raw = tiny.read(tmp_path)
    values = {v.name: (v.value_type, v.value) for v in only_element(raw).values}
    assert values == {"Kept": ("IfcLabel", "yes"), "Empty": (None, None)}
    assert codes(raw) == [IssueCode.UNSUPPORTED_PROPERTY] * 2


def test_quantities_keep_their_measure_type_and_own_unit(tmp_path: Path) -> None:
    tiny = Tiny()
    wall = tiny.wall()
    millimetre = tiny.model.create_entity(
        "IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE"
    )
    quantities = [
        tiny.model.create_entity(
            "IfcQuantityLength", Name="Length", Unit=millimetre, LengthValue=4000.0
        ),
        tiny.model.create_entity("IfcQuantityCount", Name="Count", CountValue=3),
        tiny.model.create_entity("IfcQuantityArea", Name="Area"),  # invalid: no value
        tiny.model.create_entity(
            "IfcPhysicalComplexQuantity", Name="Layers", HasQuantities=[], Discrimination="x"
        ),
    ]
    qto = tiny.root("IfcElementQuantity", Name="Qto_WallBaseQuantities", Quantities=quantities)
    tiny.root("IfcRelDefinesByProperties", RelatedObjects=[wall], RelatingPropertyDefinition=qto)
    raw = tiny.read(tmp_path)
    values = {v.name: v for v in only_element(raw).values}
    assert values["Length"].value_type == "IfcLengthMeasure"
    assert values["Length"].unit is not None
    assert values["Length"].unit.scale_to_si == pytest.approx(0.001)
    assert values["Count"].value_type == "IfcCountMeasure"
    assert all(v.from_quantity_set for v in values.values())
    assert "Area" not in values
    assert codes(raw) == [IssueCode.UNSUPPORTED_PROPERTY] * 2


# IfcOpenShell 0.9.0 crashes when an IfcPropertySetDefinitionSet is built with
# create_entity, but parses one from a file, so this file is written as STEP text.
DEFINITION_SET_FILE = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('set.ifc','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#2=IFCUNITASSIGNMENT((#1));
#3=IFCPROJECT('0project______________',$,'P',$,$,$,$,$,#2);
#4=IFCWALL('0wall_________________',$,'Basic Wall:Generic:1',$,$,$,$,'1',$);
#5=IFCPROPERTYSINGLEVALUE('x',$,IFCLABEL('1'),$);
#6=IFCPROPERTYSET('0pset_a_______________',$,'A',$,(#5));
#7=IFCPROPERTYSINGLEVALUE('y',$,IFCLABEL('2'),$);
#8=IFCPROPERTYSET('0pset_b_______________',$,'B',$,(#7));
#9=IFCRELDEFINESBYPROPERTIES('0rel__________________',$,$,$,(#4),IFCPROPERTYSETDEFINITIONSET((#6,#8)));
ENDSEC;
END-ISO-10303-21;
"""


def test_a_property_set_definition_set_is_unpacked(tmp_path: Path) -> None:
    path = tmp_path / "set.ifc"
    path.write_text(DEFINITION_SET_FILE)
    raw = read_model(path)
    assert {(v.set_name, v.name, v.order) for v in only_element(raw).values} == {
        ("A", "x", (6, 5)),
        ("B", "y", (8, 7)),
    }


def test_a_predefined_property_set_with_data_is_reported(tmp_path: Path) -> None:
    tiny = Tiny()
    door = tiny.root("IfcDoor", Name="Door:Type:2", Tag="2")
    door_type = tiny.root(
        "IfcDoorType", Name="Type", PredefinedType="DOOR", OperationType="SINGLE_SWING_LEFT"
    )
    empty = tiny.root("IfcDoorLiningProperties", Name="empty")
    filled = tiny.root("IfcDoorLiningProperties", Name="filled", LiningDepth=0.1)
    door_type.HasPropertySets = [empty, filled]
    tiny.root("IfcRelDefinesByType", RelatedObjects=[door], RelatingType=door_type)
    raw = tiny.read(tmp_path)
    assert only_element(raw).values == ()
    assert codes(raw) == [IssueCode.UNSUPPORTED_PROPERTY_SET]
    assert "filled" in raw.issues[0].message


# --------------------------------------------------------------------------- relations


def test_ifc4_references_and_unsupported_classifications(tmp_path: Path) -> None:
    tiny = Tiny()
    wall = tiny.wall()
    system = tiny.model.create_entity("IfcClassification", Name="Uniclass")
    reference = tiny.model.create_entity(
        "IfcClassificationReference", Identification="Pr_20", Name="Walls", ReferencedSource=system
    )
    orphan = tiny.model.create_entity("IfcClassificationReference", Identification="X1")
    for classification in (reference, orphan, system):
        tiny.root(
            "IfcRelAssociatesClassification",
            RelatedObjects=[wall],
            RelatingClassification=classification,
        )
    raw = tiny.read(tmp_path)
    assert [(r.system, r.code, r.title) for r in only_element(raw).references] == [
        ("Uniclass", "Pr_20", "Walls"),
        (None, "X1", None),
    ]
    assert codes(raw) == [IssueCode.UNSUPPORTED_CLASSIFICATION]


def test_nested_parts_take_their_host(tmp_path: Path) -> None:
    tiny = Tiny()
    wall = tiny.wall()
    socket = tiny.root("IfcOutlet", Name="Socket:Type:3", Tag="3")
    tiny.root("IfcRelNests", RelatingObject=wall, RelatedObjects=[socket])
    raw = tiny.read(tmp_path)
    hosts = {e.name: e.host_global_id for e in raw.elements}
    assert hosts["Socket:Type:3"] == wall.GlobalId


MaterialSetup = Callable[[Tiny, Any], None]


def _layer(tiny: Tiny, wall: Any) -> None:
    brick = tiny.model.create_entity("IfcMaterial", Name="Brick")
    layer = tiny.model.create_entity("IfcMaterialLayer", Material=brick, LayerThickness=0.1)
    tiny.root("IfcRelAssociatesMaterial", RelatedObjects=[wall], RelatingMaterial=layer)


def _inherited(tiny: Tiny, wall: Any) -> None:
    wall_type = tiny.root("IfcWallType", Name="Generic", PredefinedType="STANDARD")
    tiny.root("IfcRelDefinesByType", RelatedObjects=[wall], RelatingType=wall_type)
    steel = tiny.model.create_entity("IfcMaterial", Name="Steel")
    tiny.root("IfcRelAssociatesMaterial", RelatedObjects=[wall_type], RelatingMaterial=steel)


def _two(tiny: Tiny, wall: Any) -> None:
    for name in ("First", "Second"):
        material = tiny.model.create_entity("IfcMaterial", Name=name)
        tiny.root("IfcRelAssociatesMaterial", RelatedObjects=[wall], RelatingMaterial=material)


def _constituents(tiny: Tiny, wall: Any) -> None:
    steel = tiny.model.create_entity("IfcMaterial", Name="Steel")
    constituent = tiny.model.create_entity("IfcMaterialConstituent", Material=steel)
    constituents = tiny.model.create_entity(
        "IfcMaterialConstituentSet", MaterialConstituents=[constituent]
    )
    tiny.root("IfcRelAssociatesMaterial", RelatedObjects=[wall], RelatingMaterial=constituents)


@pytest.mark.parametrize(
    ("setup", "entries", "issue_codes"),
    [
        (_layer, [(1, "Brick", 0.1)], []),
        (_inherited, [(1, "Steel", None)], []),
        (_two, [(1, "First", None)], [IssueCode.MULTIPLE_MATERIAL_ASSOCIATIONS]),
        (_constituents, [], [IssueCode.UNSUPPORTED_MATERIAL]),
    ],
)
def test_material_definitions(
    tmp_path: Path,
    setup: MaterialSetup,
    entries: list[tuple[int, str, float | None]],
    issue_codes: list[IssueCode],
) -> None:
    tiny = Tiny()
    setup(tiny, tiny.wall())
    raw = tiny.read(tmp_path)
    assert [
        (m.position, m.material_name, m.thickness) for m in only_element(raw).materials
    ] == entries
    assert codes(raw) == issue_codes


def test_products_that_are_not_loaded_are_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tiny = Tiny(schema="IFC2X3")
    tiny.root("IfcDistributionPort", Name="Port")
    tiny.root("IfcDistributionPort", Name="Port")
    with caplog.at_level(logging.INFO, logger="ifc_sql_mcp.ingest.extract"):
        raw = tiny.read(tmp_path)
    assert raw.elements == ()
    (record,) = caplog.records
    assert record.skipped == {"IfcDistributionPort": 2}  # an `extra=` field
