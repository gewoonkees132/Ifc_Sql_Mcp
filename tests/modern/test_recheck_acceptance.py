"""The re-check of the audit's workflow on the fixed tool, as tests: one per new finding.

Deselected by default. Run with::

    uv run python scripts/download_audit_samples.py && uv run pytest -m modern

The method is test_audit_acceptance.py's. Every expected figure is ground truth read with
IfcOpenShell (and shapely, for footprints) by a script that never imports this package:
docs/audit/harness/space_truth.py and quantity_sets.py, whose output is in
docs/audit/evidence/recheck/. A finding not fixed yet is marked ``xfail(strict=True)``,
and its fix removes the marker in the same commit, so the markers below are the open part
of round 2 (docs/audit/2026-11-recheck.md, tracking issue #22).
"""

import pytest
from audit_files import Modern

from ifc_sql_mcp.server import tools

pytestmark = pytest.mark.modern


def _open(finding: str, issue: int) -> pytest.MarkDecorator:
    """Mark the acceptance test of a re-check finding that is not fixed yet."""
    return pytest.mark.xfail(strict=True, reason=f"re-check finding {finding}, issue #{issue}")


def test_r1_each_room_is_counted_once(modern: Modern) -> None:
    with modern.engine.connect() as connection:
        c, e, mep, arc = (
            tools.space_schedule(connection, modern.model(key))
            for key in ("c", "e", "duplex_mep", "duplex")
        )

    # C: Revit Rooms and MEP Spaces of the same floor, often under other names.
    assert (c.space_count, c.duplicate_spaces) == (163, 75)
    assert round(c.total_area_m2 or 0, 1) == 8_090.7
    assert round(c.total_area_without_duplicates_m2 or 0, 1) == 4_721.4
    # E: 25 of its spaces are Revit Areas (area scheme BTA), Revit's area plans.
    assert (e.space_count, e.revit_area_spaces, e.duplicate_spaces) == (307, 25, 0)
    assert round(e.revit_area_total_m2 or 0, 1) == 22_905.9
    assert round(e.total_area_m2 or 0, 1) == 7_438.8
    assert round(e.total_area_without_duplicates_m2 or 0, 1) == 7_438.8
    # The Duplex MEP model: an "MEP Space" over every room, with another number and name.
    assert (mep.space_count, mep.duplicate_spaces) == (42, 21)
    assert round(mep.total_area_without_duplicates_m2 or 0, 1) == 386.3
    # The architectural Duplex holds each room once, as before.
    assert (arc.duplicate_spaces, round(arc.total_area_m2 or 0, 3)) == (0, 422.047)
    assert [modern.findings(k, "duplicate_space") for k in ("c", "e", "duplex_mep")] == [75, 0, 21]


def test_r2_ceilings_have_their_areas(modern: Modern) -> None:
    keys = ("a", "c", "e")
    rows = modern.rows(
        "SELECT model_id, count(*), count(area_m2), round(sum(area_m2)::numeric, 1)::float8 "
        "FROM ifc.v_element WHERE model_id = ANY(:ids) AND ifc_class_base = 'IfcCovering' "
        "GROUP BY model_id",
        ids=[modern.model(k) for k in keys],
    )
    by_key = {key: next(r[1:] for r in rows if r[0] == modern.model(key)) for key in keys}
    sources = modern.rows(
        "SELECT DISTINCT q.source_ref FROM ifc.v_quantity AS q JOIN ifc.object AS o "
        "USING (object_id) WHERE o.model_id = ANY(:ids) AND o.ifc_class_base = 'IfcCovering' "
        "AND q.name = 'area_m2'",
        ids=[modern.model(k) for k in ("a", "c")],
    )

    assert by_key == {"a": (2, 2, 154.3), "c": (43, 43, 1_755.4), "e": (259, 259, 6_879.0)}
    assert sources == [("Qto_CoveringBaseQuantities.NetArea",)]


def test_r3_enumerated_property_values_are_read(modern: Modern) -> None:
    shapes = modern.rows(
        "SELECT p.value_text, p.value_type, count(DISTINCT o.object_id) "
        "FROM ifc.object AS o JOIN ifc.property AS p USING (object_id) "
        "WHERE o.model_id = :m AND o.ifc_class = 'IfcDuctSegment' "
        "AND p.pset_name = 'Pset_DuctSegmentTypeCommon' AND p.prop_name = 'Shape' "
        "GROUP BY 1, 2",
        m=modern.model("c"),
    )

    assert modern.findings("c", "unsupported_property") == 0
    assert shapes == [("Undefined", "IfcLabel", 724)]


def test_r4_the_no_quantities_warning_names_ingest_geometry(modern: Modern) -> None:
    with modern.engine.connect() as connection:
        takeoff = tools.quantity_takeoff(connection, modern.model("b"), "revit_type", "area_m2")
    finding = modern.scalar(
        "SELECT message FROM ifc.v_dq_finding WHERE model_id = :m "
        "AND rule_id = 'model_without_quantities'",
        m=modern.model("b"),
    )

    assert "ifc-sql ingest --geometry" in takeoff.note
    assert "ifc-sql ingest --geometry" in str(finding)
