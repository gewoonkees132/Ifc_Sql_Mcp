"""Print the audit's acceptance figures for a database holding the five audit files.

Usage: python -I figures.py <database URL> [--json]

The database must hold files A to E (and may hold the Duplex), loaded and checked as in
README.md, at whatever version of the tool is being measured. Each figure is computed
the way a reader of the database would get it: SQL on the views and tables, or the MCP
tool functions where the figure is about a tool's answer. A figure that the version
measured cannot produce (a column or tool it does not have yet) prints as "n/a".

The same script measured the code before the fixes and after them, so the two tables in
docs/audit/2026-11-implementation.md compare like with like.
"""

# ruff: noqa: T201

import json
import sys
from collections.abc import Callable
from typing import Any

from sqlalchemy import Connection, create_engine, text

from ifc_sql_mcp.server import tools

FILES = {
    "a": "Ifc4_Revit_ARC.ifc",
    "b": "R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc",
    "c": "Ifc4_Revit_MEP.ifc",
    "d": "Ifc4_Revit_ARC_FireRatingAdded.ifc",
    "e": "ARK_NUS_skolebygg.ifc",
}
CV003 = "Basic Wall:1AR_MUR_CV003 _Interno_Esterno Poroton Ventilata"

type Measure = Callable[[Connection, dict[str, int]], Any]


def sql(query: str, key: str, **params: str) -> Measure:
    """A figure computed by one query on the model of file ``key`` (bound as ``:m``)."""
    return lambda c, m: c.execute(text(query), {"m": m[key], **params}).scalar()


def findings(rule: str, key: str) -> Measure:
    """The findings of one rule in the model's latest data quality run."""
    return sql(
        "SELECT count(*) FROM ifc.v_dq_finding WHERE model_id = :m AND rule_id = :rule",
        key,
        rule=rule,
    )


def takeoff_group(c: Connection, m: dict[str, int]) -> Any:
    """Elements quantity_takeoff counts for one wall type of file B."""
    result = tools.quantity_takeoff(c, m["b"], "revit_type", "count")
    return next(g.elements for g in result.groups if g.group == CV003)


def takeoff_total(c: Connection, m: dict[str, int]) -> Any:
    """The total quantity_takeoff gives for wall area by type on file B."""
    return tools.quantity_takeoff(c, m["b"], "revit_type", "area_m2").total


def severities(c: Connection, m: dict[str, int]) -> Any:
    """File B's findings per severity in its latest data quality run."""
    row = c.execute(
        text(
            "SELECT dq_error_count, dq_warning_count, dq_info_count FROM ifc.v_model_summary "
            "WHERE model_id = :m"
        ),
        {"m": m["b"]},
    ).one()
    return f"{row[0]:,} / {row[1]:,} / {row[2]:,}"


WITH_WIDTH = """
SELECT count(DISTINCT q.object_id) FROM ifc.quantity AS q JOIN ifc.object AS o USING (object_id)
WHERE o.model_id = :m AND o.ifc_class_base = :class AND q.name = 'width_m'
"""

