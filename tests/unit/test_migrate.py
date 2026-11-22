import re
import tomllib
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import (
    MIGRATIONS_DIR,
    SETTINGS_ATTRIBUTE,
    SQL_DIR,
    alembic_config,
    head_revision,
    include_name,
)

PROJECT_ROOT = Path(__file__).parents[2]
VIEW_FILE = re.compile(r"^(v_[a-z_]+)\.v(\d+)\.sql$")


def test_alembic_config_points_at_the_packaged_scripts_and_carries_settings() -> None:
    settings = Settings.from_env({})

    config = alembic_config(settings)

    assert Path(config.get_main_option("script_location") or "") == MIGRATIONS_DIR
    assert config.attributes[SETTINGS_ATTRIBUTE] is settings
    assert ScriptDirectory.from_config(config).get_current_head() == head_revision()


def test_pyproject_alembic_section_points_at_the_same_scripts() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    location = pyproject["tool"]["alembic"]["script_location"]

    assert PROJECT_ROOT / location.replace("%(here)s/", "") == MIGRATIONS_DIR


@pytest.mark.parametrize(
    ("name", "type_", "included"),
    [
        ("ifc", "schema", True),
        ("public", "schema", False),
        (None, "schema", False),
        ("model", "table", True),
    ],
)
def test_include_name_limits_autogenerate_to_the_ifc_schema(
    name: str | None, type_: str, included: bool
) -> None:
    assert include_name(name, type_, {}) is included


def test_view_files_are_versioned_and_each_is_used_by_a_migration() -> None:
    """Released view files are frozen, so each must be named by version and referenced."""
    view_files = sorted(path.name for path in (SQL_DIR / "views").iterdir())
    migrations = "".join(
        path.read_text(encoding="utf-8") for path in (MIGRATIONS_DIR / "versions").glob("*.py")
    )

    assert view_files, "no view files found"
    for name in view_files:
        assert VIEW_FILE.match(name), f"{name} is not named <view>.v<N>.sql"
        assert f"views/{name}" in migrations, f"{name} is not used by any migration"


def test_each_view_file_creates_the_view_it_is_named_after() -> None:
    for path in (SQL_DIR / "views").iterdir():
        match = VIEW_FILE.match(path.name)
        assert match is not None
        # A view others read from (v_quantity) is replaced in place, keeping its readers.
        statement = re.compile(rf"CREATE (OR REPLACE )?VIEW ifc\.{match.group(1)} AS")
        assert statement.search(path.read_text(encoding="utf-8")), path.name
