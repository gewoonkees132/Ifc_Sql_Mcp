from sqlalchemy import CheckConstraint

from ifc_sql_mcp.db.schema import SCHEMA, metadata


def test_tables_live_in_the_ifc_schema() -> None:
    assert {table.schema for table in metadata.tables.values()} == {SCHEMA}
    assert sorted(table.name for table in metadata.tables.values()) == [
        "classification",
        "dq_result",
        "dq_rule",
        "dq_run",
        "element",
        "element_material_layer",
        "ingest_issue",
        "ingestion_run",
        "material",
        "model",
        "object",
        "property",
        "quantity",
        "spatial_element",
    ]


def test_every_table_and_column_is_commented() -> None:
    undocumented = [
        f"{table.name}.{column.name}"
        for table in metadata.tables.values()
        for column in table.columns
        if not column.comment
    ] + [table.name for table in metadata.tables.values() if not table.comment]

    assert undocumented == []


def test_every_check_constraint_has_a_conventional_name() -> None:
    for table in metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                assert str(constraint.name).startswith(f"ck_{table.name}_")
