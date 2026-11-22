"""``ifc-sql ingest`` end to end: exit codes, what goes to stdout and what to stderr."""

from collections.abc import Callable
from pathlib import Path

import make_revit_quirks
import pytest
from cli_output import plain
from geometry_model import write_measured_model
from sqlalchemy import Engine, text
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import head_revision, upgrade
from ifc_sql_mcp.ingest import pipeline
from ifc_sql_mcp.ingest.pipeline import IngestResult, SceneOutcome

FIXTURE = make_revit_quirks.OUTPUT


def _ingest(path: Path, discipline: str = "ARC") -> Result:
    return CliRunner().invoke(cli.app, ["ingest", str(path), "--discipline", discipline])


@pytest.fixture
def database(
    ingest_settings: Settings, ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Engine:
    """Point the CLI at the ingest database (emptied after the test)."""
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", ingest_settings.database_url)
    return ingest_engine


def test_ingest_loads_then_skips_and_exits_0_both_times(database: Engine) -> None:
    first = _ingest(FIXTURE)
    second = _ingest(FIXTURE)

    assert (first.exit_code, second.exit_code) == (0, 0), first.output + second.output
    assert first.stdout == (
        "loaded revit_quirks.ifc as model 1 (ARC): 20 objects, 36 property values, "
        "8 issues (run 1)\n"
    )
    assert second.stdout == (
        "revit_quirks.ifc is already loaded as model 1 (ARC); nothing written (run 2, skipped)\n"
    )
    # Logs go to stderr, one line per issue code; stdout stays the one-line summary.
    assert 'msg="transform issues" run_id=1 code=negative_quantity count=1' in first.stderr


def test_ingest_of_a_corrupt_file_exits_1_with_the_error_and_no_traceback(
    database: Engine, tmp_path: Path
) -> None:
    corrupt = tmp_path / "corrupt.ifc"
    corrupt.write_text("this is not an IFC file\n")

    result = _ingest(corrupt)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "ingest of corrupt.ifc failed (run 1): ExtractError: corrupt.ifc: not a readable" in (
        result.stderr
    )
    assert "Traceback" not in result.output
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("revision", "found"), [(None, "not migrated"), ("0004", "at revision 0004")]
)
def test_ingest_into_a_database_that_is_not_at_the_latest_revision_exits_1(
    create_database: Callable[[str], Settings],
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    found: str,
) -> None:
    settings = create_database("test_outdated")
    if revision is not None:
        upgrade(settings, revision)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", settings.database_url)

    result = _ingest(FIXTURE)

    assert result.exit_code == 1
    assert (
        f"is {found}, but ingest needs revision {head_revision()}: run `ifc-sql migrate` first"
        in plain(result.stderr)
    )


