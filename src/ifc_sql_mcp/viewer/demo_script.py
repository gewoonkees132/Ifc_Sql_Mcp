"""The scripted demo's questions, as data (ADR 14, decision 5).

A script names its files by SHA-256 and asks its questions of their models: ``ifc-sql
demo`` finds the models by those hashes, never by id, since ids depend on the order the
files were loaded in, and fills the ids into the arguments and the SQL when it plays.

Each step says what it asks (a tool and its arguments), what it shows (the figure the
docs cite, measured from the result) and what lights up (the selection's count, by
group). ``ifc-sql demo --check`` compares every one; ``tests/samples`` pins them.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

type Result = Mapping[str, Any]
"""A tool's structured result, or for a call that failed, ``{"error": <its text>}``."""


@dataclass(frozen=True, slots=True)
class Sample:
    """One file of a script.

    Attributes:
        key: How the steps name it, e.g. ``arc``.
        file_name: The file, as the download script saves it.
        sha256: Its SHA-256: how the demo finds its model.
        discipline: The discipline it is loaded as.
    """

    key: str
    file_name: str
    sha256: str
    discipline: str


@dataclass(frozen=True, slots=True)
class Model:
    """In a step's arguments: the id of the model of ``key``, filled in when it plays."""

    key: str


@dataclass(frozen=True, slots=True)
class Step:
    """One question of a script.

    Attributes:
        title: What it asks, in plain words, for the page and ``--check``'s report.
        tool: The tool it calls.
        arguments: Its arguments; a :class:`Model` stands for a model's id.
        sql: For ``run_sql``: the query, with ``{arc}`` for the id of the model ``arc``.
        figure: What the result shows, in words, as the docs cite it.
        measure: Reads that figure from the result: ``(result, model ids)``.
        expected: What ``measure`` must return.
        lit: The selection's count, group by group; empty for a call that lights
            nothing, ``None`` for one that lights nothing by nature (``list_models``).
        outcome: How the call must end.
    """

    title: str
    tool: str
    arguments: Mapping[str, object]
    figure: str
    measure: Callable[[Result, Mapping[str, int]], object]
    expected: object
    lit: tuple[int, ...] | None
    sql: str | None = None
    outcome: str = "ok"

    def call_arguments(self, models: Mapping[str, int]) -> dict[str, Any]:
        """The arguments as the client sends them: model ids filled in, and the SQL."""
        arguments = {
            name: models[value.key] if isinstance(value, Model) else value
            for name, value in self.arguments.items()
        }
        if self.sql is not None:
            arguments["sql"] = self.sql.format(**models)
        return arguments


@dataclass(frozen=True, slots=True)
class Script:
    """A demo: its files and its questions, in order."""

    name: str
    title: str
    samples: tuple[Sample, ...]
    steps: tuple[Step, ...]
    credit: str = field(default="")


DUPLEX_SAMPLES = (
    Sample(
        "arc",
        "Duplex_A_20110907.ifc",
        "b347a2c8aa8fff6db896a4417a9c50c22ac0ccd7c5cfc22b99b8d29336c606ed",
        "ARC",
    ),
    Sample(
        "mep",
        "Duplex_MEP_20110907.ifc",
        "13976a8e223f177a6d7123679e4b02e750cd90c13f7bb20c593be125d9407119",
        "MEP",
    ),
    Sample(
        "ele",
        "Duplex_Electrical_20121207.ifc",
        "d47b014ed5b3428496683ee75e4664013f6df8237ea571b8d13f877cea0b7a94",
        "ELE",
    ),
    Sample(
        "plu",
        "Duplex_Plumbing_20121113.ifc",
        "abfaf5c0979b6b4b05182aec7ada8945374bdb414db76c763724d549b41b212b",
        "PLU",
    ),
)

EXTERNAL_WALLS = """\
SELECT e.revit_type, count(*) AS walls, round(sum(e.area_m2)::numeric, 1) AS area_m2,
       array_agg(e.object_id ORDER BY e.object_id) AS object_ids
FROM v_element AS e JOIN property AS p ON p.object_id = e.object_id
WHERE e.model_id = {arc} AND e.ifc_class_base = 'IfcWall'
  AND p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'IsExternal' AND p.value_bool
GROUP BY e.revit_type ORDER BY area_m2 DESC"""
"""The README's query of the external walls, with their ids per type for the viewer."""

FIRE_RATING = "Pset_DoorCommon.FireRating"


