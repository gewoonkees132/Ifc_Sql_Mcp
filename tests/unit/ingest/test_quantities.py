"""The quantity mapping table: what becomes a quantity, and what is reported instead."""

from typing import get_args

import pytest
from raw_builders import SI_UNITS, value

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.properties import TypedValue, resolve_properties
from ifc_sql_mcp.ingest.quantities import QUANTITY_RULES, derive_quantities
from ifc_sql_mcp.ingest.raw import RawValue
from ifc_sql_mcp.ingest.records import QuantityName, QuantityRecord, QuantitySource

GID = "0space________________"


def typed(*raws: RawValue) -> list[TypedValue]:
    values, issues = resolve_properties(GID, raws, SI_UNITS)
    assert issues == []
    return values


def derive(*raws: RawValue) -> tuple[list[QuantityRecord], list[IssueCode]]:
    records, issues = derive_quantities(GID, typed(*raws))
    return records, [issue.code for issue in issues]


def test_the_rules_only_use_names_and_sources_the_schema_allows() -> None:
    assert set(get_args(QuantityName.__value__)) == set(schema.QUANTITY_NAMES)
    assert set(get_args(QuantitySource.__value__)) == set(schema.QUANTITY_SOURCES)
    assert {rule.quantity for rule in QUANTITY_RULES} <= set(schema.QUANTITY_NAMES)


@pytest.mark.parametrize("set_name", ["PSet_Revit_Dimensions", "Dimensions"])
@pytest.mark.parametrize(
    ("prop", "value_type", "quantity"),
    [
        ("Length", "IfcLengthMeasure", "length_m"),
        ("Area", "IfcAreaMeasure", "area_m2"),
        ("Volume", "IfcVolumeMeasure", "volume_m3"),
        ("Perimeter", "IfcLengthMeasure", "perimeter_m"),
        ("Unbounded Height", "IfcLengthMeasure", "height_m"),
    ],
)
def test_revit_dimension_parameters_of_2011_and_2013(
    set_name: str, prop: str, value_type: str, quantity: str
) -> None:
    records, issues = derive(value(set_name, prop, 2.5, value_type))
    assert records == [QuantityRecord(GID, quantity, "revit_pset", 2.5, f"{set_name}.{prop}")]
    assert issues == []


def test_the_gsa_bim_area_is_an_ifc_quantity() -> None:
    records, issues = derive(
        value("GSA Space Areas", "GSA BIM Area", 30.1, "IfcAreaMeasure", from_quantity_set=True),
        value("PSet_Revit_Dimensions", "Area", 30.1, "IfcAreaMeasure"),
    )
    assert [(r.name, r.source, r.source_ref) for r in records] == [
        ("area_m2", "ifc_qto", "GSA Space Areas.GSA BIM Area"),
        ("area_m2", "revit_pset", "PSet_Revit_Dimensions.Area"),
    ]
    assert issues == []


def test_the_kind_of_set_must_match_the_source() -> None:
    # A property set named like the quantity set is not an IFC quantity, and vice versa.
    records, issues = derive(
        value("GSA Space Areas", "GSA BIM Area", 30.1, "IfcAreaMeasure"),
        value("Dimensions", "Area", 30.1, "IfcAreaMeasure", from_quantity_set=True),
    )
    assert (records, issues) == ([], [])


def test_type_dimension_sets_and_other_parameters_are_not_quantities() -> None:
    records, issues = derive(
        value("PSet_Revit_Type_Dimensions", "Width", 0.9, "IfcLengthMeasure"),
        value("PSet_Revit_Dimensions", "Thickness", 0.2, "IfcLengthMeasure"),
    )
    assert (records, issues) == ([], [])


@pytest.mark.parametrize(
    ("raw", "value_type"),
    [(2.0, "IfcReal"), ("12 m", "IfcLabel"), (True, "IfcBoolean"), (3.0, "IfcAreaMeasure")],
)
def test_a_value_that_is_not_the_right_measure_is_reported(
    raw: float | str | bool, value_type: str
) -> None:
    records, issues = derive(value("Dimensions", "Length", raw, value_type))
    assert records == []
    assert issues == [IssueCode.QUANTITY_NOT_A_MEASURE]


def test_an_empty_parameter_is_silently_not_a_quantity() -> None:
    assert derive(value("Dimensions", "Length", None)) == ([], [])


def test_negative_values_are_reported_and_zero_is_kept() -> None:
    records, issues = derive(
        value("PSet_Revit_Dimensions", "Area", -12.0, "IfcAreaMeasure"),
        value("PSet_Revit_Dimensions", "Volume", 0.0, "IfcVolumeMeasure"),
    )
    assert [(r.name, r.value) for r in records] == [("volume_m3", 0.0)]
    assert issues == [IssueCode.NEGATIVE_QUANTITY]


