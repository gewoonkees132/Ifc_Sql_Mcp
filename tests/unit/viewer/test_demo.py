"""The scripted demo's data and its report (``viewer/demo_script.py``, ``viewer/demo.py``)."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import demo as demo_command
import download_samples
import pytest

from ifc_sql_mcp.viewer import demo
from ifc_sql_mcp.viewer.demo_script import DUPLEX, SCRIPTS, Model, Step
from ifc_sql_mcp.viewer.event import Call, Source, SqlText, decode, encode
from ifc_sql_mcp.viewer.feed import Entry
from ifc_sql_mcp.viewer.selection import Group, ModelObjects, Selection, summary

MODELS = {"arc": 11, "mep": 12, "ele": 13, "plu": 14}
SOURCE = Source(pid=5, started_at="2026-11-23T10:00:00+00:00")


def test_the_duplex_files_are_those_the_download_script_pins() -> None:
    pinned = {sample.key: sample for sample in download_samples.SAMPLES.values()}

    assert [(s.key, s.file_name, s.sha256) for s in DUPLEX.samples] == [
        (key, pinned[key].file_name, pinned[key].sha256) for key in ("arc", "mep", "ele", "plu")
    ]
    assert SCRIPTS == {"duplex": DUPLEX}
    assert len(DUPLEX.steps) == 9


def test_a_steps_arguments_get_the_model_ids_and_its_sql() -> None:
    walls, external = DUPLEX.steps[1], DUPLEX.steps[2]

    assert walls.call_arguments(MODELS) == {
        "model_id": 11,
        "ifc_class": "IfcWall",
        "storey_name": "Level 1",
    }
    sql = external.call_arguments(MODELS)["sql"]
    assert "WHERE e.model_id = 11 AND" in sql
    assert "array_agg(e.object_id ORDER BY e.object_id) AS object_ids" in sql


def _entry(
    tool: str = "find_elements",
    *,
    outcome: str = "ok",
    counts: tuple[int, ...] | None = (21,),
    arguments: dict[str, Any] | None = None,
    sql: SqlText | None = None,
) -> Entry:
    selection = None
    if counts is not None:
        selection = Selection(
            groups=[
                Group(
                    label=None,
                    colour="#ff5722",
                    count=n,
                    models=[ModelObjects(model_id=11, global_ids=[])],
                )
                for n in counts
            ],
            count=sum(counts),
            truncated=False,
            openings=0,
            not_drawn=0,
            spaces=0,
            layers=[],
            storey=None,
            storey_elevation_m=None,
            zoom=False,
            note=None,
        )
    return Entry(
        id=1,
        received_at="2026-11-23T10:00:01+00:00",
        src=SOURCE,
        source="serve 5",
        demo=True,
        n=1,
        tool=tool,
        summary=tool,
        arguments=arguments or {},
        clipped=[],
        outcome=outcome,
        seconds=0.01,
        rows=None,
        sql=sql,
        selection=selection,
    )


WALLS = DUPLEX.steps[1]


def test_a_step_that_plays_as_written_has_no_differences() -> None:
    played = demo._compare(2, WALLS, _entry(), {"total": 21}, "x" * 8_189, MODELS)

    assert played.differences == []
    assert played.line(9) == (
        "2/9 The architectural walls on Level 1 [find_elements, ok, 8,189 characters]: 21 walls; "
        "lights 21 objects: ok"
    )


@pytest.mark.parametrize(
    ("entry", "result", "differences"),
    [
        (_entry(), {"total": 20}, ["the figure is 20, not 21"]),
        (_entry(), {}, ["the figure is 'unreadable (KeyError)', not 21"]),
        (
            _entry(counts=(20, 1)),
            {"total": 21},
            ["it lights 21 objects in 2 groups (20, 1), not 21 objects"],
        ),
        (_entry(counts=None), {"total": 21}, ["it lights nothing (feed only), not 21 objects"]),
        (
            _entry(outcome="timeout", counts=()),
            {"error": "the query was cancelled"},
            [
                "the figure is 'unreadable (KeyError)', not 21",
                "the call ended timeout, not ok",
                "it lights nothing, not 21 objects",
            ],
        ),
        (None, {"total": 21}, ["no call reached the feed within 15 seconds"]),
        (None, {"error": "x", "total": 21}, ["no call reached the feed within 15 seconds"]),
    ],
)
def test_every_difference_is_named(
    entry: Entry | None, result: dict[str, Any], differences: list[str]
) -> None:
    played = demo._compare(2, WALLS, entry, result, "", MODELS)

    assert played.differences == differences
    assert played.line(9).endswith("DIFFERS: " + "; ".join(differences))


def test_a_step_without_its_call_reports_the_outcome_of_its_result() -> None:
    assert demo._compare(2, WALLS, None, {"error": "x"}, "", MODELS).outcome == "error"
    assert demo._compare(2, WALLS, None, {"total": 21}, "", MODELS).outcome == "ok"


def test_one_object_is_one_object() -> None:
    assert demo._lit((1,)) == "1 object"
    assert demo._lit(()) == "nothing"
    assert demo._lit((1, 1)) == "2 objects in 2 groups (1, 1)"


def test_the_report_counts_the_steps_that_differ() -> None:
    good = demo._compare(2, WALLS, _entry(), {"total": 21}, "", MODELS)
    bad = demo._compare(2, WALLS, _entry(), {"total": 3}, "", MODELS)

    assert demo.report(DUPLEX, [good, good])[0][-1] == "all 2 steps as the script says"
    assert demo.report(DUPLEX, [good, bad]) == (
        [good.line(9), bad.line(9), "1 of 2 steps differ from the script"],
        False,
    )


def test_a_call_is_matched_by_its_tool_and_arguments() -> None:
    arguments = WALLS.call_arguments(MODELS)
    same = _entry(arguments={**arguments, "limit": 25, "offset": 0})

    assert demo._same_call(same, WALLS, arguments)
    assert not demo._same_call(_entry(arguments={**arguments, "model_id": 12}), WALLS, arguments)
    assert not demo._same_call(_entry("list_models", arguments=arguments), WALLS, arguments)


def test_a_query_is_matched_by_its_text() -> None:
    step = Step("q", "run_sql", {}, "f", lambda r, m: 0, 0, (), sql="SELECT {arc}")
    arguments = step.call_arguments(MODELS)
    sql = SqlText(text="SELECT 1", chars=9, sha256_16="0" * 16)

    assert demo._same_call(_entry("run_sql", sql=sql), step, arguments)
    assert not demo._same_call(
        _entry("run_sql", sql=sql.model_copy(update={"chars": 8})), step, arguments
    )
    assert not demo._same_call(_entry("run_sql"), step, arguments)


def test_the_innermost_error_is_reported() -> None:
    error = ValueError("this one")

    assert demo.innermost(ExceptionGroup("outer", [ExceptionGroup("inner", [error])])) is error
    assert demo.innermost(error) is error


def test_a_model_in_the_arguments_is_only_a_key() -> None:
    assert Model("arc") == Model("arc")


# --------------------------------------------------------------------------- summaries


def _summary(tool: str, **arguments: Any) -> str:
    sql = "SELECT 1" if tool == "run_sql" else None
    call = Call(SOURCE, 1, datetime.now(UTC), tool, arguments, "ok", 0.1, sql=sql)
    return summary(decode(encode(call)))


@pytest.mark.parametrize(
    ("tool", "arguments", "line"),
    [
        ("list_models", {}, "list_models"),
        ("list_storeys", {"model_id": 3}, "list_storeys: model 3"),
        (
            "find_elements",
            {"model_id": 1, "ifc_class": "IfcWall", "storey_name": "Level 1"},
            "find_elements: IfcWall on 'Level 1', model 1",
        ),
        (
            "find_elements",
            {
                "model_id": 1,
                "revit_family": "Basic Wall",
                "revit_type": "Generic",
                "name_contains": "x",
            },
            "find_elements: elements family 'Basic Wall' type 'Generic' named like 'x', model 1",
        ),
        ("get_element", {"object_id": 23}, "get_element: object 23"),
        (
            "get_element",
            {"model_id": 1, "global_id": "0iEHWY1$XA8eQeeULq4jDb"},
            "get_element: 0iEHWY1$XA8eQeeULq4jDb in model 1",
        ),
        (
            "quantity_takeoff",
            {"model_id": 1, "group_by": "storey", "metric": "area_m2"},
            "quantity_takeoff: area_m2 by storey, model 1",
        ),
        ("space_schedule", {"model_id": 2}, "space_schedule: model 2"),
        (
            "schedule",
            {"model_id": 1, "ifc_class": "IfcDoor", "group_by": ["storey", "revit_type"]},
            "schedule: IfcDoor by storey and revit_type, model 1",
        ),
        (
            "schedule",
            {"model_id": 1, "ifc_class": "IfcDoor", "group_by": "storey", "storey_name": "L1"},
            "schedule: IfcDoor by  on 'L1', model 1",
        ),
        ("data_quality_report", {"model_id": 1}, "data_quality_report: model 1"),
        (
            "data_quality_report",
            {"model_id": 1, "rule_id": "missing_fire_rating"},
            "data_quality_report: model 1, rule missing_fire_rating",
        ),
        (
            "compare_models",
            {"new_model_id": 5},
            "compare_models: model 5 against the model it replaces",
        ),
        (
            "compare_models",
            {"new_model_id": 5, "old_model_id": 4},
            "compare_models: model 5 against model 4",
        ),
        ("describe_schema", {}, "describe_schema"),
        ("describe_schema", {"relation": "v_element"}, "describe_schema: v_element"),
        ("run_sql", {"max_rows": 5}, "run_sql: 8 characters of SQL"),
    ],
)
def test_a_call_is_summed_up_from_its_arguments(
    tool: str, arguments: dict[str, Any], line: str
) -> None:
    assert _summary(tool, **arguments) == line


def test_a_query_without_its_text_is_summed_up_as_empty() -> None:
    call = Call(SOURCE, 1, datetime.now(UTC), "run_sql", {"max_rows": 5}, "ok", 0.1)

    assert summary(decode(encode(call))) == "run_sql: 0 characters of SQL"


# --------------------------------------------------------------------------- scripts/demo.py


def test_the_demo_command_runs_each_step_in_order() -> None:
    paths = {key: Path(f"{key}.ifc") for key in ("arc", "mep", "ele", "plu")}

    steps = [command[1:] for command in demo_command.commands(paths, ["--check"])]

    assert steps == [
        ["migrate"],
        ["ingest", "arc.ifc", "--discipline", "ARC", "--viewer"],
        ["ingest", "mep.ifc", "--discipline", "MEP", "--viewer"],
        ["ingest", "ele.ifc", "--discipline", "ELE", "--viewer"],
        ["ingest", "plu.ifc", "--discipline", "PLU", "--viewer"],
        ["dq"],
        ["demo", "--check"],
    ]


def test_the_demo_command_stops_at_the_first_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []

    def run(command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        ran.append(command[1:])
        return subprocess.CompletedProcess(command, 3 if command[1] == "dq" else 0)

    monkeypatch.setattr(
        demo_command, "ensure_sample", lambda sample, dest: (dest / sample.file_name, False)
    )
    monkeypatch.setattr(demo_command.subprocess, "run", run)

    assert demo_command.main([]) == 3
    assert [step[0] for step in ran] == ["migrate", "ingest", "ingest", "ingest", "ingest", "dq"]
    assert "have Duplex_A_20110907.ifc" in capsys.readouterr().out


def test_the_demo_command_reports_a_download_that_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(sample: object, dest: Path) -> tuple[Path, bool]:
        raise download_samples.DownloadError("checksum mismatch")

    monkeypatch.setattr(demo_command, "ensure_sample", fail)

    assert demo_command.main([]) == 1
    assert capsys.readouterr().err == "demo: checksum mismatch\n"


def test_the_duplex_figures_are_read_from_the_results() -> None:
    loaded, _, _, doors = DUPLEX.steps[:4]
    models = [{"model_id": 11, "elements": 268}, {"model_id": 12, "elements": 926}]
    schedule = {
        "elements": 14,
        "group_count": 2,
        "groups": [
            {"fields": {"Pset_DoorCommon.FireRating": {"values": [{"value": "Fire Rating"}]}}},
            {"fields": {"Pset_DoorCommon.FireRating": {"values": [{"value": "EI 30"}]}}},
        ],
    }

    assert loaded.measure({"models": models}, MODELS) == [268, 926, None, None]
    assert doors.measure(schedule, MODELS) == (14, 2, ["EI 30", "Fire Rating"])
