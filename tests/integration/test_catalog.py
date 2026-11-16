"""Properties of the migrated catalogue that every future migration must keep."""

from sqlalchemy import Engine, inspect, text

from ifc_sql_mcp.db.schema import SCHEMA


def test_every_table_view_and_column_has_a_comment(engine: Engine) -> None:
    """The MCP ``ifc://schema`` resource is built from these comments; none may be missing."""
    with engine.connect() as connection:
        missing = (
            connection.execute(
                text(
                    """
                SELECT c.relname || coalesce('.' || a.attname, '') AS undocumented
                FROM pg_class AS c
                LEFT JOIN pg_attribute AS a
                    ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                WHERE c.relnamespace = CAST(:schema AS regnamespace)
                  AND c.relkind IN ('r', 'v')
                  AND (col_description(c.oid, 0) IS NULL  -- the table or view itself
                       OR (a.attnum IS NOT NULL AND col_description(c.oid, a.attnum) IS NULL))
                ORDER BY 1
                """
                ),
                {"schema": SCHEMA},
            )
            .scalars()
            .all()
        )

    assert missing == []


def test_every_foreign_key_is_backed_by_an_index(engine: Engine) -> None:
    """PostgreSQL does not index foreign keys itself.

    Without an index, joins along the key and every cascading delete from the parent
    table scan the whole child table.
    """
    inspector = inspect(engine)
    unindexed = []
    for table in inspector.get_table_names(schema=SCHEMA):
        leading_columns = [
            index["column_names"] for index in inspector.get_indexes(table, schema=SCHEMA)
        ]
        leading_columns.append(
            inspector.get_pk_constraint(table, schema=SCHEMA)["constrained_columns"]
        )
        for foreign_key in inspector.get_foreign_keys(table, schema=SCHEMA):
            columns = foreign_key["constrained_columns"]
            if not any(indexed[: len(columns)] == columns for indexed in leading_columns):
                unindexed.append(f"{table}({', '.join(columns)})")

    assert unindexed == []


def test_views_are_the_documented_set(engine: Engine) -> None:
    assert sorted(inspect(engine).get_view_names(schema=SCHEMA)) == [
        "v_element",
        "v_model_summary",
        "v_quantity",
        "v_space_schedule",
        "v_takeoff",
    ]


def test_revit_type_comments_say_the_type_has_no_colons(engine: Engine) -> None:
    """Migration 0004 corrected both comments; Phase 3 showed the type is one segment."""
    with engine.connect() as connection:
        comments = (
            connection.execute(
                text(
                    """
                SELECT col_description(CAST(relation AS regclass), attnum)
                FROM (VALUES ('ifc.element'), ('ifc.v_element')) AS r (relation)
                JOIN pg_attribute
                    ON attrelid = CAST(relation AS regclass) AND attname = 'revit_type'
                ORDER BY relation
                """
                )
            )
            .scalars()
            .all()
        )

    table_comment, view_comment = comments
    assert "never containing ':'" in table_comment
    assert "Never contains a colon" in view_comment
