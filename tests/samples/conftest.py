"""The four Duplex files, ingested once per session into a throwaway database.

Shared by the ingest and data quality sample tests, so each file is parsed and loaded
once however many of them run.
"""

from collections.abc import Callable, Iterator

import pytest
import quirks_report as report
from duplex import DISCIPLINES, sample_path
from sqlalchemy import Engine

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine
from ifc_sql_mcp.db.migrate import upgrade
from ifc_sql_mcp.ingest.pipeline import IngestResult, ingest_file


@pytest.fixture(scope="session")
def duplex(
    create_database: Callable[[str], Settings],
) -> Iterator[tuple[Engine, dict[str, IngestResult]]]:
    settings = create_database("test_samples")
    upgrade(settings)
    engine = make_engine(settings.database_url)
    results = {key: ingest_file(engine, sample_path(key), DISCIPLINES[key]) for key in report.FILES}
    yield engine, results
    engine.dispose()
