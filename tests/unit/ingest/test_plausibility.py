"""Physical bounds on quantity sets, with the values the audit found in Revit exports (A2).

The numbers are those of the audit's files, converted to SI as the transform does: wall
#3665 of the Revit 2021 house (file A), a floor slab of the same file, and room 28 of the
Revit 2021 office (file C).
"""

from dataclasses import replace

import pytest
from raw_builders import SI_UNITS, value

from ifc_sql_mcp.ingest import plausibility
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.properties import TypedValue, resolve_properties, type_value
from ifc_sql_mcp.ingest.quantities import derive_quantities

WALL = "0wall_________________"
MEASURES = {"m": "IfcLengthMeasure", "m2": "IfcAreaMeasure", "m3": "IfcVolumeMeasure"}


def qto(set_name: str, name: str, number: float, unit: str) -> object:
    """A raw quantity-set value in SI units, as extract reads an IfcPhysicalSimpleQuantity."""
    return value(set_name, name, number, MEASURES[unit], from_quantity_set=True)


def resolve(*raw: object) -> tuple[dict[str, TypedValue], list[object]]:
    values, issues = resolve_properties(WALL, raw, SI_UNITS)  # type: ignore[arg-type]
    return {v.name: v for v in values}, issues


# The wall as Revit 2021 writes it: NetSideArea and NetVolume twice, unscaled first.
WALL_SET = (
    ("Height", 6.49867729846129, "m"),
    ("Length", 6.40399999999979, "m"),
    ("Width", 0.202, "m"),
    ("NetVolume", 8406740942.70763, "m3"),
    ("NetSideArea", 41617529.4193447, "m2"),
    ("NetSideArea", 22.3549896560785, "m2"),
    ("NetVolume", 4.51570791052784, "m3"),
)


def test_the_plausible_one_of_two_duplicated_wall_quantities_wins_over_file_order() -> None:
    values, issues = resolve(*(qto("Qto_WallBaseQuantities", *q) for q in WALL_SET))

    assert values["NetSideArea"].number == pytest.approx(22.3549896560785)
    assert values["NetVolume"].number == pytest.approx(4.51570791052784)
    assert not any(v.implausible for v in values.values())
    messages = sorted(i.message for i in issues)  # type: ignore[attr-defined]
    assert messages == [
        "Qto_WallBaseQuantities.NetSideArea has several values; kept 22.3549896560785 m2 "
        "(the only value within Length x Height), dropped 41617529.4193447 m2",
        "Qto_WallBaseQuantities.NetVolume has several values; kept 4.51570791052784 m3 "
        "(the only value within Length x Height x Width), dropped 8406740942.70763 m3",
    ]
    assert {i.code for i in issues} == {IssueCode.CONFLICTING_PROPERTY}  # type: ignore[attr-defined]


def test_duplicates_the_bounds_cannot_tell_apart_keep_file_order() -> None:
    values, issues = resolve(
        qto("Qto_WallBaseQuantities", "Length", 6.4, "m"),
        qto("Qto_WallBaseQuantities", "Height", 6.5, "m"),
        qto("Qto_WallBaseQuantities", "NetSideArea", 22.0, "m2"),
        qto("Qto_WallBaseQuantities", "NetSideArea", 21.0, "m2"),
    )

    assert values["NetSideArea"].number == 22.0
    [issue] = issues
    assert "kept 22.0 m2, dropped 21.0 m2" in issue.message  # type: ignore[attr-defined]


def test_duplicates_without_a_bound_keep_file_order() -> None:
    values, _ = resolve(
        qto("Qto_WallBaseQuantities", "NetSideArea", 41617529.4, "m2"),
        qto("Qto_WallBaseQuantities", "NetSideArea", 22.35, "m2"),
    )

    # No Length or Height: nothing to measure the areas against, so the file decides.
    assert values["NetSideArea"].number == 41617529.4


