"""The queries behind the fixed tools, and the shapes of their results.

Each function takes a connection inside the call's read-only transaction
(:func:`~ifc_sql_mcp.db.engine.reader_transaction`) and returns a pydantic model, which
the server publishes as the tool's output schema and sends as both structured content
and compact JSON text. Every value from the caller reaches SQL as a bind parameter;
names that choose a column (``group_by``, ``metric``) are looked up in fixed tables and
never formatted into SQL.

Every list is bounded, and says when it was cut. Text that came from an IFC file is typed
:data:`IfcText`: it is clipped when the model is built, so no field can forget it, and it
is only ever returned as a value, never written into an instruction (ADR 6).
"""

import difflib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, NamedTuple

from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import Connection, RowMapping, text

from ifc_sql_mcp.server.values import clip

type IfcText = Annotated[str, AfterValidator(clip)]
"""Text from an IFC file: untrusted, cut to ``MAX_TEXT_CHARS`` characters."""

MAX_MODELS = 200
MAX_STOREYS = 200
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_PROPERTIES = 500
MAX_ELEMENT_FINDINGS = 100
MAX_GROUPS = 200
SPACE_PAGE_SIZE = 150
"""Most spaces per space_schedule page: about 225 characters each, 34,000 in all (A11)."""
DEFAULT_SAMPLES = 3
MAX_SAMPLES = 20

GroupBy = Literal["storey", "ifc_class", "material", "revit_type"]
Metric = Literal["count", "length_m", "area_m2", "volume_m3"]
Severity = Literal["error", "warning", "info"]
Scope = Literal["model_content", "loader_coverage"]


def _severity_order(column: str) -> str:
    """An ORDER BY expression that puts errors first, then warnings, then info."""
    return f"CASE {column} WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END"


class ToolInputError(ValueError):
    """The arguments name something that does not exist, or do not fit together.

    The message is written for the LLM that sent them: it says what to do instead.
    """


class ToolResult(BaseModel):
    """A tool's result; :meth:`logged_rows` is what the server logs for the call."""

    def logged_rows(self) -> int:
        """Return how many rows the result holds, for the call's log line."""
        return 1


# --------------------------------------------------------------------------- list_models


class DataQualityStatus(BaseModel):
    """The model's latest successful data quality run, or that it was never checked."""

    status: Literal["checked", "never_checked"] = Field(
        description="never_checked: `ifc-sql dq` has not checked this model, so the counts "
        "are null. checked with 0 findings means the rules found nothing."
    )
    dq_run_id: int | None = Field(description="The run the counts come from.")
    finished_at: datetime | None = Field(description="When that run finished (UTC).")
    errors: int | None = Field(description="Findings of severity error.")
    warnings: int | None = Field(description="Findings of severity warning.")
    info: int | None = Field(description="Findings of severity info.")


class ModelSummary(ToolResult):
    """One loaded IFC file version."""

    model_id: int = Field(description="The key every other tool takes.")
    file_name: IfcText
    discipline: str = Field(description="Discipline given at ingest, e.g. ARC, MEP, ELE, PLU.")
    ifc_schema: str | None = Field(description="e.g. IFC2X3.")
    mvd: IfcText | None = Field(description="Model View Definition from the file header.")
    authoring_app: IfcText | None
    project_name: IfcText | None = Field(description="IfcProject.Name.")
    project_long_name: IfcText | None = Field(description="IfcProject.LongName.")
    project_group: IfcText | None = Field(
        description="The project this model is compared with others in: the project given at "
        "ingest, else project_name, else project_long_name, never a Revit default (0001, "
        "Project Number, Project Name). Null: it belongs to no project."
    )
    replaces_model_id: int | None = Field(description="The earlier version this one replaces.")
    replaced_by_model_id: int | None = Field(
        description="The later version that replaces this one; null for a current model."
    )
    length_unit: str | None = Field(
        description="Unit the file declared. Every value this server returns is converted "
        "to metres, m2 and m3 whatever it says."
    )
    ingested_at: datetime
    storeys: int
    spaces: int
    elements: int
    property_values: int
    quantities: int
    materials: int
    data_quality: DataQualityStatus


class ModelList(ToolResult):
    """The loaded models, oldest first."""

    models: list[ModelSummary]
    replaced_left_out: int = Field(
        description="Models a later version replaces, left out unless include_replaced."
    )
    truncated: bool = Field(description=f"True when more than {MAX_MODELS} models are loaded.")

    def logged_rows(self) -> int:
        """Return the number of models."""
        return len(self.models)


MODEL_SUMMARY = """
SELECT s.model_id, s.file_name, s.discipline, s.ifc_schema, s.mvd, s.authoring_app,
       s.project_name, s.project_long_name, m.project_group, m.replaces_model_id,
       later.model_id AS replaced_by_model_id, s.length_unit, s.ingested_at, s.storey_count,
       s.space_count, s.element_count, s.property_count, s.quantity_count, s.material_count,
       s.dq_run_id, s.dq_finished_at, s.dq_error_count, s.dq_warning_count, s.dq_info_count
FROM ifc.v_model_summary AS s
JOIN ifc.model AS m ON m.model_id = s.model_id
LEFT JOIN ifc.model AS later ON later.replaces_model_id = s.model_id
"""


def _model_summary(row: RowMapping) -> ModelSummary:
    return ModelSummary(
        model_id=row["model_id"],
        file_name=row["file_name"],
        discipline=row["discipline"],
        ifc_schema=row["ifc_schema"],
        mvd=row["mvd"],
        authoring_app=row["authoring_app"],
        project_name=row["project_name"],
        project_long_name=row["project_long_name"],
        project_group=row["project_group"],
        replaces_model_id=row["replaces_model_id"],
        replaced_by_model_id=row["replaced_by_model_id"],
        length_unit=row["length_unit"],
        ingested_at=row["ingested_at"],
        storeys=row["storey_count"],
        spaces=row["space_count"],
        elements=row["element_count"],
        property_values=row["property_count"],
        quantities=row["quantity_count"],
        materials=row["material_count"],
        data_quality=DataQualityStatus(
            status="never_checked" if row["dq_run_id"] is None else "checked",
            dq_run_id=row["dq_run_id"],
            finished_at=row["dq_finished_at"],
            errors=row["dq_error_count"],
            warnings=row["dq_warning_count"],
            info=row["dq_info_count"],
        ),
    )


def list_models(connection: Connection, *, include_replaced: bool = False) -> ModelList:
    """The loaded models, oldest first, with their counts and data quality status.

    A model a later version replaces is left out unless ``include_replaced``: it is last
    week's export, kept for comparison (audit finding A12).
    """
    current = "" if include_replaced else "WHERE later.model_id IS NULL "
    rows = connection.execute(
        text(f"{MODEL_SUMMARY} {current}ORDER BY s.model_id LIMIT :limit"),
        {"limit": MAX_MODELS + 1},
    ).mappings()
    models = [_model_summary(row) for row in rows]
    replaced = 0
    if not include_replaced:
        replaced = connection.execute(
            text("SELECT count(*) FROM ifc.model WHERE replaces_model_id IS NOT NULL")
        ).scalar_one()
    return ModelList(
        models=models[:MAX_MODELS],
        replaced_left_out=replaced,
        truncated=len(models) > MAX_MODELS,
    )


