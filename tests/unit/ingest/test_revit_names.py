"""Revit name parsing: the three name shapes, Tag as tie-breaker, and names that do not fit."""

import pytest

from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.revit_names import RevitName, parse_revit_name

GID = "0element______________"


def parse(name: str | None, tag: str | None = None) -> tuple[RevitName, list[IssueCode]]:
    parsed, issues = parse_revit_name(GID, name, tag)
    assert all(issue.global_id == GID for issue in issues)
    return parsed, [issue.code for issue in issues]


# --------------------------------------------------------------------------- edge cases


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_missing_name_gives_nothing_and_is_not_an_issue(name: str | None) -> None:
    assert parse(name, "123") == (RevitName(None, None, None), [])


@pytest.mark.parametrize(
    "name",
    [
        "Generic Model 1",  # no colons at all
        "Family:Type",  # no ElementId
        "Family:Type:12a",  # not digits
        "Family:Type:0",  # ElementIds are positive
        f"Family:Type:{2**63}",  # does not fit a bigint
        "Family:Type:١٢٣",  # Arabic-Indic digits are not ASCII digits
        "123:456",  # an id needs a family and type before it
    ],
)
def test_a_name_without_an_element_id_is_unparsed(name: str) -> None:
    parsed, issues = parse(name)
    assert parsed == RevitName(None, None, None)
    assert issues == [IssueCode.REVIT_NAME_UNPARSED]


@pytest.mark.parametrize(
    ("name", "element_id"),
    [
        ("A:B:C:D:123", 123),  # four segments before the id: family/type unclear
        (":Type:123", 123),  # blank family
        ("Family: :123", 123),  # blank type
    ],
)
def test_an_unclear_body_keeps_the_element_id_only(name: str, element_id: int) -> None:
    parsed, issues = parse(name)
    assert parsed == RevitName(None, None, element_id)
    assert issues == [IssueCode.REVIT_NAME_UNPARSED]


def test_the_largest_bigint_is_still_an_element_id() -> None:
    assert parse(f"Family:Type:{2**63 - 1}")[0].element_id == 2**63 - 1


# --------------------------------------------------------------------------- the shapes


def test_system_family_family_type_id() -> None:
    name = "Basic Wall:Interior - 138mm Partition (1-hr):138437"
    assert parse(name, "138437") == (
        RevitName("Basic Wall", "Interior - 138mm Partition (1-hr)", 138437),
        [],
    )


def test_loadable_family_repeats_the_type_as_label() -> None:
    name = "M_Single-Flush:0762 x 2032mm:0762 x 2032mm:150173"
    assert parse(name, "150173") == (RevitName("M_Single-Flush", "0762 x 2032mm", 150173), [])


def test_loadable_family_label_that_differs_from_the_type_is_not_the_type() -> None:
    name = "M_Lighting and Appliance Panelboard - 208V MLO:400 A:Unit A Panelboard:575479"
    parsed, issues = parse(name, "575479")
    assert parsed == RevitName("M_Lighting and Appliance Panelboard - 208V MLO", "400 A", 575479)
    assert issues == []


def test_opening_takes_its_hosts_name_and_id_plus_a_part_number() -> None:
    # Openings have no Tag, so the structure decides: the last number is a part number.
    name = "M_Fixed:4835mm x 2420mm:4835mm x 2420mm:145788:1"
    assert parse(name, None) == (RevitName("M_Fixed", "4835mm x 2420mm", 145788), [])


def test_stair_part_is_not_element_id_one() -> None:
    name = "Stair:Residential - 200mm Max Riser 250mm Tread:151086:1"
    assert parse(name, "151086") == (
        RevitName("Stair", "Residential - 200mm Max Riser 250mm Tread", 151086),
        [],
    )


def test_a_part_number_must_be_smaller_than_the_id_before_it() -> None:
    # A numeric label followed by a larger id is Family:Type:Label:Id, not a part number.
    assert parse("M_Pipe:50:50:123456") == (RevitName("M_Pipe", "50", 123456), [])


# --------------------------------------------------------------------------- Tag


def test_tag_decides_when_both_readings_are_possible() -> None:
    name = "Family:Type:500:12"
    assert parse(name)[0] == RevitName("Family", "Type", 500)  # 12 read as a part number
    assert parse(name, "12")[0] == RevitName("Family", "Type", 12)  # 500 is a label
    assert parse(name, " 500 ")[0] == RevitName("Family", "Type", 500)


def test_a_tag_that_matches_no_reading_is_reported() -> None:
    parsed, issues = parse("Basic Wall:Generic:138437", "999")
    assert parsed == RevitName("Basic Wall", "Generic", 138437)
    assert issues == [IssueCode.REVIT_TAG_MISMATCH]


@pytest.mark.parametrize("tag", ["", "A-12", "0"])
def test_a_tag_that_is_not_an_element_id_is_ignored(tag: str) -> None:
    assert parse("Basic Wall:Generic:138437", tag) == (
        RevitName("Basic Wall", "Generic", 138437),
        [],
    )
