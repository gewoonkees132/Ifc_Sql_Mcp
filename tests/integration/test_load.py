"""The loader on the synthetic fixture: every record arrives intact, and the views answer.

Each test loads the fixture inside the rolled-back ``conn`` transaction. References
between rows of one model are only checked at commit, which never comes, so the
``loaded`` fixture runs ``SET CONSTRAINTS ALL IMMEDIATE`` to check them at once.
"""

import dataclasses
from decimal import Decimal
from typing import Any

import make_revit_quirks as fx
import pytest
from counts import FIXTURE_ROWS, table_counts
from rows import new_sha256, violates
from sqlalchemy import Connection, RowMapping, select, text

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.load import LoadResult, copy_rows, load
from ifc_sql_mcp.ingest.records import (
    ClassificationRecord,
    ElementMaterialLayerRecord,
    ElementRecord,
    MaterialRecord,
    ModelRecord,
    ModelRecords,
    ObjectRecord,
    PropertyRecord,
    QuantityRecord,
    SpatialElementRecord,
)


@pytest.fixture
def loaded(conn: Connection, fixture_records: ModelRecords) -> LoadResult:
    result = load(conn, fixture_records, "ARC")
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return result


def _rows(conn: Connection, sql: str, **params: object) -> list[RowMapping]:
    return list(conn.execute(text(sql), params).mappings())


def _one(conn: Connection, sql: str, **params: object) -> dict[str, Any]:
    rows = _rows(conn, sql, **params)
    assert len(rows) == 1, rows
    return dict(rows[0])


# --------------------------------------------------------------------------- counts and contents


def test_every_table_gets_one_row_per_record(conn: Connection, loaded: LoadResult) -> None:
    assert table_counts(conn, loaded.model_id) == FIXTURE_ROWS
    assert loaded.row_counts == FIXTURE_ROWS


# Each query reads one table back with ids translated into the records' natural keys.
READ_BACK = {
    "objects": (
        ObjectRecord,
        "SELECT global_id, object_kind, ifc_class, ifc_class_base, name "
        "FROM ifc.object WHERE model_id = :m ORDER BY global_id",
    ),
    "spatial_elements": (
        SpatialElementRecord,
        "SELECT o.global_id, parent.global_id, s.long_name, s.elevation_m "
        "FROM ifc.spatial_element AS s JOIN ifc.object AS o USING (object_id) "
        "LEFT JOIN ifc.object AS parent ON s.parent_id = parent.object_id "
        "WHERE o.model_id = :m ORDER BY o.global_id",
    ),
    "elements": (
        ElementRecord,
        "SELECT o.global_id, container.global_id, storey.global_id, e.tag, e.is_feature, "
        "e.revit_family, e.revit_type, e.host_revit_family, e.host_revit_type, "
        "e.revit_element_id, e.type_name, e.predefined_type "
        "FROM ifc.element AS e JOIN ifc.object AS o USING (object_id) "
        "LEFT JOIN ifc.object AS container ON e.container_id = container.object_id "
        "LEFT JOIN ifc.object AS storey ON e.storey_id = storey.object_id "
        "WHERE o.model_id = :m ORDER BY o.global_id",
    ),
    "properties": (
        PropertyRecord,
        "SELECT o.global_id, p.pset_name, p.prop_name, p.value_type, p.value_text, "
        "p.value_num, p.value_bool, p.unit, p.is_type_property, p.is_implausible "
        "FROM ifc.property AS p JOIN ifc.object AS o USING (object_id) "
        "WHERE o.model_id = :m ORDER BY o.global_id, p.pset_name, p.prop_name",
    ),
    "quantities": (
        QuantityRecord,
        "SELECT o.global_id, q.name, q.source, q.value, q.source_ref "
        "FROM ifc.quantity AS q JOIN ifc.object AS o USING (object_id) "
        "WHERE o.model_id = :m ORDER BY o.global_id, q.name, q.source",
    ),
    "materials": (
        MaterialRecord,
        "SELECT name FROM ifc.material WHERE model_id = :m ORDER BY name",
    ),
    "material_layers": (
        ElementMaterialLayerRecord,
        "SELECT o.global_id, l.layer_index, mat.name, l.thickness_m "
        "FROM ifc.element_material_layer AS l "
        "JOIN ifc.object AS o ON l.element_id = o.object_id "
        "JOIN ifc.material AS mat USING (material_id) "
        "WHERE o.model_id = :m ORDER BY o.global_id, l.layer_index",
    ),
    "classifications": (
        ClassificationRecord,
        "SELECT o.global_id, c.system, c.code, c.title "
        "FROM ifc.classification AS c JOIN ifc.object AS o USING (object_id) "
        "WHERE o.model_id = :m ORDER BY o.global_id, c.system, c.code",
    ),
}