def test_a_revit_2021_space_volume_and_perimeter_are_flagged() -> None:
    values, issues = resolve(
        qto("Qto_SpaceBaseQuantities", "NetFloorArea", 18.3058468487132, "m2"),
        qto("Qto_SpaceBaseQuantities", "Height", 3.8, "m"),
        # 60.1164 is the perimeter in feet; the file labels it millimetres.
        qto("Qto_SpaceBaseQuantities", "GrossPerimeter", 0.0601164407678728, "m"),
        qto("Qto_SpaceBaseQuantities", "GrossFloorArea", 18.3058468487132, "m2"),
        qto("Qto_SpaceBaseQuantities", "GrossVolume", 69562.2180251101, "m3"),
    )

    assert {n for n, v in values.items() if v.implausible} == {"GrossVolume", "GrossPerimeter"}
    assert sorted(i.message for i in issues) == [  # type: ignore[attr-defined]
        "Qto_SpaceBaseQuantities.GrossPerimeter = 0.0601164 m breaks the bound at least 1/2 "
        "of 2√(π x plan area) = 7.5835 m: kept in ifc.property as written (is_implausible), "
        "not used as a quantity",
        "Qto_SpaceBaseQuantities.GrossVolume = 69562.2 m3 breaks the bound at most 2 x floor "
        "area x Height = 139.124 m3: kept in ifc.property as written (is_implausible), not "
        "used as a quantity",
    ]
    assert {i.code for i in issues} == {IssueCode.IMPLAUSIBLE_QUANTITY}  # type: ignore[attr-defined]


def test_a_revit_2021_slab_net_area_and_gross_volume_are_flagged() -> None:
    values, issues = resolve(
        qto("Qto_SlabBaseQuantities", "GrossArea", 105.906, "m2"),
        qto("Qto_SlabBaseQuantities", "NetArea", 219.012, "m2"),  # the whole solid's surface
        qto("Qto_SlabBaseQuantities", "GrossVolume", 15885.9, "m3"),  # m2 x mm
        qto("Qto_SlabBaseQuantities", "NetVolume", 15.8859, "m3"),
        qto("Qto_SlabBaseQuantities", "Perimeter", 48.0, "m"),
        qto("Qto_SlabBaseQuantities", "Width", 0.15, "m"),
    )

    assert {n for n, v in values.items() if v.implausible} == {"NetArea", "GrossVolume"}
    net_area = next(i for i in issues if "NetArea" in i.message)  # type: ignore[attr-defined]
    assert "breaks the bound at most 1.05 x GrossArea = 111.201 m2" in net_area.message


def test_a_sound_set_is_left_alone() -> None:
    values, issues = resolve(
        qto("Qto_WallBaseQuantities", "Length", 6.404, "m"),
        qto("Qto_WallBaseQuantities", "Height", 6.4987, "m"),
        qto("Qto_WallBaseQuantities", "Width", 0.202, "m"),
        # A gable wall can pass length x height a little; it is not a unit error.
        qto("Qto_WallBaseQuantities", "NetSideArea", 1.1 * 6.404 * 6.4987, "m2"),
        qto("Qto_WallBaseQuantities", "NetVolume", 4.5, "m3"),
    )

    assert (issues, [n for n, v in values.items() if v.implausible]) == ([], [])


def test_only_quantity_sets_are_checked() -> None:
    values, issues = resolve(
        value("Dimensions", "Length", 1.0, "IfcLengthMeasure"),
        value("Dimensions", "Height", 1.0, "IfcLengthMeasure"),
        value("Dimensions", "NetSideArea", 50.0, "IfcAreaMeasure"),
    )

    assert (issues, values["NetSideArea"].implausible) == ([], False)


