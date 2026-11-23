"""Fixtures for tests that need PostgreSQL (``make up`` locally, a service container in CI).

The tests never read or write the development database itself. Each session creates
throwaway databases on the same server (``create_database`` in ``tests/conftest.py``) and
migrates them from scratch, so ``make up && make test`` works on a fresh clone without a
migrate step, and no test can depend on (or damage) data a developer has loaded.
"""

from collections.abc import Callable, Iterator

import make_revit_quirks
import pytest
from geometry_model import write_measured_model
from sqlalchemy import Connection, Engine, text

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine, make_reader_engine, reader_database_url
from ifc_sql_mcp.db.migrate import upgrade
from ifc_sql_mcp.dq.catalogue import CATALOGUE, load_catalogue
from ifc_sql_mcp.dq.run import check_model
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.pipeline import ingest_file

CreateDatabase = Callable[[str], Settings]

FIXTURE = make_revit_quirks.OUTPUT


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


@pytest.fixture(scope="session")
def ingest_settings(create_database: CreateDatabase) -> Settings:
    """A second migrated database, for tests that commit (the pipeline and the CLI)."""
    settings = create_database("test_ingest")
    upgrade(settings)
    return settings


@pytest.fixture
def ingest_engine(ingest_settings: Settings) -> Iterator[Engine]:
    """Engine on the ingest database, emptied after the test.

    These tests commit, so no rollback cleans up after them. Truncating ``model``,
    ``ingestion_run`` and ``dq_rule`` cascades to every table that references them, and
    restarting the identities makes run and model ids start at 1 in every test.
    """
    engine = make_engine(ingest_settings.database_url)
    yield engine
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE ifc.model, ifc.ingestion_run, ifc.dq_rule RESTART IDENTITY CASCADE")
        )
    engine.dispose()


@pytest.fixture(scope="session")
def mcp_settings(
    create_database: CreateDatabase, tmp_path_factory: pytest.TempPathFactory
) -> Settings:
    """A database for the MCP server tests, which only read: the fixture, loaded twice.

    Model 1 (ARC) is the fixture, checked by the data quality rules. Model 2 (MEP) is the
    fixture one byte longer, a new file version, never checked. The server opens its own
    sessions, which cannot see a test's uncommitted rows, so this is committed once.
    """
    settings = create_database("test_mcp")
    upgrade(settings)
    changed = tmp_path_factory.mktemp("mcp") / "revit_quirks.ifc"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")
    engine = make_engine(settings.database_url)
    try:
        ingest_file(engine, FIXTURE, "ARC")
        ingest_file(engine, changed, "MEP")
        rules = load_catalogue(CATALOGUE)
        with engine.begin() as connection:
            sync_rules(connection, rules)
        check_model(engine, 1, rules)
    finally:
        engine.dispose()
    return settings


@pytest.fixture(scope="session")
def mcp_engine(mcp_settings: Settings) -> Iterator[Engine]:
    """The MCP server's own engine on that database: ifc_reader, read-only, no pool."""
    engine = make_reader_engine(mcp_settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def view_settings(
    create_database: CreateDatabase, tmp_path_factory: pytest.TempPathFactory
) -> Settings:
    """A database for ``ifc-sql view``, which only reads: two projects, one without scenes.

    Models 1 (ARC) and 2 (MEP, the same file one byte longer) are the scene fixture of
    ``geometry_model.py``, loaded with ``--viewer``: project ``G-1``, a scene each. Model 3
    (ELE) is the Revit fixture, loaded without it: project ``0042``, no scene.
    """
    settings = create_database("test_view")
    upgrade(settings)
    folder = tmp_path_factory.mktemp("view")
    scene = write_measured_model(folder / "scene.ifc", scene=True)
    longer = folder / "scene_mep.ifc"
    longer.write_bytes(scene.read_bytes() + b"\n")
    engine = make_engine(settings.database_url)
    try:
        ingest_file(engine, scene, "ARC", viewer=True)
        ingest_file(engine, longer, "MEP", viewer=True)
        ingest_file(engine, FIXTURE, "ELE")
    finally:
        engine.dispose()
    return settings


@pytest.fixture(scope="session")
def view_engine(view_settings: Settings) -> Iterator[Engine]:
    """``ifc-sql view``'s own engine on that database: ifc_reader, read-only, no pool."""
    engine = make_reader_engine(view_settings, application="viewer")
    yield engine
    engine.dispose()