@pytest.mark.parametrize("field", sorted(READ_BACK))
def test_rows_read_back_equal_the_records(
    conn: Connection, loaded: LoadResult, fixture_records: ModelRecords, field: str
) -> None:
    """Exact equality: ids map to the right objects, and COPY's text format loses nothing."""
    record_type, sql = READ_BACK[field]
    stored = tuple(record_type(*row) for row in conn.execute(text(sql), {"m": loaded.model_id}))
    assert stored == getattr(fixture_records, field)


def test_model_row_has_the_record_and_the_discipline(
    conn: Connection, loaded: LoadResult, fixture_records: ModelRecords
) -> None:
    model = schema.model
    columns = [model.c[field.name] for field in dataclasses.fields(ModelRecord)]
    statement = select(*columns, model.c.discipline).where(model.c.model_id == loaded.model_id)
    row = dict(conn.execute(statement).mappings().one())

    assert row.pop("discipline") == "ARC"
    assert ModelRecord(**row) == fixture_records.model


def test_issues_keep_their_order_and_point_at_their_objects(
    conn: Connection, loaded: LoadResult, fixture_records: ModelRecords
) -> None:
    rows = _rows(
        conn,
        "SELECT i.issue_index, i.code, i.global_id, i.message, o.global_id AS object_global_id "
        "FROM ifc.ingest_issue AS i LEFT JOIN ifc.object AS o USING (object_id) "
        "WHERE i.model_id = :m ORDER BY i.issue_index",
        m=loaded.model_id,
    )

    assert [row["issue_index"] for row in rows] == list(range(1, len(rows) + 1))
    assert (
        tuple(Issue(IssueCode(row["code"]), row["global_id"], row["message"]) for row in rows)
        == fixture_records.issues
    )
    # Every issue in the fixture concerns a loaded object, so each one is linked.
    assert [row["object_global_id"] for row in rows] == [row["global_id"] for row in rows]
    negative_area = next(row for row in rows if row["code"] == "negative_quantity")
    assert negative_area["object_global_id"] == fx.SLAB


# --------------------------------------------------------------------------- the views


def test_v_element_describes_the_stud_wall(conn: Connection, loaded: LoadResult) -> None:
    wall = _one(conn, "SELECT * FROM ifc.v_element WHERE global_id = :g", g=fx.WALL)

    assert {key: wall[key] for key in ("ifc_class", "ifc_class_base", "discipline")} == {
        "ifc_class": "IfcWallStandardCase",
        "ifc_class_base": "IfcWall",
        "discipline": "ARC",
    }
    assert (wall["revit_family"], wall["revit_type"], wall["revit_element_id"]) == (
        "Basic Wall",
        "Interior - Stud Partition",
        200001,
    )
    assert (wall["storey_name"], wall["storey_elevation_m"], wall["space_id"]) == (
        "Level 1",
        Decimal("0.000"),
        None,
    )
    # Written in feet, stored in metres.
    assert wall["length_m"] == pytest.approx(fx.WALL_LENGTH_M)
    assert wall["area_m2"] == pytest.approx(fx.WALL_AREA_M2)
    assert wall["volume_m3"] == pytest.approx(fx.WALL_VOLUME_M3)
    assert wall["materials"] == "Plasterboard / Metal Stud / Plasterboard"


def test_v_element_puts_the_chair_in_the_kitchen(conn: Connection, loaded: LoadResult) -> None:
    chair = _one(conn, "SELECT * FROM ifc.v_element WHERE global_id = :g", g=fx.CHAIR)

    assert (chair["space_number"], chair["space_name"], chair["storey_name"]) == (
        "F101",
        "Kitchen",
        "Level 1",
    )
    assert (chair["revit_family"], chair["revit_type"], chair["type_name"]) == (
        "M_Chair-Breuer",
        "Chair-Breuer",
        "Chair-Breuer",
    )
    assert chair["materials"] is None


def test_v_space_schedule_gives_the_kitchen_its_area_and_omniclass_code(
    conn: Connection, loaded: LoadResult
) -> None:
    kitchen = _one(conn, "SELECT * FROM ifc.v_space_schedule WHERE global_id = :g", g=fx.KITCHEN)

    assert (kitchen["number"], kitchen["name"], kitchen["storey_name"]) == (
        "F101",
        "Kitchen",
        "Level 1",
    )
    assert (kitchen["omniclass_code"], kitchen["omniclass_title"]) == (
        "13-51 24 17",
        "Kitchen Space",
    )
    assert kitchen["area_m2"] == pytest.approx(fx.KITCHEN_AREA_M2)
    assert kitchen["perimeter_m"] == pytest.approx(fx.KITCHEN_PERIMETER_M)
    assert kitchen["height_m"] == pytest.approx(fx.KITCHEN_HEIGHT_M)
    assert kitchen["volume_m3"] == pytest.approx(fx.KITCHEN_VOLUME_M3)
    # The GSA quantity set (ifc_qto) outranks the Revit parameter for the area.
    source = _one(
        conn,
        "SELECT q.source FROM ifc.v_quantity AS q JOIN ifc.object AS o USING (object_id) "
        "WHERE o.global_id = :g AND q.name = 'area_m2'",
        g=fx.KITCHEN,
    )
    assert source == {"source": "ifc_qto"}


