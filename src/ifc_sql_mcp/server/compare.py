"""``compare_models``: what changed between two exports of a model (audit finding A15).

"What changed since my last export?" needed hand-written SQL. Objects are matched by
GlobalId, which Revit keeps for an element from one export to the next:

* **objects** only in the new model are added, only in the old one removed, per class;
* **property values** of the objects in both are added, removed or changed, per
  property set and name. A value is changed when its text, number, boolean or unit
  differs. Numbers are compared exactly: a re-export of the same Revit model writes the
  same digits, so a difference is a change someone made.

Everything is counted in SQL. The result holds the counts per class and per property,
and the first few examples of each, by GlobalId.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from ifc_sql_mcp.server.tools import (
    MAX_GROUPS,
    IfcText,
    ToolInputError,
    ToolResult,
    _require_model,
)

DEFAULT_COMPARE_SAMPLES = 3
MAX_COMPARE_SAMPLES = 10


class ObjectRef(BaseModel):
    """An object, as one model holds it."""

    global_id: str
    ifc_class: str
    name: IfcText | None


class ClassChange(BaseModel):
    """Objects of one class that only one of the two models has."""

    ifc_class: str
    objects: int
    samples: list[ObjectRef] = Field(description="The first of them, by GlobalId.")


type PropertyValue = IfcText | bool | float | None


class ValueChange(BaseModel):
    """One property value of one object, before and after."""

    global_id: str
    object_name: IfcText | None
    old: PropertyValue = Field(description="Null when the old model has no such value.")
    new: PropertyValue = Field(description="Null when the new model has no such value.")
    old_unit: str | None
    new_unit: str | None


class PropertyChange(BaseModel):
    """How one property (set and name) changed on the objects both models have."""

    pset_name: IfcText
    prop_name: IfcText
    added: int = Field(description="Objects that have it only in the new model.")
    removed: int = Field(description="Objects that have it only in the old model.")
    changed: int = Field(description="Objects whose value differs.")
    samples: list[ValueChange] = Field(description="The first changes, by GlobalId.")


class ModelComparison(ToolResult):
    """The differences between an older and a newer model."""

    old_model_id: int
    new_model_id: int
    objects_in_both: int = Field(description="Objects matched by GlobalId.")
    objects_added: int
    objects_removed: int
    added_by_class: list[ClassChange] = Field(description="Most objects first.")
    removed_by_class: list[ClassChange]
    property_changes: list[PropertyChange] = Field(
        description="Per property, on the objects in both models: most changes first."
    )
    truncated: bool = Field(
        description=f"True when more than {MAX_GROUPS} classes or properties changed."
    )
    note: str | None = Field(description="Says when the two models look unrelated.")

    def logged_rows(self) -> int:
        """Return the number of classes and properties listed."""
        return len(self.added_by_class) + len(self.removed_by_class) + len(self.property_changes)


_ONLY_IN = """
SELECT ifc_class, count(*) OVER (PARTITION BY ifc_class) AS objects, global_id, name,
       row_number() OVER (PARTITION BY ifc_class ORDER BY global_id) AS rank
FROM ifc.object AS o
WHERE o.model_id = :here
  AND NOT EXISTS (
      SELECT 1 FROM ifc.object AS other
      WHERE other.model_id = :there AND other.global_id = o.global_id
  )
"""

_PROPERTY_CHANGES = """
WITH kept AS (
    SELECT o.global_id, o.object_id AS old_id, n.object_id AS new_id, n.name
    FROM ifc.object AS o
    JOIN ifc.object AS n ON n.global_id = o.global_id AND n.model_id = :new
    WHERE o.model_id = :old
),
old_values AS (
    SELECT k.global_id, k.name, p.pset_name, p.prop_name, p.value_text, p.value_num,
           p.value_bool, p.unit
    FROM kept AS k JOIN ifc.property AS p ON p.object_id = k.old_id
),
new_values AS (
    SELECT k.global_id, k.name, p.pset_name, p.prop_name, p.value_text, p.value_num,
           p.value_bool, p.unit
    FROM kept AS k JOIN ifc.property AS p ON p.object_id = k.new_id
),
diff AS (
    SELECT coalesce(n.pset_name, o.pset_name) AS pset_name,
           coalesce(n.prop_name, o.prop_name) AS prop_name,
           coalesce(n.global_id, o.global_id) AS global_id,
           coalesce(n.name, o.name) AS object_name,
           CASE WHEN o.global_id IS NULL THEN 'added'
                WHEN n.global_id IS NULL THEN 'removed'
                ELSE 'changed' END AS change,
           o.value_text AS old_text, o.value_num AS old_num, o.value_bool AS old_bool,
           o.unit AS old_unit,
           n.value_text AS new_text, n.value_num AS new_num, n.value_bool AS new_bool,
           n.unit AS new_unit
    FROM old_values AS o
    FULL JOIN new_values AS n
        ON n.global_id = o.global_id AND n.pset_name = o.pset_name
       AND n.prop_name = o.prop_name
    WHERE o.global_id IS NULL OR n.global_id IS NULL
       OR (o.value_text, o.value_num, o.value_bool, o.unit)
          IS DISTINCT FROM (n.value_text, n.value_num, n.value_bool, n.unit)
)
SELECT *,
       count(*) FILTER (WHERE change = 'added') OVER w AS added,
       count(*) FILTER (WHERE change = 'removed') OVER w AS removed,
       count(*) FILTER (WHERE change = 'changed') OVER w AS changed,
       row_number() OVER (w ORDER BY global_id) AS rank
