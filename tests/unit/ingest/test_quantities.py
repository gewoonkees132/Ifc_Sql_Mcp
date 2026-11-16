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