def test_two_sets_for_one_quantity_keep_the_first_set_in_the_rule() -> None:
    records, issues = derive(
        value("Dimensions", "Length", 4.0, "IfcLengthMeasure"),
        value("PSet_Revit_Dimensions", "Length", 5.0, "IfcLengthMeasure"),
    )
    assert [(r.value, r.source_ref) for r in records] == [(5.0, "PSet_Revit_Dimensions.Length")]
    assert issues == [IssueCode.CONFLICTING_QUANTITY]


def test_two_sets_with_the_same_value_do_not_conflict() -> None:
    records, issues = derive(
        value("Dimensions", "Length", 4.0, "IfcLengthMeasure"),
        value("PSet_Revit_Dimensions", "Length", 4.0, "IfcLengthMeasure"),
    )
    assert len(records) == 1
    assert issues == []


# --------------------------------------------------------------- IFC base quantities

MEASURES = {"m": "IfcLengthMeasure", "m2": "IfcAreaMeasure", "m3": "IfcVolumeMeasure"}


def qto(set_name: str, name: str, number: float, unit: str) -> RawValue:
    return value(set_name, name, number, MEASURES[unit], from_quantity_set=True)


def quantities(ifc_class_base: str | None, *raws: RawValue) -> dict[str, tuple[float, str]]:
    """Canonical quantities of one object: name -> (value, source_ref)."""
    values, _ = resolve_properties(GID, raws, SI_UNITS)
    records, issues = derive_quantities(GID, values, ifc_class_base)
    assert issues == []
    return {r.name: (r.value, r.source_ref) for r in records if r.source == "ifc_qto"}


def test_a_revit_2021_wall_gets_its_net_side_area_and_its_thickness() -> None:
    assert quantities(
        "IfcWall",
        qto("Qto_WallBaseQuantities", "Length", 6.404, "m"),
        qto("Qto_WallBaseQuantities", "Height", 6.4987, "m"),
        qto("Qto_WallBaseQuantities", "Width", 0.202, "m"),
        qto("Qto_WallBaseQuantities", "GrossFootprintArea", 1.2936, "m2"),
        qto("Qto_WallBaseQuantities", "NetSideArea", 22.355, "m2"),
        qto("Qto_WallBaseQuantities", "NetVolume", 4.5157, "m3"),
    ) == {
        "length_m": (6.404, "Qto_WallBaseQuantities.Length"),
        "height_m": (6.4987, "Qto_WallBaseQuantities.Height"),
        "width_m": (0.202, "Qto_WallBaseQuantities.Width"),
        "area_m2": (22.355, "Qto_WallBaseQuantities.NetSideArea"),
        "volume_m3": (4.5157, "Qto_WallBaseQuantities.NetVolume"),
    }


def test_an_ifc2x3_base_quantities_set_is_read_as_its_class_set() -> None:
    # Revit 2019 (file E) writes one set named BaseQuantities, with gross values only.
    wall = quantities(
        "IfcWall",
        qto("BaseQuantities", "Length", 5.0, "m"),
        qto("BaseQuantities", "Height", 3.0, "m"),
        qto("BaseQuantities", "GrossSideArea", 15.0, "m2"),
        qto("BaseQuantities", "GrossVolume", 3.0, "m3"),
    )
    space = quantities("IfcSpace", qto("BaseQuantities", "NetFloorArea", 98.8, "m2"))

    assert wall["area_m2"] == (15.0, "BaseQuantities.GrossSideArea")
    assert wall["volume_m3"] == (3.0, "BaseQuantities.GrossVolume")
    assert space == {"area_m2": (98.8, "BaseQuantities.NetFloorArea")}


def test_base_quantities_of_a_class_without_a_qto_set_are_not_quantities() -> None:
    assert quantities("IfcFurnishingElement", qto("BaseQuantities", "Length", 1.0, "m")) == {}
    assert quantities(None, qto("BaseQuantities", "Length", 1.0, "m")) == {}


def test_a_gross_value_stands_in_silently_only_for_a_missing_or_implausible_net_one() -> None:
    both = quantities(
        "IfcWall",
        qto("Qto_WallBaseQuantities", "NetSideArea", 20.0, "m2"),
        qto("Qto_WallBaseQuantities", "GrossSideArea", 24.0, "m2"),
    )
    # Revit 2021's space: GrossVolume is 1,000 x area x height, so no volume at all.
    flagged = quantities(
        "IfcSpace",
        qto("Qto_SpaceBaseQuantities", "NetFloorArea", 18.3, "m2"),
        qto("Qto_SpaceBaseQuantities", "Height", 3.8, "m"),
        qto("Qto_SpaceBaseQuantities", "GrossVolume", 69562.2, "m3"),
    )

    assert both["area_m2"] == (20.0, "Qto_WallBaseQuantities.NetSideArea")
    assert "volume_m3" not in flagged