def test_a_flagged_value_bounds_nothing_else() -> None:
    # NetFloorArea breaks net <= gross, so the perimeter is measured against GrossFloorArea.
    values, _ = resolve(
        qto("Qto_SpaceBaseQuantities", "NetFloorArea", 18_000_000.0, "m2"),
        qto("Qto_SpaceBaseQuantities", "GrossFloorArea", 18.0, "m2"),
        qto("Qto_SpaceBaseQuantities", "GrossPerimeter", 17.0, "m"),
    )

    assert {n for n, v in values.items() if v.implausible} == {"NetFloorArea"}


def test_choose_needs_exactly_one_plausible_candidate() -> None:
    siblings = {"Length": 2.0, "Height": 3.0}

    def area(number: float) -> TypedValue:
        raw = qto("Qto_WallBaseQuantities", "NetSideArea", number, "m2")
        typed, _ = type_value(WALL, raw, SI_UNITS)  # type: ignore[arg-type]
        assert typed is not None
        return typed

    sound, huge, smaller, bigger = area(6.0), area(6e6), area(5.0), area(7e6)

    assert plausibility.choose([sound, huge], siblings) is sound
    assert plausibility.choose([sound, smaller], siblings) is None  # both plausible
    assert plausibility.choose([huge, bigger], siblings) is None  # neither


def test_an_implausible_value_becomes_no_quantity() -> None:
    values, _ = resolve(
        value("GSA Space Areas", "GSA BIM Area", 18.0, "IfcAreaMeasure", from_quantity_set=True)
    )
    flagged = [replace(v, implausible=True) for v in values.values()]

    # No quantity, and no second issue: implausible_quantity has said it already.
    assert derive_quantities(WALL, flagged) == ([], [])


def test_a_revit_2021_roof_slab_flags_its_area_not_its_sound_volume() -> None:
    # Basic Roof:Warm Roof - Timber:724430 in file A: the plan is about 70.8 m2
    # ((153.6 - 35.6 x 0.338) / 2), and 70.8 x 0.338 is the NetVolume of 23.93 m3.
    values, _ = resolve(
        qto("Qto_SlabBaseQuantities", "GrossArea", 6.5764, "m2"),
        qto("Qto_SlabBaseQuantities", "GrossVolume", 2222.8301, "m3"),
        qto("Qto_SlabBaseQuantities", "NetArea", 153.6074, "m2"),
        qto("Qto_SlabBaseQuantities", "NetVolume", 23.9263, "m3"),
        qto("Qto_SlabBaseQuantities", "Perimeter", 35.596, "m"),
        qto("Qto_SlabBaseQuantities", "Width", 0.338, "m"),
    )

    assert {n for n, v in values.items() if v.implausible} == {
        "GrossArea",
        "GrossVolume",
        "NetArea",
    }


def test_an_ifc2x3_slab_without_a_net_volume_checks_its_area_against_nothing() -> None:
    # File E (Revit 2019) writes BaseQuantities with GrossArea, GrossVolume, Width and
    # Perimeter only: the area stands, the volume (m2 x mm) is flagged.
    values, _ = resolve(
        qto("BaseQuantities", "GrossArea", 1013.4941, "m2"),
        qto("BaseQuantities", "GrossVolume", 354722.9247, "m3"),
        qto("BaseQuantities", "Perimeter", 197.8159625, "m"),
        qto("BaseQuantities", "Width", 0.35, "m"),
    )

    assert {n for n, v in values.items() if v.implausible} == {"GrossVolume"}


def test_a_candidate_without_a_number_keeps_every_bound() -> None:
    raw = value("Qto_WallBaseQuantities", "NetSideArea", None, from_quantity_set=True)
    empty, _ = type_value(WALL, raw, SI_UNITS)
    huge, _ = type_value(WALL, qto("Qto_WallBaseQuantities", "NetSideArea", 6e6, "m2"), SI_UNITS)  # type: ignore[arg-type]
    assert empty is not None
    assert huge is not None

    assert plausibility.choose([empty, huge], {"Length": 2.0, "Height": 3.0}) is empty