def _elements(result: Result, models: Mapping[str, int]) -> list[int | None]:
    by_id = {model["model_id"]: model["elements"] for model in result["models"]}
    return [by_id.get(models[key]) for key in ("arc", "mep", "ele", "plu")]


def _door_schedule(result: Result, models: Mapping[str, int]) -> tuple[int, int, list[str]]:
    ratings = {
        value["value"]
        for group in result["groups"]
        for value in group["fields"][FIRE_RATING]["values"]
    }
    return result["elements"], result["group_count"], sorted(ratings)


DUPLEX = Script(
    name="duplex",
    title="The Duplex, step by step",
    samples=DUPLEX_SAMPLES,
    credit=(
        'The "Duplex Apartment" models, buildingSMART International, CC BY 4.0; converted '
        "to glTF by IfcOpenShell triangulation, openings cut."
    ),
    steps=(
        Step(
            title="What is loaded?",
            tool="list_models",
            arguments={},
            figure="4 models: 268, 926, 103 and 498 elements",
            measure=_elements,
            expected=[268, 926, 103, 498],
            lit=None,
        ),
        Step(
            title="The architectural walls on Level 1",
            tool="find_elements",
            arguments={"model_id": Model("arc"), "ifc_class": "IfcWall", "storey_name": "Level 1"},
            figure="21 walls",
            measure=lambda result, _: result["total"],
            expected=21,
            lit=(21,),
        ),
        Step(
            title="Which walls are external, by type?",
            tool="run_sql",
            arguments={},
            sql=EXTERNAL_WALLS,
            figure="12, 4, 4 and 3 walls of four types",
            measure=lambda result, _: [row[1] for row in result["rows"]],
            expected=[12, 4, 4, 3],
            lit=(12, 4, 4, 3),
        ),
        Step(
            title="The door schedule",
            tool="schedule",
            arguments={
                "model_id": Model("arc"),
                "ifc_class": "IfcDoor",
                "group_by": ["revit_type"],
                "fields": ["width_m", FIRE_RATING],
            },
            figure="14 doors in 4 types, every fire rating the placeholder 'Fire Rating'",
            measure=_door_schedule,
            expected=(14, 4, ["Fire Rating"]),
            lit=(6, 4, 2, 2),
        ),
        Step(
            title="The rooms",
            tool="space_schedule",
            arguments={"model_id": Model("arc")},
            figure="21 spaces, 422.0 m²",
            measure=lambda result, _: (result["space_count"], round(result["total_area_m2"], 1)),
            expected=(21, 422.0),
            lit=(21,),
        ),
        Step(
            title="Rooms counted twice",
            tool="space_schedule",
            arguments={"model_id": Model("mep")},
            figure="42 spaces, 21 of them an MEP Space over a room",
            measure=lambda result, _: (result["space_count"], result["duplicate_spaces"]),
            expected=(42, 21),
            lit=(21, 21),
        ),
        Step(
            title="What lacks a fire rating?",
            tool="data_quality_report",
            arguments={"model_id": Model("arc"), "rule_id": "missing_fire_rating"},
            figure="78 walls, doors and slabs without a fire rating",
            measure=lambda result, _: [rule["findings"] for rule in result["rules"]],
            expected=[78],
            lit=(78,),
        ),
        Step(
            title="One wall in full",
            tool="get_element",
            arguments={"model_id": Model("arc"), "global_id": "0iEHWY1$XA8eQeeULq4jDb"},
            figure="everything stored about one wall: its properties, materials and findings",
            measure=lambda result, _: (result["ifc_class"], result["name"]),
            expected=("IfcWallStandardCase", "Basic Wall:Interior - Furring (152 mm Stud):190774"),
            lit=(1,),
        ),
        Step(
            title="A write is refused",
            tool="run_sql",
            arguments={},
            sql="DELETE FROM ifc.property",
            figure="refused before it reaches the database",
            measure=lambda result, _: result["error"],
            expected=(
                "query refused: only a query that reads data is allowed (SELECT, WITH … "
                "SELECT, TABLE, UNION, INTERSECT, EXCEPT or VALUES); this is a DELETE "
                "statement"
            ),
            lit=(),
            outcome="refused",
        ),
    ),
)

SCRIPTS: dict[str, Script] = {DUPLEX.name: DUPLEX}
"""The scripts ``ifc-sql demo --script`` plays. Phase 4 of the plan adds ``audit``."""
