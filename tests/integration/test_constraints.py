"""Constraints reject bad rows, each with the constraint meant to catch it.

Every test runs in a transaction that is rolled back (the ``conn`` fixture), and every
expected failure is wrapped in a savepoint by ``violates``.
"""

from decimal import Decimal
from typing import Any

import pytest
from rows import (
    add_classification,
    add_element,
    add_layer,
    add_material,
    add_model,
    add_object,
    add_property,
    add_quantity,
    add_spatial,
    new_global_id,
    new_sha256,
    violates,
)
from sqlalchemy import Connection, delete, func, insert, select, text, update

from ifc_sql_mcp.db import schema

# --------------------------------------------------------------------------- model


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("file_sha256", "B347A2C8", "ck_model_file_sha256_hex"),
        ("file_name", "  ", "ck_model_file_name_not_blank"),
        ("file_size_bytes", 0, "ck_model_file_size_bytes_positive"),
        ("ifc_schema", "ifc2x3", "ck_model_ifc_schema_format"),
        ("discipline", "Architecture", "ck_model_discipline_format"),
        ("project_global_id", "not-a-global-id", "ck_model_project_global_id_format"),
        ("length_unit", "", "ck_model_length_unit_not_blank"),
        ("project_key", " ", "ck_model_project_key_not_blank"),
    ],
)
def test_model_rejects_malformed_values(
    conn: Connection, column: str, value: Any, constraint: str
) -> None:
    with violates(conn, constraint):
        add_model(conn, **{column: value})


def test_the_same_file_cannot_be_loaded_twice(conn: Connection) -> None:
    sha256 = new_sha256()
    add_model(conn, file_sha256=sha256)

    with violates(conn, "uq_model_file_sha256"):
        add_model(conn, file_sha256=sha256, discipline="MEP")


def test_a_model_is_replaced_once_and_never_by_itself(conn: Connection) -> None:
    first = add_model(conn)
    add_model(conn, replaces_model_id=first)

    with violates(conn, "uq_model_replaces_model_id"):
        add_model(conn, replaces_model_id=first)
    with violates(conn, "fk_model_replaces_model_id"):
        add_model(conn, replaces_model_id=999_999)
    with violates(conn, "ck_model_replaces_model_id_not_itself"):
        conn.execute(
            text("UPDATE ifc.model SET replaces_model_id = model_id WHERE model_id = :m"),
            {"m": first},
        )


def test_deleting_a_replaced_model_leaves_its_successor_current(conn: Connection) -> None:
    first = add_model(conn)
    later = add_model(conn, replaces_model_id=first)

    conn.execute(text("DELETE FROM ifc.model WHERE model_id = :m"), {"m": first})

    replaces = conn.execute(
        text("SELECT replaces_model_id FROM ifc.model WHERE model_id = :m"), {"m": later}
    ).scalar_one()
    assert replaces is None


# --------------------------------------------------------------------------- objects


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        # 22 characters, but the first one can only encode two bits (0-3).
        ("global_id", "4" + "0" * 21, "ck_object_global_id_format"),
        ("global_id", "1xS3BCk291UvhgP2a6efl", "ck_object_global_id_format"),
        ("object_kind", "product", "ck_object_object_kind_valid"),
        ("ifc_class", "Wall", "ck_object_ifc_class_format"),
        ("ifc_class_base", "ifcwall", "ck_object_ifc_class_base_format"),
    ],
)
def test_object_rejects_malformed_values(
    conn: Connection, column: str, value: str, constraint: str
) -> None:
    model_id = add_model(conn)

    values = {"ifc_class": "IfcWall", "ifc_class_base": "IfcWall"} | {column: value}

    with violates(conn, constraint):
        add_object(conn, model_id, **values)


def test_global_id_is_unique_within_a_model_but_not_across_models(conn: Connection) -> None:
    arc, mep = add_model(conn), add_model(conn, discipline="MEP")
    global_id = new_global_id()
    add_object(conn, arc, "IfcWall", global_id=global_id)
    add_object(conn, mep, "IfcWall", global_id=global_id)

    with violates(conn, "uq_object_model_id_global_id"):
        add_object(conn, arc, "IfcWall", global_id=global_id)


def test_object_needs_an_existing_model(conn: Connection) -> None:
    with violates(conn, "fk_object_model_id"):
        add_object(conn, -1, "IfcWall")


