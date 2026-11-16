"""Write one model's records into the database: the load stage.

Everything goes in on the caller's connection, inside the caller's transaction, so a
model is either loaded completely or not at all.

Two kinds of insert, chosen by measuring both on the largest table of the samples with
``make bench-load``:

* **INSERT ... RETURNING** for the three tables whose generated ids the other rows need:
  ``model``, ``object`` (``global_id`` -> ``object_id``) and ``material`` (``name`` ->
  ``material_id``). SQLAlchemy sends a list of rows as multi-row INSERTs (its
  "insertmanyvalues" feature). Returning the natural key with each id means the mapping
  never depends on the order in which rows come back.
* **COPY** for every other table, streamed through the psycopg connection under the
  SQLAlchemy one, in the same transaction. For MEP's 63,683 property rows it took well
  under half the time of SQLAlchemy's executemany. Most of the time that remains is
  PostgreSQL checking each row's foreign key and maintaining the indexes: the price of
  the integrity rules, paid whichever way the rows arrive.

Order. Parent tables come first because ``object_id`` references are checked at once.
References between rows of one model (``parent_id``, ``container_id``, ``storey_id``,
``element_material_layer.material_id``) are checked at commit (``DEFERRABLE INITIALLY
DEFERRED``), so the order of rows within a table does not matter.

Records already satisfy every constraint (the transform guarantees it, and
``tests/support/record_contract.py`` checks it), so this module only translates natural
keys into ids. A natural key it cannot translate is a bug upstream and raises
``KeyError``, which rolls the whole model back.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from psycopg import sql
from sqlalchemy import Connection, Table, insert

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.db.engine import psycopg_connection
from ifc_sql_mcp.ingest.records import (
    MaterialRecord,
    ModelRecord,
    ModelRecords,
    ObjectRecord,
)

type Row = Mapping[str, object]
type Ids = Mapping[str, int]


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What :func:`load` wrote.

    Attributes:
        model_id: Key of the new ``ifc.model`` row.
        row_counts: Rows written per table, in load order, including tables with none.
    """

    model_id: int
    row_counts: dict[str, int]


def load(connection: Connection, records: ModelRecords, discipline: str) -> LoadResult:
    """Insert one model and everything that belongs to it.

    Args:
        connection: A connection inside the transaction that should hold the model.
        records: The transform's output for one file.
        discipline: Discipline code for ``model.discipline``, e.g. ``ARC``.

    Returns:
        The new model's id and the number of rows written to each table.
    """
    model_id = _insert_model(connection, records.model, discipline)
    object_ids = _insert_objects(connection, model_id, records.objects)
    material_ids = _insert_materials(connection, model_id, records.materials)

    copied: dict[Table, list[Row]] = {
        schema.spatial_element: _spatial_element_rows(records, object_ids),
        schema.element: _element_rows(records, object_ids),
        schema.property_: _property_rows(records, object_ids),
        schema.quantity: _quantity_rows(records, object_ids),
        schema.element_material_layer: _layer_rows(records, object_ids, material_ids),
        schema.classification: _classification_rows(records, object_ids),
        schema.ingest_issue: _issue_rows(records, model_id, object_ids),
    }
    for table, rows in copied.items():
        copy_rows(connection, table, rows)

    row_counts = {
        schema.model.name: 1,
        schema.object_.name: len(object_ids),
        schema.material.name: len(material_ids),
    } | {table.name: len(rows) for table, rows in copied.items()}
    return LoadResult(model_id=model_id, row_counts=row_counts)


def copy_rows(connection: Connection, table: Table, rows: Sequence[Row]) -> None:
    """Stream ``rows`` into ``table`` with COPY, inside the connection's transaction.

    Each row maps every column of the table to its value. A missing column raises
    ``KeyError``, which aborts the COPY and with it the transaction. psycopg's COPY writer
    escapes text itself, so values from the file (names, property values) need no
    quoting here.
    """
    if not rows:
        return
    columns = [column.name for column in table.columns]
    statement = sql.SQL("COPY {table} ({columns}) FROM STDIN").format(
        table=sql.Identifier(schema.SCHEMA, table.name),
        columns=sql.SQL(", ").join(sql.Identifier(name) for name in columns),
    )
    with psycopg_connection(connection).cursor() as cursor, cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row([row[name] for name in columns])


# --------------------------------------------------------------------------- tables with ids


def _insert_model(connection: Connection, model: ModelRecord, discipline: str) -> int:
    statement = (
        insert(schema.model)
        .values(
            file_name=model.file_name,
            file_sha256=model.file_sha256,
            file_size_bytes=model.file_size_bytes,
            ifc_schema=model.ifc_schema,
            mvd=model.mvd,
            authoring_app=model.authoring_app,
            discipline=discipline,
            project_global_id=model.project_global_id,
            project_name=model.project_name,
            project_long_name=model.project_long_name,
            length_unit=model.length_unit,
        )
        .returning(schema.model.c.model_id)
    )
    return int(connection.execute(statement).scalar_one())


