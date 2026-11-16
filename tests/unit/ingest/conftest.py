"""The committed synthetic fixture, extracted and transformed once per test session."""

from pathlib import Path

import make_revit_quirks
import pytest

from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.raw import RawModel
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import transform

FIXTURE: Path = make_revit_quirks.OUTPUT


@pytest.fixture(scope="session")
def fixture_raw() -> RawModel:
    return read_model(FIXTURE)


@pytest.fixture(scope="session")
def fixture_records(fixture_raw: RawModel) -> ModelRecords:
    return transform(fixture_raw)
