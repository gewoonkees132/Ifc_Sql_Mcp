"""Records the transform produces: one type per table in ``db/schema.py``.

Each record has the columns of its table, with one difference. Database ids do not exist
yet, so references use natural keys instead: ``global_id`` for objects and
``material_name`` for materials. The loader inserts ``model``, ``object`` and
``material`` first, maps those keys to the generated ids and bulk-inserts the rest.

Every record already satisfies its table's CHECK constraints; the transform drops or
nulls anything that would not, and reports it as an :class:`Issue`.
``tests/unit/ingest/test_records.py`` checks that the fields match the columns.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ifc_sql_mcp.ingest.issues import Issue

type ObjectKind = Literal["spatial_element", "element"]
type QuantityName = Literal["length_m", "area_m2", "volume_m3", "perimeter_m", "height_m"]
type QuantitySource = Literal["ifc_qto", "revit_pset", "geometry"]


@dataclass(frozen=True, slots=True)
class ModelRecord:
    """A row for ``ifc.model``, except ``discipline``, which the user supplies at ingest."""

    file_name: str
    file_sha256: str
    file_size_bytes: int
    ifc_schema: str
    mvd: str | None
    authoring_app: str | None
    project_global_id: str | None
    project_name: str | None
    project_long_name: str | None
    length_unit: str


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    """A row for ``ifc.object``."""

    global_id: str
    object_kind: ObjectKind
    ifc_class: str
    ifc_class_base: str
    name: str | None


@dataclass(frozen=True, slots=True)
class SpatialElementRecord:
    """A row for ``ifc.spatial_element``; ``parent_global_id`` becomes ``parent_id``."""

    global_id: str
    parent_global_id: str | None
    long_name: str | None
    elevation_m: Decimal | None


@dataclass(frozen=True, slots=True)
class ElementRecord:
    """A row for ``ifc.element``; the two ``*_global_id`` fields become ``*_id``.

    A feature element (an opening, say) has its name's family and type in the ``host_*``
    fields, because Revit names it after the element it cuts; ``revit_family`` and
    ``revit_type`` are then ``None``.
    """

    global_id: str
    container_global_id: str | None
    storey_global_id: str | None
    tag: str | None
    is_feature: bool
    revit_family: str | None
    revit_type: str | None
    host_revit_family: str | None
    host_revit_type: str | None
    revit_element_id: int | None
    type_name: str | None
    predefined_type: str | None


@dataclass(frozen=True, slots=True)
class PropertyRecord:
    """A row for ``ifc.property``."""

    global_id: str
    pset_name: str
    prop_name: str
    value_type: str | None
    value_text: str | None
    value_num: float | None
    value_bool: bool | None
    unit: str | None
    is_type_property: bool
    is_implausible: bool


@dataclass(frozen=True, slots=True)
class QuantityRecord:
    """A row for ``ifc.quantity``."""

    global_id: str
    name: QuantityName
    source: QuantitySource
    value: float
    source_ref: str


@dataclass(frozen=True, slots=True)
class MaterialRecord:
    """A row for ``ifc.material``."""

    name: str


@dataclass(frozen=True, slots=True)
class ElementMaterialLayerRecord:
    """A row for ``ifc.element_material_layer``; ``material_name`` becomes ``material_id``."""

    element_global_id: str
    layer_index: int
    material_name: str
    thickness_m: float | None


@dataclass(frozen=True, slots=True)
class ClassificationRecord:
    """A row for ``ifc.classification``."""

    global_id: str
    system: str
    code: str
    title: str | None


@dataclass(frozen=True, slots=True)
class ModelRecords:
    """Everything the loader writes for one file, plus the issues found on the way.

    Each tuple is sorted by its table's key, so the same file always gives the same
    records in the same order.
    """

    model: ModelRecord
    objects: tuple[ObjectRecord, ...]
    spatial_elements: tuple[SpatialElementRecord, ...]
    elements: tuple[ElementRecord, ...]
    properties: tuple[PropertyRecord, ...]
    quantities: tuple[QuantityRecord, ...]
    materials: tuple[MaterialRecord, ...]
    material_layers: tuple[ElementMaterialLayerRecord, ...]
    classifications: tuple[ClassificationRecord, ...]
    issues: tuple[Issue, ...]
