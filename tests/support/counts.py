"""Count a model's rows in every table, in the database itself rather than from the loader."""

from sqlalchemy import Connection, func, select, text

from ifc_sql_mcp.db import schema

# Rows the synthetic fixture produces per table (the transform's record counts).
FIXTURE_ROWS = {
    "model": 1,
    "object": 20,
    "material": 6,
    "spatial_element": 7,
    "element": 13,
    "property": 36,
    "quantity": 8,
    "element_material_layer": 9,
    "classification": 4,
    "ingest_issue": 8,
}


def table_counts(conn: Connection, model_id: int) -> dict[str, int]:
    """Rows of one model per table, with the same keys as ``ingestion_run.row_counts``."""
    row = conn.execute(
        text(
            """
            WITH o AS (SELECT object_id FROM ifc.object WHERE model_id = :m)
            SELECT
                (SELECT count(*) FROM ifc.model WHERE model_id = :m) AS model,
                (SELECT count(*) FROM o) AS object,
                (SELECT count(*) FROM ifc.material WHERE model_id = :m) AS material,
                (SELECT count(*) FROM ifc.spatial_element JOIN o USING (object_id))
                    AS spatial_element,
                (SELECT count(*) FROM ifc.element JOIN o USING (object_id)) AS element,
                (SELECT count(*) FROM ifc.property JOIN o USING (object_id)) AS property,
                (SELECT count(*) FROM ifc.quantity JOIN o USING (object_id)) AS quantity,
                (SELECT count(*) FROM ifc.element_material_layer AS l
                 JOIN o ON l.element_id = o.object_id) AS element_material_layer,
                (SELECT count(*) FROM ifc.classification JOIN o USING (object_id))
                    AS classification,
                (SELECT count(*) FROM ifc.ingest_issue WHERE model_id = :m) AS ingest_issue
            """
        ),
        {"m": model_id},
    )
    return dict(row.mappings().one())


def total_rows(conn: Connection) -> dict[str, int]:
    """Rows in each table the loader writes, whatever model they belong to."""
    tables = {name: schema.metadata.tables[f"{schema.SCHEMA}.{name}"] for name in FIXTURE_ROWS}
    return {
        name: conn.execute(select(func.count()).select_from(table)).scalar_one()
        for name, table in tables.items()
    }
