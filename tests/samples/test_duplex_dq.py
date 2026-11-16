"""Data quality on the four Duplex files: every rule's findings per file, pinned.

Deselected by default. Run with::

    make samples && uv run pytest -m sample

The figures are the ones docs/data-quality.md cites. Two independent sources back them:
the issue rules must find exactly the issues the transform reports
(``test_duplex_samples.EXPECTED[...]["issues"]``), and the placeholder rules must count
what ``scripts/quirks_report.py`` counts in the transform's records.
"""

import pytest
import quirks_report as report
from sqlalchemy import Engine, text
from test_duplex_samples import EXPECTED as TRANSFORM_FIGURES

from ifc_sql_mcp.dq.catalogue import load_catalogue
from ifc_sql_mcp.dq.run import DqResult, check_model
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.pipeline import IngestResult

pytestmark = pytest.mark.sample

RULES = load_catalogue()

# Findings per rule and file; every rule not listed finds nothing in that file.
EXPECTED_FINDINGS = {
    "arc": {
        "value_is_property_name": 7_650,
        "value_is_empty": 473,
    },
    "mep": {
        "value_is_property_name": 29_967,
        "value_is_empty": 1_961,
        "conflicting_property": 2,
        "quantity_not_a_measure": 44,
    },
    "ele": {
        "value_is_property_name": 1,
        "value_is_not_applicable": 3_018,
        "value_is_empty": 273,
        "conflicting_property": 2,
        "omniclass_unparsed": 1,
        "incomplete_classification_reference": 85,
    },
    "plu": {
        "value_is_property_name": 921,
        "value_is_not_applicable": 11_464,
        "value_is_empty": 1_759,
        "conflicting_property": 225,
        "quantity_not_a_measure": 61,
        "omniclass_unparsed": 1,
        "incomplete_classification_reference": 263,
    },
}
EXPECTED_SEVERITIES = {
    "arc": {"error": 0, "warning": 7_650, "info": 473},
    "mep": {"error": 0, "warning": 30_013, "info": 1_961},
    "ele": {"error": 0, "warning": 89, "info": 3_291},
    "plu": {"error": 0, "warning": 1_471, "info": 13_223},
}


@pytest.fixture(scope="module")
def checked(duplex: tuple[Engine, dict[str, IngestResult]]) -> dict[str, DqResult]:
    engine, ingested = duplex
    with engine.begin() as connection:
        sync_rules(connection, RULES)
    return {key: check_model(engine, ingested[key].model_id, RULES) for key in report.FILES}


def _nonzero(result: DqResult) -> dict[str, int]:
    return {rule_id: n for rule_id, n in result.finding_counts.items() if n}


@pytest.mark.parametrize("key", report.FILES)
def test_every_rule_finds_the_pinned_number(
    duplex: tuple[Engine, dict[str, IngestResult]], checked: dict[str, DqResult], key: str
) -> None:
    engine, _ = duplex
    with engine.connect() as connection:
        stored = dict(
            connection.execute(
                text(
                    "SELECT rule_id, count(*) FROM ifc.dq_result WHERE dq_run_id = :run "
                    "GROUP BY rule_id"
                ),
                {"run": checked[key].run_id},
            ).all()
        )

    assert stored == _nonzero(checked[key]) == EXPECTED_FINDINGS[key]
    assert checked[key].severity_counts == EXPECTED_SEVERITIES[key]


@pytest.mark.parametrize("key", report.FILES)
def test_issue_findings_are_the_transforms_issues(checked: dict[str, DqResult], key: str) -> None:
    issue_rules = {rule.rule_id for rule in RULES if rule.issue_code is not None}

    from_issues = {r: n for r, n in _nonzero(checked[key]).items() if r in issue_rules}

    assert from_issues == TRANSFORM_FIGURES[key]["issues"]


@pytest.mark.parametrize("key", report.FILES)
def test_placeholder_findings_match_the_transform_figures(
    checked: dict[str, DqResult], key: str
) -> None:
    figures = TRANSFORM_FIGURES[key]["property_values"]
    counts = checked[key].finding_counts

    assert counts["value_is_property_name"] == figures["placeholder (value = name)"]
    assert counts["value_is_not_applicable"] == figures.get("placeholder (value = 'N/A')", 0)
    # value_is_empty also counts whitespace-only strings; the files have none.
    assert counts["value_is_empty"] == figures["empty string"]


def test_the_model_summary_and_v_dq_finding_agree_with_the_runs(
    duplex: tuple[Engine, dict[str, IngestResult]], checked: dict[str, DqResult]
) -> None:
    engine, _ = duplex
    with engine.connect() as connection:
        summary = {
            row.model_id: (
                row.dq_run_id,
                row.dq_error_count,
                row.dq_warning_count,
                row.dq_info_count,
            )
            for row in connection.execute(text("SELECT * FROM ifc.v_model_summary"))
        }
        findings = dict(
            connection.execute(
                text("SELECT model_id, count(*) FROM ifc.v_dq_finding GROUP BY model_id")
            ).all()
        )

    for result in checked.values():
        counts = result.severity_counts
        assert summary[result.model_id] == (
            result.run_id,
            counts["error"],
            counts["warning"],
            counts["info"],
        )
        assert findings[result.model_id] == sum(counts.values())
