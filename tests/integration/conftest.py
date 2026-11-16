"""Shared fixtures for tests that need a running PostgreSQL (``make up`` or the CI service)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine, redact


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine for the configured database; fails fast with a clear hint if it is down."""
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
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