def test_spatial_element_cannot_be_its_own_parent(conn: Connection) -> None:
    storey = add_spatial(conn, add_model(conn), "IfcBuildingStorey", "Level 1")

    with violates(conn, "ck_spatial_element_parent_is_not_self"):
        conn.execute(
            update(schema.spatial_element)
            .where(schema.spatial_element.c.object_id == storey)
            .values(parent_id=storey)
        )


def test_elevation_is_rounded_to_the_millimetre(conn: Connection) -> None:
    """Revit writes Level 2 as 3.100000000000378 m; storage makes it compare equal to 3.1."""
    storey = add_spatial(
        conn, add_model(conn), "IfcBuildingStorey", "Level 2", elevation_m=3.100000000000378
    )

    elevation = conn.execute(
        select(schema.spatial_element.c.elevation_m).where(
            schema.spatial_element.c.object_id == storey
        )
    ).scalar_one()

    assert elevation == Decimal("3.100")


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("revit_element_id", 0, "ck_element_revit_element_id_positive"),
        ("predefined_type", "floor", "ck_element_predefined_type_format"),
        ("storey_id", -1, "fk_element_storey_id"),
        ("container_id", -1, "fk_element_container_id"),
    ],
)
def test_element_rejects_bad_values(
    conn: Connection, column: str, value: Any, constraint: str
) -> None:
    model_id = add_model(conn)

    with violates(conn, constraint):
        add_element(conn, model_id, "IfcSlab", **{column: value})
        # References to storeys and containers are checked at commit; run that check now.
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_a_storey_in_use_cannot_be_deleted_on_its_own(conn: Connection) -> None:
    """References within a model are checked at commit (see ``_within_model`` in schema.py).

    ``SET CONSTRAINTS ALL IMMEDIATE`` runs the pending checks now, as COMMIT would, so the
    test can see the failure without committing.
    """
    model_id = add_model(conn)
    storey = add_spatial(conn, model_id, "IfcBuildingStorey", "Level 1")
    add_element(conn, model_id, "IfcWall", storey_id=storey)
    conn.execute(delete(schema.spatial_element).where(schema.spatial_element.c.object_id == storey))

    with violates(conn, "fk_element_storey_id"):
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


# --------------------------------------------------------------------------- properties


def test_property_is_unique_per_object_set_and_name(conn: Connection) -> None:
    wall = add_element(conn, add_model(conn), "IfcWall")
    add_property(
        conn, wall, "Pset_WallCommon", "IsExternal", value_type="IfcBoolean", value_bool=True
    )

    with violates(conn, "pk_property"):
        add_property(
            conn, wall, "Pset_WallCommon", "IsExternal", value_type="IfcBoolean", value_bool=False
        )


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        (
            {"value_type": "IfcLabel", "value_text": "x", "value_num": 1.0},
            "ck_property_at_most_one_value",
        ),
        ({"value_type": "IfcLabel"}, "ck_property_value_type_iff_value"),
        ({"value_text": "Brick"}, "ck_property_value_type_iff_value"),
        (
            {"value_type": "IfcLabel", "value_text": "x", "unit": "m"},
            "ck_property_unit_only_for_numbers",
        ),
        ({"value_type": "IfcReal", "value_num": float("nan")}, "ck_property_value_num_finite"),
        ({"value_type": "IfcReal", "value_num": float("inf")}, "ck_property_value_num_finite"),
        ({"value_type": "IfcReal", "value_num": float("-inf")}, "ck_property_value_num_finite"),
        ({"value_type": "label", "value_text": "x"}, "ck_property_value_type_format"),
    ],
)
def test_property_rejects_inconsistent_values(
    conn: Connection, values: dict[str, Any], constraint: str
) -> None:
    wall = add_element(conn, add_model(conn), "IfcWall")

    with violates(conn, constraint):
        add_property(conn, wall, "PSet_Revit_Other", "Manufacturer", **values)


def test_property_accepts_what_revit_files_contain(conn: Connection) -> None:
    """Empty strings, placeholders and negatives are data (and DQ findings), not errors."""
    wall = add_element(conn, add_model(conn), "IfcWall")

    add_property(conn, wall, "Pset_WallCommon", "Reference", value_type="IfcLabel", value_text="")
    add_property(
        conn,
        wall,
        "PSet_Revit_Other",
        "Manufacturer",
        value_type="IfcLabel",
        value_text="Manufacturer",
    )
    add_property(
        conn,
        wall,
        "PSet_Revit_Constraints",
        "Top Offset",
        value_type="IfcLengthMeasure",
        value_num=-0.305,
        unit="m",
    )
    add_property(conn, wall, "PSet_Revit_Other", "No Value")  # a property without a value

    count = conn.execute(
        select(func.count())
        .select_from(schema.property_)
        .where(schema.property_.c.object_id == wall)
    ).scalar_one()
    assert count == 4


