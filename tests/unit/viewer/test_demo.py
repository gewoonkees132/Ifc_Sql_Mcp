"""The scripted demo's data and its report (``viewer/demo_script.py``, ``viewer/demo.py``)."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import demo as demo_command
import download_audit_samples
import download_samples
import pytest

from ifc_sql_mcp.viewer import demo
from ifc_sql_mcp.viewer.demo_script import AUDIT, DUPLEX, SCRIPTS, Model, Step
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
    assert SCRIPTS == {"duplex": DUPLEX, "audit": AUDIT}
    assert len(DUPLEX.steps) == 9


def test_the_audit_files_are_those_the_audit_download_script_pins() -> None:
    pinned = download_audit_samples.AUDIT_SAMPLES

    # File A is the model D replaces: compare_models finds it by default, never by name.
    assert [(s.key, s.file_name, s.sha256) for s in AUDIT.samples] == [
        (key, pinned[key].file_name, pinned[key].sha256) for key in ("b", "c", "d", "e")
    ]
    assert [step.tool for step in AUDIT.steps] == [
        "space_schedule",
        "space_schedule",
        "schedule",
        "compare_models",
    ]
    assert AUDIT.steps[3].call_arguments({"d": 8}) == {"new_model_id": 8}


def test_the_page_shows_first_the_models_a_script_names() -> None:
    audit = {"b": 2, "c": 3, "d": 4, "e": 5}

    assert DUPLEX.first_models(MODELS) == [11, 12, 13, 14]
    assert AUDIT.first_models(audit) == [3]


def test_the_audit_figures_are_read_from_the_results() -> None:
    rooms, areas, doors, changes = AUDIT.steps
    spaces = {
        "space_count": 163,
        "duplicate_spaces": 75,
        "total_area_m2": 8_090.71,
        "total_area_without_duplicates_m2": 4_721.44,
        "revit_area_spaces": 0,
        "revit_area_total_m2": None,
    }
    comparison = {
        "objects_added": 4,
        "objects_removed": 4,
        "property_changes": [
            {"pset_name": "Pset_WallCommon", "prop_name": "FireRating", "added": 42, "changed": 0}
        ],
    }

    assert rooms.measure(spaces, {}) == (163, 75, 8_090.7, 4_721.4)
    with pytest.raises(TypeError):  # no Areas: --check reports it as unreadable
        areas.measure(spaces, {})
    assert doors.measure({"elements": 159, "group_count": 6}, {}) == (159, 6)
    assert changes.measure(comparison, {}) == (4, 4, [["Pset_WallCommon", "FireRating", 42, 0]])


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
        "2/9 Which walls are on Level 1? [find_elements, ok, 8,189 characters]: 21 walls; "
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
    step = Step("q", "run_sql", {}, "f", "a", "a", lambda r, m: 0, 0, (), sql="SELECT {arc}")
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


def _commands_run(
    monkeypatch: pytest.MonkeyPatch, *, fails: str = "", stdout: str | None = None
) -> list[list[str]]:
    """Replace the downloads and the commands; the command ``fails`` names exits 3.

    An ingest whose stdout is read says it loaded its file as model 11, 12, … in turn.
    """
    ran: list[list[str]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        ran.append(command[1:])
        ingested = sum(1 for step in ran if step[0] == "ingest")
        said = None
        if options.get("stdout") is not None:
            said = f"loaded x.ifc as model {10 + ingested} (ARC)\n" if stdout is None else stdout
        return subprocess.CompletedProcess(command, 3 if command[1] == fails else 0, said)

    monkeypatch.setattr(
        demo_command, "ensure_sample", lambda sample, dest: (dest / sample.file_name, False)
    )
    monkeypatch.setattr(demo_command.subprocess, "run", run)
    return ran


def test_the_demo_command_stops_at_the_first_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _commands_run(monkeypatch, fails="dq")

    assert demo_command.main([]) == 3
    assert [step[0] for step in ran] == ["migrate", "ingest", "ingest", "ingest", "ingest", "dq"]
    assert "have Duplex_A_20110907.ifc" in capsys.readouterr().out


def test_the_audit_demo_loads_d_as_the_new_version_of_a(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _commands_run(monkeypatch)

    assert demo_command.main(["--script", "audit", "--check"]) == 0
    folder = demo_command.ROOT / "data" / "audit"
    assert ran == [
        ["migrate"],
        ["ingest", str(folder / "Ifc4_Revit_ARC.ifc"), "--discipline", "ARC", "--viewer"],
        [
            "ingest",
            str(folder / "R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc"),
            "--discipline",
            "ARC",
            "--viewer",
        ],
        ["ingest", str(folder / "Ifc4_Revit_MEP.ifc"), "--discipline", "MEP", "--viewer"],
        [
            "ingest",
            str(folder / "Ifc4_Revit_ARC_FireRatingAdded.ifc"),
            "--discipline",
            "ARC",
            "--replaces",
            "11",  # the model file A was loaded as
            "--viewer",
        ],
        ["ingest", str(folder / "ARK_NUS_skolebygg.ifc"), "--discipline", "ARC", "--viewer"],
        ["dq"],
        ["demo", "--script", "audit", "--check"],
    ]
    out = capsys.readouterr().out
    assert "have R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc" in out
    assert "loaded x.ifc as model 11 (ARC)" in out


@pytest.mark.parametrize(
    ("options", "fails", "stdout", "ran", "code"),
    [
        (["--script=audit"], "migrate", None, ["migrate"], 3),
        (["--script=audit"], "ingest", None, ["migrate", "ingest"], 3),
        (["--script", "audit"], "demo", None, ["migrate", *["ingest"] * 5, "dq", "demo"], 3),
        (["--script", "audit"], "", "no line\n", ["migrate", "ingest"], 1),
    ],
)
def test_the_audit_demo_stops_at_the_first_failure(
    monkeypatch: pytest.MonkeyPatch,
    options: list[str],
    fails: str,
    stdout: str | None,
    ran: list[str],
    code: int,
) -> None:
    done = _commands_run(monkeypatch, fails=fails, stdout=stdout)

    assert demo_command.main(options) == code
    assert [step[0] for step in done] == ran


def test_the_model_is_read_from_the_ingest_line_loaded_or_already_loaded() -> None:
    loaded = "loaded Ifc4_Revit_ARC.ifc as model 5 (ARC): 500 objects, … (run 5)\n"
    again = "Ifc4_Revit_ARC.ifc is already loaded as model 5 (ARC); nothing written (run 9)\n"

    assert demo_command.model_of(loaded) == demo_command.model_of(again) == 5
    with pytest.raises(demo_command.DemoError, match="ifc-sql ingest named no model: 'what'"):
        demo_command.model_of("what\n")


@pytest.mark.parametrize(
    ("options", "script"),
    [([], "duplex"), (["--check"], "duplex"), (["--script", "audit"], "audit"),
     (["--script=audit", "--step"], "audit"), (["--script"], "duplex")],
)  # fmt: skip
def test_the_script_is_the_one_the_options_name(options: list[str], script: str) -> None:
    assert demo_command.script_of(options) == script


def test_ctrl_c_stops_the_demo_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        demo_command, "ensure_sample", lambda sample, dest: (dest / sample.file_name, False)
    )
    monkeypatch.setattr(demo_command.subprocess, "run", interrupted)

    assert demo_command.main([]) == 0
    assert capsys.readouterr().err == "demo: stopped\n"


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


def test_the_duplex_questions_are_questions_each_with_its_one_line_answer() -> None:
    assert [(step.title, step.answer) for step in DUPLEX.steps] == [
        ("What is loaded?", "4 models, 1,795 elements"),
        ("Which walls are on Level 1?", "21 walls, in the architectural model"),
        ("Which walls are external, by type?", "23 walls are external, of four types"),
        (
            "What does the door schedule say?",
            '14 doors of 4 types; every fire rating is the placeholder "Fire Rating"',
        ),
        ("Which rooms are there, and how big?", "21 rooms, 422.0 m² in all"),
        ("Are any rooms counted twice?", "Yes: 21 of the 42 spaces are MEP spaces over a room"),
        ("What lacks a fire rating?", "78 walls and slabs"),
        (
            "What is stored about one wall?",
            "Everything about one wall: its properties, materials and findings",
        ),
        ("Can it change the model?", "No: a delete is refused before it reaches the database"),
    ]


def test_each_duplex_answer_agrees_with_what_the_script_checks() -> None:
    loaded, walls, external, doors, rooms, twice, fire, wall, write = DUPLEX.steps

    counts = loaded.expected
    assert isinstance(counts, list)
    assert loaded.answer == f"{len(counts)} models, {sum(counts):,} elements"
    assert walls.answer == f"{walls.expected} walls, in the architectural model"
    assert walls.call_arguments(MODELS)["model_id"] == MODELS["arc"]
    by_type = external.expected
    assert isinstance(by_type, list)
    assert len(by_type) == 4
    assert external.answer == f"{sum(by_type)} walls are external, of four types"
    elements, types, ratings = doors.expected
    assert doors.answer == (
        f'{elements} doors of {types} types; every fire rating is the placeholder "{ratings[0]}"'
    )
    assert len(ratings) == 1
    spaces, area = rooms.expected
    assert rooms.answer == f"{spaces} rooms, {area:.1f} m² in all"
    total, copies = twice.expected
    assert twice.answer == f"Yes: {copies} of the {total} spaces are MEP spaces over a room"
    # The 14 doors hold the placeholder (above), which the rule does not count as missing.
    assert fire.expected == [78]
    assert fire.answer == "78 walls and slabs"
    assert wall.expected[0] == "IfcWallStandardCase"
    assert wall.answer.startswith("Everything about one wall")
    assert write.outcome == "refused"
    assert str(write.expected).endswith("this is a DELETE statement")
    assert write.answer.startswith("No: a delete is refused")


def test_the_card_shows_the_headline_big_and_the_rest_of_the_answer_below() -> None:
    assert [(step.headline, step.detail) for step in DUPLEX.steps] == [
        ("4 models", "1,795 elements"),
        ("21 walls", "in the architectural model"),
        ("23 walls", "are external, of four types"),
        ("14 doors", 'of 4 types; every fire rating is the placeholder "Fire Rating"'),
        ("21 rooms", "422.0 m² in all"),
        ("Yes", "21 of the 42 spaces are MEP spaces over a room"),
        ("78 walls and slabs", ""),
        ("Everything about one wall", "its properties, materials and findings"),
        ("No", "a delete is refused before it reaches the database"),
    ]
    for step in AUDIT.steps:
        assert step.answer.startswith(step.headline), step.title


def test_a_headline_that_is_not_the_start_of_the_answer_is_refused() -> None:
    with pytest.raises(ValueError, match="the headline must be the start of the answer"):
        Step("q", "list_models", {}, "f", "21 walls", "22 walls", lambda r, m: 0, 0, None)
    with pytest.raises(ValueError, match="the headline must be the start of the answer"):
        Step("q", "list_models", {}, "f", "21 walls", "", lambda r, m: 0, 0, None)


def test_the_page_gets_every_question_of_the_script_numbered() -> None:
    questions = demo.questions_of(DUPLEX)

    assert [(q.index, q.steps, q.title) for q in questions[:2]] == [
        (1, 9, "What is loaded?"),
        (2, 9, "Which walls are on Level 1?"),
    ]
    assert questions[2].headline == "23 walls"
    assert questions[2].detail == "are external, of four types"
    assert questions[6].figure == "78 walls and slabs without a fire rating"
