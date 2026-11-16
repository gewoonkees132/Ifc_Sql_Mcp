"""Each record type has exactly its table's columns, so the loader can insert it as-is."""

import dataclasses
import re

import pytest
from sqlalchemy import Table

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.ingest import records
from ifc_sql_mcp.ingest.issues import Issue, IssueCode

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


def test_issues_fit_the_ingest_issue_table() -> None:
    """The loader writes ``Issue`` objects as rows; it adds the index and the object_id."""
    fields = {field.name for field in dataclasses.fields(Issue)}
    columns = {column.name for column in schema.ingest_issue.columns}
    assert fields == columns - {"model_id", "issue_index", "object_id"}


@pytest.mark.parametrize("code", list(IssueCode))
def test_every_issue_code_fits_the_code_column(code: IssueCode) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]*", code.value)


def test_object_kinds_match_the_schema() -> None:
    assert set(records.ObjectKind.__value__.__args__) == set(schema.OBJECT_KINDS)


def test_records_are_immutable() -> None:
    material = records.MaterialRecord("Brick")
    with pytest.raises(dataclasses.FrozenInstanceError):
        material.name = "Stone"