FIGURES: list[tuple[str, str, str, Measure]] = [
    (
        "A1",
        "A: wall area (v_element.area_m2), m²",
        "946.0",
        sql(
            "SELECT round(sum(area_m2)::numeric, 1) FROM ifc.v_element "
            "WHERE model_id = :m AND ifc_class_base = 'IfcWall'",
            "a",
        ),
    ),
    (
        "A1",
        "C: spaces with an area",
        "163 of 163",
        sql(
            "SELECT count(area_m2) || ' of ' || count(*) FROM ifc.v_space_schedule "
            "WHERE model_id = :m",
            "c",
        ),
    ),
    (
        "A2",
        "A: sum of stored Qto_WallBaseQuantities.NetSideArea, m²",
        "946.0",
        sql(
            "SELECT round(sum(p.value_num)::numeric, 1) FROM ifc.property AS p "
            "JOIN ifc.object AS o USING (object_id) WHERE o.model_id = :m "
            "AND p.pset_name = 'Qto_WallBaseQuantities' AND p.prop_name = 'NetSideArea'",
            "a",
        ),
    ),
    ("A3", "B: elements of wall type 1AR_MUR_CV003 (quantity_takeoff)", "1,165", takeoff_group),
    ("A4", "B: doors with width_m", "1,458 of 1,458", sql(WITH_WIDTH, "b", **{"class": "IfcDoor"})),
    (
        "A4",
        "B: windows with width_m",
        "1,199 of 1,199",
        sql(WITH_WIDTH, "b", **{"class": "IfcWindow"}),
    ),
    ("A4", "B: unsupported_property_set findings", "0", findings("unsupported_property_set", "b")),
    (
        "A6",
        "A: windows with a material",
        "17 of 17",
        sql(
            "SELECT count(materials) FROM ifc.v_element "
            "WHERE model_id = :m AND ifc_class_base = 'IfcWindow'",
            "a",
        ),
    ),
    ("A6", "C: unsupported_material findings", "0", findings("unsupported_material", "c")),
    ("A7", "C: duplicate_space findings", "47", findings("duplicate_space", "c")),
    ("A8", "B: area takeoff by Revit type, total", "null", takeoff_total),
    ("A9", "B: findings error / warning / info", "(model gaps, not loader gaps)", severities),
    ("A9", "B: model_without_quantities findings", "1", findings("model_without_quantities", "b")),
    ("A9", "C: space_without_area findings", "0", findings("space_without_area", "c")),
    (
        "A12",
        "B: other models it is aligned with (v_storey_alignment)",
        "0",
        sql(
            "SELECT coalesce(max(project_model_count) - 1, 0) FROM ifc.v_storey_alignment "
            "WHERE model_id = :m",
            "b",
        ),
    ),
    (
        "A13",
        "B: measure_not_converted findings",
        "0 for U-values",
        findings("measure_not_converted", "b"),
    ),
    (
        "A13",
        "B: revit_tag_mismatch findings on openings",
        "0",
        sql(
            "SELECT count(*) FROM ifc.v_dq_finding WHERE model_id = :m "
            "AND rule_id = 'revit_tag_mismatch' AND ifc_class LIKE 'IfcOpening%'",
            "b",
        ),
    ),
    (
        "A14",
        "B: stair flights with a Revit type",
        "182 of 182",
        sql(
            "SELECT count(revit_type) FROM ifc.v_element "
            "WHERE model_id = :m AND ifc_class_base = 'IfcStairFlight'",
            "b",
        ),
    ),
    (
        "A16",
        "B: elements whose revit_type holds a control character",
        "0",
        sql(
            "SELECT count(*) FROM ifc.element AS e JOIN ifc.object AS o USING (object_id) "
            r"WHERE o.model_id = :m AND e.revit_type ~ '[\x01-\x1f]'",
            "b",
        ),
    ),
]


def measure(url: str) -> list[dict[str, str]]:
    """Compute every figure; a figure the database cannot produce is "n/a"."""
    engine = create_engine(url)
    rows = []
    try:
        with engine.connect() as connection:
            models = {
                key: connection.execute(
                    text("SELECT max(model_id) FROM ifc.model WHERE file_name = :f"), {"f": name}
                ).scalar_one()
                for key, name in FILES.items()
            }
            for finding, figure, truth, compute in FIGURES:
                savepoint = connection.begin_nested()
                try:
                    value = compute(connection, models)
                    savepoint.commit()
                except Exception:
                    savepoint.rollback()
                    value = "n/a"
                shown = f"{value:,}" if isinstance(value, int) else str(value)
                rows.append({"finding": finding, "figure": figure, "truth": truth, "value": shown})
    finally:
        engine.dispose()
    return rows


def main() -> None:
    """Print the figures as a Markdown table, or as JSON with ``--json``."""
    rows = measure(sys.argv[1])
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=2))
        return
    print("| Finding | Figure | Audit's ground truth | Measured |\n|---|---|---|---:|")
    for row in rows:
        print(f"| {row['finding']} | {row['figure']} | {row['truth']} | {row['value']} |")


if __name__ == "__main__":
    main()