# --------------------------------------------------------------------------- quantities


@pytest.mark.parametrize(
    ("name", "source", "value", "constraint"),
    [
        ("area", "revit_pset", 1.0, "ck_quantity_name_valid"),
        ("area_m2", "manual", 1.0, "ck_quantity_source_valid"),
        ("area_m2", "revit_pset", -0.5, "ck_quantity_value_non_negative"),
        ("area_m2", "revit_pset", float("nan"), "ck_quantity_value_non_negative"),
        ("area_m2", "revit_pset", float("inf"), "ck_quantity_value_non_negative"),
    ],
)
def test_quantity_rejects_bad_rows(
    conn: Connection, name: str, source: str, value: float, constraint: str
) -> None:
    wall = add_element(conn, add_model(conn), "IfcWall")

    with violates(conn, constraint):
        add_quantity(conn, wall, name, value, source=source)


def test_one_value_per_quantity_and_source_but_sources_coexist(conn: Connection) -> None:
    space = add_spatial(conn, add_model(conn), "IfcSpace", "A101")
    add_quantity(conn, space, "area_m2", 17.94, "revit_pset", "PSet_Revit_Dimensions.Area")
    add_quantity(conn, space, "area_m2", 17.94, "ifc_qto", "GSA Space Areas.GSA BIM Area")

    with violates(conn, "pk_quantity"):
        add_quantity(conn, space, "area_m2", 18.0, "revit_pset", "PSet_Revit_Dimensions.Area")


# --------------------------------------------------------------------------- materials


def test_material_name_is_unique_per_model(conn: Connection) -> None:
    model_id = add_model(conn)
    add_material(conn, model_id, "Masonry - Brick")

    with violates(conn, "uq_material_model_id_name"):
        add_material(conn, model_id, "Masonry - Brick")


@pytest.mark.parametrize(
    ("layer_index", "thickness_m", "constraint"),
    [
        (0, 0.1, "ck_element_material_layer_layer_index_positive"),
        (1, -0.016, "ck_element_material_layer_thickness_m_non_negative"),
        (1, float("nan"), "ck_element_material_layer_thickness_m_non_negative"),
    ],
)
def test_layer_rejects_bad_rows(
    conn: Connection, layer_index: int, thickness_m: float, constraint: str
) -> None:
    model_id = add_model(conn)
    wall = add_element(conn, model_id, "IfcWall")
    brick = add_material(conn, model_id, "Masonry - Brick")

    with violates(conn, constraint):
        add_layer(conn, wall, layer_index, brick, thickness_m)


def test_layers_belong_to_elements_not_spaces(conn: Connection) -> None:
    model_id = add_model(conn)
    space = add_spatial(conn, model_id, "IfcSpace", "A101")
    brick = add_material(conn, model_id, "Masonry - Brick")

    with violates(conn, "fk_element_material_layer_element_id"):
        add_layer(conn, space, 1, brick, 0.092)


def test_classification_code_must_not_be_blank(conn: Connection) -> None:
    space = add_spatial(conn, add_model(conn), "IfcSpace", "A101")

    with violates(conn, "ck_classification_code_not_blank"):
        add_classification(conn, space, "OmniClass Table 13", " ")


# --------------------------------------------------------------------------- runs


def _run(**values: Any) -> dict[str, Any]:
    return {"file_name": "Duplex_A_20110907.ifc", "file_sha256": new_sha256()} | values


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        # PostgreSQL evaluates CHECK constraints in name order and reports the first that
        # fails, so each case breaks exactly one rule.
        ({"status": "done", "finished_at": func.now()}, "ck_ingestion_run_status_valid"),
        ({"finished_at": func.now()}, "ck_ingestion_run_finished_iff_done"),
        ({"status": "succeeded"}, "ck_ingestion_run_finished_iff_done"),
        ({"status": "failed", "finished_at": func.now()}, "ck_ingestion_run_error_iff_failed"),
        (
            {"status": "succeeded", "finished_at": func.now(), "error": "boom"},
            "ck_ingestion_run_error_iff_failed",
        ),
        (
            {"status": "succeeded", "finished_at": func.now() - func.make_interval(0, 0, 0, 1)},
            "ck_ingestion_run_finished_after_started",
        ),
        ({"row_counts": [1, 2]}, "ck_ingestion_run_row_counts_is_object"),
    ],
)
def test_ingestion_run_lifecycle_is_consistent(
    conn: Connection, values: dict[str, Any], constraint: str
) -> None:
    with violates(conn, constraint):
        conn.execute(insert(schema.ingestion_run).values(_run(**values)))


