"""The ``ifc_reader`` role: it can read the ifc schema and do nothing else.

Two layers are tested separately. The privilege boundary (GRANT SELECT only) must hold
even in a read-write transaction, because a client can override the session defaults;
the read-only default is a second layer on top.
"""

from collections.abc import Callable

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, literal_column, select, table, text
from sqlalchemy.exc import DBAPIError, InternalError

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import alembic_config, current_revision, head_revision
from ifc_sql_mcp.db.schema import READER_ROLE, SCHEMA, VIEWER_SCHEMA

INSUFFICIENT_PRIVILEGE = "42501"
READ_ONLY_SQL_TRANSACTION = "25006"
RAISE_EXCEPTION = "P0001"


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def test_reader_can_select_from_every_table_and_view(engine: Engine, reader_engine: Engine) -> None:
    with engine.connect() as owner:
        relations = (
            owner.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relnamespace = CAST(:schema AS regnamespace) AND relkind IN ('r', 'v') "
                    "ORDER BY relname"
                ),
                {"schema": SCHEMA},
            )
            .scalars()
            .all()
        )

    with reader_engine.connect() as reader:
        for relation in relations:
            reader.execute(select(literal_column("1")).select_from(table(relation, schema=SCHEMA)))

    assert "v_element" in relations
    assert "property" in relations


def test_reader_resolves_unqualified_names_in_the_ifc_schema(reader_engine: Engine) -> None:
    with reader_engine.connect() as reader:
        count = reader.execute(text("SELECT count(*) FROM v_element")).scalar_one()

    assert count == 0


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("default_transaction_read_only", "on"),
        ("statement_timeout", "5s"),
        ("idle_in_transaction_session_timeout", "30s"),
        ("search_path", "ifc"),
    ],
)
def test_reader_sessions_start_with_safe_defaults(
    reader_engine: Engine, setting: str, expected: str
) -> None:
    with reader_engine.connect() as reader:
        assert reader.execute(text(f"SHOW {setting}")).scalar_one() == expected


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ifc.dq_rule VALUES ('r', 'd', 'info', 'validity')",
        "UPDATE ifc.model SET discipline = 'STR'",
        "DELETE FROM ifc.property",
        "TRUNCATE ifc.quantity",
        "CREATE TABLE ifc.scratch (a int)",
        "CREATE VIEW ifc.v_scratch AS SELECT 1 AS a",
        "CREATE TABLE public.scratch (a int)",
        "UPDATE public.alembic_version SET version_num = '0001'",
        "CREATE TEMP TABLE scratch (a int)",
        "SET ROLE ifc",
        "INSERT INTO ifc_viewer.scene_chunk (model_id, chunk_no, kind, element_count, glb) "
        "VALUES (1, 0, 'elements', 1, 'glTF')",
        "UPDATE ifc_viewer.scene SET byte_count = 0",
        "DELETE FROM ifc_viewer.scene_chunk",
        "TRUNCATE ifc_viewer.scene",
        "CREATE TABLE ifc_viewer.scratch (a int)",
    ],
)
def test_reader_is_refused_everything_else_even_in_a_read_write_transaction(
    reader_engine: Engine, statement: str
) -> None:
    with reader_engine.connect() as reader:
        # Override the read-only default, as a hostile client could: what remains is the
        # privilege boundary itself.
        reader.execute(text("SET TRANSACTION READ WRITE"))
        with pytest.raises(DBAPIError) as excinfo:
            reader.execute(text(statement))

    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


def test_reader_transactions_are_read_only_by_default(reader_engine: Engine) -> None:
    with reader_engine.connect() as reader, pytest.raises(InternalError) as excinfo:
        reader.execute(text("DELETE FROM ifc.property"))

    assert _sqlstate(excinfo.value) == READ_ONLY_SQL_TRANSACTION


def test_reader_role_has_no_elevated_attributes(engine: Engine) -> None:
    with engine.connect() as owner:
        role = owner.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
                "rolbypassrls, rolconnlimit FROM pg_roles WHERE rolname = :role"
            ),
            {"role": READER_ROLE},
        ).one()

    assert tuple(role) == (True, False, False, False, False, False, 20)


def test_reader_password_is_stored_as_a_scram_verifier(engine: Engine) -> None:
    """The migration hashes the password client-side; the server never sees the plaintext."""
    with engine.connect() as owner:
        stored = owner.execute(
            text("SELECT rolpassword FROM pg_authid WHERE rolname = :role"), {"role": READER_ROLE}
        ).scalar_one()

    assert stored.startswith("SCRAM-SHA-256$4096:")