def test_ingest_says_when_a_concurrent_ingest_loaded_the_file(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    concurrent = IngestResult(
        run_id=7, status="skipped", model_id=3, discipline="ARC", concurrent=True
    )
    monkeypatch.setattr(cli, "ingest_file", lambda *args, **options: concurrent)

    result = _ingest(FIXTURE)

    assert result.exit_code == 0
    assert result.stdout == (
        "revit_quirks.ifc was loaded as model 3 (ARC) by another ingest running at the same "
        "time; nothing written (run 7, skipped)\n"
    )


def test_ingest_reports_a_file_it_cannot_read(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typer checks the path first; this covers a file that becomes unreadable after that."""

    def unreadable(*args: object, **options: object) -> IngestResult:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "ingest_file", unreadable)

    result = _ingest(FIXTURE)

    assert result.exit_code == 1
    assert f"cannot read {FIXTURE}: Permission denied" in plain(result.stderr)


# --------------------------------------------------------------------------- projects, versions


def _variant(tmp_path: Path, name: str, number: str, long_name: str) -> Path:
    """The fixture with another project number and name: other bytes, so a new model."""
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "'0042',$,$,'Fixture House'", f"'{number}',$,$,'{long_name}'"
    )
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _models(database: Engine) -> list[tuple[object, ...]]:
    with database.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT m.model_id, m.project_key, m.project_group, m.replaces_model_id, "
                    "c.model_id IS NOT NULL FROM ifc.model AS m "
                    "LEFT JOIN ifc.v_model_current AS c USING (model_id) ORDER BY 1"
                )
            )
        ]


def test_a_project_given_at_ingest_and_a_new_version_that_replaces_the_old(
    database: Engine, tmp_path: Path
) -> None:
    first = CliRunner().invoke(
        cli.app, ["ingest", str(FIXTURE), "--discipline", "ARC", "--project", " tower "]
    )
    newer = _variant(tmp_path, "revit_quirks_week_2.ifc", "0042", "Fixture House 2")
    second = CliRunner().invoke(
        cli.app, ["ingest", str(newer), "--discipline", "ARC", "--replaces", "1"]
    )

    assert (first.exit_code, second.exit_code) == (0, 0), first.output + second.output
    assert "warning:" not in first.stderr + second.stderr
    # The new version keeps the project, and only it is current.
    assert _models(database) == [(1, "tower", "tower", None, False), (2, "tower", "tower", 1, True)]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--replaces", "99"], "there is no model 99 to replace"),
        (["--replaces", "1"], "model 1 is already replaced by model 2; replace model 2"),
        (["--project", "  "], "the project key must not be blank"),
    ],
)
def test_a_replacement_or_project_that_cannot_be_is_a_usage_error(
    database: Engine, tmp_path: Path, arguments: list[str], message: str
) -> None:
    _ingest(FIXTURE)
    week_2 = _variant(tmp_path, "week_2.ifc", "0042", "Fixture House 2")
    CliRunner().invoke(cli.app, ["ingest", str(week_2), "--discipline", "ARC", "--replaces", "1"])
    with database.connect() as connection:
        runs = connection.execute(text("SELECT count(*) FROM ifc.ingestion_run")).scalar_one()
    week_3 = _variant(tmp_path, "week_3.ifc", "0042", "Fixture House 3")

    result = CliRunner().invoke(cli.app, ["ingest", str(week_3), "--discipline", "ARC", *arguments])

    assert result.exit_code == 2
    assert message in plain(result.stderr)
    with database.connect() as connection:  # nothing was written, not even a run
        assert (
            connection.execute(text("SELECT count(*) FROM ifc.ingestion_run")).scalar_one() == runs
        )


@pytest.mark.parametrize(
    ("number", "long_name", "warning"),
    [
        (
            "0001",
            "Fixture House",
            "warning: the project number '0001' is Revit's default, so model 1 is grouped by "
            "its project name 'Fixture House'; give it a project with --project, or fill in "
            "the Project Number in Revit",
        ),
        (
            "0001",
            "Project Name",
            "warning: the project number '0001' and name 'Project Name' are Revit's defaults, "
            "so model 1 joins no project; give it one with --project, and fill in Project "
            "Information in Revit",
        ),
    ],
)
def test_a_revit_default_project_gets_a_warning(
    database: Engine, tmp_path: Path, number: str, long_name: str, warning: str
) -> None:
    result = _ingest(_variant(tmp_path, "default.ifc", number, long_name))

    assert result.exit_code == 0
    assert warning in result.stderr
    assert result.stdout.startswith("loaded default.ifc as model 1 (ARC)")


def test_a_project_given_stops_the_warning(database: Engine, tmp_path: Path) -> None:
    path = _variant(tmp_path, "default.ifc", "0001", "Project Name")

    result = CliRunner().invoke(
        cli.app, ["ingest", str(path), "--discipline", "ARC", "--project", "tower"]
    )

    assert result.exit_code == 0
    assert "warning:" not in result.stderr


def test_options_on_a_file_already_loaded_are_not_applied_and_it_says_so(
    database: Engine,
) -> None:
    _ingest(FIXTURE)

    result = CliRunner().invoke(
        cli.app, ["ingest", str(FIXTURE), "--discipline", "ARC", "--project", "tower"]
    )

    assert result.exit_code == 0
    assert (
        "warning: revit_quirks.ifc is already loaded as model 1, so --project, --replaces and "
        "--geometry were not applied: that model is unchanged" in result.stderr
    )
    assert _models(database) == [(1, None, "0042", None, True)]


def test_geometry_is_measured_on_request_and_logged(database: Engine, tmp_path: Path) -> None:
    path = write_measured_model(tmp_path / "measured.ifc")

    result = CliRunner().invoke(cli.app, ["ingest", str(path), "--discipline", "ARC", "--geometry"])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("loaded measured.ifc as model 1 (ARC)")
    assert 'msg="measured geometry" run_id=1 shapes=4' in result.stderr
    assert "code=geometry_not_measured count=1" in result.stderr


def test_geometry_on_a_file_already_loaded_measures_it_in_place(
    database: Engine, tmp_path: Path
) -> None:
    path = write_measured_model(tmp_path / "measured.ifc")
    runner = CliRunner()
    runner.invoke(cli.app, ["ingest", str(path), "--discipline", "ARC"])

    result = runner.invoke(cli.app, ["ingest", str(path), "--discipline", "ARC", "--geometry"])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "measured the shapes of measured.ifc, already loaded as model 1 (ARC): 7 quantities "
        "and 1 issues added (run 2); check it again with ifc-sql dq 1\n"
    )


# --------------------------------------------------------------------------- --viewer

DRAWN = "9 elements drawn, 1 without shape, 1 openings not drawn, 3 chunks, 0.0 MB"


def _viewer(path: Path, *options: str) -> Result:
    return CliRunner().invoke(
        cli.app, ["ingest", str(path), "--discipline", "ARC", "--viewer", *options]
    )


def test_viewer_builds_the_scene_and_says_what_it_draws(database: Engine, tmp_path: Path) -> None:
    path = write_measured_model(tmp_path / "scene.ifc", scene=True)

    first = _viewer(path)
    again = _viewer(path)

    assert (first.exit_code, again.exit_code) == (0, 0), first.output + again.output
    assert first.stdout.startswith("loaded scene.ifc as model 1 (ARC): ")
    assert first.stdout.endswith(f" issues (run 1); scene built: {DRAWN}\n")
    assert 'msg="triangulated the scene" run_id=1 meshes=10' in first.stderr
    assert 'msg="scene written" model_id=1 status=built chunks=3' in first.stderr
    assert again.stdout == (
        "scene.ifc is already loaded as model 1 (ARC); nothing written (run 2, skipped); "
        f"scene kept: {DRAWN}\n"
    )
    assert "warning" not in again.stderr


def test_viewer_on_a_file_already_loaded_builds_its_scene_in_place(
    database: Engine, tmp_path: Path
) -> None:
    path = write_measured_model(tmp_path / "scene.ifc", scene=True)
    _ingest(path)

    drawn = _viewer(path)
    both = _viewer(write_measured_model(tmp_path / "other.ifc", scene=True), "--geometry")

    assert drawn.exit_code == 0, drawn.output
    assert drawn.stdout == (
        f"scene.ifc is already loaded as model 1 (ARC); scene built: {DRAWN} (run 2, skipped)\n"
    )
    # other.ifc is new: loaded, measured and drawn in one pass.
    assert both.exit_code == 0, both.output
    assert both.stdout.endswith(f"; scene built: {DRAWN}\n")
    assert 'msg="measured geometry" run_id=3 shapes=9' in both.stderr


def test_viewer_and_geometry_in_place_report_both(database: Engine, tmp_path: Path) -> None:
    path = write_measured_model(tmp_path / "scene.ifc", scene=True)
    _ingest(path)

    result = _viewer(path, "--geometry")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("measured the shapes of scene.ifc, already loaded as model 1")
    assert result.stdout.endswith(f"check it again with ifc-sql dq 1; scene built: {DRAWN}\n")


def test_a_scene_that_fails_exits_1_and_leaves_the_model_loaded(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "add_scene", refuse)
    path = write_measured_model(tmp_path / "scene.ifc", scene=True)

    result = _viewer(path)

    assert result.exit_code == 1
    assert result.stdout.endswith(" issues (run 1); its scene was not built\n")
    assert (
        "the scene of model 1 was not built: RuntimeError: disk full; the model is loaded, so "
        "run the same command again once that is fixed" in plain(result.stderr)
    )
    with database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ifc.model")).scalar_one() == 1


def test_a_concurrent_ingest_with_viewer_still_reports_the_scene(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    concurrent = IngestResult(
        run_id=7,
        status="skipped",
        model_id=3,
        discipline="ARC",
        concurrent=True,
        scene=SceneOutcome(status="kept", element_count=1, chunk_count=1, byte_count=2_000_000),
    )
    monkeypatch.setattr(cli, "ingest_file", lambda *args, **options: concurrent)

    result = _viewer(FIXTURE)

    assert result.exit_code == 0
    assert result.stdout == (
        "revit_quirks.ifc was loaded as model 3 (ARC) by another ingest running at the same "
        "time; nothing written (run 7, skipped); scene kept: 1 elements drawn, 0 without "
        "shape, 0 openings not drawn, 1 chunks, 2.0 MB\n"
    )
