"""``ifc-sql demo --export``: the demo played through a real ``serve``, then its static copy.

The database is ``view_settings`` (``conftest.py``): models 1 (ARC) and 2 (MEP) of project
``G-1``, the scene fixture with a scene each, and model 3 (ELE), the Revit fixture of
project ``0042``, without one. A script of three steps on models 1 and 2 stands in for the
Duplex; the Duplex's own copy is pinned in ``tests/samples/test_duplex_demo.py``.
"""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from cli_output import plain
from geometry_model import WALL
from glb_file import read_glb
from sqlalchemy import Engine, text
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.server import tools
from ifc_sql_mcp.viewer import export
from ifc_sql_mcp.viewer.demo import Played
from ifc_sql_mcp.viewer.demo_script import Credit, Model, Sample, Script, Step
from ifc_sql_mcp.viewer.feed import demo_entry
from ifc_sql_mcp.viewer.http import STATIC, page_files, page_text

CREDIT = Credit(
    text="The scene fixture, MIT; converted to glTF by IfcOpenShell triangulation.",
    work="The scene fixture",
    author="the tests",
    licence="MIT",
    source_url="https://example.org/scene-fixture",
    licence_url="https://example.org/mit",
)

DATA_FILES = re.compile(
    r"data/(models|events|elements|details)\.json|data/scenes/\d+\.json|data/scenes/\d+/\d+\.glb"
)
"""The names of the data files: fixed names and ids, never text from an IFC file."""


def _global_id(engine: Engine, model_id: int, name: str) -> str:
    with engine.connect() as connection:
        found: str = connection.execute(
            text("SELECT global_id FROM ifc.object WHERE model_id = :m AND name = :n"),
            {"m": model_id, "n": name},
        ).scalar_one()
    return found


def _script(engine: Engine, *, walls: int = 1, with_ele: bool = False) -> Script:
    with engine.connect() as connection:
        hashes = dict(connection.execute(text("SELECT model_id, file_sha256 FROM ifc.model")).all())
    samples = [
        Sample("arc", "scene.ifc", hashes[1], "ARC"),
        Sample("mep", "scene.ifc", hashes[2], "MEP"),
    ]
    if with_ele:
        samples.append(Sample("ele", "revit_quirks.ifc", hashes[3], "ELE"))
    return Script(
        name="duplex",
        title="The scene fixture",
        samples=tuple(samples),
        credit=CREDIT,
        steps=(
            Step(
                title="The walls",
                tool="find_elements",
                arguments={"model_id": Model("arc"), "ifc_class": "IfcWall"},
                figure=f"{walls} wall",
                answer=f"{walls} wall, in the fixture",
                headline=f"{walls} wall",
                measure=lambda result, _: result["total"],
                expected=walls,
                lit=(1,),
            ),
            Step(
                title="One wall in full",
                tool="get_element",
                arguments={"model_id": Model("arc"), "global_id": _global_id(engine, 1, WALL)},
                figure="the wall",
                answer="Everything about the wall",
                headline="Everything",
                measure=lambda result, _: result["name"],
                expected=WALL,
                lit=(1,),
            ),
            Step(
                title="What is loaded?",
                tool="list_models",
                arguments={},
                figure="3 models",
                answer="3 models",
                headline="3 models",
                measure=lambda result, _: len(result["models"]),
                expected=3,
                lit=None,
            ),
        ),
    )


def _demo(*arguments: str) -> Result:
    return CliRunner().invoke(cli.app, ["demo", *arguments])


@pytest.fixture
def scenes(view_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("IFC_SQL_DATA_DIR", raising=False)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", view_settings.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", view_settings.reader_password)
    return view_settings