def test_v_takeoff_counts_plasterboard_on_both_faces_of_the_stud_wall(
    conn: Connection, loaded: LoadResult
) -> None:
    plasterboard = _one(
        conn,
        "SELECT element_count, length_m, area_m2, volume_m3, storey_name FROM ifc.v_takeoff "
        "WHERE model_id = :m AND ifc_class_base = 'IfcWall' AND material_name = 'Plasterboard'",
        m=loaded.model_id,
    )

    assert (plasterboard["element_count"], plasterboard["storey_name"]) == (1, "Level 1")
    assert plasterboard["length_m"] == pytest.approx(fx.WALL_LENGTH_M)
    assert plasterboard["area_m2"] == pytest.approx(2 * fx.WALL_AREA_M2)
    assert plasterboard["volume_m3"] == pytest.approx(fx.WALL_AREA_M2 * 2 * fx.PLASTERBOARD_M)


def test_v_model_summary_counts_what_was_loaded(conn: Connection, loaded: LoadResult) -> None:
    summary = _one(
        conn,
        "SELECT storey_count, space_count, element_count, property_count, quantity_count, "
        "material_count, dq_run_id FROM ifc.v_model_summary WHERE model_id = :m",
        m=loaded.model_id,
    )

    assert summary == {
        "storey_count": 3,
        "space_count": 2,
        "element_count": 13,
        "property_count": 36,
        "quantity_count": 8,
        "material_count": 6,
        "dq_run_id": None,
    }


# --------------------------------------------------------------------------- edge cases


def _without_rows(records: ModelRecords, *issues: Issue) -> ModelRecords:
    """The fixture's model row with nothing else, plus the given issues."""
    return ModelRecords(
        model=dataclasses.replace(records.model, file_sha256=new_sha256()),
        objects=(),
        spatial_elements=(),
        elements=(),
        properties=(),
        quantities=(),
        materials=(),
        material_layers=(),
        classifications=(),
        issues=issues,
    )


def test_a_model_without_objects_or_materials_loads(
    conn: Connection, fixture_records: ModelRecords
) -> None:
    result = load(conn, _without_rows(fixture_records), "MEP")

    assert result.row_counts == {table: 1 if table == "model" else 0 for table in FIXTURE_ROWS}
    assert table_counts(conn, result.model_id) == result.row_counts


def test_an_issue_about_an_object_that_was_not_loaded_keeps_its_global_id(
    conn: Connection, fixture_records: ModelRecords
) -> None:
    records = _without_rows(
        fixture_records,
        Issue(IssueCode.INVALID_GLOBAL_ID, "not-a-global-id", "IfcWall 'x' has a malformed id"),
        Issue(IssueCode.MULTIPLE_PROJECTS, None, "the file has 2 IfcProjects"),
    )

    result = load(conn, records, "ARC")

    rows = _rows(
        conn,
        "SELECT code, object_id, global_id FROM ifc.ingest_issue WHERE model_id = :m "
        "ORDER BY issue_index",
        m=result.model_id,
    )
    assert [dict(row) for row in rows] == [
        {"code": "invalid_global_id", "object_id": None, "global_id": "not-a-global-id"},
        {"code": "multiple_projects", "object_id": None, "global_id": None},
    ]


def test_the_same_file_cannot_be_loaded_twice(
    conn: Connection, fixture_records: ModelRecords
) -> None:
    """The constraint the pipeline relies on when two ingests of one file race."""
    load(conn, fixture_records, "ARC")

    with violates(conn, "uq_model_file_sha256"):
        load(conn, fixture_records, "ARC")


def test_a_changed_file_is_a_second_model_with_the_same_global_ids(
    conn: Connection, fixture_records: ModelRecords
) -> None:
    changed = dataclasses.replace(
        fixture_records, model=dataclasses.replace(fixture_records.model, file_sha256=new_sha256())
    )

    first = load(conn, fixture_records, "ARC")
    second = load(conn, changed, "ARC")

    walls = _rows(
        conn,
        "SELECT model_id FROM ifc.object WHERE global_id = :g ORDER BY model_id",
        g=fx.WALL,
    )
    assert [row["model_id"] for row in walls] == [first.model_id, second.model_id]


def test_copy_rows_refuses_a_row_without_every_column(conn: Connection) -> None:
    with pytest.raises(KeyError, match="source"):
        copy_rows(conn, schema.quantity, [{"object_id": 1, "name": "length_m", "value": 1.0}])