def _insert_objects(
    connection: Connection, model_id: int, objects: Sequence[ObjectRecord]
) -> dict[str, int]:
    """Insert the objects and return ``object_id`` by GlobalId."""
    # An empty parameter list would execute the INSERT once with no values at all.
    if not objects:
        return {}
    table = schema.object_
    rows = [
        {
            "model_id": model_id,
            "global_id": o.global_id,
            "object_kind": o.object_kind,
            "ifc_class": o.ifc_class,
            "ifc_class_base": o.ifc_class_base,
            "name": o.name,
        }
        for o in objects
    ]
    result = connection.execute(insert(table).returning(table.c.global_id, table.c.object_id), rows)
    return dict(result.all())


def _insert_materials(
    connection: Connection, model_id: int, materials: Sequence[MaterialRecord]
) -> dict[str, int]:
    """Insert the materials and return ``material_id`` by name."""
    if not materials:
        return {}
    table = schema.material
    rows = [{"model_id": model_id, "name": m.name} for m in materials]
    result = connection.execute(insert(table).returning(table.c.name, table.c.material_id), rows)
    return dict(result.all())


# --------------------------------------------------------------------------- copied tables


def _optional(ids: Ids, key: str | None) -> int | None:
    """Translate a natural key that may be absent (no parent, no container)."""
    return None if key is None else ids[key]


def _spatial_element_rows(records: ModelRecords, object_ids: Ids) -> list[Row]:
    return [
        {
            "object_id": object_ids[s.global_id],
            "parent_id": _optional(object_ids, s.parent_global_id),
            "long_name": s.long_name,
            "elevation_m": s.elevation_m,
        }
        for s in records.spatial_elements
    ]


def _element_rows(records: ModelRecords, object_ids: Ids) -> list[Row]:
    return [
        {
            "object_id": object_ids[e.global_id],
            "container_id": _optional(object_ids, e.container_global_id),
            "storey_id": _optional(object_ids, e.storey_global_id),
            "tag": e.tag,
            "revit_family": e.revit_family,
            "revit_type": e.revit_type,
            "revit_element_id": e.revit_element_id,
            "type_name": e.type_name,
            "predefined_type": e.predefined_type,
        }
        for e in records.elements
    ]


def _property_rows(records: ModelRecords, object_ids: Ids) -> list[Row]:
    return [
        {
            "object_id": object_ids[p.global_id],
            "pset_name": p.pset_name,
            "prop_name": p.prop_name,
            "value_type": p.value_type,
            "value_text": p.value_text,
            "value_num": p.value_num,
            "value_bool": p.value_bool,
            "unit": p.unit,
            "is_type_property": p.is_type_property,
        }
        for p in records.properties
    ]


def _quantity_rows(records: ModelRecords, object_ids: Ids) -> list[Row]:
    return [
        {
            "object_id": object_ids[q.global_id],
            "name": q.name,
            "source": q.source,
            "value": q.value,
            "source_ref": q.source_ref,
        }
        for q in records.quantities
    ]


def _layer_rows(records: ModelRecords, object_ids: Ids, material_ids: Ids) -> list[Row]:
    return [
        {
            "element_id": object_ids[layer.element_global_id],
            "layer_index": layer.layer_index,
            "material_id": material_ids[layer.material_name],
            "thickness_m": layer.thickness_m,
        }
        for layer in records.material_layers
    ]


def _classification_rows(records: ModelRecords, object_ids: Ids) -> list[Row]:
    return [
        {
            "object_id": object_ids[c.global_id],
            "system": c.system,
            "code": c.code,
            "title": c.title,
        }
        for c in records.classifications
    ]


def _issue_rows(records: ModelRecords, model_id: int, object_ids: Ids) -> list[Row]:
    """Number the issues in the order reported and link each to its object, if loaded.

    Unlike every other reference, an issue's GlobalId may legitimately be unknown: an
    object with a malformed GlobalId is skipped, and the issue about it has no object to
    point at. (An object that repeats a GlobalId is skipped too, but its issue links to
    the object that kept the id.) ``global_id`` keeps what the file said either way.
    """
    return [
        {
            "model_id": model_id,
            "issue_index": index,
            "code": issue.code.value,
            "object_id": None if issue.global_id is None else object_ids.get(issue.global_id),
            "global_id": issue.global_id,
            "message": issue.message,
        }
        for index, issue in enumerate(records.issues, start=1)
    ]
