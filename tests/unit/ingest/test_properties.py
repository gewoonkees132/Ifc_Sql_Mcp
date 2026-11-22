"""Property typing, SI conversion, type properties and one value per key."""

import math

import pytest
from raw_builders import DEGREE, FOOT, MILLIMETRE, SI_UNITS, SQUARE_FOOT, value

from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.properties import TypedValue, resolve_properties, type_value
from ifc_sql_mcp.ingest.raw import RawUnit, RawValue

GID = "0element______________"
FEET = SI_UNITS | {"LENGTHUNIT": FOOT, "AREAUNIT": SQUARE_FOOT, "PLANEANGLEUNIT": DEGREE}


def typed(raw: RawValue, units: dict[str, RawUnit] = FEET) -> TypedValue:
    result, issues = type_value(GID, raw, units)
    assert issues == []
    assert result is not None
    return result


def issue_codes(raw: RawValue, units: dict[str, RawUnit] = FEET) -> list[IssueCode]:
    _, issues = type_value(GID, raw, units)
    return [issue.code for issue in issues]


# --------------------------------------------------------------------------- edge cases


@pytest.mark.parametrize(
    ("set_name", "name"), [(None, "A"), ("Other", None), (" ", "A"), ("S", "")]
)
def test_a_value_without_a_set_or_property_name_is_dropped(
    set_name: str | None, name: str | None
) -> None:
    result, issues = type_value(GID, value(set_name, name, "x"), FEET)
    assert result is None
    assert [issue.code for issue in issues] == [IssueCode.UNNAMED_PROPERTY]


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_a_non_finite_number_is_dropped(number: float) -> None:
    raw = value("PSet_Revit_Dimensions", "Area", number, "IfcAreaMeasure")
    result, issues = type_value(GID, raw, FEET)
    assert result is None
    assert [issue.code for issue in issues] == [IssueCode.NON_FINITE_VALUE]


def test_a_number_that_overflows_on_conversion_is_dropped() -> None:
    huge_unit = RawUnit("LENGTHUNIT", "LIGHTYEAR", None, False, 1e300)
    raw = value("Dimensions", "Length", 1e10, "IfcLengthMeasure", unit=huge_unit)
    assert issue_codes(raw) == [IssueCode.NON_FINITE_VALUE]


def test_a_unit_without_an_si_factor_drops_the_value() -> None:
    unknown = RawUnit("LENGTHUNIT", "STEP", None, False, None)
    raw = value("Dimensions", "Length", 3.0, "IfcLengthMeasure")
    result, issues = type_value(GID, raw, SI_UNITS | {"LENGTHUNIT": unknown})
    assert result is None
    assert [issue.code for issue in issues] == [IssueCode.UNIT_NOT_CONVERTIBLE]


def test_a_property_without_a_value_has_no_value_type() -> None:
    result = typed(value("Pset_WallCommon", "FireRating", None))
    assert (result.value_type, result.text, result.number, result.boolean) == (
        None,
        None,
        None,
        None,
    )


# --------------------------------------------------------------------------- typing


def test_text_is_kept_verbatim_including_empty_and_placeholder_values() -> None:
    assert typed(value("PSet_Revit_Other", "BarCode", "BarCode")).text == "BarCode"
    empty = typed(value("Pset_WallCommon", "Reference", "", "IfcIdentifier"))
    assert (empty.text, empty.value_type) == ("", "IfcIdentifier")


def test_names_are_kept_as_written() -> None:
    result = typed(value(" Other ", "Mark ", "1"))
    assert (result.set_name, result.name) == (" Other ", "Mark ")


def test_booleans_are_not_numbers() -> None:
    result = typed(value("Pset_WallCommon", "IsExternal", True, "IfcBoolean"))
    assert (result.boolean, result.number) == (True, None)


def test_logical_unknown_is_text() -> None:
    result = typed(value("Pset_DoorCommon", "HandicapAccessible", "UNKNOWN", "IfcLogical"))
    assert (result.text, result.value_type) == ("UNKNOWN", "IfcLogical")


def test_integers_are_numbers_without_a_unit() -> None:
    result = typed(value("PSet_Revit_Dimensions", "Actual Number of Risers", 17, "IfcInteger"))
    assert (result.number, result.unit) == (17.0, None)


