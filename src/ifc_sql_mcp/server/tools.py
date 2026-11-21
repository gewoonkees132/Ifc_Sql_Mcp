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

from collections.abc import Mapping
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
MAX_SPACES = 500
DEFAULT_SAMPLES = 3
MAX_SAMPLES = 20

GroupBy = Literal["storey", "ifc_class", "material", "revit_type"]
Metric = Literal["count", "length_m", "area_m2", "volume_m3"]
Severity = Literal["error", "warning", "info"]


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
    truncated: bool = Field(description=f"True when more than {MAX_MODELS} models are loaded.")

    def logged_rows(self) -> int:
        """Return the number of models."""
        return len(self.models)


MODEL_SUMMARY = """
SELECT model_id, file_name, discipline, ifc_schema, mvd, authoring_app, project_name,
       project_long_name, length_unit, ingested_at, storey_count, space_count, element_count,
       property_count, quantity_count, material_count, dq_run_id, dq_finished_at,
       dq_error_count, dq_warning_count, dq_info_count
FROM ifc.v_model_summary
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


def list_models(connection: Connection) -> ModelList:
    """Every loaded model, oldest first, with its counts and data quality status."""
    rows = connection.execute(
        text(f"{MODEL_SUMMARY} ORDER BY model_id LIMIT :limit"), {"limit": MAX_MODELS + 1}
    ).mappings()
    models = [_model_summary(row) for row in rows]
    return ModelList(models=models[:MAX_MODELS], truncated=len(models) > MAX_MODELS)


def model_summary(connection: Connection, model_id: int) -> ModelSummary:
    """One model's summary (the ``ifc://models/{model_id}/summary`` resource)."""
    row = (
        connection.execute(
            text(f"{MODEL_SUMMARY} WHERE model_id = :model_id"), {"model_id": model_id}
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
    """The room schedule of a model, by storey and number."""

    model_id: int
    spaces: list[Space]
    space_count: int
    total_area_m2: float | None = Field(
        description="Sum of area_m2 over the spaces returned; null, not 0, when none has one. "
        "Counts a room exported twice twice: see total_area_without_duplicates_m2."
    )
    duplicate_spaces: int = Field(description="Spaces that repeat an earlier one (duplicate_of).")
    total_area_without_duplicates_m2: float | None = Field(
        description="Sum of area_m2 over the spaces that repeat no other: each room once."
    )
    spaces_without_area: int
    note: str | None = Field(description="A warning when some or all spaces have no area.")
    truncated: bool = Field(description=f"True when there are more than {MAX_SPACES} spaces.")

    def logged_rows(self) -> int:
        """Return the number of spaces."""
        return len(self.spaces)


def space_schedule(connection: Connection, model_id: int) -> SpaceSchedule:
    """The model's spaces from v_space_schedule, lowest storey first."""
    _require_model(connection, model_id)
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
            LIMIT :limit
            """
        ),
        {"model_id": model_id, "limit": MAX_SPACES + 1},
    ).mappings()
    spaces = [Space.model_validate(dict(row)) for row in rows]
    kept = spaces[:MAX_SPACES]
    with_area = [space.area_m2 for space in kept if space.area_m2 is not None]
    unique_area = [
        space.area_m2 for space in kept if space.area_m2 is not None and space.duplicate_of is None
    ]
    duplicates = sum(space.duplicate_of is not None for space in kept)
    without = len(kept) - len(with_area)
    note = None
    if kept and not with_area:
        note = (
            f"WARNING: none of the {len(kept):,} spaces has an area, so there is no total "
            '(null, not 0). Export the IFC from Revit with "Export base quantities" ticked.'
        )
    elif without:
        note = f"{without:,} of {len(kept):,} spaces have no area; the total covers the others."
    if duplicates:
        repeat = (
            f"{duplicates:,} spaces repeat an earlier one with the same number, name, storey "
            "and area (Revit Rooms and MEP Spaces both exported): total_area_m2 counts those "
            "rooms twice, total_area_without_duplicates_m2 once."
        )
        note = f"{note} {repeat}" if note else repeat
    return SpaceSchedule(
        model_id=model_id,
        spaces=kept,
        space_count=len(kept),
        total_area_m2=sum(with_area) if with_area else None,
        duplicate_spaces=duplicates,
        total_area_without_duplicates_m2=sum(unique_area) if unique_area else None,
        spaces_without_area=without,
        note=note,
        truncated=len(spaces) > MAX_SPACES,
    )


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
    rules: list[RuleFindings] = Field(
        description="Rules with findings that pass the filters, errors first, then most found."
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
    where = " AND ".join(conditions)
    # Put together from the constant conditions above; values are parameters.
    counts = connection.execute(
        text(
            "SELECT f.rule_id, f.severity, f.dimension, r.description, count(*) AS findings "  # noqa: S608
            "FROM ifc.v_dq_finding AS f JOIN ifc.dq_rule AS r ON f.rule_id = r.rule_id "
            f"WHERE {where} GROUP BY f.rule_id, f.severity, f.dimension, r.description "
            f"ORDER BY {_severity_order('f.severity')}, findings DESC, "
            "f.rule_id"
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
        rules=rules,
        rules_without_findings=list(quiet),
    )
