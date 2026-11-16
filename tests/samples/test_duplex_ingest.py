"""The four Duplex files, ingested into a throwaway database: every row and the storeys.

The ``duplex`` fixture (``conftest.py``) ingests them once per session.

Deselected by default. Run with::

    make samples && uv run pytest -m sample

Each table must hold exactly one row per record the transform produced for the file
(the figures below), so nothing was lost or added on the way into the database. The
issue codes must match ``test_duplex_samples.EXPECTED``, the figures the transform tests
pin for docs/revit-ifc-quirks.md.
"""

from decimal import Decimal

import pytest
import quirks_report as report
from counts import table_counts
from duplex import DISCIPLINES, sample_path
from sqlalchemy import Engine, text
from test_duplex_samples import EXPECTED as TRANSFORM_FIGURES

from ifc_sql_mcp.ingest.pipeline import IngestResult, ingest_file

pytestmark = pytest.mark.sample

# Rows per table: the record counts the transform produces for each file.
EXPECTED_ROWS = {
    "arc": {
        "object": 295,
        "spatial_element": 27,
        "element": 268,
        "property": 13_455,
        "quantity": 426,
        "material": 18,
        "element_material_layer": 271,
        "classification": 138,
        "ingest_issue": 0,
    },
    "mep": {
        "object": 973,
        "spatial_element": 47,
        "element": 926,
        "property": 63_683,
        "quantity": 643,
        "material": 0,
        "element_material_layer": 0,
        "classification": 549,
        "ingest_issue": 46,
    },
    "ele": {
        "object": 109,
        "spatial_element": 6,
        "element": 103,
        "property": 6_862,
        "quantity": 7,
        "material": 6,
        "element_material_layer": 36,
        "classification": 117,
        "ingest_issue": 88,
    },
    "plu": {
        "object": 504,
        "spatial_element": 6,
        "element": 498,
        "property": 41_649,
        "quantity": 236,
        "material": 3,
        "element_material_layer": 6,
        "classification": 261,
        "ingest_issue": 550,
    },
}


@pytest.mark.parametrize("key", report.FILES)
def test_every_table_holds_one_row_per_record(
    duplex: tuple[Engine, dict[str, IngestResult]], key: str
) -> None:
    engine, results = duplex
    result = results[key]
    expected = {"model": 1} | EXPECTED_ROWS[key]

    assert (result.status, result.discipline) == ("succeeded", DISCIPLINES[key])
    with engine.connect() as connection:
        assert table_counts(connection, result.model_id) == expected
    assert result.row_counts == expected


@pytest.mark.parametrize("key", report.FILES)
def test_stored_issues_match_the_transform_figures(
    duplex: tuple[Engine, dict[str, IngestResult]], key: str
) -> None:
    engine, results = duplex
    with engine.connect() as connection:
        stored = dict(
            connection.execute(
                text(
                    "SELECT code, count(*) FROM ifc.ingest_issue WHERE model_id = :m GROUP BY code"
                ),
                {"m": results[key].model_id},
            ).all()
        )
    assert stored == results[key].issue_counts == TRANSFORM_FIGURES[key]["issues"]


def test_storeys_line_up_across_the_four_disciplines(
    duplex: tuple[Engine, dict[str, IngestResult]],
) -> None:
    """Level 1, Level 2 and Roof agree everywhere, in three units; T/FDN is ARC's alone."""
    engine, _ = duplex
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT storey_key, discipline, elevation_m, storey_model_count, "
                "project_model_count, elevation_spread_m FROM ifc.v_storey_alignment "
                "WHERE project_name = '0001' ORDER BY elevation_m, storey_key, discipline"
            )
        ).all()

    shared = [
        (key, discipline, Decimal(elevation), 4, 4, Decimal("0.000"))
        for key, elevation in (("level 1", "0.000"), ("level 2", "3.100"), ("roof", "6.000"))
        for discipline in ("ARC", "ELE", "MEP", "PLU")
    ]
    only_arc = [("t/fdn", "ARC", Decimal("-1.250"), 1, 4, Decimal("0.000"))]
    assert [tuple(row) for row in rows] == only_arc + shared


def test_ingesting_the_four_files_again_skips_them_all(
    duplex: tuple[Engine, dict[str, IngestResult]],
) -> None:
    engine, results = duplex

    again = {key: ingest_file(engine, sample_path(key), DISCIPLINES[key]) for key in report.FILES}

    assert {key: (r.status, r.model_id) for key, r in again.items()} == {
        key: ("skipped", r.model_id) for key, r in results.items()
    }
    with engine.connect() as connection:
        models = connection.execute(text("SELECT count(*) FROM ifc.model")).scalar_one()
    assert models == 4
