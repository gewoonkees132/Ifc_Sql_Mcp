"""The audit's five Revit exports, plus two of the Duplex models, loaded once.

Shared by every ``-m modern`` test. One throwaway database holds, in this order:

==========  =====================================  ======================================
model       file                                   why
==========  =====================================  ======================================
a           Ifc4_Revit_ARC.ifc                     Revit 2021, IFC4 Reference View (house)
b           R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc     Revit 2022, IFC2X3 CV 2.0 (28 storeys)
c           Ifc4_Revit_MEP.ifc                     Revit 2021, IFC4, 163 spaces
d           Ifc4_Revit_ARC_FireRatingAdded.ifc     file A re-exported with fire ratings
e           ARK_NUS_skolebygg.ifc                  Revit 2019, with Revit property sets
duplex      Duplex_A_20110907.ifc                  shares Revit's default number 0001
duplex_mep  Duplex_MEP_20110907.ifc                an MEP Space over every room (R1)
==========  =====================================  ======================================

Every model is checked by the data quality rules once, as ``ifc-sql dq`` does.

A second database, ``measured``, holds files A and B loaded with ``--geometry`` (audit
finding A5) and ``--viewer``, which share one triangulation (ADR 14, P1), and files C, D
and E loaded with ``--viewer``: every audit file's scene. It is separate so that every
other test sees the files as an architect's plain ``ifc-sql ingest`` loads them.
"""

from collections.abc import Callable, Iterator

import pytest
from audit_files import AUDIT_DISCIPLINES, Modern, audit_path
from duplex import sample_path

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine
from ifc_sql_mcp.db.migrate import upgrade
from ifc_sql_mcp.dq.catalogue import load_catalogue
from ifc_sql_mcp.dq.run import check_model
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.pipeline import ingest_file


@pytest.fixture(scope="session")
def modern(create_database: Callable[[str], Settings]) -> Iterator[Modern]:
    settings = create_database("test_modern")
    upgrade(settings)
    engine = make_engine(settings.database_url)
    # D is A with fire ratings added: loaded as A's new version (audit finding A12).
    ingested = {}
    for key, discipline in AUDIT_DISCIPLINES.items():
        replaces = ingested["a"].model_id if key == "d" else None
        ingested[key] = ingest_file(engine, audit_path(key), discipline, replaces=replaces)
    ingested["duplex"] = ingest_file(engine, sample_path("arc"), "ARC")
    ingested["duplex_mep"] = ingest_file(engine, sample_path("mep"), "MEP")
    rules = load_catalogue()
    with engine.begin() as connection:
        sync_rules(connection, rules)
    for result in ingested.values():
        check_model(engine, result.model_id, rules)
    yield Modern(engine=engine, settings=settings, ingested=ingested)
    engine.dispose()


@pytest.fixture(scope="session")
def measured(create_database: Callable[[str], Settings]) -> Iterator[Modern]:
    """Files A and B loaded with --geometry --viewer, C, D and E with --viewer."""
    settings = create_database("test_modern_geometry")
    upgrade(settings)
    engine = make_engine(settings.database_url)
    ingested = {
        key: ingest_file(
            engine, audit_path(key), discipline, geometry=key in ("a", "b"), viewer=True
        )
        for key, discipline in AUDIT_DISCIPLINES.items()
    }
    yield Modern(engine=engine, settings=settings, ingested=ingested)
    engine.dispose()
