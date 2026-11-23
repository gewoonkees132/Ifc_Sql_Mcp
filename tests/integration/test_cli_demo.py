"""``ifc-sql demo``: a script played through a real ``ifc-sql serve``, over stdio.

The Duplex is not in these databases, so a script of three steps on the Revit fixture
(the MCP test database: the fixture as model 1, checked, and one byte longer as model 2)
stands in for it. The demo runs in this process; the server it starts is a process of
its own, as a client starts it, and its calls reach the demo's listener through
PostgreSQL. uvicorn's serving coroutine is replaced, as ``test_cli_view.py`` replaces
its serving loop.
"""

import hashlib
import re
import sys
from collections.abc import Callable

import anyio
import make_revit_quirks
import pytest
import uvicorn
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.viewer.demo_script import Model, Sample, Script, Step
from ifc_sql_mcp.viewer.feed import Feed
from ifc_sql_mcp.viewer.security import Security

FIXTURE = make_revit_quirks.OUTPUT

URL = re.compile(r"http://127\.0\.0\.1:(\d+)/#t=([A-Za-z0-9_-]{43})\n")


def _fixture_script(total: int = 2) -> Script:
    data = FIXTURE.read_bytes()
    return Script(
        name="duplex",
        title="The fixture",
        samples=(
            Sample("arc", FIXTURE.name, hashlib.sha256(data).hexdigest(), "ARC"),
            Sample("mep", FIXTURE.name, hashlib.sha256(data + b"\n").hexdigest(), "MEP"),
        ),
        steps=(
            Step(
                title="The panelboards",
                tool="find_elements",
                arguments={"model_id": Model("arc"), "ifc_class": "IfcFlowTerminal"},
                figure="2 panelboards",
                measure=lambda result, _: result["total"],
                expected=total,
                lit=(2,),
            ),
            Step(
                title="A write is refused",
                tool="run_sql",
                arguments={},
                sql="DELETE FROM ifc.property WHERE object_id = {arc}",
                figure="refused",
                measure=lambda result, _: result["error"][:14],
                expected="query refused:",
                lit=(),
                outcome="refused",
            ),
            Step(
                title="What is loaded?",
                tool="list_models",
                arguments={},
                figure="2 models",
                measure=lambda result, models: (
                    [m["model_id"] for m in result["models"]] == sorted(models.values())
                ),
                expected=True,
                lit=None,
            ),
        ),
    )


@pytest.fixture
def database(mcp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("IFC_SQL_DATA_DIR", raising=False)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", mcp_settings.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", mcp_settings.reader_password)
    return mcp_settings


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Script:
    played = _fixture_script()
    monkeypatch.setitem(cli.SCRIPTS, "duplex", played)
    return played


def _demo(*arguments: str, stdin: str | None = None) -> Result:
    return CliRunner().invoke(cli.app, ["demo", *arguments], input=stdin)


def _feed_of(server: uvicorn.Server) -> Feed:
    """The feed of the app ``ifc-sql demo`` handed uvicorn."""
    app = server.config.app
    assert isinstance(app, Security)
    [route] = [r for r in app.app.routes if getattr(r, "path", "") == "/api/feed"]
    feed: Feed = route.endpoint.__self__.feed  # type: ignore[attr-defined]
    return feed


def _serving(
    monkeypatch: pytest.MonkeyPatch, until: Callable[[Feed], bool]
) -> list[uvicorn.Server]:
    """Replace uvicorn's serving coroutine: it returns, as on Ctrl+C, once ``until`` holds."""
    served: list[uvicorn.Server] = []

    async def serve(self: uvicorn.Server, sockets: list[object] | None = None) -> None:
        served.append(self)
        while not until(_feed_of(self)):
            await anyio.sleep(0.05)

    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    return served


# --------------------------------------------------------------------------- --check


@pytest.mark.usefixtures("database", "script")
def test_check_plays_every_step_through_serve_and_reports_each() -> None:
    result = _demo("--check")

    assert result.exit_code == 0, result.output
    first, second, third, last = result.stdout.splitlines()
    assert re.fullmatch(
        r"1/3 The panelboards \[find_elements, ok, [\d,]+ characters\]: 2 panelboards; "
        r"lights 2 objects: ok",
        first,
    )
    assert second == (
        "2/3 A write is refused [run_sql, refused, 149 characters]: refused; lights nothing: ok"
    )
    assert re.fullmatch(
        r"3/3 What is loaded\? \[list_models, ok, [\d,]+ characters\]: 2 models; "
        r"lights nothing \(feed only\): ok",
        third,
    )
    assert last == "all 3 steps as the script says"


