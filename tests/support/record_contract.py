"""Check transform output against every rule the ``ifc`` schema enforces.

The loader (Phase 4) must never hit a constraint, so the records have to satisfy the
CHECK constraints, keys and foreign keys of ``db/schema.py`` before they reach the
database. This module restates those rules in Python, using the patterns and value lists
exported by ``schema.py`` where it has them, and collects every violation so a failing
test shows all of them at once.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.ingest.records import ModelRecords

_GLOBAL_ID = re.compile(schema.GLOBAL_ID_PATTERN)
_IFC_CLASS = re.compile(schema.IFC_CLASS_PATTERN)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IFC_SCHEMA = re.compile(r"^IFC[0-9A-Z_]+$")
_PREDEFINED = re.compile(r"^[A-Z][A-Z0-9_]*$")
_BIGINT_MAX = 2**63 - 1
_SMALLINT_MAX = 2**15 - 1


def _blank(value: str | None) -> bool:
    return value is None or re.search(r"\S", value) is None


def _duplicates(keys: Iterable[object]) -> list[object]:
    return [key for key, count in Counter(keys).items() if count > 1]


def _finite(value: float | None) -> bool:
    return value is None or math.isfinite(value)


def violations(records: ModelRecords) -> list[str]:
    """Return a description of every rule the records break; empty when they fit."""
    found: list[str] = []
    model = records.model
    if _blank(model.file_name):
        found.append("model.file_name is blank")
    if not _SHA256.match(model.file_sha256):
        found.append(f"model.file_sha256 {model.file_sha256!r}")
    if model.file_size_bytes <= 0:
        found.append("model.file_size_bytes <= 0")
    if not _IFC_SCHEMA.match(model.ifc_schema):
        found.append(f"model.ifc_schema {model.ifc_schema!r}")
    if model.project_global_id is not None and not _GLOBAL_ID.match(model.project_global_id):
        found.append(f"model.project_global_id {model.project_global_id!r}")
    if _blank(model.length_unit):
        found.append("model.length_unit is blank")

    objects = {o.global_id: o for o in records.objects}
    found += [
        f"object key repeated: {k}" for k in _duplicates(o.global_id for o in records.objects)
    ]
    for o in records.objects:
        if not _GLOBAL_ID.match(o.global_id):
            found.append(f"object.global_id {o.global_id!r}")
        if o.object_kind not in schema.OBJECT_KINDS:
            found.append(f"object.object_kind {o.object_kind!r}")
        if not _IFC_CLASS.match(o.ifc_class) or not _IFC_CLASS.match(o.ifc_class_base):
            found.append(f"object class {o.ifc_class!r} / {o.ifc_class_base!r}")

    spatial = {s.global_id: s for s in records.spatial_elements}
    elements = {e.global_id: e for e in records.elements}
    # The loader writes exactly one subtype row per object, matching object_kind.
    for global_id, o in objects.items():
        in_spatial, in_elements = global_id in spatial, global_id in elements
        expected = o.object_kind == "spatial_element"
        if in_spatial != expected or in_elements == expected:
            found.append(f"{global_id} has no single subtype row for {o.object_kind}")
    found += [
        f"subtype row without object: {g}"
        for g in (spatial.keys() | elements.keys()) - objects.keys()
    ]

    storeys = {g for g, o in objects.items() if o.ifc_class_base == "IfcBuildingStorey"}
    for s in records.spatial_elements:
        if s.parent_global_id is not None and s.parent_global_id not in spatial:
            found.append(f"{s.global_id}: parent {s.parent_global_id} is not a spatial element")
        if s.parent_global_id == s.global_id:
            found.append(f"{s.global_id} is its own parent")
        if s.elevation_m is not None:
            exponent = s.elevation_m.as_tuple().exponent
            if exponent != -3 or abs(s.elevation_m) > Decimal("999999.999"):
                found.append(f"{s.global_id}: elevation_m {s.elevation_m!r} does not fit (9,3)")

    for e in records.elements:
        if e.container_global_id is not None and e.container_global_id not in spatial:
            found.append(f"{e.global_id}: container {e.container_global_id} is not spatial")
        if e.storey_global_id is not None and e.storey_global_id not in storeys:
            found.append(f"{e.global_id}: storey {e.storey_global_id} is not a storey")
        if e.revit_element_id is not None and not 0 < e.revit_element_id <= _BIGINT_MAX:
            found.append(f"{e.global_id}: revit_element_id {e.revit_element_id}")
        if e.predefined_type is not None and not _PREDEFINED.match(e.predefined_type):
            found.append(f"{e.global_id}: predefined_type {e.predefined_type!r}")

    keys = [(p.global_id, p.pset_name, p.prop_name) for p in records.properties]
    found += [f"property key repeated: {k}" for k in _duplicates(keys)]
    for p in records.properties:
        where = f"property {p.global_id} {p.pset_name}.{p.prop_name}"
        values = [v for v in (p.value_text, p.value_num, p.value_bool) if v is not None]
        if p.global_id not in objects:
            found.append(f"{where}: unknown object")
        if _blank(p.pset_name) or _blank(p.prop_name):
            found.append(f"{where}: blank name")
        if p.value_type is not None and not _IFC_CLASS.match(p.value_type):
            found.append(f"{where}: value_type {p.value_type!r}")
        if len(values) > 1:
            found.append(f"{where}: more than one value")
        if (p.value_type is None) != (not values):
            found.append(f"{where}: value_type present without a value or vice versa")
        if p.unit is not None and p.value_num is None:
            found.append(f"{where}: unit without a number")
        if not _finite(p.value_num):
            found.append(f"{where}: value_num {p.value_num!r}")

    keys = [(q.global_id, q.name, q.source) for q in records.quantities]
    found += [f"quantity key repeated: {k}" for k in _duplicates(keys)]
    for q in records.quantities:
        where = f"quantity {q.global_id} {q.name}/{q.source}"
        if q.global_id not in objects:
            found.append(f"{where}: unknown object")
        if q.name not in schema.QUANTITY_NAMES or q.source not in schema.QUANTITY_SOURCES:
            found.append(f"{where}: name or source not allowed")
        if not (math.isfinite(q.value) and q.value >= 0):
            found.append(f"{where}: value {q.value!r}")
        if _blank(q.source_ref):
            found.append(f"{where}: blank source_ref")

    materials = [m.name for m in records.materials]
    found += [f"material repeated: {m}" for m in _duplicates(materials)]
    found += [f"blank material name {m!r}" for m in materials if _blank(m)]
    keys = [(layer.element_global_id, layer.layer_index) for layer in records.material_layers]
    found += [f"layer key repeated: {k}" for k in _duplicates(keys)]
    for layer in records.material_layers:
        where = f"layer {layer.element_global_id}#{layer.layer_index}"
        if layer.element_global_id not in elements:
            found.append(f"{where}: unknown element")
        if not 1 <= layer.layer_index <= _SMALLINT_MAX:
            found.append(f"{where}: layer_index out of range")
        if layer.material_name not in materials:
            found.append(f"{where}: unknown material {layer.material_name!r}")
        if layer.thickness_m is not None and not (
            math.isfinite(layer.thickness_m) and layer.thickness_m >= 0
        ):
            found.append(f"{where}: thickness_m {layer.thickness_m!r}")

    keys = [(c.global_id, c.system, c.code) for c in records.classifications]
    found += [f"classification key repeated: {k}" for k in _duplicates(keys)]
    for c in records.classifications:
        if c.global_id not in objects:
            found.append(f"classification {c.global_id}: unknown object")
        if _blank(c.system) or _blank(c.code):
            found.append(f"classification {c.global_id}: blank system or code")
    return found