def test_ingestion_run_valid_lifecycle(conn: Connection) -> None:
    run_id = conn.execute(
        insert(schema.ingestion_run)
        .values(_run())
        .returning(schema.ingestion_run.c.ingestion_run_id)
    ).scalar_one()

    conn.execute(
        update(schema.ingestion_run)
        .where(schema.ingestion_run.c.ingestion_run_id == run_id)
        .values(status="succeeded", finished_at=func.now(), row_counts={"element": 268})
    )

    status = conn.execute(
        select(schema.ingestion_run.c.status).where(
            schema.ingestion_run.c.ingestion_run_id == run_id
        )
    ).scalar_one()
    assert status == "succeeded"


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"rule_id": "Placeholder-Value"}, "ck_dq_rule_rule_id_format"),
        ({"severity": "critical"}, "ck_dq_rule_severity_valid"),
        ({"dimension": "timeliness"}, "ck_dq_rule_dimension_valid"),
        ({"scope": "the_exporter"}, "ck_dq_rule_scope_valid"),
    ],
)
def test_dq_rule_rejects_bad_values(
    conn: Connection, values: dict[str, Any], constraint: str
) -> None:
    rule = {
        "rule_id": "placeholder_value",
        "description": "Value equals the property name.",
        "severity": "warning",
        "dimension": "validity",
        "scope": "model_content",
    } | values

    with violates(conn, constraint):
        conn.execute(insert(schema.dq_rule).values(rule))


# --------------------------------------------------------------------------- deletes


def test_deleting_a_model_removes_everything_loaded_from_it(conn: Connection) -> None:
    """Re-loading or removing a model is one DELETE; the run history survives it."""
    model_id = add_model(conn)
    site = add_spatial(conn, model_id, "IfcSite", "Default")
    building = add_spatial(conn, model_id, "IfcBuilding", parent_id=site)
    storey = add_spatial(conn, model_id, "IfcBuildingStorey", "Level 1", parent_id=building)
    space = add_spatial(conn, model_id, "IfcSpace", "A101", parent_id=storey)
    wall = add_element(conn, model_id, "IfcWall", storey_id=storey, container_id=storey)
    add_element(conn, model_id, "IfcFurnishingElement", storey_id=storey, container_id=space)
    add_property(
        conn, wall, "Pset_WallCommon", "IsExternal", value_type="IfcBoolean", value_bool=True
    )
    add_quantity(conn, wall, "area_m2", 10.0)
    add_layer(conn, wall, 1, add_material(conn, model_id, "Masonry - Brick"), 0.092)
    add_classification(conn, storey, "UniFormat", "B2010")
    run_id = conn.execute(
        insert(schema.ingestion_run)
        .values(_run(model_id=model_id))
        .returning(schema.ingestion_run.c.ingestion_run_id)
    ).scalar_one()
    conn.execute(
        insert(schema.dq_rule).values(
            rule_id="test_rule",
            description="Test.",
            severity="info",
            dimension="validity",
            scope="model_content",
        )
    )
    dq_run_id = conn.execute(
        insert(schema.dq_run).values(model_id=model_id).returning(schema.dq_run.c.dq_run_id)
    ).scalar_one()
    conn.execute(
        insert(schema.dq_result).values(
            dq_run_id=dq_run_id, rule_id="test_rule", object_id=wall, severity="info", message="x"
        )
    )

    conn.execute(delete(schema.model).where(schema.model.c.model_id == model_id))
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # what COMMIT would check

    for table in (
        schema.object_,
        schema.spatial_element,
        schema.element,
        schema.property_,
        schema.quantity,
        schema.material,
        schema.element_material_layer,
        schema.classification,
        schema.dq_run,
        schema.dq_result,
    ):
        assert conn.execute(select(func.count()).select_from(table)).scalar_one() == 0, table.name
    run_model_id = conn.execute(
        select(schema.ingestion_run.c.model_id).where(
            schema.ingestion_run.c.ingestion_run_id == run_id
        )
    ).scalar_one()
    assert run_model_id is None
