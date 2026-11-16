"""The migrations: they run both ways, and they agree with schema.py."""

import io
from collections.abc import Callable

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import (
    AUTOGENERATE_OPTIONS,
    MIGRATIONS_DIR,
    alembic_config,
    current_revision,
    head_revision,
    migration_engine,
)
from ifc_sql_mcp.db.schema import READER_ROLE, SCHEMA, metadata

CreateDatabase = Callable[[str], Settings]


def _revisions_oldest_first() -> list[str]:
    script = ScriptDirectory(str(MIGRATIONS_DIR))
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def _cluster_state(settings: Settings) -> dict[str, object]:
    """What the migrations leave behind in one database, as seen from the catalogue."""
    engine = migration_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        """
                        SELECT
                            (SELECT count(*) FROM pg_namespace WHERE nspname = :schema)
                                AS schemas,
                            (SELECT count(*) FROM pg_default_acl) AS default_acls,
                            (SELECT count(*) FROM pg_db_role_setting AS s
                             JOIN pg_database AS d ON s.setdatabase = d.oid
                             WHERE d.datname = current_database()) AS role_settings,
                            (SELECT count(*) FROM pg_roles WHERE rolname = :role) AS roles
                        """
                    ),
                    {"schema": SCHEMA, "role": READER_ROLE},
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()


def test_migration_history_is_one_linear_chain() -> None:
    script = ScriptDirectory(str(MIGRATIONS_DIR))

    assert script.get_heads() == [head_revision()]
    assert _revisions_oldest_first() == ["0001", "0002", "0003", "0004", "0005", "0006"]


def test_test_database_is_at_head(test_settings: Settings) -> None:
    assert current_revision(test_settings) == head_revision()


def test_upgrade_downgrade_upgrade_round_trip(create_database: CreateDatabase) -> None:
    settings = create_database("test_round_trip")
    config = alembic_config(settings)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    assert current_revision(settings) is None
    # Nothing of ours is left in this database; the cluster-wide role is kept on purpose
    # (other databases on the cluster, such as the development one, may still use it).
    assert _cluster_state(settings) == {
        "schemas": 0,
        "default_acls": 0,
        "role_settings": 0,
        "roles": 1,
    }

    command.upgrade(config, "head")

    assert current_revision(settings) == head_revision()
    assert _cluster_state(settings) == {
        "schemas": 1,
        "default_acls": 1,
        "role_settings": 1,
        "roles": 1,
    }


def test_every_revision_downgrades_and_upgrades_again(create_database: CreateDatabase) -> None:
    """A "stairway" test: each step down is checked on its own, not only base to head."""
    settings = create_database("test_stairway")
    config = alembic_config(settings)

    for revision in _revisions_oldest_first():
        command.upgrade(config, revision)
        command.downgrade(config, "-1")
        command.upgrade(config, revision)
        assert current_revision(settings) == revision


def test_schema_py_matches_migrated_database(test_settings: Settings) -> None:
    """Autogenerate finds nothing to do: schema.py and the migrations describe the same tables.

    This compares tables, columns, types, nullability, server defaults, comments, primary
    and foreign keys, unique constraints and indexes. Alembic does not compare CHECK
    constraints, so the next test covers those by name.
    """
    engine = migration_engine(test_settings.database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts=AUTOGENERATE_OPTIONS)
            differences = compare_metadata(context, metadata)
    finally:
        engine.dispose()

    assert differences == []


def test_database_has_exactly_the_constraints_in_schema_py(engine: Engine) -> None:
    expected = {
        str(constraint.name)
        for table in metadata.tables.values()
        for constraint in table.constraints
    }

    with engine.connect() as connection:
        actual = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = CAST(:schema AS regnamespace)"
                ),
                {"schema": SCHEMA},
            ).scalars()
        )

    assert actual == expected
    assert any(name.startswith("ck_") for name in actual)


def test_alembic_version_table_stays_out_of_the_ifc_schema(engine: Engine) -> None:
    """The dev role is named ``ifc`` like the schema; "$user" in search_path must not matter."""
    tables = inspect(engine).get_table_names(schema=SCHEMA)

    assert "alembic_version" not in tables
    assert "alembic_version" in inspect(engine).get_table_names(schema="public")


def test_plain_alembic_cli_config_reads_settings_from_environment(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setup ``alembic current`` uses: ``[tool.alembic]`` plus IFC_SQL_* variables."""
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", test_settings.database_url)
    output = io.StringIO()
    config = Config(toml_file="pyproject.toml", stdout=output)

    command.current(config)

    assert output.getvalue().strip() == f"{head_revision()} (head)"


def test_offline_sql_mode_is_refused(test_settings: Settings) -> None:
    with pytest.raises(SystemExit, match="offline"):
        command.upgrade(alembic_config(test_settings), "head", sql=True)
