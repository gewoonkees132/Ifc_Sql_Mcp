"""OmniClass parameters and IfcClassificationReferences become classification rows."""

import pytest
from raw_builders import SI_UNITS, value

from ifc_sql_mcp.ingest.classifications import (
    OMNICLASS_TABLE_13,
    collect_classifications,
    omniclass_table,
    parse_omniclass_category,
)
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.properties import resolve_properties
from ifc_sql_mcp.ingest.raw import RawReference, RawValue
from ifc_sql_mcp.ingest.records import ClassificationRecord

GID = "0space________________"


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("13-51 24 11: General Residential Space", ("13-51 24 11", "General Residential Space")),
        ("  13-41 11 14 11 :Bathroom  ", ("13-41 11 14 11", "Bathroom")),
        ("13-51 24", ("13-51 24", None)),
        ("13-51 24:", ("13-51 24", None)),
        ("Other General Facility Service Spaces", None),  # Revit 2013: a title, no code
        ("23-51 24 11: Not Table 13", None),
        ("13-5 24: Malformed", None),
        ("", None),
    ],
)
def test_parse_omniclass_category(text: str, parsed: tuple[str, str | None] | None) -> None:
    assert parse_omniclass_category(text) == parsed


@pytest.mark.parametrize(
    ("number", "system"),
    [
        ("23.40.35.17.47.11", "OmniClass Table 23"),
        (" 23.30.10.00 ", "OmniClass Table 23"),
        ("21.10", "OmniClass Table 21"),
        ("23", None),
        ("23.4", None),
        ("Doors", None),
    ],
)
def test_omniclass_table(number: str, system: str | None) -> None:
    assert omniclass_table(number) == system


def collect(
    *raws: RawValue, references: tuple[RawReference, ...] = ()
) -> tuple[list[ClassificationRecord], list[IssueCode]]:
    values, _ = resolve_properties(GID, raws, SI_UNITS)
    records, issues = collect_classifications(GID, values, references)
    return records, [issue.code for issue in issues]


def test_omniclass_table_13_on_a_space_uses_the_exact_system_name() -> None:
    records, issues = collect(
        value("PSet_Revit_Identity Data", "OmniClass Table 13 Category", "13-51 21 11: Bedroom")
    )
    assert records == [ClassificationRecord(GID, OMNICLASS_TABLE_13, "13-51 21 11", "Bedroom")]
    assert OMNICLASS_TABLE_13 == "OmniClass Table 13"  # v_space_schedule filters on it
    assert issues == []


def test_an_omniclass_title_without_code_is_reported() -> None:
    records, issues = collect(
        value(
            "Identity Data", "OmniClass Table 13 Category", "Other General Facility Service Spaces"
        )
    )
    assert (records, issues) == ([], [IssueCode.OMNICLASS_UNPARSED])


def test_empty_omniclass_parameters_are_not_reported() -> None:
    records, issues = collect(
        value("Identity Data", "OmniClass Table 13 Category", " "),
        value("Identity Data", "OmniClass Number", ""),
        value("Identity Data", "OmniClass Number", None),
    )
    assert (records, issues) == ([], [])


def test_omniclass_number_takes_its_title_from_the_same_set() -> None:
    records, issues = collect(
        value("PSet_Revit_Type_Identity Data", "OmniClass Number", "23.30.10.00"),
        value("PSet_Revit_Type_Identity Data", "OmniClass Title", " Doors "),
        value("Other", "OmniClass Title", "Not this one"),
    )
    assert records == [ClassificationRecord(GID, "OmniClass Table 23", "23.30.10.00", "Doors")]
    assert issues == []


def test_omniclass_number_without_a_title() -> None:
    records, _ = collect(
        value("Identity Data", "OmniClass Number", "23.30.10.00"),
        value("Identity Data", "OmniClass Title", ""),
    )
    assert [r.title for r in records] == [None]


def test_a_malformed_omniclass_number_is_reported() -> None:
    assert collect(value("Identity Data", "OmniClass Number", "Doors")) == (
        [],
        [IssueCode.OMNICLASS_UNPARSED],
    )


def test_references_need_a_system_and_a_code() -> None:
    records, issues = collect(
        references=(
            RawReference("UniFormat", "D5020220", "Lighting - Incandescent"),
            RawReference("UniFormat", "", ""),  # how most Revit 2013 references look
            RawReference(None, "D2010710", "Showers"),
            RawReference("UniFormat", "D2010710", " "),
        )
    )
    assert records == [
        ClassificationRecord(GID, "UniFormat", "D2010710", None),
        ClassificationRecord(GID, "UniFormat", "D5020220", "Lighting - Incandescent"),
    ]
    assert issues == [IssueCode.INCOMPLETE_CLASSIFICATION_REFERENCE] * 2


def test_one_code_twice_keeps_a_title_over_none_and_reports_different_titles() -> None:
    records, issues = collect(
        references=(
            RawReference("UniFormat", "D5010", None),
            RawReference("UniFormat", "D5010", "Panelboards"),
            RawReference("UniFormat", "D5010", "Electrical Service"),
        )
    )
    assert records == [ClassificationRecord(GID, "UniFormat", "D5010", "Electrical Service")]
    assert issues == [IssueCode.CONFLICTING_CLASSIFICATION]


def test_the_same_reference_twice_is_merged_silently() -> None:
    reference = RawReference("UniFormat", "D5010", "Panelboards")
    assert collect(references=(reference, reference)) == (
        [ClassificationRecord(GID, "UniFormat", "D5010", "Panelboards")],
        [],
    )