def test_a_slab_area_is_its_gross_area_never_its_net_area() -> None:
    # Revit 2021 writes NetArea as the whole solid's surface: 219 m2 for a 105.9 m2 floor.
    slab = quantities(
        "IfcSlab",
        qto("Qto_SlabBaseQuantities", "GrossArea", 105.906, "m2"),
        qto("Qto_SlabBaseQuantities", "NetArea", 219.012, "m2"),
        qto("Qto_SlabBaseQuantities", "NetVolume", 15.8859, "m3"),
        qto("Qto_SlabBaseQuantities", "Perimeter", 48.0, "m"),
        qto("Qto_SlabBaseQuantities", "Width", 0.15, "m"),
    )
    without_gross = quantities("IfcSlab", qto("Qto_SlabBaseQuantities", "NetArea", 320.5, "m2"))

    assert slab["area_m2"] == (105.906, "Qto_SlabBaseQuantities.GrossArea")
    assert (slab["width_m"], slab["perimeter_m"]) == (
        (0.15, "Qto_SlabBaseQuantities.Width"),
        (48.0, "Qto_SlabBaseQuantities.Perimeter"),
    )
    assert without_gross == {}


@pytest.mark.parametrize("set_name", ["Qto_DoorBaseQuantities", "Qto_WindowBaseQuantities"])
def test_doors_and_windows_get_width_height_and_area(set_name: str) -> None:
    assert quantities(
        "IfcDoor",
        qto(set_name, "Width", 0.8, "m"),
        qto(set_name, "Height", 2.1, "m"),
        qto(set_name, "Area", 2.965, "m2"),
    ) == {
        "width_m": (0.8, f"{set_name}.Width"),
        "height_m": (2.1, f"{set_name}.Height"),
        "area_m2": (2.965, f"{set_name}.Area"),
    }


@pytest.mark.parametrize(
    "set_name",
    [
        "Qto_ColumnBaseQuantities",
        "Qto_BeamBaseQuantities",
        "Qto_MemberBaseQuantities",
        "Qto_StairFlightBaseQuantities",
    ],
)
def test_linear_elements_get_length_and_volume(set_name: str) -> None:
    assert quantities(
        "IfcColumn",
        qto(set_name, "Length", 3.0, "m"),
        qto(set_name, "GrossVolume", 0.27, "m3"),
    ) == {"length_m": (3.0, f"{set_name}.Length"), "volume_m3": (0.27, f"{set_name}.GrossVolume")}


def test_a_railing_gets_its_length() -> None:
    assert quantities("IfcRailing", qto("Qto_RailingBaseQuantities", "Length", 7.5, "m")) == {
        "length_m": (7.5, "Qto_RailingBaseQuantities.Length")
    }


def test_the_gsa_area_stands_in_for_a_missing_space_net_floor_area_only() -> None:
    gsa = qto("GSA Space Areas", "GSA BIM Area", 30.1, "m2")

    assert quantities("IfcSpace", gsa)["area_m2"] == (30.1, "GSA Space Areas.GSA BIM Area")
    assert quantities("IfcSpace", qto("Qto_SpaceBaseQuantities", "NetFloorArea", 30.0, "m2"), gsa)[
        "area_m2"
    ] == (30.0, "Qto_SpaceBaseQuantities.NetFloorArea")


# --------------------------------------------------------------------------- IFC attributes (A4)


def test_a_doors_overall_size_is_its_width_and_height_from_its_attributes() -> None:
    raws = (
        value("IfcDoor", "OverallWidth", 1.697, "IfcPositiveLengthMeasure", from_attribute=True),
        value("IfcDoor", "OverallHeight", 2.1, "IfcPositiveLengthMeasure", from_attribute=True),
    )
    values, _ = resolve_properties(GID, raws, SI_UNITS)
    records, issues = derive_quantities(GID, values, "IfcDoor")

    assert (issues, [(r.name, r.source, r.value, r.source_ref) for r in records]) == (
        [],
        [
            ("height_m", "ifc_attribute", 2.1, "IfcDoor.OverallHeight"),
            ("width_m", "ifc_attribute", 1.697, "IfcDoor.OverallWidth"),
        ],
    )


def test_a_property_set_named_after_an_entity_is_not_its_attributes() -> None:
    records, issues = derive(value("IfcDoor", "OverallWidth", 0.9, "IfcPositiveLengthMeasure"))

    assert (records, issues) == ([], [])
