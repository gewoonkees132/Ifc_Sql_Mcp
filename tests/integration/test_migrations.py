"""The migrations: they run both ways, and they agree with schema.py."""

import io
from collections.abc import Callable

import make_revit_quirks
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import ProgrammingError

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine, reader_database_url
from ifc_sql_mcp.db.migrate import (
    AUTOGENERATE_OPTIONS,
    MIGRATIONS_DIR,
    RevisionUnreadableError,
    alembic_config,
    current_revision,
    head_revision,
    migration_engine,
    reader_revision,
)
from ifc_sql_mcp.db.schema import READER_ROLE, SCHEMA, metadata
from ifc_sql_mcp.ingest.pipeline import ingest_file

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
    assert _revisions_oldest_first() == [
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0010",
        "0011",
        "0012",
        "0013",
        "0014",
    ]


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


def _reader_revision(settings: Settings) -> str | None:
    engine = make_engine(reader_database_url(settings.database_url, settings.reader_password))
    try:
        with engine.connect() as connection:
            return reader_revision(connection)
    finally:
        engine.dispose()


def test_the_reader_sees_the_revision_from_0009_on(
    test_settings: Settings, create_database: CreateDatabase
) -> None:
    """``ifc-sql serve`` checks the revision as ifc_reader; before 0009 it may not read it."""
    older = create_database("test_reader_revision")
    command.upgrade(alembic_config(older), "0008")

    assert _reader_revision(test_settings) == head_revision()
    with pytest.raises(
        RevisionUnreadableError, match="permission denied for table alembic_version"
    ):
        _reader_revision(older)


# The migrated test database creates the cluster-wide role, which can then connect here.
@pytest.mark.usefixtures("test_settings")
def test_the_reader_sees_no_revision_in_a_database_never_migrated(
    create_database: CreateDatabase,
) -> None:
    empty = create_database("test_reader_empty")

    assert _reader_revision(empty) is None


def test_the_reader_reports_any_other_error_reading_the_revision_as_it_is(
    test_settings: Settings, create_database: CreateDatabase
) -> None:
    """Only "permission denied" means "before 0009"; a broken version table is not that."""
    broken = create_database("test_reader_broken")
    engine = migration_engine(broken.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE public.alembic_version (other text)"))
            connection.execute(text(f"GRANT SELECT ON public.alembic_version TO {READER_ROLE}"))
    finally:
        engine.dispose()

    with pytest.raises(
        ProgrammingError, match=r"column alembic_version\.version_num does not exist"
    ):
        _reader_revision(broken)


FIXTURE = make_revit_quirks.OUTPUT


def test_0010_moves_a_loaded_openings_name_parts_both_ways(
    create_database: CreateDatabase,
) -> None:
    """A model loaded before 0010 gets the split it would get from a new load, and back."""
    settings = create_database("test_0010")
    config = alembic_config(settings)
    command.upgrade(config, "head")
    engine = make_engine(settings.database_url)
    try:
        ingest_file(engine, FIXTURE, "ARC")
        opening = (
            "SELECT e.is_feature, e.revit_family, e.revit_type, e.host_revit_family, "
            "e.host_revit_type FROM ifc.element AS e JOIN ifc.object AS o USING (object_id) "
            "WHERE o.global_id = :g"
        )
        name_parts = ("M_Single-Flush", "0915 x 2134mm")

        command.downgrade(config, "0009")
        with engine.connect() as connection:
            before = connection.execute(
                text(
                    "SELECT e.revit_family, e.revit_type FROM ifc.element AS e "
                    "JOIN ifc.object AS o USING (object_id) WHERE o.global_id = :g"
                ),
                {"g": make_revit_quirks.DOOR_OPENING},
            ).one()
        command.upgrade(config, "0010")
        with engine.connect() as connection:
            after = connection.execute(text(opening), {"g": make_revit_quirks.DOOR_OPENING}).one()
            walls = connection.execute(
                text(
                    "SELECT count(*) FROM ifc.element AS e JOIN ifc.object AS o "
                    "USING (object_id) WHERE e.is_feature AND o.ifc_class_base <> "
                    "'IfcOpeningElement'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert tuple(before) == name_parts  # 0009: the opening carries the door's name parts
    assert tuple(after) == (True, None, None, *name_parts)
    assert walls == 0
