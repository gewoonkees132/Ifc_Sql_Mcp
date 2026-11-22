"""ifc.v_storey_alignment on a small project with the mismatches it is meant to reveal.

Three discipline models of project 2011-07: MEP spells Level 1 with other case and
spacing, ELE puts Level 2 fifty millimetres lower, and only ARC has a foundation level.
A model of another project, a model without a project name, a space and an unnamed
storey must stay out of the comparison.

Which models are one project is model.project_group (audit finding A12): the project
given at ingest, else the IFC project number, else its name, never one of Revit's
placeholders; and only current models are compared, not those a re-export replaced.
"""

from decimal import Decimal

import pytest
from rows import add_model, add_spatial
from sqlalchemy import Connection, text

from ifc_sql_mcp.server import tools


@pytest.fixture
def project(conn: Connection) -> dict[str, int]:
    models = {
        discipline: add_model(conn, discipline=discipline, project_name="2011-07")
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

    other = add_model(conn, discipline="ARC", project_name="2011-08")
    add_spatial(conn, other, "IfcBuildingStorey", "Level 1", elevation_m=Decimal("0.000"))
    nameless = add_model(conn, discipline="ARC", project_name=None)
    add_spatial(conn, nameless, "IfcBuildingStorey", "Level 1", elevation_m=Decimal("0.000"))
    return models


def _alignment(conn: Connection, project_group: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT storey_key, discipline, storey_name, elevation_m, storey_model_count, "
                "project_model_count, elevation_spread_m FROM ifc.v_storey_alignment "
                "WHERE project_group = :p ORDER BY storey_key, discipline"
            ),
            {"p": project_group},
        )
    ]


def test_storeys_match_on_the_normalised_name_and_show_each_elevation(
    conn: Connection, project: dict[str, int]
) -> None:
    d = Decimal
    assert _alignment(conn, "2011-07") == [
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
    assert _alignment(conn, "2011-08") == [
        ("level 1", "ARC", "Level 1", d("0.000"), 1, 1, d("0.000"))
    ]
    projects = conn.execute(
        text("SELECT DISTINCT project_group FROM ifc.v_storey_alignment ORDER BY 1")
    ).scalars()
    assert list(projects) == ["2011-07", "2011-08"]


def test_misaligned_storeys_are_one_where_clause_away(
    conn: Connection, project: dict[str, int]
) -> None:
    """The query the view's comment suggests for "what does not line up?"."""
    misaligned = conn.execute(
        text(
            "SELECT DISTINCT storey_key FROM ifc.v_storey_alignment "
            "WHERE project_group = '2011-07' "
            "AND (storey_model_count < project_model_count OR elevation_spread_m <> 0) "
            "ORDER BY 1"
        )
    ).scalars()
    assert list(misaligned) == ["level 2", "t/fdn"]


# --------------------------------------------------------------------------- projects, versions


def _group(conn: Connection, model_id: int) -> object:
    return conn.execute(
        text("SELECT project_group FROM ifc.model WHERE model_id = :m"), {"m": model_id}
    ).scalar_one()


@pytest.mark.parametrize(
    ("values", "group"),
    [
        ({"project_name": "2011-07", "project_long_name": "Duplex Apartment"}, "2011-07"),
        # Revit's default number: the name says which project it is.
        ({"project_name": "0001", "project_long_name": "Duplex Apartment"}, "Duplex Apartment"),
        # Both are defaults, as on the audit's Italian tower: no project at all.
        ({"project_name": "0001", "project_long_name": "Project Name"}, None),
        ({"project_name": "Project Number", "project_long_name": "Project Name"}, None),
        ({"project_name": " ", "project_long_name": None}, None),
        # A project given at ingest comes first, whatever the file says.
        (
            {"project_name": "0001", "project_long_name": "Project Name", "project_key": "tower"},
            "tower",
        ),
    ],
)
def test_the_project_group_never_comes_from_a_revit_placeholder(
    conn: Connection, values: dict[str, str | None], group: str | None
) -> None:
    assert _group(conn, add_model(conn, **values)) == group


def test_two_models_with_revit_defaults_are_not_one_project(conn: Connection) -> None:
    for name in ("Duplex_A_20110907.ifc", "R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc"):
        model_id = add_model(
            conn, file_name=name, project_name="0001", project_long_name="Project Name"
        )
        add_spatial(conn, model_id, "IfcBuildingStorey", "Level 1", elevation_m=Decimal("0.000"))

    assert conn.execute(text("SELECT count(*) FROM ifc.v_storey_alignment")).scalar_one() == 0


def test_a_replaced_model_is_not_current_and_not_compared(conn: Connection) -> None:
    first = add_model(conn, project_name="001-00", discipline="ARC")
    later = add_model(conn, project_name="001-00", discipline="ARC", replaces_model_id=first)
    for model_id in (first, later):
        add_spatial(conn, model_id, "IfcBuildingStorey", "Level 1", elevation_m=Decimal("0.000"))

    current = conn.execute(
        text("SELECT model_id FROM ifc.v_model_current WHERE model_id IN (:a, :b)"),
        {"a": first, "b": later},
    ).scalars()
    assert list(current) == [later]
    assert _alignment(conn, "001-00") == [
        ("level 1", "ARC", "Level 1", Decimal("0.000"), 1, 1, Decimal("0.000"))
    ]


def test_list_models_leaves_replaced_models_out_unless_asked(conn: Connection) -> None:
    first = add_model(conn, project_name="001-00")
    later = add_model(conn, project_name="001-00", replaces_model_id=first)

    current = tools.list_models(conn)
    every = tools.list_models(conn, include_replaced=True)

    assert later in [m.model_id for m in current.models]
    assert first not in [m.model_id for m in current.models]
    assert current.replaced_left_out >= 1
    old = next(m for m in every.models if m.model_id == first)
    new = next(m for m in every.models if m.model_id == later)
    assert (old.replaced_by_model_id, new.replaces_model_id, new.project_group) == (
        later,
        first,
        "001-00",
    )
    assert every.replaced_left_out == 0
