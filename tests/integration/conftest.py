"""Fixtures for tests that need PostgreSQL (``make up`` locally, a service container in CI).

The tests never read or write the development database itself. Each session creates
throwaway databases on the same server (``create_database`` in ``tests/conftest.py``) and
migrates them from scratch, so ``make up && make test`` works on a fresh clone without a
migrate step, and no test can depend on (or damage) data a developer has loaded.
"""

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Connection, Engine

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine, reader_database_url
from ifc_sql_mcp.db.migrate import upgrade

CreateDatabase = Callable[[str], Settings]


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
