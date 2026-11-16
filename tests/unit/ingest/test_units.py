"""Unit labels, project unit selection, conversion and elevation rounding."""

import math
from decimal import Decimal

import pytest
from raw_builders import DEGREE, FOOT, METRE, MILLIMETRE, SQUARE_FOOT

from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.raw import RawUnit
from ifc_sql_mcp.ingest.units import project_units, round_elevation, to_si, unit_label


@pytest.mark.parametrize(
    ("unit", "label"),
    [
        (METRE, "m"),
        (MILLIMETRE, "mm"),
        (RawUnit("LENGTHUNIT", "METER", "CENTI", True, 0.01), "cm"),  # American spelling
        (RawUnit("AREAUNIT", "SQUARE_METRE", "MILLI", True, 1e-6), "mm2"),
        (FOOT, "ft"),
        (SQUARE_FOOT, "ft2"),
        (DEGREE, "deg"),
        (RawUnit("LENGTHUNIT", "foot", None, False, 0.3048), "ft"),  # IfcOpenShell's spelling
        (RawUnit("LENGTHUNIT", "Rod", None, False, 5.0292), "Rod"),  # unknown: its own name
        (RawUnit("LENGTHUNIT", " ", None, False, None), "LENGTHUNIT"),  # never blank
        (RawUnit("LENGTHUNIT", "PARSEC", None, True, 1.0), "parsec"),  # unknown SI name
    ],
)
def test_unit_label(unit: RawUnit, label: str) -> None:
    assert unit_label(unit) == label


def test_project_units_assumes_si_for_each_missing_dimension_and_says_so() -> None:
    units, issues = project_units([FOOT])

    assert units["LENGTHUNIT"] is FOOT
    assert units["AREAUNIT"].scale_to_si == 1.0
    assert unit_label(units["VOLUMEUNIT"]) == "m3"
    assert unit_label(units["PLANEANGLEUNIT"]) == "rad"
    assert [issue.code for issue in issues] == [IssueCode.UNIT_ASSUMED_SI] * 3
    assert all(issue.global_id is None for issue in issues)


def test_project_units_keeps_the_first_unit_of_a_type() -> None:
    units, _ = project_units([FOOT, METRE])
    assert units["LENGTHUNIT"] is FOOT


def test_to_si_multiplies_by_the_scale_and_refuses_unknown_scales() -> None:
    assert to_si(10.0, FOOT) == pytest.approx(3.048)
    assert to_si(10.0, RawUnit("LENGTHUNIT", "STEP", None, False, None)) is None


@pytest.mark.parametrize(
    ("metres", "stored"),
    [
        (3.100000000000378, "3.100"),  # Revit float noise
        (-1.25, "-1.250"),
        (0.0625, "0.063"),  # an exact binary tie rounds away from zero, as PostgreSQL does
        (-0.0625, "-0.063"),
        (-0.0001, "0.000"),  # no negative zero
        (999999.999, "999999.999"),
    ],
)
def test_round_elevation(metres: float, stored: str) -> None:
    rounded = round_elevation(metres)
    assert rounded is not None
    assert str(rounded) == stored
    assert rounded == Decimal(stored)


@pytest.mark.parametrize("metres", [math.nan, math.inf, -math.inf, 1_000_000.0, -999999.9996])
def test_round_elevation_rejects_what_numeric_9_3_cannot_hold(metres: float) -> None:
    assert round_elevation(metres) is None
