"""The four Duplex models' scenes, as ``make ingest-samples`` builds them (ADR 14).

The ``duplex`` fixture (``conftest.py``) ingests the files with ``--viewer``. Deselected
by default. Run with::

    make samples && uv run pytest -m sample

The counts are pinned exactly: they decide what the page draws and the line it shows
("268 elements: 218 drawn, 0 without shape, 50 openings not drawn"). The bytes are pinned
within a tolerance only, because OpenCASCADE may triangulate a curved face differently
on Linux and on Windows.
"""

from collections import Counter
from typing import Any

import pytest
import quirks_report as report
from glb_file import read_glb
from sqlalchemy import Engine, text

from ifc_sql_mcp.ingest.pipeline import IngestResult

pytestmark = pytest.mark.sample

# Elements drawn, without shape and openings, and chunks per kind (Phase 0, ADR 14).
EXPECTED = {
    "arc": (218, 0, 50, {"elements": 4, "spaces": 3}),
    "mep": (926, 0, 0, {"elements": 3, "spaces": 3, "space_copies": 3}),
    "ele": (103, 0, 0, {"elements": 3, "spaces": 1}),
    "plu": (498, 0, 0, {"elements": 3, "spaces": 1}),
}
# The chunks' bytes, as measured on Windows (IfcOpenShell 0.9.0.post1).
BYTES = {"arc": 480_456, "mep": 4_738_960, "ele": 424_632, "plu": 4_650_056}
TOLERANCE = 0.05

Duplex = tuple[Engine, dict[str, IngestResult]]


def _scene(engine: Engine, model_id: int) -> dict[str, Any]:
    with engine.connect() as connection:
        scene = dict(
            connection.execute(
                text("SELECT * FROM ifc_viewer.scene WHERE model_id = :m"), {"m": model_id}
            )
            .mappings()
            .one()
        )
        scene["chunks"] = list(
            connection.execute(
                text(
                    "SELECT kind, element_count, glb FROM ifc_viewer.scene_chunk "
                    "WHERE model_id = :m ORDER BY chunk_no"
                ),
                {"m": model_id},
            ).mappings()
        )
        scene["elements"] = connection.execute(
            text(
                "SELECT count(*) FROM ifc.element JOIN ifc.object USING (object_id) "
                "WHERE model_id = :m"
            ),
            {"m": model_id},
        ).scalar_one()
    return scene


@pytest.mark.parametrize("key", report.FILES)
def test_each_model_draws_what_phase_0_counted(duplex: Duplex, key: str) -> None:
    engine, results = duplex
    scene = _scene(engine, results[key].model_id)

    drawn, without_shape, openings, chunks = EXPECTED[key]
    assert (scene["element_count"], scene["without_shape_count"], scene["opening_count"]) == (
        drawn,
        without_shape,
        openings,
    )
    assert drawn + without_shape + openings == scene["elements"]
    assert Counter(c["kind"] for c in scene["chunks"]) == chunks
    assert scene["byte_count"] == sum(len(c["glb"]) for c in scene["chunks"])
    assert scene["byte_count"] == pytest.approx(BYTES[key], rel=TOLERANCE)
    assert results[key].scene is not None
    assert results[key].scene.status == "built"


@pytest.mark.parametrize("key", report.FILES)
def test_every_chunk_holds_the_objects_it_counts(duplex: Duplex, key: str) -> None:
    engine, results = duplex

    for chunk in _scene(engine, results[key].model_id)["chunks"]:
        nodes = read_glb(chunk["glb"]).gltf["nodes"]
        assert len(nodes) == chunk["element_count"]
        assert all(len(node["name"]) == 22 for node in nodes)


def test_the_architectural_models_three_aggregates_are_drawn_through_their_parts(
    duplex: Duplex,
) -> None:
    """215 elements with a body of their own, and two stairs and a roof drawn by their parts."""
    engine, results = duplex
    nodes = [
        node
        for chunk in _scene(engine, results["arc"].model_id)["chunks"]
        if chunk["kind"] == "elements"
        for node in read_glb(chunk["glb"]).gltf["nodes"]
    ]

    assert sum(1 for node in nodes if "mesh" in node) == 215
    aggregates = Counter(node["extras"]["ifc_class"] for node in nodes if "children" in node)
    assert aggregates == {"IfcStair": 2, "IfcRoof": 1}
    assert all("mesh" not in node for node in nodes if "children" in node)


def test_the_four_disciplines_share_their_origin_to_within_a_metre(duplex: Duplex) -> None:
    """The four files place IfcSite at 0, 0, 0 and line up (ADR 14, P4)."""
    engine, results = duplex

    origins = {
        key: tuple(
            _scene(engine, results[key].model_id)[f"origin_{axis}_m"] for axis in ("x", "y", "z")
        )
        for key in report.FILES
    }

    assert origins == {
        "arc": (4.0, -9.0, 3.0),
        "mep": (4.0, -9.0, 3.0),
        "ele": (4.0, -9.0, 3.0),
        "plu": (3.0, -9.0, 3.0),
    }
