"""``make smoke`` (``scripts/mcp_smoke.py``): a client session against a running server."""

import mcp_smoke
import pytest

from ifc_sql_mcp.config import Settings


def test_the_smoke_session_calls_every_kind_of_thing_and_exits_0(
    mcp_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", mcp_settings.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", mcp_settings.reader_password)

    assert mcp_smoke.main() == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "connected to ifc-sql-mcp 0.1.0 over stdio (protocol 2026-07-28): 8 tools, "
        "1 resource, 1 resource template, 1 prompt"
    )
    assert lines[1:4] == [
        "list_models: 2 models",
        "  model 1 ARC revit_quirks.ifc: elements 13, spaces 2; dq run 1: 0 error, 11 warning, "
        "5 info",
        "  model 2 MEP revit_quirks.ifc: elements 13, spaces 2; never checked",
    ]
    assert lines[5:7] == ["  ['ARC', 13]", "  ['MEP', 13]"]
    assert lines[-1].startswith("ifc://schema: ")


def test_the_smoke_session_fails_when_the_server_cannot_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", "postgresql+psycopg://ifc@127.0.0.1:1/ifc")

    assert mcp_smoke.main() == 1

    assert capsys.readouterr().err.endswith("smoke test failed: Connection closed\n")
