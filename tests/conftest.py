"""Fixtures shared by the unit, integration and sample tests.

* The synthetic fixture file, extracted and transformed once per session.
* Throwaway PostgreSQL databases. Nothing connects until a test asks for one, so unit
  tests still run without a database.
"""

import dataclasses
from collections.abc import Callable, Iterator
from pathlib import Path

import make_revit_quirks
import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import redact
from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.raw import RawModel
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import transform

CreateDatabase = Callable[[str], Settings]

FIXTURE: Path = make_revit_quirks.OUTPUT


@pytest.fixture(scope="session")
def fixture_raw() -> RawModel:
    return read_model(FIXTURE)


@pytest.fixture(scope="session")
def fixture_records(fixture_raw: RawModel) -> ModelRecords:
    return transform(fixture_raw)


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