@pytest.mark.usefixtures("database")
def test_check_exits_1_on_a_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cli.SCRIPTS, "duplex", _fixture_script(total=3))

    result = _demo("--check")

    assert result.exit_code == 1
    first, *_, last = result.stdout.splitlines()
    assert first.endswith("lights 2 objects: DIFFERS: the figure is 2, not 3")
    assert last == "1 of 3 steps differ from the script"


@pytest.mark.usefixtures("database")
def test_the_demo_needs_its_files_loaded() -> None:
    result = _demo("--check")

    assert result.exit_code == 1
    assert result.stderr.strip().splitlines()[-1] == (
        "the demo needs Duplex_A_20110907.ifc, Duplex_MEP_20110907.ifc, "
        "Duplex_Electrical_20121207.ifc, Duplex_Plumbing_20121113.ifc loaded with --viewer, "
        "and checked: run `uv run python scripts/demo.py`, which downloads, loads and checks "
        "them"
    )


@pytest.mark.usefixtures("database", "script")
def test_a_server_that_does_not_start_is_reported_with_what_it_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    said = "import sys; print('no database for me', file=sys.stderr); sys.exit(1)"
    monkeypatch.setattr(cli, "SERVE", [sys.executable, "-c", said])

    result = _demo("--check")

    assert result.exit_code == 1
    assert result.stderr.strip().endswith("the MCP server stopped: no database for me")


@pytest.mark.usefixtures("database", "script")
def test_a_server_that_says_nothing_is_reported_with_the_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "SERVE", [sys.executable, "-c", "pass"])

    result = _demo("--check")

    assert result.exit_code == 1
    assert "the MCP server stopped: " in result.stderr


@pytest.mark.usefixtures("database", "script")
def test_a_feed_that_does_not_listen_stops_the_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    unreachable = cli.make_reader_engine(
        Settings.from_env({"IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc@127.0.0.1:1/ifc"})
    )
    connect = cli.listen_connection
    monkeypatch.setattr(cli, "listen_connection", lambda _: connect(unreachable))
    monkeypatch.setattr(cli, "FEED_START_SECONDS", 0.3)

    result = _demo("--check")
    unreachable.dispose()

    assert result.exit_code == 1
    assert "the feed did not start listening on postgresql+psycopg://ifc_reader:***@" in (
        result.stderr
    )


def test_an_unreachable_database_is_reported_without_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IFC_SQL_DATA_DIR", raising=False)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc@127.0.0.1:1/ifc")
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", "s3cret")

    result = _demo("--check")

    assert result.exit_code == 1
    assert "cannot reach the database at postgresql+psycopg://ifc_reader:***@127.0.0.1:1/ifc" in (
        result.stderr
    )
    assert "s3cret" not in result.output


# --------------------------------------------------------------------------- the page


@pytest.mark.usefixtures("database", "script")
def test_the_demo_serves_the_page_while_it_plays(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    served = _serving(monkeypatch, lambda feed: len(feed.entries()) == 3)

    result = _demo("--pause", "0")

    assert result.exit_code == 0, result.output
    assert URL.fullmatch(result.stdout) is not None
    assert opened == [result.stdout.strip()]
    assert "played 3 questions, as the script says; stop with Ctrl+C" in result.stderr
    feed = _feed_of(served[0])
    assert [entry.step.title if entry.step else None for entry in feed.entries()] == [
        "The panelboards",
        "A write is refused",
        "What is loaded?",
    ]
    assert feed.status().demo is True
    token = URL.fullmatch(result.stdout).group(2)  # type: ignore[union-attr]
    assert token not in result.stderr


@pytest.mark.usefixtures("database")
def test_the_demo_says_when_it_differs_and_waits_for_enter_with_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(cli.SCRIPTS, "duplex", _fixture_script(total=3))
    _serving(monkeypatch, lambda feed: len(feed.entries()) == 3)

    result = _demo("--step", "--no-open", stdin="\n\n\n")

    assert result.exit_code == 0, result.output
    assert result.stderr.count("Enter for the next question") == 3
    assert "played 3 questions, with differences: run it with --check" in result.stderr


@pytest.mark.usefixtures("database", "script")
def test_ctrl_c_stops_the_page_and_the_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    _serving(monkeypatch, lambda feed: True)  # Ctrl+C at once: the questions stop

    result = _demo("--no-open", "--pause", "0")

    assert result.exit_code == 0, result.output
    assert "played" not in result.stderr


@pytest.mark.usefixtures("database", "script")
def test_ctrl_c_through_uvicorn_says_the_demo_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(self: uvicorn.Server, sockets: list[object] | None = None) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "serve", interrupted)

    result = _demo("--no-open")

    assert result.exit_code == 0, result.output
    assert result.stderr.strip().endswith("stopped the demo")
