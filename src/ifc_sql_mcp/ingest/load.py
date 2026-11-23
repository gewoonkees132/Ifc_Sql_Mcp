"""Write one model's records into the database: the load stage.

Everything goes in on the caller's connection, inside the caller's transaction, so a
model is either loaded completely or not at all. The pipeline (``pipeline.py``) wraps
:func:`load` in ``engine.begin()``.

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

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from psycopg import sql
from sqlalchemy import Connection, Table, delete, func, insert, select

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.db.engine import psycopg_connection
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.records import (
    MaterialRecord,
    ModelRecord,
    ModelRecords,
    ObjectRecord,
)
from ifc_sql_mcp.viewer.scene import Scene

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


def load(
    connection: Connection,
    records: ModelRecords,
    discipline: str,
    *,
    project_key: str | None = None,
    replaces_model_id: int | None = None,
) -> LoadResult:
    """Insert one model and everything that belongs to it.

    Args:
        connection: A connection inside the transaction that should hold the model.
        records: The transform's output for one file.
        discipline: Discipline code for ``model.discipline``, e.g. ``ARC``.
        project_key: ``model.project_key``, the project given at ingest.
        replaces_model_id: ``model.replaces_model_id``, the model this one replaces.

    Returns:
        The new model's id and the number of rows written to each table.
    """
    model_id = _insert_model(connection, records.model, discipline, project_key, replaces_model_id)
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


def _insert_model(
    connection: Connection,
    model: ModelRecord,
    discipline: str,
    project_key: str | None,
    replaces_model_id: int | None,
) -> int:
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
            project_key=project_key,
            replaces_model_id=replaces_model_id,
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


def add_geometry(connection: Connection, records: ModelRecords, model_id: int) -> dict[str, int]:
    """Add what measuring the shapes found to a model already loaded from the same bytes.

    ``ifc-sql ingest --geometry`` on a file that is loaded without it (re-check finding R4).
    Everything else in ``records`` is in the model already, so only two things are written:
    the quantities of source ``geometry``, and the ``geometry_not_measured`` issues the
    model does not have yet, numbered after its last issue.

    Returns:
        Rows written per table.
    """
    obj = schema.object_
    object_ids = {
        row.global_id: row.object_id
        for row in connection.execute(
            select(obj.c.global_id, obj.c.object_id).where(obj.c.model_id == model_id)
        )
    }
    issue = schema.ingest_issue
    known = {
        (row.code, row.global_id, row.message)
        for row in connection.execute(
            select(issue.c.code, issue.c.global_id, issue.c.message).where(
                issue.c.model_id == model_id
            )
        )
    }
    last = connection.execute(
        select(func.coalesce(func.max(issue.c.issue_index), 0)).where(issue.c.model_id == model_id)
    ).scalar_one()
    measured = dataclasses.replace(
        records,
        quantities=tuple(
            q for q in records.quantities if q.source == "geometry" and q.global_id in object_ids
        ),
    )
    new_issues = [
        i
        for i in records.issues
        if i.code is IssueCode.GEOMETRY_NOT_MEASURED
        and (i.code.value, i.global_id, i.message) not in known
    ]
    copied: dict[Table, list[Row]] = {
        schema.quantity: _quantity_rows(measured, object_ids),
        schema.ingest_issue: _issue_rows(
            dataclasses.replace(records, issues=tuple(new_issues)),
            model_id,
            object_ids,
            first=last + 1,
        ),
    }
    for table, rows in copied.items():
        copy_rows(connection, table, rows)
    return {table.name: len(rows) for table, rows in copied.items()}


def add_scene(
    connection: Connection,
    model_id: int,
    scene: Scene,
    *,
    format_version: int,
    build_seconds: float,
    ifcopenshell_version: str,
) -> dict[str, int]:
    """Write a model's scene into schema ``ifc_viewer``, replacing the one it has, if any.

    ``ifc-sql ingest --viewer``, after the model is loaded (ADR 14). Nothing in schema
    ``ifc`` is written. The caller holds the model row's lock.

    Args:
        connection: A connection inside the transaction that should hold the scene.
        model_id: The model, already loaded.
        scene: The scene, from ``viewer.scene.build_scene``.
        format_version: The scene format it was built in.
        build_seconds: How long triangulating and writing it took.
        ifcopenshell_version: The IfcOpenShell release that triangulated it.

    Returns:
        Rows written per table.
    """
    obj = schema.object_
    storeys = {chunk.storey_global_id for chunk in scene.chunks} - {None}
    storey_ids = dict(
        connection.execute(
            select(obj.c.global_id, obj.c.object_id).where(
                obj.c.model_id == model_id, obj.c.global_id.in_(storeys)
            )
        ).all()
    )
    connection.execute(delete(schema.scene).where(schema.scene.c.model_id == model_id))
    x, y, z = scene.origin
    connection.execute(
        insert(schema.scene).values(
            model_id=model_id,
            # Not the column's default, now(): that is when the transaction began, before
            # the caller waited for the model row's lock, which can take minutes.
            built_at=func.clock_timestamp(),
            format_version=format_version,
            build_seconds=build_seconds,
            ifcopenshell_version=ifcopenshell_version,
            origin_x_m=x,
            origin_y_m=y,
            origin_z_m=z,
            element_count=scene.element_count,
            without_shape_count=scene.without_shape_count,
            opening_count=scene.opening_count,
            byte_count=scene.byte_count,
        )
    )
    # An empty parameter list would execute the INSERT once with no values at all.
    if scene.chunks:
        connection.execute(
            insert(schema.scene_chunk),
            [
                {
                    "model_id": model_id,
                    "chunk_no": chunk_no,
                    "storey_id": _optional(storey_ids, chunk.storey_global_id),
                    "kind": chunk.kind,
                    "element_count": chunk.element_count,
                    "glb": chunk.glb,
                }
                for chunk_no, chunk in enumerate(scene.chunks)
            ],
        )
    return {schema.scene.name: 1, schema.scene_chunk.name: len(scene.chunks)}


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
            "revit_category": s.revit_category,
            "footprint_m2": s.footprint_m2,
            "repeats_id": _optional(object_ids, s.repeats_global_id),
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
            "is_feature": e.is_feature,
            "revit_family": e.revit_family,
            "revit_type": e.revit_type,
            "host_revit_family": e.host_revit_family,
            "host_revit_type": e.host_revit_type,
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
            "is_implausible": p.is_implausible,
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


def _issue_rows(
    records: ModelRecords, model_id: int, object_ids: Ids, *, first: int = 1
) -> list[Row]:
    """Number the issues in the order reported, from ``first``, and link each to its object.

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
        for index, issue in enumerate(records.issues, start=first)
    ]
