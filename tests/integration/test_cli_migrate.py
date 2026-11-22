import pytest
from typer.testing import CliRunner

from ifc_sql_mcp.cli import app
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import head_revision


def test_migrate_command_brings_the_database_to_head(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", test_settings.database_url)

    result = CliRunner().invoke(app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert f"database is at revision {head_revision()}" in result.output
