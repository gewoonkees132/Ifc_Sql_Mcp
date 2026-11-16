"""Each record type has exactly its table's columns, so the loader can insert it as-is."""

import dataclasses

import pytest
from sqlalchemy import Table

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.ingest import records

# Record type, table, columns the database generates, and record fields that stand in
# for foreign key columns until the loader knows the generated ids.
CASES = [
    (records.ModelRecord, schema.model, {"model_id", "ingested_at", "discipline"}, {}),
    (records.ObjectRecord, schema.object_, {"object_id", "model_id"}, {"global_id": "global_id"}),
    (
        records.SpatialElementRecord,
        schema.spatial_element,
        set(),
        {"global_id": "object_id", "parent_global_id": "parent_id"},
    ),
    (
        records.ElementRecord,
        schema.element,
        set(),
        {
            "global_id": "object_id",
            "container_global_id": "container_id",
            "storey_global_id": "storey_id",
        },
    ),
    (records.PropertyRecord, schema.property_, set(), {"global_id": "object_id"}),
    (records.QuantityRecord, schema.quantity, set(), {"global_id": "object_id"}),
    (records.MaterialRecord, schema.material, {"material_id", "model_id"}, {}),
    (
        records.ElementMaterialLayerRecord,
        schema.element_material_layer,
        set(),
        {"element_global_id": "element_id", "material_name": "material_id"},
    ),
    (records.ClassificationRecord, schema.classification, set(), {"global_id": "object_id"}),
]


@pytest.mark.parametrize(
    ("record", "table", "generated", "stand_ins"), CASES, ids=[case[1].name for case in CASES]
)
def test_record_fields_are_the_table_columns(
    record: type, table: Table, generated: set[str], stand_ins: dict[str, str]
) -> None:
    fields = [field.name for field in dataclasses.fields(record)]
    as_columns = {stand_ins.get(name, name) for name in fields}
    assert as_columns == {column.name for column in table.columns} - generated


def test_object_kinds_match_the_schema() -> None:
    assert set(records.ObjectKind.__value__.__args__) == set(schema.OBJECT_KINDS)


def test_records_are_immutable() -> None:
    material = records.MaterialRecord("Brick")
    with pytest.raises(dataclasses.FrozenInstanceError):
        material.name = "Stone"