def test_lengths_areas_and_angles_are_converted_with_the_project_unit() -> None:
    length = typed(value("PSet_Revit_Dimensions", "Length", 10.0, "IfcLengthMeasure"))
    area = typed(value("PSet_Revit_Dimensions", "Area", 100.0, "IfcAreaMeasure"))
    positive = typed(value("D", "Width", 1.0, "IfcPositiveLengthMeasure"))
    angle = typed(value("PSet_Revit_Constraints", "Angle", 90.0, "IfcPlaneAngleMeasure"))

    assert (length.number, length.unit) == (pytest.approx(3.048), "m")
    assert (area.number, area.unit) == (pytest.approx(9.290304), "m2")
    assert (positive.number, positive.unit) == (pytest.approx(0.3048), "m")
    assert (angle.number, angle.unit) == (pytest.approx(math.pi / 2), "rad")


def test_a_unit_on_the_property_overrides_the_project_unit_of_its_dimension() -> None:
    raw = value("Dimensions", "Length", 1500.0, "IfcLengthMeasure", unit=MILLIMETRE)
    assert typed(raw).number == pytest.approx(1.5)


def test_a_property_unit_of_another_dimension_is_ignored() -> None:
    raw = value("Dimensions", "Length", 10.0, "IfcLengthMeasure", unit=SQUARE_FOOT)
    assert typed(raw).number == pytest.approx(3.048)


def test_other_measures_with_a_unit_are_stored_as_written_and_reported() -> None:
    raw = value("PSet_Revit_Electrical", "Apparent Load", 1200.0, "IfcPowerMeasure")
    result, issues = type_value(GID, raw, FEET)
    assert result is not None
    assert (result.number, result.unit) == (1200.0, None)
    assert [issue.code for issue in issues] == [IssueCode.MEASURE_NOT_CONVERTED]


U_VALUE_UNIT = RawUnit("THERMALTRANSMITTANCEUNIT", "THERMALTRANSMITTANCEUNIT", None, True, 1.0)
KILOWATT = RawUnit("POWERUNIT", "WATT", "KILO", True, 1000.0)


def test_a_derived_measure_in_a_declared_si_unit_gets_its_unit() -> None:
    """Revit declares U-values in coherent SI (audit finding A13): no conversion, a unit."""
    raw = value("Pset_DoorCommon", "ThermalTransmittance", 1.4, "IfcThermalTransmittanceMeasure")
    result = typed(raw, FEET | {"THERMALTRANSMITTANCEUNIT": U_VALUE_UNIT})
    assert (result.number, result.unit) == (1.4, "W/(m2*K)")


def test_a_derived_measure_is_scaled_by_its_prefix_and_its_own_unit_comes_first() -> None:
    raw = value("PSet_Revit_Electrical", "Apparent Load", 1.2, "IfcPowerMeasure", unit=KILOWATT)
    result = typed(raw)
    assert (result.number, result.unit) == (1200.0, "W")


def test_a_derived_measure_in_a_unit_without_an_si_factor_is_kept_and_reported() -> None:
    incoherent = RawUnit("VOLUMETRICFLOWRATEUNIT", "VOLUMETRICFLOWRATEUNIT", None, False, None)
    raw = value("Mechanical", "Flow", 12.0, "IfcVolumetricFlowRateMeasure")
    result, issues = type_value(GID, raw, FEET | {"VOLUMETRICFLOWRATEUNIT": incoherent})
    assert result is not None
    assert (result.number, result.unit) == (12.0, None)
    assert [issue.code for issue in issues] == [IssueCode.MEASURE_NOT_CONVERTED]


def test_a_plain_number_with_an_explicit_unit_is_reported_too() -> None:
    raw = value("Other", "Height", 6.0, "IfcReal", unit=FOOT)
    assert issue_codes(raw) == [IssueCode.MEASURE_NOT_CONVERTED]


@pytest.mark.parametrize("value_type", ["IfcReal", "IfcInteger", "IfcCountMeasure"])
def test_dimensionless_numbers_are_not_reported(value_type: str) -> None:
    assert issue_codes(value("Other", "Count", 3.0, value_type)) == []


# --------------------------------------------------------------------------- type properties


def test_values_from_a_type_object_are_type_properties() -> None:
    assert typed(value("Pset_DoorCommon", "FireRating", "FD30", from_type=True)).is_type_property


def test_revit_type_sets_on_the_instance_are_type_properties() -> None:
    assert typed(
        value("PSet_Revit_Type_Construction", "Width", 0.5, "IfcLengthMeasure")
    ).is_type_property
    # Revit 2013 drops the prefix, so its type parameters cannot be told apart by name.
    assert not typed(value("Construction", "Width", 0.5, "IfcLengthMeasure")).is_type_property


