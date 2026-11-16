"""Fixtures for tests that need PostgreSQL (``make up`` locally, a service container in CI).

The tests never read or write the development database itself. Each session creates
throwaway databases on the same server and migrates them from scratch, so
``make up && make test`` works on a fresh clone without a migrate step, and no test can
depend on (or damage) data a developer has loaded.
"""

import dataclasses
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine, reader_database_url, redact
from ifc_sql_mcp.db.migrate import upgrade

CreateDatabase = Callable[[str], Settings]


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings from the environment, pointing at the development database."""
    return Settings.from_env()


@pytest.fixture(scope="session")
def server_engine(settings: Settings) -> Iterator[Engine]:
    """Autocommit engine on the configured database, used only to create and drop others.

    CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT. Fails fast with a
    hint when the server is down instead of erroring in every test.
    """
    engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        engine.dispose()
        pytest.fail(
            f"PostgreSQL is not reachable at {redact(settings.database_url)}. "
            f"Start it with `make up`. ({exc.orig})",
            pytrace=False,
        )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def create_database(server_engine: Engine, settings: Settings) -> Iterator[CreateDatabase]:
    """Return a function that creates an empty database and returns Settings for it.

    The database is named after the configured one plus a suffix (``ifc_test``), dropped
    first if a crashed earlier session left it behind, and dropped again at the end.
    """
    base_url = make_url(settings.database_url)
    created: set[str] = set()

    def create(suffix: str) -> Settings:
        name = f"{base_url.database}_{suffix}"
        quoted = server_engine.dialect.identifier_preparer.quote(name)
        with server_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {quoted}"))
        created.add(quoted)
        url = base_url.set(database=name).render_as_string(hide_password=False)
        return dataclasses.replace(settings, database_url=url)

    yield create

    with server_engine.connect() as connection:
        for quoted in created:
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))


@pytest.fixture(scope="session")
def test_settings(create_database: CreateDatabase) -> Settings:
    """Settings for this session's test database, migrated to the latest revision."""
    settings = create_database("test")
    upgrade(settings)
    return settings


@pytest.fixture(scope="session")
def engine(test_settings: Settings) -> Iterator[Engine]:
    """Engine on the migrated test database, as the owning role."""
    engine = make_engine(test_settings.database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after the test.

    Tests can insert whatever they need without cleaning up or seeing each other's rows.
    A test that expects an error wraps the statement in ``conn.begin_nested()`` so that
    only the savepoint is lost and the connection stays usable.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture(scope="session")
def reader_engine(test_settings: Settings) -> Iterator[Engine]:
    """Engine on the test database as the read-only ``ifc_reader`` role."""
    url = reader_database_url(test_settings.database_url, test_settings.reader_password)
    engine = make_engine(url)
    yield engine
    engine.dispose()
