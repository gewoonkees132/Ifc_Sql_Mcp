"""``ifc-sql view``: its checks before it serves, the port it binds, and what it prints.

uvicorn's serving loop is replaced here, so the command runs in this process up to the
point where it would serve; the app it would serve is then called through TestClient.
``test_view_socket.py`` runs the real command on a real socket.
"""

import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
import uvicorn
from alembic import command
from cli_output import plain
from starlette.testclient import TestClient
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import reader_database_url, redact
from ifc_sql_mcp.db.migrate import alembic_config, head_revision

CreateDatabase = Callable[[str], Settings]
URL = re.compile(r"http://127\.0\.0\.1:(\d+)/#t=([A-Za-z0-9_-]{43})\n")


@dataclass
class Served:
    """What ``view`` handed uvicorn, and what it opened."""

    configs: list[uvicorn.Config] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Served:
    served = Served()

    def run(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        assert sockets is not None
        served.configs.append(self.config)
        served.ports.extend(sock.getsockname()[1] for sock in sockets)

    monkeypatch.setattr(uvicorn.Server, "run", run)
    monkeypatch.setattr(cli.webbrowser, "open", served.opened.append)
    return served


def _view(settings: Settings, monkeypatch: pytest.MonkeyPatch, *arguments: str) -> Result:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", settings.reader_password)
    return CliRunner().invoke(cli.app, ["view", *arguments])


def _reader_url(settings: Settings) -> str:
    return redact(reader_database_url(settings.database_url, settings.reader_password))


@pytest.fixture
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_view_prints_the_url_once_opens_it_and_serves_on_its_own_socket(
    view_settings: Settings, monkeypatch: pytest.MonkeyPatch, served: Served
) -> None:
    result = _view(view_settings, monkeypatch)

    assert result.exit_code == 0, result.output
    match = URL.fullmatch(result.stdout)
    assert match is not None, result.stdout
    port, token = int(match.group(1)), match.group(2)
    assert served.ports == [port]
    assert port != 0
    assert served.opened == [result.stdout.strip()]
    # The token is the user's: on stdout once, never on stderr or in the log.
    assert token not in result.stderr
    assert (
        f"msg=viewing database={_reader_url(view_settings)} address=http://127.0.0.1:{port}/"
        in result.stderr
    )
    assert f"showing the models at http://127.0.0.1:{port}/; stop with Ctrl+C" in result.stderr
    assert view_settings.reader_password not in result.output

    [config] = served.configs
    assert (config.lifespan, config.ws, config.access_log) == ("off", "none", False)
    assert config.log_config is None
    assert config.timeout_graceful_shutdown == 2
    with TestClient(config.app, base_url=f"http://127.0.0.1:{port}") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/models").status_code == 401
        models = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})
        assert models.json()["shown"]["model_ids"] == [1, 2]
        # The Host must name this port and no other.
        other = client.get("/", headers={"Host": f"127.0.0.1:{port + 1}"})
        assert other.status_code == 421


def test_view_on_a_port_of_its_choosing(
    view_settings: Settings, monkeypatch: pytest.MonkeyPatch, served: Served, free_port: int
) -> None:
    result = _view(view_settings, monkeypatch, "--port", str(free_port), "--no-open")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"http://127.0.0.1:{free_port}/#t=")
    assert served.ports == [free_port]
    assert served.opened == []


def test_view_shows_a_project_or_models_first(
    view_settings: Settings, monkeypatch: pytest.MonkeyPatch, served: Served
) -> None:
    shown = []
    for arguments in (["--project", "0042"], ["--model", "2", "--model", "1"]):
        result = _view(view_settings, monkeypatch, *arguments, "--no-open")
        assert result.exit_code == 0, result.output
        token = result.stdout.strip().rsplit("#t=", 1)[1]
        port = served.ports[-1]
        with TestClient(served.configs[-1].app, base_url=f"http://127.0.0.1:{port}") as client:
            body = client.get("/api/models", headers={"Authorization": f"Bearer {token}"}).json()
        shown.append(body["shown"]["model_ids"])

    assert shown == [[3], [2, 1]]


def test_view_exits_1_on_a_taken_port(
    view_settings: Settings, monkeypatch: pytest.MonkeyPatch, served: Served
) -> None:
    with socket.create_server(("127.0.0.1", 0)) as taken:
        port = taken.getsockname()[1]
        result = _view(view_settings, monkeypatch, "--port", str(port), "--no-open")

    assert result.exit_code == 1
    assert result.stdout == ""
    last = result.stderr.splitlines()[-1]
    assert last.startswith(f"cannot listen on 127.0.0.1:{port}: ")
    assert last.endswith("; pick another --port, or leave it out for a free one")
    assert served.configs == []


def test_view_stops_on_ctrl_c(view_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "run", interrupted)

    result = _view(view_settings, monkeypatch, "--no-open")

    assert result.exit_code == 0
    assert result.stderr.splitlines()[-1] == "stopped the viewer"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--model", "9"], "Invalid value for '--model': no model with id 9"),
        (
            ["--project", "nowhere"],
            "Invalid value for '--project': no current model belongs to project 'nowhere'; "
            "without --project, the page lists the projects",
        ),
        (
            ["--project", "G-1", "--model", "1"],
            "Invalid value for '--project': give --project or --model, not both",
        ),
        (["--port", "70000"], "Invalid value for '--port'"),
        (["--model", "0"], "Invalid value for '--model'"),
    ],
)
def test_view_refuses_what_names_nothing_loaded(
    view_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    served: Served,
    arguments: list[str],
    message: str,
) -> None:
    result = _view(view_settings, monkeypatch, *arguments)

    assert result.exit_code == 2
    assert message in plain(result.stderr)
    assert served.configs == []


@pytest.mark.usefixtures("test_settings")  # creates the cluster-wide ifc_reader role
def test_view_refuses_a_database_that_was_never_migrated(
    create_database: CreateDatabase, monkeypatch: pytest.MonkeyPatch, served: Served
) -> None:
    empty = create_database("test_view_empty")

    result = _view(empty, monkeypatch)

    assert result.exit_code == 1
    assert result.stderr.splitlines()[-1] == (
        f"the database at {_reader_url(empty)} is not migrated, but view needs revision "
        f"{head_revision()}: run `ifc-sql migrate` first"
    )


def test_view_refuses_a_database_before_the_reader_could_read_its_revision(
    create_database: CreateDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = create_database("test_view_0008")
    command.upgrade(alembic_config(older), "0008")

    result = _view(older, monkeypatch)

    assert result.exit_code == 1
    assert "but view needs revision" in result.stderr.splitlines()[-1]


def test_view_reports_an_unreachable_database_without_its_password(
    monkeypatch: pytest.MonkeyPatch, served: Served
) -> None:
    settings = Settings.from_env(
        {
            "IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc:owner@127.0.0.1:1/ifc",
            "IFC_SQL_READER_PASSWORD": "s3cret-reader-password",
        }
    )

    result = _view(settings, monkeypatch)

    assert result.exit_code == 1
    assert plain(result.stderr).startswith(
        f"cannot reach the database at {_reader_url(settings)}: "
    )
    assert "s3cret-reader-password" not in result.output