FROM diff
WINDOW w AS (PARTITION BY pset_name, prop_name)
"""


def compare_models(
    connection: Connection,
    new_model_id: int,
    old_model_id: int | None = None,
    *,
    samples: int = DEFAULT_COMPARE_SAMPLES,
) -> ModelComparison:
    """Compare a model with an older one: by default, the one it replaces.

    Raises:
        ToolInputError: A model does not exist, the two are the same, or no older model
            was given and the new one replaces none.
    """
    _require_model(connection, new_model_id)
    if old_model_id is None:
        old_model_id = connection.execute(
            text("SELECT replaces_model_id FROM ifc.model WHERE model_id = :m"),
            {"m": new_model_id},
        ).scalar_one()
        if old_model_id is None:
            raise ToolInputError(
                f"model {new_model_id} replaces no model; give old_model_id, a model_id "
                "from list_models (include_replaced lists the older versions)"
            )
    _require_model(connection, old_model_id)
    if old_model_id == new_model_id:
        raise ToolInputError("old_model_id and new_model_id are the same model")

    added = _class_changes(connection, new_model_id, old_model_id, samples)
    removed = _class_changes(connection, old_model_id, new_model_id, samples)
    properties = _property_changes(connection, old_model_id, new_model_id, samples)
    in_both = connection.execute(
        text(
            "SELECT count(*) FROM ifc.object AS o JOIN ifc.object AS n "
            "ON n.global_id = o.global_id AND n.model_id = :new WHERE o.model_id = :old"
        ),
        {"old": old_model_id, "new": new_model_id},
    ).scalar_one()
    note = None
    if in_both == 0:
        note = (
            "No object of one model has a GlobalId of the other: they are not versions of "
            "one model, so everything shows as added and removed."
        )
    return ModelComparison(
        old_model_id=old_model_id,
        new_model_id=new_model_id,
        objects_in_both=in_both,
        objects_added=sum(change.objects for change in added),
        objects_removed=sum(change.objects for change in removed),
        added_by_class=added[:MAX_GROUPS],
        removed_by_class=removed[:MAX_GROUPS],
        property_changes=properties[:MAX_GROUPS],
        truncated=max(len(added), len(removed), len(properties)) > MAX_GROUPS,
        note=note,
    )


def _class_changes(
    connection: Connection, here: int, there: int, samples: int
) -> list[ClassChange]:
    """The objects of model ``here`` whose GlobalId model ``there`` lacks, per class."""
    changes: dict[str, ClassChange] = {}
    for row in connection.execute(
        text(f"SELECT * FROM ({_ONLY_IN}) AS only_in ORDER BY objects DESC, ifc_class, rank"),  # noqa: S608
        {"here": here, "there": there},
    ).mappings():
        change = changes.setdefault(
            row["ifc_class"],
            ClassChange(ifc_class=row["ifc_class"], objects=row["objects"], samples=[]),
        )
        if row["rank"] <= samples:
            change.samples.append(
                ObjectRef(global_id=row["global_id"], ifc_class=row["ifc_class"], name=row["name"])
            )
    return list(changes.values())


def _value(row: Any, side: Literal["old", "new"]) -> PropertyValue:
    """The value one side of a change holds: its text, boolean or number."""
    for column in (f"{side}_text", f"{side}_bool", f"{side}_num"):
        if row[column] is not None:
            return row[column]  # type: ignore[no-any-return]
    return None


def _property_changes(
    connection: Connection, old: int, new: int, samples: int
) -> list[PropertyChange]:
    """Per property, the values added, removed and changed on the objects in both."""
    changes: dict[tuple[str, str], PropertyChange] = {}
    for row in connection.execute(
        text(
            f"SELECT * FROM ({_PROPERTY_CHANGES}) AS d WHERE rank <= :samples "  # noqa: S608
            "ORDER BY added + removed + changed DESC, pset_name, prop_name, rank"
        ),
        {"old": old, "new": new, "samples": max(samples, 1)},
    ).mappings():
        key = (row["pset_name"], row["prop_name"])
        change = changes.setdefault(
            key,
            PropertyChange(
                pset_name=row["pset_name"],
                prop_name=row["prop_name"],
                added=row["added"],
                removed=row["removed"],
                changed=row["changed"],
                samples=[],
            ),
        )
        if row["rank"] <= samples:
            change.samples.append(
                ValueChange(
                    global_id=row["global_id"],
                    object_name=row["object_name"],
                    old=_value(row, "old"),
                    new=_value(row, "new"),
                    old_unit=row["old_unit"],
                    new_unit=row["new_unit"],
                )
            )
    return list(changes.values())
