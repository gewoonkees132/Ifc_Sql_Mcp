"""The scenes of the audit's five Revit exports, as ``ingest --viewer`` builds them (ADR 14).

Deselected by default. Run with::

    uv run python scripts/download_audit_samples.py && uv run pytest -m modern

The ``scenes`` fixture (``conftest.py``) loads every audit file with ``--viewer``, A and
B with ``--geometry`` too. Phase 0 of the viewer plan counted what each file draws
with its own script (``docs/plans/viewer-phase0/phase0_triangulate.py``), which never
imports this package; the counts below are its figures, pinned exactly. The bytes are
pinned within a tolerance, because OpenCASCADE may triangulate a curved face differently
on Linux and on Windows.
"""

from collections import Counter
from typing import Any

import pytest
from audit_files import Modern
from glb_file import read_glb

pytestmark = pytest.mark.modern

# Elements with a body of their own, aggregates drawn through their parts, elements
# without shape, openings, and chunks per kind.
EXPECTED = {
    "a": (442, 12, 0, 35, {"elements": 6}),
    "b": (11_123, 174, 1, 3_314, {"elements": 28}),
    "c": (6_787, 37, 0, 131, {"elements": 5, "spaces": 3, "space_copies": 3}),
    "d": (442, 12, 0, 35, {"elements": 6}),
    "e": (4_459, 71, 0, 789, {"elements": 6, "spaces": 4, "areas": 4}),
}
# The chunks' bytes, as measured on Windows (IfcOpenShell 0.9.0.post1).
BYTES = {
    "a": 4_545_068,
    "b": 25_154_068,
    "c": 15_771_704,
    "d": 4_545_068,
    "e": 20_514_224,
}
TOLERANCE = 0.05


def _scene(scenes: Modern, key: str) -> dict[str, Any]:
    model_id = scenes.model(key)
    [scene] = scenes.rows(
        "SELECT element_count, without_shape_count, opening_count, byte_count, "
        "origin_x_m, origin_y_m, origin_z_m FROM ifc_viewer.scene WHERE model_id = :m",
        m=model_id,
    )
    chunks = scenes.rows(
        "SELECT kind, element_count, glb FROM ifc_viewer.scene_chunk WHERE model_id = :m "
        "ORDER BY chunk_no",
        m=model_id,
    )
    nodes = [
        node
        for kind, _, glb in chunks
        if kind == "elements"
        for node in read_glb(glb).gltf["nodes"]
    ]
    return {
        "counts": scene[:3],
        "bytes": scene[3],
        "origin": scene[4:],
        "chunks": Counter(kind for kind, _, _ in chunks),
        "largest": max(count for _, count, _ in chunks),
        "own_body": sum(1 for node in nodes if "mesh" in node),
        "through_parts": sum(1 for node in nodes if "children" in node),
        "elements": scenes.scalar(
            "SELECT count(*) FROM ifc.element JOIN ifc.object USING (object_id) "
            "WHERE model_id = :m",
            m=model_id,
        ),
    }


@pytest.mark.parametrize("key", list(EXPECTED))
def test_each_file_draws_what_phase_0_counted(scenes: Modern, key: str) -> None:
    scene = _scene(scenes, key)

    own_body, through_parts, without_shape, openings, chunks = EXPECTED[key]
    assert (scene["own_body"], scene["through_parts"]) == (own_body, through_parts)
    assert scene["counts"] == (own_body + through_parts, without_shape, openings)
    assert sum(scene["counts"]) == scene["elements"]
    assert scene["chunks"] == chunks
    assert scene["bytes"] == pytest.approx(BYTES[key], rel=TOLERANCE)
    assert scene["largest"] <= 2_000


def test_a_and_d_share_their_origin_and_b_keeps_its_survey_coordinates(scenes: Modern) -> None:
    """A and D line up exactly (ADR 14, P4); B sits 5,040 km from 0, which float32 alone
    could not hold to the millimetre."""
    origins = {key: _scene(scenes, key)["origin"] for key in ("a", "b", "d")}

    assert origins == {
        "a": (-10.0, -17.0, 5.0),
        "b": (1_507_997.0, 5_039_599.0, 37.0),
        "d": (-10.0, -17.0, 5.0),
    }


def test_the_scenes_triangulation_is_also_the_measures_of_a_and_b(scenes: Modern) -> None:
    """One pass for --geometry --viewer: the measures' time is in the run, and the scene's
    time includes it (ADR 14, P1)."""
    for key in ("a", "b"):
        seconds, build = scenes.rows(
            "SELECT r.geometry_seconds, s.build_seconds FROM ifc.ingestion_run AS r "
            "JOIN ifc_viewer.scene AS s USING (model_id) WHERE r.model_id = :m",
            m=scenes.model(key),
        )[0]
        assert build >= seconds > 0
