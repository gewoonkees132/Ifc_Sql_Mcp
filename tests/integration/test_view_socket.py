"""``ifc-sql view`` as a user runs it: a process of its own, listening on a real socket.

``scripts/view_smoke.py`` starts it and fetches a chunk over 127.0.0.1, as the windows job
in CI does; here it runs against the test database of ``conftest.py``.
"""

import subprocess

import pytest
import view_smoke

from ifc_sql_mcp.config import Settings


@pytest.fixture(autouse=True)
def _database(view_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The viewer inherits this process's environment: point it at the test database."""
    monkeypatch.delenv("IFC_SQL_DATA_DIR", raising=False)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", view_settings.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", view_settings.reader_password)


def test_view_binds_serves_a_chunk_and_never_writes_its_token_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert view_smoke.main() == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("ifc-sql view on http://127.0.0.1:")
    assert lines[0].endswith(": 3 models, 2 with a scene; shown first: G-1 (models 1, 2)")
    assert lines[1].startswith("model 1 (ARC): 3 chunks, 9 elements drawn; chunk 0: ")
    assert lines[1].endswith(" bytes of glTF 2")
    assert lines[2:] == [
        "chunk 0 again, with its ETag: 304",
        "a foreign Host: 421",
        "stopped; the token never reached stderr",
    ]


def test_view_exits_1_with_a_message_on_a_port_another_view_holds() -> None:
    with view_smoke.running_view() as first:
        second = subprocess.run(  # noqa: S603 - our own command, with fixed arguments
            [str(view_smoke.SERVER), "view", "--no-open", "--port", str(first.port)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        status, _, body = first.get("/api/models/1/scene/0.glb")

    assert second.returncode == 1
    assert second.stdout == ""
    assert f"cannot listen on 127.0.0.1:{first.port}: " in second.stderr
    # The first keeps serving.
    assert (status, body[:4]) == (200, b"glTF")
    assert first.token not in first.stderr
    assert "msg=viewing" in first.stderr


def test_the_smoke_test_fails_when_view_cannot_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc@127.0.0.1:1/ifc")

    assert view_smoke.main() == 1

    error = capsys.readouterr().err
    assert error.startswith("view smoke test failed: ifc-sql view printed no URL (exit code 1): ")
    assert "cannot reach the database at " in error
