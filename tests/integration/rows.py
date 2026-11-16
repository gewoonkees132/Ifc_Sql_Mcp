"""Insert minimal valid rows, so that each test spells out only the values it is about.

Plain functions over SQLAlchemy Core (the same tables the loader will use), imported by
the test modules next to this file.
"""

import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, insert
from sqlalchemy.exc import IntegrityError

from ifc_sql_mcp.db import schema

_sequence = itertools.count(1)


def new_global_id() -> str:
    """Return a fresh GlobalId: 22 characters, first one in 0-3, as IFC requires."""
    return f"0{next(_sequence):021d}"


def new_sha256() -> str:
    """Return a fresh, well-formed SHA-256 hex digest."""
    return f"{next(_sequence):064x}"


@contextmanager
def violates(conn: Connection, constraint: str) -> Iterator[None]:
    """Assert that the block fails on exactly ``constraint``, then let the test go on.

    Checking the constraint name, not just "some IntegrityError", proves the intended
    rule caught the bad row. The savepoint keeps the surrounding transaction usable.
    """
    with pytest.raises(IntegrityError) as excinfo, conn.begin_nested():
        yield
    assert excinfo.value.orig is not None
    assert excinfo.value.orig.diag.constraint_name == constraint


def add_model(conn: Connection, **values: Any) -> int:
    """Insert a model; ``values`` override the defaults."""
    row = {
        "file_name": "Duplex_A_20110907.ifc",
        "file_sha256": new_sha256(),
        "file_size_bytes": 2_380_763,
        "ifc_schema": "IFC2X3",
        "discipline": "ARC",
        "length_unit": "m",
    } | values
    statement = insert(schema.model).values(row).returning(schema.model.c.model_id)
    return int(conn.execute(statement).scalar_one())


def add_object(
    conn: Connection,
    model_id: int,
    ifc_class: str,
    *,
    object_kind: str = "element",
    **values: Any,
) -> int:
    """Insert an object row only (no subtype row); ``ifc_class_base`` defaults to the class."""
    row = {
        "model_id": model_id,
        "global_id": new_global_id(),
        "object_kind": object_kind,
        "ifc_class": ifc_class,
        "ifc_class_base": ifc_class,
    } | values
    statement = insert(schema.object_).values(row).returning(schema.object_.c.object_id)
    return int(conn.execute(statement).scalar_one())


def add_spatial(
    conn: Connection,
    model_id: int,
    ifc_class: str,
    name: str | None = None,
    **values: Any,
) -> int:
    """Insert a site, building, storey or space (object + spatial_element rows)."""
    object_id = add_object(conn, model_id, ifc_class, object_kind="spatial_element", name=name)
    conn.execute(insert(schema.spatial_element).values(object_id=object_id, **values))
    return object_id


def add_element(
    conn: Connection,
    model_id: int,
    ifc_class: str,
    name: str | None = None,
    *,
    ifc_class_base: str | None = None,
    **values: Any,
) -> int:
    """Insert a physical element (object + element rows)."""
    object_id = add_object(
        conn, model_id, ifc_class, name=name, ifc_class_base=ifc_class_base or ifc_class
    )
    conn.execute(insert(schema.element).values(object_id=object_id, **values))
    return object_id


def add_property(
    conn: Connection, object_id: int, pset_name: str, prop_name: str, **values: Any
) -> None:
    """Insert one property value; pass value_type and one value_* column."""
    row = {"object_id": object_id, "pset_name": pset_name, "prop_name": prop_name} | values
    conn.execute(insert(schema.property_).values(row))


def add_quantity(
    conn: Connection,
    object_id: int,
    name: str,
    value: float,
    source: str = "revit_pset",
    source_ref: str = "PSet_Revit_Dimensions",
) -> None:
    """Insert one canonical quantity."""
    conn.execute(
        insert(schema.quantity).values(
            object_id=object_id, name=name, value=value, source=source, source_ref=source_ref
        )
    )


def add_material(conn: Connection, model_id: int, name: str) -> int:
    """Insert a material and return its id."""
    statement = (
        insert(schema.material)
        .values(model_id=model_id, name=name)
        .returning(schema.material.c.material_id)
    )
    return int(conn.execute(statement).scalar_one())


def add_layer(
    conn: Connection,
    element_id: int,
    layer_index: int,
    material_id: int,
    thickness_m: float | None = None,
) -> None:
    """Insert one material layer of an element."""
    conn.execute(
        insert(schema.element_material_layer).values(
            element_id=element_id,
            layer_index=layer_index,
            material_id=material_id,
            thickness_m=thickness_m,
        )
    )


def add_classification(
    conn: Connection, object_id: int, system: str, code: str, title: str | None = None
) -> None:
    """Insert one classification code."""
    conn.execute(
        insert(schema.classification).values(
            object_id=object_id, system=system, code=code, title=title
        )
    )
