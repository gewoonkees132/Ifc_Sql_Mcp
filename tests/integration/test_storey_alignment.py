"""ifc.v_storey_alignment on a small project with the mismatches it is meant to reveal.

Three discipline models of project 0001: MEP spells Level 1 with other case and
spacing, ELE puts Level 2 fifty millimetres lower, and only ARC has a foundation level.
A model of another project, a model without a project name, a space and an unnamed
storey must stay out of the comparison.
"""

from decimal import Decimal

import pytest
from rows import add_model, add_spatial
from sqlalchemy import Connection, text


@pytest.fixture
def project(conn: Connection) -> dict[str, int]:
    models = {
        discipline: add_model(conn, discipline=discipline, project_name="0001")
        for discipline in ("ARC", "MEP", "ELE")
    }
    storeys = {
        "ARC": [("Level 1", "0.000"), ("Level 2", "3.100"), ("T/FDN", "-1.250")],
        "MEP": [(" LEVEL  1 ", "0.000"), ("Level 2", "3.100")],
        "ELE": [("Level\t1", "0.000"), ("Level 2", "3.050")],
    }
    for discipline, levels in storeys.items():
        for name, elevation in levels:
            add_spatial(
                conn, models[discipline], "IfcBuildingStorey", name, elevation_m=Decimal(elevation)
            )
    add_spatial(conn, models["ARC"], "IfcSpace", "Level 1")  # a space, not a storey
    add_spatial(conn, models["ARC"], "IfcBuildingStorey", None)  # an unnamed storey

    other = add_model(conn, discipline="ARC", project_name="0002")
    add_spatial(conn, other, "IfcBuildingStorey", "Level 1", elevation_m=Decimal("0.000"))
    nameless = add_model(conn, discipline="ARC", project_name=None)
    add_spatial(conn, nameless, "IfcBuildingStorey", "Level 1", elevation_m=Decimal("0.000"))
    return models


def _alignment(conn: Connection, project_name: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT storey_key, discipline, storey_name, elevation_m, storey_model_count, "
                "project_model_count, elevation_spread_m FROM ifc.v_storey_alignment "
                "WHERE project_name = :p ORDER BY storey_key, discipline"
            ),
            {"p": project_name},
        )
    ]


def test_storeys_match_on_the_normalised_name_and_show_each_elevation(
    conn: Connection, project: dict[str, int]
) -> None:
    d = Decimal
    assert _alignment(conn, "0001") == [
        ("level 1", "ARC", "Level 1", d("0.000"), 3, 3, d("0.000")),
        ("level 1", "ELE", "Level\t1", d("0.000"), 3, 3, d("0.000")),
        ("level 1", "MEP", " LEVEL  1 ", d("0.000"), 3, 3, d("0.000")),
        # Present everywhere, but ELE is 50 mm lower: the spread shows it.
        ("level 2", "ARC", "Level 2", d("3.100"), 3, 3, d("0.050")),
        ("level 2", "ELE", "Level 2", d("3.050"), 3, 3, d("0.050")),
        ("level 2", "MEP", "Level 2", d("3.100"), 3, 3, d("0.050")),
        # Only one of the three models has it.
        ("t/fdn", "ARC", "T/FDN", d("-1.250"), 1, 3, d("0.000")),
    ]


def test_other_projects_and_models_without_a_project_name_stay_apart(
    conn: Connection, project: dict[str, int]
) -> None:
    d = Decimal
    assert _alignment(conn, "0002") == [("level 1", "ARC", "Level 1", d("0.000"), 1, 1, d("0.000"))]
    projects = conn.execute(
        text("SELECT DISTINCT project_name FROM ifc.v_storey_alignment ORDER BY 1")
    ).scalars()
    assert list(projects) == ["0001", "0002"]


def test_misaligned_storeys_are_one_where_clause_away(
    conn: Connection, project: dict[str, int]
) -> None:
    """The query the view's comment suggests for "what does not line up?"."""
    misaligned = conn.execute(
        text(
            "SELECT DISTINCT storey_key FROM ifc.v_storey_alignment "
            "WHERE project_name = '0001' "
            "AND (storey_model_count < project_model_count OR elevation_spread_m <> 0) "
            "ORDER BY 1"
        )
    ).scalars()
    assert list(misaligned) == ["level 2", "t/fdn"]