def test_tables_created_by_later_migrations_are_readable_automatically(conn: Connection) -> None:
    """ALTER DEFAULT PRIVILEGES covers tables and views the owning role creates later."""
    conn.execute(text("CREATE TABLE ifc.future_table (a int)"))
    conn.execute(text("CREATE VIEW ifc.v_future AS SELECT a FROM ifc.future_table"))

    readable = conn.execute(
        text(
            "SELECT has_table_privilege(:role, 'ifc.future_table', 'SELECT'), "
            "has_table_privilege(:role, 'ifc.v_future', 'SELECT'), "
            "has_table_privilege(:role, 'ifc.future_table', 'INSERT')"
        ),
        {"role": READER_ROLE},
    ).one()

    assert tuple(readable) == (True, True, False)


def test_migration_refuses_a_reader_role_that_is_privileged(
    create_database: Callable[[str], Settings], engine: Engine
) -> None:
    """A pre-existing ``ifc_reader`` with more rights must not be passed off as read-only.

    Role membership is cluster-wide, so the grant is revoked again whatever happens.
    """
    settings = create_database("test_privileged_reader")
    with engine.connect() as owner:
        owner.execute(text("GRANT pg_read_all_settings TO ifc_reader"))
        owner.commit()
        try:
            with pytest.raises(DBAPIError, match="ifc_reader is privileged") as excinfo:
                command.upgrade(alembic_config(settings), "head")
            assert _sqlstate(excinfo.value) == RAISE_EXCEPTION
            # All migrations run in one transaction, so the refusal rolled back 0001-0002 too.
            assert current_revision(settings) is None
        finally:
            owner.execute(text("REVOKE pg_read_all_settings FROM ifc_reader"))
            owner.commit()


def test_reader_can_read_the_scenes_and_create_nothing_beside_them(
    engine: Engine, reader_engine: Engine
) -> None:
    """Migration 0020: ``ifc-sql view`` reads the scenes as ifc_reader (ADR 14)."""
    with reader_engine.connect() as reader:
        for relation in ("scene", "scene_chunk"):
            reader.execute(
                select(literal_column("1")).select_from(table(relation, schema=VIEWER_SCHEMA))
            )
    with engine.connect() as owner:
        privileges = owner.execute(
            text(
                "SELECT has_schema_privilege(:role, :schema, 'USAGE'), "
                "has_schema_privilege(:role, :schema, 'CREATE')"
            ),
            {"role": READER_ROLE, "schema": VIEWER_SCHEMA},
        ).one()

    assert tuple(privileges) == (True, False)


def test_reader_can_read_the_migration_revision_and_no_other_table_outside_ifc(
    engine: Engine, reader_engine: Engine
) -> None:
    """Migration 0009: ``ifc-sql serve`` checks the revision without the owner's password.

    Migration 0020 adds the viewer's two tables, which ``run_sql`` still cannot read.
    """
    with reader_engine.connect() as reader:
        revision = reader.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
    with engine.connect() as owner:
        readable = (
            owner.execute(
                text(
                    "SELECT n.nspname || '.' || c.relname FROM pg_class AS c "
                    "JOIN pg_namespace AS n ON c.relnamespace = n.oid "
                    "WHERE n.nspname NOT IN ('ifc', 'pg_catalog', 'information_schema') "
                    "AND c.relkind IN ('r', 'v', 'm', 'p') "
                    "AND has_table_privilege(:role, c.oid, 'SELECT')"
                ),
                {"role": READER_ROLE},
            )
            .scalars()
            .all()
        )

    assert revision == head_revision()
    assert sorted(readable) == [
        "ifc_viewer.scene",
        "ifc_viewer.scene_chunk",
        "public.alembic_version",
    ]


def test_reader_may_not_create_temporary_tables(engine: Engine) -> None:
    """PostgreSQL grants TEMPORARY to PUBLIC by default; migration 0009 revokes it."""
    with engine.connect() as owner:
        privileges = owner.execute(
            text(
                "SELECT has_database_privilege(:role, current_database(), 'TEMPORARY'), "
                "has_database_privilege(current_user, current_database(), 'TEMPORARY')"
            ),
            {"role": READER_ROLE},
        ).one()

    assert tuple(privileges) == (False, True)