def model_summary(connection: Connection, model_id: int) -> ModelSummary:
    """One model's summary (the ``ifc://models/{model_id}/summary`` resource)."""
    row = (
        connection.execute(
            text(f"{MODEL_SUMMARY} WHERE s.model_id = :model_id"), {"model_id": model_id}
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ToolInputError(_unknown_model(model_id))
    return _model_summary(row)


def _unknown_model(model_id: int) -> str:
    return f"no model with id {model_id}; list_models shows the loaded models"


def _require_model(connection: Connection, model_id: int) -> None:
    found = connection.execute(
        text("SELECT 1 FROM ifc.model WHERE model_id = :model_id"), {"model_id": model_id}
    ).scalar()
    if found is None:
        raise ToolInputError(_unknown_model(model_id))


# --------------------------------------------------------------------------- list_storeys


class Storey(BaseModel):
    """One IfcBuildingStorey."""

    storey_id: int = Field(description="Object id of the storey.")
    global_id: str
    name: IfcText | None = Field(description="e.g. Level 1; use it in find_elements.")
    long_name: IfcText | None
    elevation_m: float | None = Field(description="Elevation in metres, millimetre precision.")
    elements: int = Field(description="Elements on this storey.")
    spaces: int = Field(description="Spaces (rooms) on this storey.")


class StoreyList(ToolResult):
    """The storeys of a model, lowest first."""

    model_id: int
    storeys: list[Storey]
    elements_without_storey: int = Field(
        description="Elements on no storey; takeoffs by storey show them under null."
    )
    truncated: bool

    def logged_rows(self) -> int:
        """Return the number of storeys."""
        return len(self.storeys)


def list_storeys(connection: Connection, model_id: int) -> StoreyList:
    """The model's storeys with elevation and how many elements and spaces each holds."""
    _require_model(connection, model_id)
    rows = connection.execute(
        text(
            """
            SELECT o.object_id AS storey_id, o.global_id, o.name, s.long_name, s.elevation_m,
                   (SELECT count(*) FROM ifc.element AS e
                    WHERE e.storey_id = o.object_id) AS elements,
                   (SELECT count(*) FROM ifc.spatial_element AS child
                    JOIN ifc.object AS child_object ON child.object_id = child_object.object_id
                    WHERE child.parent_id = o.object_id
                      AND child_object.ifc_class_base = 'IfcSpace') AS spaces
            FROM ifc.object AS o
            JOIN ifc.spatial_element AS s ON o.object_id = s.object_id
            WHERE o.model_id = :model_id AND o.ifc_class_base = 'IfcBuildingStorey'
            ORDER BY s.elevation_m NULLS LAST, o.name, o.object_id
            LIMIT :limit
            """
        ),
        {"model_id": model_id, "limit": MAX_STOREYS + 1},
    ).mappings()
    storeys = [Storey.model_validate(dict(row)) for row in rows]
    without_storey = connection.execute(
        text(
            "SELECT count(*) FROM ifc.element AS e JOIN ifc.object AS o USING (object_id) "
            "WHERE o.model_id = :model_id AND e.storey_id IS NULL"
        ),
        {"model_id": model_id},
    ).scalar_one()
    return StoreyList(
        model_id=model_id,
        storeys=storeys[:MAX_STOREYS],
        elements_without_storey=without_storey,
        truncated=len(storeys) > MAX_STOREYS,
    )


# --------------------------------------------------------------------------- find_elements


class ElementRow(BaseModel):
    """One element, in brief; get_element has the rest."""

    object_id: int
    global_id: str
    ifc_class: str
    name: IfcText | None
    revit_family: IfcText | None
    revit_type: IfcText | None
    storey_name: IfcText | None
    space_number: IfcText | None = Field(description="Room the element sits in, if any.")
    length_m: float | None
    area_m2: float | None
    volume_m3: float | None
    height_m: float | None
    width_m: float | None = Field(description="A door's or window's width; a wall's thickness.")


class ElementPage(ToolResult):
    """One page of the elements that match."""

    model_id: int
    total: int = Field(description="Elements that match, on every page together.")
    offset: int
    limit: int
    next_offset: int | None = Field(description="Offset of the next page; null on the last.")
    elements: list[ElementRow]

    def logged_rows(self) -> int:
        """Return the number of elements on the page."""
        return len(self.elements)


# Each filter is one fixed condition on v_element, with the caller's value as a bind
# parameter named after the filter. Matching ignores case.
ELEMENT_FILTERS = {
    "ifc_class": "(lower(e.ifc_class) = lower(:ifc_class) "
    "OR lower(e.ifc_class_base) = lower(:ifc_class))",
    "storey_name": "lower(e.storey_name) = lower(:storey_name)",
    "revit_family": "lower(e.revit_family) = lower(:revit_family)",
    "revit_type": "lower(e.revit_type) = lower(:revit_type)",
    "name_contains": "strpos(lower(e.name), lower(:name_contains)) > 0",
}


def find_elements(
    connection: Connection,
    model_id: int,
    filters: Mapping[str, str | None],
    *,
    limit: int,
    offset: int,
) -> ElementPage:
    """One page of the model's elements that match every given filter, by object id."""
    _require_model(connection, model_id)
    given = {name: value for name, value in filters.items() if value is not None}
    where = " AND ".join(["e.model_id = :model_id", *(ELEMENT_FILTERS[name] for name in given)])
    params: dict[str, Any] = {"model_id": model_id, **given}
    # The SQL is put together from the constant conditions above; values are parameters.
    total = connection.execute(
        text(f"SELECT count(*) FROM ifc.v_element AS e WHERE {where}"),  # noqa: S608
        params,
    ).scalar_one()
    rows = connection.execute(
        text(
            "SELECT e.object_id, e.global_id, e.ifc_class, e.name, e.revit_family, "  # noqa: S608
            "e.revit_type, e.storey_name, e.space_number, e.length_m, e.area_m2, e.volume_m3, "
            "e.height_m, e.width_m "
            f"FROM ifc.v_element AS e WHERE {where} "
            "ORDER BY e.object_id LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings()
    elements = [ElementRow.model_validate(dict(row)) for row in rows]
    return ElementPage(
        model_id=model_id,
        total=total,
        offset=offset,
        limit=limit,
        next_offset=offset + limit if offset + limit < total else None,
        elements=elements,
    )


# --------------------------------------------------------------------------- get_element


class QuantityValue(BaseModel):
    """A canonical quantity, as v_quantity chooses it."""

    name: str = Field(
        description="length_m, area_m2, volume_m3, perimeter_m, height_m or width_m (a door's "
        "or window's width, a wall's or slab's thickness)."
    )
    value: float = Field(description="In the unit the name ends with.")
    source: str = Field(description="ifc_qto, revit_pset or geometry: where it came from.")
    source_ref: IfcText | None = Field(
        description="e.g. Qto_WallBaseQuantities.NetSideArea or PSet_Revit_Dimensions.Area."
    )


class PropertyValue(BaseModel):
    """One property value, as the file wrote it (converted to SI if a measure)."""

    name: IfcText
    value: IfcText | float | bool | None
    value_type: str | None = Field(description="IFC type, e.g. IfcLabel, IfcLengthMeasure.")
    unit: str | None = Field(description="SI unit of a numeric value.")
    from_type: bool = Field(description="True when the value comes from the element's type.")
    implausible: bool = Field(
        description="True for a quantity-set value that breaks a physical bound of its set "
        "(e.g. a wall area above twice length x height): it is no measurement, so do not "
        "quote or sum it; the data_quality findings say which bound it broke."
    )


class PropertySet(BaseModel):
    """A property set and its values."""

    name: IfcText
    properties: list[PropertyValue]


class Classification(BaseModel):
    """A classification code, e.g. OmniClass or UniFormat."""

    system: IfcText
    code: IfcText
    title: IfcText | None


class MaterialLayer(BaseModel):
    """A material of the element, in layer order."""

    layer_index: int
    material: IfcText
    thickness_m: float | None = Field(description="Null for a material without layers.")


class Finding(BaseModel):
    """A data quality finding about one object."""

    rule_id: str
    severity: str
    dimension: str
    message: IfcText


class ElementFindings(BaseModel):
    """The object's findings in the model's latest successful data quality run."""

    status: Literal["checked", "never_checked"]
    dq_run_id: int | None
    findings: list[Finding]
    truncated: bool


class ElementDetail(ToolResult):
    """Everything stored about one element or spatial element (storey, space, ...)."""

    object_id: int
    model_id: int
    discipline: str
    global_id: str
    object_kind: str = Field(description="element, or spatial_element (site to space).")
    ifc_class: str
    ifc_class_base: str
    is_feature: bool = Field(
        description="True for an opening, void or projection: it shapes another element "
        "(host_revit_family and host_revit_type name it) and is no building part itself."
    )
    name: IfcText | None
    long_name: IfcText | None = Field(description="Spatial elements: e.g. a room's name.")
    revit_family: IfcText | None
    revit_type: IfcText | None
    host_revit_family: IfcText | None = Field(description="Feature elements: the host's family.")
    host_revit_type: IfcText | None = Field(description="Feature elements: the host's type.")
    revit_element_id: int | None
    type_name: IfcText | None
    predefined_type: IfcText | None
    tag: IfcText | None
    elevation_m: float | None = Field(description="Spatial elements: their own elevation.")
    storey_id: int | None
    storey_name: IfcText | None
    storey_elevation_m: float | None
    space_id: int | None = Field(description="Elements: the room that contains them.")
    space_number: IfcText | None
    space_name: IfcText | None
    parent_id: int | None = Field(description="Spatial elements: what they belong to.")
    parent_ifc_class: str | None
    parent_name: IfcText | None
    quantities: list[QuantityValue]
    property_sets: list[PropertySet]
    properties_truncated: bool = Field(
        description=f"True when the object has more than {MAX_PROPERTIES} property values."
    )
    classifications: list[Classification]
    materials: list[MaterialLayer]
    data_quality: ElementFindings


OBJECT_DETAIL = """
SELECT o.object_id, o.model_id, m.discipline, o.global_id, o.object_kind, o.ifc_class,
       o.ifc_class_base, coalesce(e.is_feature, false) AS is_feature, o.name, s.long_name,
       e.revit_family, e.revit_type, e.host_revit_family, e.host_revit_type,
       e.revit_element_id, e.type_name, e.predefined_type, e.tag, s.elevation_m,
       storey.object_id AS storey_id, storey.name AS storey_name,
       storey_detail.elevation_m AS storey_elevation_m,
       space.object_id AS space_id, space.name AS space_number,
       space_detail.long_name AS space_name,
       parent.object_id AS parent_id, parent.ifc_class AS parent_ifc_class,
       parent.name AS parent_name
FROM ifc.object AS o
JOIN ifc.model AS m ON o.model_id = m.model_id
LEFT JOIN ifc.spatial_element AS s ON o.object_id = s.object_id
LEFT JOIN ifc.element AS e ON o.object_id = e.object_id
LEFT JOIN ifc.object AS parent ON s.parent_id = parent.object_id
-- An element's storey is resolved at ingest; a space's is the storey it belongs to.
LEFT JOIN ifc.object AS storey ON storey.object_id = coalesce(
    e.storey_id,
    CASE WHEN parent.ifc_class_base = 'IfcBuildingStorey' THEN parent.object_id END)
LEFT JOIN ifc.spatial_element AS storey_detail ON storey.object_id = storey_detail.object_id
LEFT JOIN ifc.object AS space
    ON e.container_id = space.object_id AND space.ifc_class_base = 'IfcSpace'
LEFT JOIN ifc.spatial_element AS space_detail ON space.object_id = space_detail.object_id
"""


def get_element(
    connection: Connection,
    *,
    object_id: int | None,
    model_id: int | None,
    global_id: str | None,
) -> ElementDetail:
    """One object by ``object_id``, or by ``model_id`` and ``global_id``."""
    params: dict[str, Any]
    if object_id is not None and model_id is None and global_id is None:
        where, params = "o.object_id = :object_id", {"object_id": object_id}
        missing = f"no object with id {object_id}; find_elements and space_schedule list them"
    elif object_id is None and model_id is not None and global_id is not None:
        where, params = (
            "o.model_id = :model_id AND o.global_id = :global_id",
            {"model_id": model_id, "global_id": global_id},
        )
        _require_model(connection, model_id)
        missing = f"model {model_id} has no object with GlobalId {global_id!r}"
    else:
        raise ToolInputError("give either object_id, or both model_id and global_id")
    row = (
        connection.execute(text(f"{OBJECT_DETAIL} WHERE {where}"), params).mappings().one_or_none()
    )
    if row is None:
        raise ToolInputError(missing)
    key = {"object_id": row["object_id"]}
    quantities = connection.execute(
        text(
            "SELECT name, value, source, source_ref FROM ifc.v_quantity "
            "WHERE object_id = :object_id ORDER BY name"
        ),
        key,
    ).mappings()
    properties = list(
        connection.execute(
            text(
                "SELECT pset_name, prop_name, value_type, value_text, value_num, value_bool, "
                "unit, is_type_property, is_implausible FROM ifc.property "
                "WHERE object_id = :object_id "
                "ORDER BY pset_name, prop_name LIMIT :limit"
            ),
            {**key, "limit": MAX_PROPERTIES + 1},
        ).mappings()
    )
    classifications = connection.execute(
        text(
            "SELECT system, code, title FROM ifc.classification WHERE object_id = :object_id "
            "ORDER BY system, code"
        ),
        key,
    ).mappings()
    materials = connection.execute(
        text(
            "SELECT layer.layer_index, material.name AS material, layer.thickness_m "
            "FROM ifc.element_material_layer AS layer "
            "JOIN ifc.material AS material ON layer.material_id = material.material_id "
            "WHERE layer.element_id = :object_id ORDER BY layer.layer_index"
        ),
        key,
    ).mappings()
    return ElementDetail(
        **dict(row),
        quantities=[QuantityValue.model_validate(dict(q)) for q in quantities],
        property_sets=_property_sets(properties[:MAX_PROPERTIES]),
        properties_truncated=len(properties) > MAX_PROPERTIES,
        classifications=[Classification.model_validate(dict(c)) for c in classifications],
        materials=[MaterialLayer.model_validate(dict(m)) for m in materials],
        data_quality=_element_findings(connection, row["model_id"], row["object_id"]),
    )


def _property_sets(rows: list[RowMapping]) -> list[PropertySet]:
    """Group property rows, already ordered by set and name, into their sets."""
    sets: dict[str, list[PropertyValue]] = {}
    for row in rows:
        value = row["value_text"]
        if value is None:
            value = row["value_num"] if row["value_num"] is not None else row["value_bool"]
        sets.setdefault(row["pset_name"], []).append(
            PropertyValue(
                name=row["prop_name"],
                value=value,
                value_type=row["value_type"],
                unit=row["unit"],
                from_type=row["is_type_property"],
                implausible=row["is_implausible"],
            )
        )
    return [PropertySet(name=name, properties=values) for name, values in sets.items()]


def _element_findings(connection: Connection, model_id: int, object_id: int) -> ElementFindings:
    dq_run_id = connection.execute(
        text("SELECT dq_run_id FROM ifc.v_model_summary WHERE model_id = :model_id"),
        {"model_id": model_id},
    ).scalar()
    rows = connection.execute(
        text(
            "SELECT rule_id, severity, dimension, message FROM ifc.v_dq_finding "  # noqa: S608
            f"WHERE object_id = :object_id ORDER BY {_severity_order('severity')}, rule_id, "
            "message LIMIT :limit"
        ),
        {"object_id": object_id, "limit": MAX_ELEMENT_FINDINGS + 1},
    ).mappings()
    findings = [Finding.model_validate(dict(row)) for row in rows]
    return ElementFindings(
        status="never_checked" if dq_run_id is None else "checked",
        dq_run_id=dq_run_id,
        findings=findings[:MAX_ELEMENT_FINDINGS],
        truncated=len(findings) > MAX_ELEMENT_FINDINGS,
    )


# --------------------------------------------------------------------------- quantity_takeoff


class TakeoffGroup(BaseModel):
    """One group of the takeoff."""

    group: IfcText | None = Field(
        description="Storey name, IFC class, Revit Family:Type or material name. Null: no "
        "storey, no Revit type or no material."
    )
    group_id: int | None = Field(description="Storey or material id, where there is one.")
    elements: int = Field(description="Elements in the group.")
    elements_with_value: int | None = Field(
        description="Of those, how many have the metric; null for group_by material."
    )
    value: int | float | None = Field(description="Total of the metric (for count: elements).")


class Coverage(BaseModel):
    """How many of the elements in the takeoff have the metric at all."""

    elements: int = Field(description="Elements in the takeoff (openings excluded by default).")
    elements_with_value: int = Field(description="Of those, how many have the metric.")
    summary: str = Field(description="The same in words, e.g. '0 of 6,095 elements have area_m2'.")


class Takeoff(ToolResult):
    """A quantity takeoff of one model."""

    model_id: int
    group_by: GroupBy
    metric: Metric
    unit: str = Field(description="elements, m, m2 or m3.")
    basis: Literal["element", "material"] = Field(
        description="element: each element counted once, with its own quantity. material: "
        "v_takeoff's material split, where totals are not element totals (see note)."
    )
    note: str
    include_openings: bool = Field(
        description="False (the default): openings and other feature elements, which Revit "
        "names after the wall or door they cut, are left out of every group."
    )
    openings_left_out: int = Field(
        description="Feature elements of the model left out; 0 with include_openings."
    )
    groups: list[TakeoffGroup]
    total: int | float | None = Field(
        description="Sum of value over the groups returned; null, not 0, when no element "
        "has the metric (coverage says so), because nobody measured it."
    )
    coverage: Coverage
    truncated: bool = Field(description=f"True when there are more than {MAX_GROUPS} groups.")

    def logged_rows(self) -> int:
        """Return the number of groups."""
        return len(self.groups)


METRIC_UNITS: dict[Metric, str] = {
    "count": "elements",
    "length_m": "m",
    "area_m2": "m2",
    "volume_m3": "m3",
}


class ElementGrouping(NamedTuple):
    """How a takeoff on v_element groups: the label, the id, GROUP BY and ORDER BY."""

    label: str
    group_id: str
    group_by: str
    order_by: str


ELEMENT_GROUPINGS: dict[GroupBy, ElementGrouping] = {
    "storey": ElementGrouping(
        label="e.storey_name",
        group_id="e.storey_id",
        group_by="e.storey_id, e.storey_name, e.storey_elevation_m",
        order_by="e.storey_elevation_m NULLS LAST, e.storey_name",
    ),
    "ifc_class": ElementGrouping(
        label="e.ifc_class_base",
        group_id="NULL::bigint",
        group_by="e.ifc_class_base",
        order_by="value DESC NULLS LAST, e.ifc_class_base",
    ),
    "revit_type": ElementGrouping(
        label="e.revit_family || ':' || e.revit_type",
        group_id="NULL::bigint",
        group_by="e.revit_family, e.revit_type",
        order_by="value DESC NULLS LAST, e.revit_family, e.revit_type",
    ),
}

ELEMENT_NOTE = (
    "Each element is counted once, with its own quantity from v_element. Elements without "
    "the metric count in elements but add nothing to value; elements_with_value says how "
    "many had one."
)
MATERIAL_NOTE = (
    "From v_takeoff, which splits elements by material: an element made of several "
    "materials counts once under each; a layered material's area is the element's area "
    "times its number of layers, and its volume that area times the layers' thickness. "
    "These totals are therefore not element totals: group by storey or ifc_class for "
    "those. Elements without a material are in the null group. v_takeoff does not count "
    "elements that lack the metric, so elements_with_value is null."
)


# Openings, voids and projections shape another element and are named after it, so a
# takeoff that kept them would count each wall's openings as more walls of its type.
NOT_FEATURE = "NOT e.is_feature"
MATERIAL_NOT_FEATURE = (
    "NOT EXISTS (SELECT 1 FROM ifc.element AS fe JOIN ifc.object AS fo USING (object_id) "
    "WHERE fe.is_feature AND fo.model_id = t.model_id AND fo.ifc_class_base = t.ifc_class_base)"
)


NO_QUANTITIES_NOTE = (
    "WARNING: no element in this takeoff has {metric}, so there is no total (null, not 0). "
    "The model holds no IFC base quantities and no Revit dimension parameters (at most a "
    "door's or window's size): the IFC file was exported without them. In "
    'Revit\'s IFC export setup, tick "Export base quantities" (or "Export Revit property '
    'sets") and export again.'
)
NO_METRIC_NOTE = (
    "WARNING: no element in this takeoff has {metric}, so there is no total (null, not 0). "
    "The model has other quantities; get_element on one element shows which."
)
PARTIAL_NOTE = "Only {summary}: the total covers those, and the others add nothing to it."


def _coverage(
    connection: Connection, model_id: int, metric: Metric, include_openings: bool
) -> Coverage:
    """Count the elements in the takeoff, and those of them that have the metric."""
    with_value = "count(*)" if metric == "count" else f"count(e.{metric})"
    features = "" if include_openings else f" AND {NOT_FEATURE}"
    row = connection.execute(
        text(
            f"SELECT count(*) AS elements, {with_value} AS with_value "  # noqa: S608
            f"FROM ifc.v_element AS e WHERE e.model_id = :model_id{features}"
        ),
        {"model_id": model_id},
    ).one()
    elements, with_value_count = int(row.elements), int(row.with_value)
    what = "elements" if elements != 1 else "element"
    verb = "have" if elements != 1 else "has"
    subject = "elements" if metric == "count" else metric
    return Coverage(
        elements=elements,
        elements_with_value=with_value_count,
        summary=f"{with_value_count:,} of {elements:,} {what} {verb} {subject}",
    )


def _coverage_note(
    connection: Connection, model_id: int, metric: Metric, coverage: Coverage
) -> str:
    """A warning when nothing or only part of the takeoff is measured; empty otherwise."""
    if metric == "count" or coverage.elements_with_value == coverage.elements:
        return ""
    if coverage.elements_with_value:
        return PARTIAL_NOTE.format(summary=coverage.summary)
    # Attributes (a door's overall size) are written whatever the export options, so only
    # quantity sets and Revit parameters say whether the file was exported with quantities.
    quantities = connection.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM ifc.quantity AS q JOIN ifc.object AS o "
            "USING (object_id) WHERE o.model_id = :model_id "
            "AND q.source IN ('ifc_qto', 'revit_pset'))"
        ),
        {"model_id": model_id},
    ).scalar_one()
    return (NO_METRIC_NOTE if quantities else NO_QUANTITIES_NOTE).format(metric=metric)


def quantity_takeoff(
    connection: Connection,
    model_id: int,
    group_by: GroupBy,
    metric: Metric,
    *,
    include_openings: bool = False,
) -> Takeoff:
    """Totals of one metric per group; see ELEMENT_NOTE and MATERIAL_NOTE for the meaning.

    Feature elements (openings) are left out unless ``include_openings``; v_takeoff has no
    is_feature column, but its grain includes the class, and a class is either all
    features or none.
    """
    _require_model(connection, model_id)
    # Every piece of this SQL comes from the constants above. The lookups are the
    # whitelist: an unknown metric or group_by raises KeyError here, before any SQL is put
    # together (the tool's schema already admits only their keys). model_id is a parameter.
    unit = METRIC_UNITS[metric]
    if group_by == "material":
        # v_takeoff's grain is model x storey x class x material: add up per material.
        value = "sum(t.element_count)" if metric == "count" else f"sum(t.{metric})"
        features = "" if include_openings else f" AND {MATERIAL_NOT_FEATURE}"
        sql = (
            "SELECT t.material_name AS label, t.material_id AS group_id, "  # noqa: S608
            "sum(t.element_count) AS elements, NULL::bigint AS elements_with_value, "
            f"{value} AS value FROM ifc.v_takeoff AS t WHERE t.model_id = :model_id{features} "
            "GROUP BY t.material_id, t.material_name "
            "ORDER BY value DESC NULLS LAST, t.material_name LIMIT :limit"
        )
        basis: Literal["element", "material"] = "material"
        note = MATERIAL_NOTE
    else:
        grouping = ELEMENT_GROUPINGS[group_by]
        if metric == "count":
            with_value, value = "count(*)", "count(*)"
        else:
            with_value, value = f"count(e.{metric})", f"sum(e.{metric})"
        features = "" if include_openings else f" AND {NOT_FEATURE}"
        sql = (
            f"SELECT {grouping.label} AS label, {grouping.group_id} AS group_id, "  # noqa: S608
            f"count(*) AS elements, {with_value} AS elements_with_value, {value} AS value "
            f"FROM ifc.v_element AS e WHERE e.model_id = :model_id{features} "
            f"GROUP BY {grouping.group_by} ORDER BY {grouping.order_by} LIMIT :limit"
        )
        basis, note = "element", ELEMENT_NOTE
    rows = connection.execute(text(sql), {"model_id": model_id, "limit": MAX_GROUPS + 1}).mappings()
    groups = [
        TakeoffGroup(
            group=row["label"],
            group_id=row["group_id"],
            elements=row["elements"],
            elements_with_value=row["elements_with_value"],
            value=row["value"],
        )
        for row in rows
    ]
    kept = groups[:MAX_GROUPS]
    coverage = _coverage(connection, model_id, metric, include_openings)
    warning = _coverage_note(connection, model_id, metric, coverage)
    total = None
    if metric == "count" or coverage.elements_with_value:
        total = sum(group.value or 0 for group in kept)
    left_out = 0
    if not include_openings:
        left_out = connection.execute(
            text(
                "SELECT count(*) FROM ifc.element AS e JOIN ifc.object AS o USING (object_id) "
                "WHERE o.model_id = :model_id AND e.is_feature"
            ),
            {"model_id": model_id},
        ).scalar_one()
    return Takeoff(
        model_id=model_id,
        group_by=group_by,
        metric=metric,
        unit=unit,
        basis=basis,
        note=f"{warning} {note}" if warning else note,
        include_openings=include_openings,
        openings_left_out=left_out,
        groups=kept,
        total=total,
        coverage=coverage,
        truncated=len(groups) > MAX_GROUPS,
    )


# --------------------------------------------------------------------------- space_schedule


class Space(BaseModel):
    """One space (room)."""

    object_id: int
    global_id: str
    number: IfcText | None = Field(description="e.g. A101 (the IFC Name).")
    name: IfcText | None = Field(description="e.g. Kitchen (the IFC LongName).")
    storey_name: IfcText | None
    area_m2: float | None
    perimeter_m: float | None
    volume_m3: float | None
    height_m: float | None
    omniclass_code: IfcText | None = Field(description="OmniClass Table 13 code.")
    omniclass_title: IfcText | None
    duplicate_of: int | None = Field(
        description="object_id of an earlier space with the same number, name, storey and "
        "area: this one repeats it (Rooms and MEP Spaces both exported). Null for the first."
    )


class SpaceSchedule(ToolResult):
    """One page of the room schedule of a model, by storey and number, with its totals."""

    model_id: int
    spaces: list[Space] = Field(description="This page of the spaces.")
    space_count: int = Field(description="Spaces in the model, on every page together.")
    offset: int
    limit: int
    next_offset: int | None = Field(description="Offset of the next page; null on the last.")
    total_area_m2: float | None = Field(
        description="Sum of area_m2 over all the model's spaces, not only this page; null, not "
        "0, when none has one. Counts a room exported twice twice: see "
        "total_area_without_duplicates_m2."
    )
    duplicate_spaces: int = Field(description="Spaces that repeat an earlier one (duplicate_of).")
    total_area_without_duplicates_m2: float | None = Field(
        description="Sum of area_m2 over the spaces that repeat no other: each room once."
    )
    spaces_without_area: int
    note: str | None = Field(description="A warning when some or all spaces have no area.")

    def logged_rows(self) -> int:
        """Return the number of spaces on the page."""
        return len(self.spaces)


def space_schedule(
    connection: Connection, model_id: int, *, limit: int = SPACE_PAGE_SIZE, offset: int = 0
) -> SpaceSchedule:
    """One page of the model's spaces from v_space_schedule, lowest storey first.

    The totals and counts cover every space of the model, whichever page is asked for: a
    schedule of 500 rooms no longer has to be read whole (about 112,000 characters) for its
    total (audit finding A11).
    """
    _require_model(connection, model_id)
    totals = connection.execute(
        text(
            """
            SELECT count(*) AS spaces, count(area_m2) AS with_area, sum(area_m2) AS area,
                   count(*) FILTER (WHERE duplicate_of IS NOT NULL) AS duplicates,
                   sum(area_m2) FILTER (WHERE duplicate_of IS NULL) AS unique_area
            FROM ifc.v_space_schedule
            WHERE model_id = :model_id
            """
        ),
        {"model_id": model_id},
    ).one()
    rows = connection.execute(
        text(
            """
            SELECT s.object_id, s.global_id, s.number, s.name, s.storey_name, s.area_m2,
                   s.perimeter_m, s.volume_m3, s.height_m, s.omniclass_code, s.omniclass_title,
                   s.duplicate_of
            FROM ifc.v_space_schedule AS s
            LEFT JOIN ifc.spatial_element AS storey ON s.storey_id = storey.object_id
            WHERE s.model_id = :model_id
            ORDER BY storey.elevation_m NULLS LAST, s.number, s.object_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"model_id": model_id, "limit": limit, "offset": offset},
    ).mappings()
    spaces = [Space.model_validate(dict(row)) for row in rows]
    without = totals.spaces - totals.with_area
    note = None
    if totals.spaces and not totals.with_area:
        note = (
            f"WARNING: none of the {totals.spaces:,} spaces has an area, so there is no total "
            '(null, not 0). Export the IFC from Revit with "Export base quantities" ticked.'
        )
    elif without:
        note = f"{without:,} of {totals.spaces:,} spaces have no area; the total covers the others."
    if totals.duplicates:
        repeat = (
            f"{totals.duplicates:,} spaces repeat an earlier one with the same number, name, "
            "storey and area (Revit Rooms and MEP Spaces both exported): total_area_m2 counts "
            "those rooms twice, total_area_without_duplicates_m2 once."
        )
        note = f"{note} {repeat}" if note else repeat
    following = offset + len(spaces)
    return SpaceSchedule(
        model_id=model_id,
        spaces=spaces,
        space_count=totals.spaces,
        offset=offset,
        limit=limit,
        next_offset=following if following < totals.spaces else None,
        total_area_m2=totals.area,
        duplicate_spaces=totals.duplicates,
        total_area_without_duplicates_m2=totals.unique_area,
        spaces_without_area=without,
        note=note,
    )


# --------------------------------------------------------------------------- schedule

ScheduleGroupBy = Literal["storey", "revit_type", "ifc_class"]
QuantityField = Literal["length_m", "area_m2", "volume_m3", "perimeter_m", "height_m", "width_m"]
QUANTITY_FIELDS: tuple[QuantityField, ...] = (
    "length_m",
    "area_m2",
    "volume_m3",
    "perimeter_m",
    "height_m",
    "width_m",
)
MAX_SCHEDULE_FIELDS = 8
MAX_SCHEDULE_VALUES = 10
"""Most distinct values listed per field and group, most common first."""
MAX_SCHEDULE_CHARS = 30_000
"""Most characters of groups (as compact JSON) one schedule carries, as for run_sql."""
SIGNIFICANT_DIGITS = 6
"""Numbers are told apart to this many digits: Revit writes one door's 0.87 m width as
0.870000000000002 and the next one's as 0.870000000000012."""

type ScheduleValue = IfcText | bool | float


class ValueCount(BaseModel):
    """One value of a field, and how many elements of the group have it."""

    value: ScheduleValue
    elements: int


class FieldSummary(BaseModel):
    """One field of a schedule row: its values in the group, the way Revit schedules them."""

    elements_with_value: int = Field(description="Elements of the group that have a value.")
    distinct_values: int
    values: list[ValueCount] = Field(
        description=f"The most common values first, at most {MAX_SCHEDULE_VALUES}."
    )
    unit: str | None = Field(description="Unit of the numbers: m, m2, m3, or the property's.")
    min: float | None = Field(description="Smallest number, for a numeric field.")
    max: float | None
    sum: float | None = Field(description="Sum of the numbers, e.g. a type's total area.")


class ScheduleRow(BaseModel):
    """One group of a schedule: a storey, a Revit type, a class, or a combination."""

    group: list[IfcText | None] = Field(
        description="The group's value for each group_by, in order; null means none (no "
        "storey, no Revit type)."
    )
    elements: int
    fields: dict[str, FieldSummary] = Field(description="One summary per field asked for.")


class Schedule(ToolResult):
    """Elements of one class, grouped, with chosen quantities and properties."""

    model_id: int
    ifc_class: str
    storey_name: str | None
    group_by: list[ScheduleGroupBy]
    fields: list[str]
    elements: int = Field(description="Elements in the schedule, all groups together.")
    groups: list[ScheduleRow] = Field(
        description="By storey elevation when grouped by storey, then the most elements first."
    )
    group_count: int = Field(description="Groups in all; more than returned when truncated.")
    truncated: bool = Field(
        description=f"True when more than {MAX_GROUPS} groups, or {MAX_SCHEDULE_CHARS:,} "
        "characters of them, exist: narrow with storey_name, fewer fields or group_by keys."
    )
    note: str | None = Field(
        description="Says when the schedule was cut, or no element has a field (with the "
        "closest names that exist)."
    )

    def logged_rows(self) -> int:
        """Return the number of groups."""
        return len(self.groups)


class _Key(NamedTuple):
    """A schedule's group_by on v_element: the label, and how it sorts."""

    label: str
    order: str


SCHEDULE_KEYS: dict[ScheduleGroupBy, _Key] = {
    "storey": _Key("e.storey_name", "min(e.storey_elevation_m) NULLS LAST"),
    "revit_type": _Key("e.revit_family || ':' || e.revit_type", ""),
    "ifc_class": _Key("e.ifc_class_base", ""),
}


def schedule(
    connection: Connection,
    model_id: int,
    ifc_class: str,
    group_by: list[ScheduleGroupBy],
    fields: list[str],
    *,
    storey_name: str | None = None,
) -> Schedule:
    """Group one class's elements and summarise the chosen fields per group (audit A11).

    A field is a canonical quantity (``width_m``) or a property as ``PsetName.PropName``
    (``Pset_DoorCommon.FireRating``, ``IfcDoor.OverallWidth``), matched without regard to
    case. Instead of one row per element, each group lists each field's distinct values
    with how many elements have them, and the minimum, maximum and sum of its numbers:
    a door schedule of a 1,458-door tower is a few kilobytes, where paging the doors
    through find_elements and get_element was about 490,000 characters.

    Raises:
        ToolInputError: The model does not exist, or a field is neither a quantity nor
            ``PsetName.PropName``, or there are too many fields or group_by keys.
    """
    _require_model(connection, model_id)
    keys = list(dict.fromkeys(group_by))
    wanted = list(dict.fromkeys(field.strip() for field in fields))
    if not keys:
        raise ToolInputError("give at least one group_by: storey, revit_type or ifc_class")
    if not wanted or len(wanted) > MAX_SCHEDULE_FIELDS:
        raise ToolInputError(
            f"give 1 to {MAX_SCHEDULE_FIELDS} fields: quantities ({', '.join(QUANTITY_FIELDS)}) "
            "or properties as PsetName.PropName, e.g. Pset_DoorCommon.FireRating"
        )
    for field in wanted:
        if field not in QUANTITY_FIELDS and "." not in field.strip("."):
            raise ToolInputError(
                f"field {field!r} is neither a quantity ({', '.join(QUANTITY_FIELDS)}) nor a "
                "property as PsetName.PropName, e.g. Pset_DoorCommon.FireRating; get_element "
                "lists an element's properties"
            )

    labels = [SCHEDULE_KEYS[key].label for key in keys]
    columns = ", ".join(f"{label} AS g{n}" for n, label in enumerate(labels))
    group_columns = ", ".join(f"g{n}" for n in range(len(labels)))
    conditions = ["e.model_id = :model_id", ELEMENT_FILTERS["ifc_class"]]
    params: dict[str, Any] = {"model_id": model_id, "ifc_class": ifc_class}
    if storey_name is not None:
        conditions.append(ELEMENT_FILTERS["storey_name"])
        params["storey_name"] = storey_name
    # Put together from the constant expressions above; values are parameters.
    elements = (
        f"SELECT e.*, {columns} FROM ifc.v_element AS e "  # noqa: S608
        f"WHERE {' AND '.join(conditions)}"
    )
    order = [SCHEDULE_KEYS[key].order for key in keys if SCHEDULE_KEYS[key].order]
    groups = connection.execute(
        text(
            f"SELECT {group_columns}, count(*) AS elements FROM ({elements}) AS e "  # noqa: S608
            f"GROUP BY {group_columns} "
            f"ORDER BY {', '.join([*order, 'elements DESC'])}, {group_columns}"
        ),
        params,
    ).all()
    rows = {
        tuple(row[:-1]): ScheduleRow(group=list(row[:-1]), elements=row[-1], fields={})
        for row in groups
    }
    missing = []
    for field in wanted:
        summaries = _field_summaries(
            connection, elements, group_columns, len(labels), field, params
        )
        for key, row in rows.items():
            row.fields[field] = summaries.get(key, _EMPTY_FIELD)
        if groups and not summaries:
            missing.append(field)
    kept: list[ScheduleRow] = []
    size = 0
    for row in rows.values():
        size += len(row.model_dump_json())
        if kept and (len(kept) == MAX_GROUPS or size > MAX_SCHEDULE_CHARS):
            break  # the first group is always shown, however long
        kept.append(row)
    note = _schedule_note(connection, elements, params, groups, missing, ifc_class)
    if len(kept) < len(rows):
        cut = (
            f"Showing the first {len(kept):,} of {len(rows):,} groups: all of them would pass "
            f"{MAX_GROUPS} groups or {MAX_SCHEDULE_CHARS:,} characters. Narrow it with "
            "storey_name, fewer fields or fewer group_by keys."
        )
        note = f"{cut} {note}" if note else cut
    return Schedule(
        model_id=model_id,
        ifc_class=ifc_class,
        storey_name=storey_name,
        group_by=keys,
        fields=wanted,
        elements=sum(row.elements for row in rows.values()),
        groups=kept,
        group_count=len(rows),
        truncated=len(kept) < len(rows),
        note=note,
    )


_EMPTY_FIELD = FieldSummary(
    elements_with_value=0, distinct_values=0, values=[], unit=None, min=None, max=None, sum=None
)


def _field_values(elements: str, group_columns: str, field: str) -> tuple[str, dict[str, Any]]:
    """A query of one row per element of ``elements`` that has a value for ``field``."""
    if field in QUANTITY_FIELDS:
        unit = {"area_m2": "m2", "volume_m3": "m3"}.get(field, "m")
        # field is one of the constant quantity names, never caller text.
        return (
            f"SELECT {group_columns}, e.{field} AS num, NULL::text AS txt, "  # noqa: S608
            f"NULL::boolean AS bool, '{unit}'::text AS unit FROM ({elements}) AS e "
            f"WHERE e.{field} IS NOT NULL"
        ), {}
    return (
        f"SELECT {group_columns}, p.value_num AS num, p.value_text AS txt, "  # noqa: S608
        f"p.value_bool AS bool, p.unit FROM ({elements}) AS e "
        "JOIN ifc.property AS p ON p.object_id = e.object_id "
        "AND lower(p.pset_name || '.' || p.prop_name) = lower(:field) "
        "WHERE p.value_num IS NOT NULL OR p.value_text IS NOT NULL OR p.value_bool IS NOT NULL"
    ), {"field": field}


def _field_summaries(
    connection: Connection,
    elements: str,
    group_columns: str,
    width: int,
    field: str,
    params: dict[str, Any],
) -> dict[tuple[Any, ...], FieldSummary]:
    """Each group's summary of ``field``; groups where no element has it are absent."""
    exact, extra = _field_values(elements, group_columns, field)
    # Values are counted with their numbers to SIGNIFICANT_DIGITS; min, max and sum are exact.
    values = (
        "SELECT v.*, CASE WHEN v.num IS NULL OR v.num = 0 THEN v.num ELSE "  # noqa: S608
        "round(v.num::numeric, greatest(0, :digits - 1 - floor(log(abs(v.num::numeric)))::int))"
        f"::float8 END AS rnum FROM ({exact}) AS v"
    )
    bound = {**params, **extra, "values": MAX_SCHEDULE_VALUES, "digits": SIGNIFICANT_DIGITS}
    summaries: dict[tuple[Any, ...], FieldSummary] = {}
    for row in connection.execute(
        text(
            f"SELECT {group_columns}, count(*), count(DISTINCT (rnum, txt, bool)), "  # noqa: S608
            f"min(unit), min(num), max(num), sum(num) FROM ({values}) AS v GROUP BY {group_columns}"
        ),
        bound,
    ):
        key = tuple(row[:width])
        count, distinct, unit, low, high, total = row[width:]
        summaries[key] = FieldSummary(
            elements_with_value=count,
            distinct_values=distinct,
            values=[],
            unit=unit,
            min=low,
            max=high,
            sum=total,
        )
    for row in connection.execute(
        text(
            f"SELECT {group_columns}, rnum, txt, bool, n FROM ("  # noqa: S608
            f"SELECT {group_columns}, rnum, txt, bool, count(*) AS n, row_number() OVER ("
            f"PARTITION BY {group_columns} ORDER BY count(*) DESC, rnum, txt, bool) AS rank "
            f"FROM ({values}) AS v GROUP BY {group_columns}, rnum, txt, bool) AS ranked "
            f"WHERE rank <= :values ORDER BY {group_columns}, rank"
        ),
        bound,
    ):
        number, text_value, boolean, count = row[width:]
        value = next(v for v in (text_value, boolean, number) if v is not None)
        summaries[tuple(row[:width])].values.append(ValueCount(value=value, elements=count))
    return summaries


def _schedule_note(
    connection: Connection,
    elements: str,
    params: dict[str, Any],
    groups: Sequence[Any],
    missing: list[str],
    ifc_class: str,
) -> str | None:
    """Why a schedule is empty, or which fields no element has and what exists instead."""
    if not groups:
        return (
            f"No element of class {ifc_class} matches; quantity_takeoff with group_by ifc_class "
            "lists the model's classes, and list_storeys its storeys. Spaces are not "
            "elements: space_schedule lists them."
        )
    if not missing:
        return None
    existing = (
        connection.execute(
            text(
                f"SELECT DISTINCT p.pset_name || '.' || p.prop_name FROM ({elements}) AS e "  # noqa: S608
                "JOIN ifc.property AS p ON p.object_id = e.object_id"
            ),
            params,
        )
        .scalars()
        .all()
    )
    notes = []
    for field in missing:
        closest = difflib.get_close_matches(field, existing, n=5, cutoff=0.5)
        hint = f" The closest these elements have: {', '.join(closest)}." if closest else ""
        notes.append(f"No element in this schedule has {field}.{hint}")
    return " ".join(notes)


# --------------------------------------------------------------------------- data_quality_report


class SampleFinding(BaseModel):
    """One finding, with the object it is about."""

    object_id: int | None = Field(description="Null for a finding about the whole model.")
    global_id: str | None
    ifc_class: str | None
    object_name: IfcText | None
    message: IfcText


class RuleFindings(BaseModel):
    """What one rule found."""

    rule_id: str
    severity: Severity = Field(description="As it was when the run found it.")
    dimension: str = Field(description="completeness, validity, consistency or uniqueness.")
    scope: Scope = Field(
        description="model_content: about what the file contains, for the model's author to "
        "act on. loader_coverage: something this tool does not read; the model is not at "
        "fault."
    )
    description: str = Field(description="What the rule checks, from ifc.dq_rule.")
    findings: int
    samples: list[SampleFinding] = Field(description="The first findings, by object id.")


class SeverityCounts(BaseModel):
    """Findings per severity."""

    error: int
    warning: int
    info: int


class DataQualityReport(ToolResult):
    """A model's findings in its latest successful data quality run."""

    model_id: int
    status: Literal["checked", "never_checked"] = Field(
        description="never_checked: no run yet (`ifc-sql dq`); everything else is empty."
    )
    dq_run_id: int | None
    finished_at: datetime | None
    findings: SeverityCounts | None = Field(description="All findings, whatever the filters.")
    findings_by_scope: dict[Scope, SeverityCounts] | None = Field(
        description="The same, split into model_content (the model's gaps) and "
        "loader_coverage (the tool's): judge a model on the first."
    )
    rules: list[RuleFindings] = Field(
        description="Rules with findings that pass the filters: model_content first, then "
        "errors first, then most found."
    )
    rules_without_findings: list[str] = Field(
        description="Rules in the current catalogue that found nothing in this run. A rule "
        "added to the catalogue after the run also appears here."
    )

    def logged_rows(self) -> int:
        """Return the number of rules with findings."""
        return len(self.rules)


def data_quality_report(
    connection: Connection,
    model_id: int,
    *,
    severity: Severity | None,
    rule_id: str | None,
    samples_per_rule: int,
    scope: Scope | None = None,
) -> DataQualityReport:
    """Counts per rule, and the first findings of each, for the model's latest run."""
    _require_model(connection, model_id)
    if rule_id is not None:
        known = connection.execute(
            text("SELECT 1 FROM ifc.dq_rule WHERE rule_id = :rule_id"), {"rule_id": rule_id}
        ).scalar()
        if known is None:
            raise ToolInputError(
                f"no rule {rule_id!r}; the rules are in the ifc.dq_rule table, and this "
                "report without rule_id lists those that found something"
            )
    run = (
        connection.execute(
            text(
                "SELECT dq_run_id, dq_finished_at, dq_error_count, dq_warning_count, "
                "dq_info_count FROM ifc.v_model_summary WHERE model_id = :model_id"
            ),
            {"model_id": model_id},
        )
        .mappings()
        .one()
    )
    if run["dq_run_id"] is None:
        return DataQualityReport(
            model_id=model_id,
            status="never_checked",
            dq_run_id=None,
            finished_at=None,
            findings=None,
            findings_by_scope=None,
            rules=[],
            rules_without_findings=[],
        )
    conditions = ["f.model_id = :model_id"]
    params: dict[str, Any] = {"model_id": model_id, "samples": samples_per_rule}
    if severity is not None:
        conditions.append("f.severity = :severity")
        params["severity"] = severity
    if rule_id is not None:
        conditions.append("f.rule_id = :rule_id")
        params["rule_id"] = rule_id
    if scope is not None:
        conditions.append("f.scope = :scope")
        params["scope"] = scope
    where = " AND ".join(conditions)
    # Put together from the constant conditions above; values are parameters.
    counts = connection.execute(
        text(
            "SELECT f.rule_id, f.severity, f.dimension, f.scope, r.description, "  # noqa: S608
            "count(*) AS findings "
            "FROM ifc.v_dq_finding AS f JOIN ifc.dq_rule AS r ON f.rule_id = r.rule_id "
            f"WHERE {where} GROUP BY f.rule_id, f.severity, f.dimension, f.scope, r.description "
            "ORDER BY CASE f.scope WHEN 'model_content' THEN 1 ELSE 2 END, "
            f"{_severity_order('f.severity')}, findings DESC, f.rule_id"
        ),
        params,
    ).mappings()
    samples: dict[str, list[SampleFinding]] = {}
    for row in connection.execute(
        text(
            "SELECT rule_id, object_id, global_id, ifc_class, object_name, message FROM ("  # noqa: S608
            "SELECT f.*, row_number() OVER (PARTITION BY f.rule_id "
            "ORDER BY f.object_id NULLS FIRST, f.message) AS n "
            f"FROM ifc.v_dq_finding AS f WHERE {where}) AS numbered "
            "WHERE n <= :samples ORDER BY rule_id, n"
        ),
        params,
    ).mappings():
        sample = SampleFinding.model_validate({k: v for k, v in row.items() if k != "rule_id"})
        samples.setdefault(row["rule_id"], []).append(sample)
    rules = [RuleFindings(**dict(row), samples=samples.get(row["rule_id"], [])) for row in counts]
    quiet = connection.execute(
        text(
            "SELECT r.rule_id FROM ifc.dq_rule AS r WHERE r.retired_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM ifc.v_dq_finding AS f "
            "WHERE f.model_id = :model_id AND f.rule_id = r.rule_id) ORDER BY r.rule_id"
        ),
        {"model_id": model_id},
    ).scalars()
    by_scope: dict[Scope, SeverityCounts] = {
        name: SeverityCounts(error=0, warning=0, info=0)
        for name in ("model_content", "loader_coverage")
    }
    for row in connection.execute(
        text(
            "SELECT scope, severity, count(*) AS findings FROM ifc.v_dq_finding "
            "WHERE model_id = :model_id GROUP BY scope, severity"
        ),
        {"model_id": model_id},
    ).mappings():
        setattr(by_scope[row["scope"]], row["severity"], row["findings"])
    return DataQualityReport(
        model_id=model_id,
        status="checked",
        dq_run_id=run["dq_run_id"],
        finished_at=run["dq_finished_at"],
        findings=SeverityCounts(
            error=run["dq_error_count"],
            warning=run["dq_warning_count"],
            info=run["dq_info_count"],
        ),
        findings_by_scope=by_scope,
        rules=rules,
        rules_without_findings=list(quiet),
    )