# --------------------------------------------------------------------------- one value per key


def resolve(*raws: RawValue) -> tuple[list[TypedValue], list[IssueCode]]:
    kept, issues = resolve_properties(GID, raws, FEET)
    return kept, [issue.code for issue in issues]


def test_the_occurrence_overrides_its_type_silently() -> None:
    kept, issues = resolve(
        value("Pset_DoorCommon", "IsExternal", True, "IfcBoolean", from_type=True, order=(1, 1)),
        value("Pset_DoorCommon", "IsExternal", False, "IfcBoolean", order=(9, 9)),
    )
    assert [(v.boolean, v.is_type_property) for v in kept] == [(False, False)]
    assert issues == []


def test_the_first_value_in_the_file_wins_and_a_different_loser_is_reported() -> None:
    kept, issues = resolve(
        value("Other", "Material", "N/A", order=(20, 2)),
        value("Other", "Material", "Steel", order=(20, 1)),
    )
    assert [v.text for v in kept] == ["Steel"]
    assert issues == [IssueCode.CONFLICTING_PROPERTY]


def test_two_different_type_values_conflict_like_two_instance_values() -> None:
    kept, issues = resolve(
        value("Pset_X", "A", "1", from_type=True, order=(1, 1)),
        value("Pset_X", "A", "2", from_type=True, order=(2, 2)),
    )
    assert [v.text for v in kept] == ["1"]
    assert issues == [IssueCode.CONFLICTING_PROPERTY]


def test_an_occurrence_value_settles_disagreeing_type_values() -> None:
    kept, issues = resolve(
        value("Pset_X", "A", "1", from_type=True, order=(1, 1)),
        value("Pset_X", "A", "2", from_type=True, order=(2, 2)),
        value("Pset_X", "A", "3", order=(3, 3)),
    )
    assert [v.text for v in kept] == ["3"]
    assert issues == []  # only same-level losers are compared with the winner


def test_identical_duplicates_are_merged_silently() -> None:
    kept, issues = resolve(value("Other", "Color", "Grey"), value("Other", "Color", "Grey"))
    assert len(kept) == 1
    assert issues == []


def test_resolved_values_are_sorted_by_set_and_name_and_include_quantity_sets() -> None:
    kept, _ = resolve(
        value("Z", "b", "1"),
        value("GSA Space Areas", "GSA BIM Area", 10.0, "IfcAreaMeasure", from_quantity_set=True),
        value("Z", "a", "2"),
    )
    assert [v.ref for v in kept] == ["GSA Space Areas.GSA BIM Area", "Z.a", "Z.b"]
    assert kept[0].from_quantity_set


def test_to_record_copies_every_column() -> None:
    record = typed(value("PSet_Revit_Dimensions", "Area", 100.0, "IfcAreaMeasure")).to_record(GID)
    assert (record.global_id, record.pset_name, record.prop_name, record.unit) == (
        GID,
        "PSet_Revit_Dimensions",
        "Area",
        "m2",
    )
    assert record.value_num == pytest.approx(9.290304)


def test_conflict_messages_show_both_values() -> None:
    _, issues = resolve_properties(
        GID,
        [
            value("Mechanical", "Loss Method", 5.0, "IfcReal", order=(1, 1)),
            value("Mechanical", "Loss Method", 1.0, "IfcReal", order=(2, 2)),
            value("Other", "Flag", True, "IfcBoolean", order=(3, 3)),
            value("Other", "Flag", False, "IfcBoolean", order=(4, 4)),
            value("Other", "Note", "x", order=(5, 5)),
            value("Other", "Note", None, order=(6, 6)),
        ],
        FEET,
    )
    assert [issue.message for issue in issues] == [
        "Mechanical.Loss Method has several values; kept 5.0, dropped 1.0",
        "Other.Flag has several values; kept True, dropped False",
        "Other.Note has several values; kept 'x', dropped no value",
    ]


def test_values_that_cannot_be_typed_are_left_out_of_resolution() -> None:
    kept, issues = resolve(value(None, "Orphan", "x"), value("Other", "Kept", "y"))
    assert [v.ref for v in kept] == ["Other.Kept"]
    assert issues == [IssueCode.UNNAMED_PROPERTY]
