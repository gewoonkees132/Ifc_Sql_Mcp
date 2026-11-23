"""The four Duplex files, ingested and checked once per session in a throwaway database.

Shared by the ingest, data quality, MCP and scene sample tests, so each file is parsed,
loaded and checked once however many of them run. They are loaded with ``--viewer``, as
``make ingest-samples`` and the viewer's demo load them (ADR 14); a scene writes nothing
in schema ``ifc``, so every other figure is the same as without it.
"""

from collections.abc import Callable, Iterator

import pytest
import quirks_report as report
from duplex import DISCIPLINES, sample_path
from sqlalchemy import Engine

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine
from ifc_sql_mcp.db.migrate import upgrade
from ifc_sql_mcp.dq.catalogue import load_catalogue
from ifc_sql_mcp.dq.run import DqResult, check_model
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.pipeline import IngestResult, ingest_file


@pytest.fixture(scope="session")
def duplex(
    create_database: Callable[[str], Settings],
) -> Iterator[tuple[Engine, dict[str, IngestResult]]]:
    settings = create_database("test_samples")
    upgrade(settings)
    engine = make_engine(settings.database_url)
    results = {
        key: ingest_file(engine, sample_path(key), DISCIPLINES[key], viewer=True)
        for key in report.FILES
    }
    yield engine, results
    engine.dispose()


@pytest.fixture(scope="session")
def checked(duplex: tuple[Engine, dict[str, IngestResult]]) -> dict[str, DqResult]:
    """One data quality run per file, as ``ifc-sql dq`` makes it."""
    engine, ingested = duplex
    rules = load_catalogue()
    with engine.begin() as connection:
        sync_rules(connection, rules)
    return {key: check_model(engine, ingested[key].model_id, rules) for key in report.FILES}