@dataclass(frozen=True)
class Exported:
    """One run of ``demo --export``: what it printed, wrote, and how the steps played."""

    result: Result
    site: Path
    script: Script
    played: list[Played]

    def json(self, path: str) -> Any:
        return json.loads((self.site / path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def exported(
    view_settings: Settings, view_engine: Engine, tmp_path_factory: pytest.TempPathFactory
) -> Exported:
    """The scene fixture's demo, exported once for the tests below."""
    site = tmp_path_factory.mktemp("export") / "site"
    script = _script(view_engine)
    played: list[Played] = []
    write = export.export

    def recorded(*arguments: Any, **options: Any) -> export.Exported:
        played.extend(arguments[2])
        return write(*arguments, **options)

    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("IFC_SQL_DATA_DIR", raising=False)
        patch.setenv("IFC_SQL_DATABASE_URL", view_settings.database_url)
        patch.setenv("IFC_SQL_READER_PASSWORD", view_settings.reader_password)
        patch.setitem(cli.SCRIPTS, "duplex", script)
        patch.setattr(cli.demo_export, "export", recorded)
        result = _demo("--export", str(site), "--pause", "3")
    return Exported(result, site, script, played)


# --------------------------------------------------------------------------- what it writes


def test_export_checks_every_step_as_check_does_then_says_what_it_wrote(
    exported: Exported,
) -> None:
    assert exported.result.exit_code == 0, exported.result.output
    *steps, verdict, wrote = exported.result.stdout.splitlines()
    assert [line.split(" [")[0] for line in steps] == [
        "1/3 The walls",
        "2/3 One wall in full",
        "3/3 What is loaded?",
    ]
    assert verdict == "all 3 steps as the script says"
    files = sum(1 for path in exported.site.rglob("*") if path.is_file())
    assert re.fullmatch(
        rf"exported the demo to {re.escape(str(exported.site))}: {files} files, [\d.]+ MB; to "
        rf"look at it, serve the folder: uv run python -m http.server -d "
        rf"{re.escape(str(exported.site))}",
        wrote,
    )


def test_the_copy_lists_the_scripts_models_alone(exported: Exported) -> None:
    catalogue = exported.json(export.MODELS)

    # Model 3 is loaded too, in another project, and is not the script's: it is left out.
    assert [model["model_id"] for model in catalogue["models"]] == [1, 2]
    assert catalogue["truncated"] is False
    assert catalogue["projects"] == [{"label": "G-1", "project_group": "G-1", "model_ids": [1, 2]}]
    assert catalogue["shown"]["model_ids"] == [1, 2]


def test_every_chunk_the_manifests_name_is_written_as_the_database_holds_it(
    exported: Exported, view_engine: Engine
) -> None:
    with view_engine.connect() as connection:
        stored = {
            (row.model_id, row.chunk_no): bytes(row.glb)
            for row in connection.execute(
                text(
                    "SELECT model_id, chunk_no, glb FROM ifc_viewer.scene_chunk WHERE model_id < 3"
                )
            )
        }

    written = {}
    for model_id in (1, 2):
        manifest = exported.json(export.SCENE.format(model_id=model_id))
        assert manifest["model_id"] == model_id
        for chunk in manifest["chunks"]:
            path = exported.site / export.CHUNK.format(
                model_id=model_id, chunk_no=chunk["chunk_no"]
            )
            body = path.read_bytes()
            assert hashlib.sha256(body).hexdigest() == chunk["sha256"]
            assert len(body) == chunk["byte_count"]
            written[model_id, chunk["chunk_no"]] = body

    assert written == stored


def test_every_object_a_chunk_draws_has_a_record_for_a_click(exported: Exported) -> None:
    elements = exported.json(export.ELEMENTS)

    assert elements["note"] == export.BRIEF_NOTE
    for model_id, discipline in ((1, "ARC"), (2, "MEP")):
        model = elements["models"][str(model_id)]
        assert model["discipline"] == discipline
        drawn = set()
        for path in (exported.site / f"data/scenes/{model_id}").glob("*.glb"):
            drawn |= set(read_glb(path.read_bytes()).nodes_by_name())
        assert drawn
        assert drawn <= set(model["objects"])
    wall = elements["models"]["1"]["objects"][exported.script.steps[1].arguments["global_id"]]
    assert wall == {"ifc_class": "IfcWall", "name": WALL, "storey_name": "Level 1"}


def test_the_details_are_get_element_of_the_objects_the_calls_looked_up(
    exported: Exported, view_engine: Engine
) -> None:
    global_id = exported.script.steps[1].arguments["global_id"]
    assert isinstance(global_id, str)
    with view_engine.connect() as connection:
        detail = tools.get_element(connection, object_id=None, model_id=1, global_id=global_id)

    assert exported.json(export.DETAILS) == {
        "models": {"1": {global_id: json.loads(detail.model_dump_json())}}
    }


def test_events_json_is_what_the_check_resolved(exported: Exported) -> None:
    recording = exported.json(export.EVENTS)

    assert recording["entries"] == [
        json.loads(demo_entry(item.entry).model_dump_json())
        for item in exported.played
        if item.entry is not None
    ]
    assert len(recording["entries"]) == 3
    lit = [
        None if entry["selection"] is None else [g["count"] for g in entry["selection"]["groups"]]
        for entry in recording["entries"]
    ]
    assert lit == [[1], [1], None]
    assert [entry["step"]["title"] for entry in recording["entries"]] == [
        "The walls",
        "One wall in full",
        "What is loaded?",
    ]
    assert recording["title"] == "The scene fixture"
    assert "pause_seconds" not in recording
    assert recording["credit"] == {
        "text": CREDIT.text,
        "work": CREDIT.work,
        "author": CREDIT.author,
        "licence": CREDIT.licence,
        "source_url": CREDIT.source_url,
        "licence_url": CREDIT.licence_url,
    }
    assert [q["title"] for q in recording["status"]["questions"]] == [
        "The walls",
        "One wall in full",
        "What is loaded?",
    ]
    assert recording["status"]["state"] == "recorded"
    assert recording["status"]["database"] is None


# --------------------------------------------------------------------------- no IFC text


def test_no_ifc_text_is_written_outside_json_values(exported: Exported) -> None:
    files = {
        path.relative_to(exported.site).as_posix(): path.read_bytes()
        for path in exported.site.rglob("*")
        if path.is_file()
    }

    # The page is view's, marked static; its files are view's own, byte for byte.
    page = files.pop("index.html").decode("utf-8")
    assert page == export.static_page(page_text(), "The scene fixture")
    own = {f"static/{path}": file.body for path, file in page_files(STATIC).items()}
    assert {path: body for path, body in files.items() if path.startswith("static/")} == own
    # Everything else is data, named by ids alone: JSON, or the chunks checked above.
    data = sorted(path for path in files if not path.startswith("static/"))
    assert data
    assert [path for path in data if not DATA_FILES.fullmatch(path)] == []
    for path in data:
        if path.endswith(".json"):
            json.loads(files[path])


def test_every_file_the_page_links_is_written(exported: Exported) -> None:
    page = (exported.site / "index.html").read_text(encoding="utf-8")
    imports = json.loads(re.search(r'type="importmap">(.*?)</script>', page, re.DOTALL)[1])  # type: ignore[index]

    linked = re.findall(r'(?:href|src)="([^"]+)"', page)
    # The page's own files, by relative paths; every other link leaves the page, by https.
    own = [path for path in linked if not path.startswith("https://")]
    assert own == [
        "static/images/favicon.svg",
        "static/viewer.css",
        "static/main.js",
        "static/images/duplex.webp",
    ]
    for path in own:
        assert (exported.site / path).is_file(), path
    for target in imports["imports"].values():
        assert (exported.site / target).exists(), target
    # The fonts the stylesheet loads, by paths relative to it.
    style = (exported.site / "static/viewer.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("([^"]+)"\)', style)
    assert fonts == [
        "vendor/nunito/nunito-latin-ext-wght-normal.woff2",
        "vendor/nunito/nunito-latin-wght-normal.woff2",
    ]
    for font in fonts:
        assert (exported.site / "static" / font).is_file(), font


# --------------------------------------------------------------------------- refusals


@pytest.mark.usefixtures("scenes")
def test_a_folder_that_holds_anything_is_refused_before_anything_plays(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("an older export", encoding="utf-8")

    result = _demo("--export", str(tmp_path))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "is not empty: --export writes only into a new or empty folder" in plain(result.stderr)
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "an older export"


@pytest.mark.usefixtures("scenes")
def test_the_audit_script_is_never_exported(tmp_path: Path) -> None:
    result = _demo("--script", "audit", "--export", str(tmp_path / "site"))

    assert result.exit_code == 2
    assert "the audit script's files stay on this computer" in plain(result.stderr)
    assert not (tmp_path / "site").exists()


@pytest.mark.usefixtures("scenes")
def test_a_model_without_a_scene_stops_the_export_and_writes_nothing(
    view_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(cli.SCRIPTS, "duplex", _script(view_engine, with_ele=True))

    result = _demo("--export", str(tmp_path / "site"))

    assert result.exit_code == 1, result.output
    assert result.stdout.splitlines()[-1] == "all 3 steps as the script says"
    assert result.stderr.strip().endswith(
        "revit_quirks.ifc has no scene: load it with --viewer (scripts/demo.py does)"
    )
    assert not (tmp_path / "site").exists()


@pytest.mark.usefixtures("scenes")
def test_a_step_that_differs_stops_the_export_and_writes_nothing(
    view_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(cli.SCRIPTS, "duplex", _script(view_engine, walls=2))

    result = _demo("--export", str(tmp_path / "site"))

    assert result.exit_code == 1
    assert result.stdout.splitlines()[-1] == "1 of 3 steps differ from the script"
    assert not (tmp_path / "site").exists()


@pytest.mark.usefixtures("scenes")
def test_a_script_without_a_credit_is_refused(
    view_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(cli.SCRIPTS, "duplex", replace(_script(view_engine), credit=None))

    result = _demo("--export", str(tmp_path / "site"))

    assert result.exit_code == 2
    assert "the duplex script's files stay on this computer" in plain(result.stderr)
