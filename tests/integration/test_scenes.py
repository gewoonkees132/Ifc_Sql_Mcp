"""``ingest --viewer`` against a real database: what the scene holds, in place, races, deletes.

The file is the synthetic one of ``tests/support/geometry_model.py`` with ``scene``: a
wall with an opening, a slab, a column, a beam whose body is a point, two stairs of two
flights each (one with a body of its own), a space and a copy of it (ADR 14).
"""

import dataclasses
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import ifcopenshell
import pytest
from geometry_model import (
    BARE_STAIR,
    BEAM,
    COLUMN,
    FLIGHTS,
    SLAB,
    SPACE,
    SPACE_COPY,
    STAIR,
    WALL,
    write_measured_model,
)
from glb_file import read_glb
from locks import wait_until_a_session_waits_on_a_lock
from sqlalchemy import Engine, RowMapping, text
from sqlalchemy.exc import IntegrityError

from ifc_sql_mcp.ingest import pipeline
from ifc_sql_mcp.ingest.extract import ExtractError
from ifc_sql_mcp.ingest.pipeline import IngestResult, SceneOutcome, ingest_file
from ifc_sql_mcp.viewer import scene as viewer_scene

DRAWN = SceneOutcome(
    status="built",
    element_count=9,  # wall, slab, column, two stairs and four flights
    without_shape_count=1,  # the beam
    opening_count=1,
    chunk_count=3,
)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return write_measured_model(tmp_path / "scene.ifc", scene=True)


def _scene(engine: Engine, model_id: int) -> RowMapping | None:
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT * FROM ifc_viewer.scene WHERE model_id = :m"), {"m": model_id}
            )
            .mappings()
            .one_or_none()
        )


def _chunks(engine: Engine, model_id: int) -> list[RowMapping]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT c.*, o.name AS storey FROM ifc_viewer.scene_chunk AS c "
                    "LEFT JOIN ifc.object AS o ON o.object_id = c.storey_id "
                    "WHERE c.model_id = :m ORDER BY c.chunk_no"
                ),
                {"m": model_id},
            ).mappings()
        )


def _names(engine: Engine, model_id: int) -> dict[str, str]:
    """Each object's name by its GlobalId."""
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT global_id, name FROM ifc.object WHERE model_id = :m"),
                {"m": model_id},
            ).all()
        )


def _counted(result: IngestResult) -> SceneOutcome:
    """The scene outcome without its byte count, which depends on the triangulation."""
    assert result.scene is not None
    assert result.scene.byte_count > 0
    return SceneOutcome(
        status=result.scene.status,
        element_count=result.scene.element_count,
        without_shape_count=result.scene.without_shape_count,
        opening_count=result.scene.opening_count,
        chunk_count=result.scene.chunk_count,
        error=result.scene.error,
    )


# --------------------------------------------------------------------------- what is drawn


def test_ingest_with_viewer_draws_every_element_with_a_body_and_no_opening(
    ingest_engine: Engine, path: Path
) -> None:
    result = ingest_file(ingest_engine, path, "ARC", viewer=True)

    assert (result.status, _counted(result)) == ("succeeded", DRAWN)
    chunks = _chunks(ingest_engine, result.model_id)
    assert [(c["chunk_no"], c["kind"], c["storey"], c["element_count"]) for c in chunks] == [
        (0, "elements", "Level 1", 9),
        (1, "spaces", "Level 1", 1),
        (2, "space_copies", "Level 1", 1),
    ]
    names = _names(ingest_engine, result.model_id)
    elements = read_glb(chunks[0]["glb"])
    nodes = {names[name]: node for name, node in elements.nodes_by_name().items()}
    assert sorted(nodes) == sorted(
        [WALL, SLAB, COLUMN, STAIR, BARE_STAIR, *FLIGHTS[STAIR], *FLIGHTS[BARE_STAIR]]
    )
    assert f"{WALL}:1" not in nodes
    assert BEAM not in nodes
    for stair, flights in FLIGHTS.items():
        children = [elements.gltf["nodes"][i]["name"] for i in nodes[stair]["children"]]
        assert sorted(names[child] for child in children) == sorted(flights)
        # The stair with a body of its own is drawn through its flights only.
        assert "mesh" not in nodes[stair]
    assert nodes[WALL]["extras"] == {"ifc_class": "IfcWall"}
    assert [names[n] for n in read_glb(chunks[1]["glb"]).nodes_by_name()] == [SPACE]
    assert [names[n] for n in read_glb(chunks[2]["glb"]).nodes_by_name()] == [SPACE_COPY]


def test_the_scene_row_says_what_was_drawn_with_what_and_from_where(
    ingest_engine: Engine, path: Path
) -> None:
    result = ingest_file(ingest_engine, path, "ARC", viewer=True)

    scene = _scene(ingest_engine, result.model_id)
    chunks = _chunks(ingest_engine, result.model_id)
    with ingest_engine.connect() as connection:
        elements = connection.execute(
            text(
                "SELECT count(*) FROM ifc.element JOIN ifc.object USING (object_id) "
                "WHERE model_id = :m"
            ),
            {"m": result.model_id},
        ).scalar_one()
    assert scene is not None
    assert (scene["format_version"], scene["ifcopenshell_version"]) == (
        viewer_scene.FORMAT_VERSION,
        ifcopenshell.version,
    )
    assert scene["build_seconds"] >= 0
    # The bounding box runs from 0 to 10 m along X (the turned flights) and 0 to 21 m
    # along Y; its centre, rounded, is the origin.
    assert (scene["origin_x_m"], scene["origin_y_m"], scene["origin_z_m"]) == (5.0, 10.0, 2.0)
    assert scene["element_count"] + scene["without_shape_count"] + scene["opening_count"] == (
        elements
    )
    assert scene["byte_count"] == sum(len(c["glb"]) for c in chunks) == result.scene.byte_count
    assert all(c["glb_sha256"] == hashlib.sha256(c["glb"]).hexdigest() for c in chunks)


def test_without_viewer_there_is_no_scene(ingest_engine: Engine, path: Path) -> None:
    result = ingest_file(ingest_engine, path, "ARC")

    assert result.scene is None
    assert _scene(ingest_engine, result.model_id) is None


def test_a_model_with_nothing_drawn_gets_a_scene_without_chunks(
    ingest_engine: Engine, tmp_path: Path
) -> None:
    beam = write_measured_model(tmp_path / "beam.ifc", only_unmeasurable=True)

    result = ingest_file(ingest_engine, beam, "ARC", viewer=True)

    assert result.scene == SceneOutcome(status="built", without_shape_count=1)
    scene = _scene(ingest_engine, result.model_id)
    assert scene is not None
    assert (scene["byte_count"], scene["origin_x_m"]) == (0, 0.0)
    assert _chunks(ingest_engine, result.model_id) == []


# --------------------------------------------------------------------------- in place


def test_a_file_already_loaded_gets_its_scene_in_place_and_then_keeps_it(
    ingest_engine: Engine, path: Path
) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")

    drawn = ingest_file(ingest_engine, path, "ARC", viewer=True)
    built_at = _scene(ingest_engine, plain.model_id)
    again = ingest_file(ingest_engine, path, "ARC", viewer=True)

    assert (drawn.status, drawn.model_id, drawn.warnings) == ("skipped", plain.model_id, ())
    assert _counted(drawn) == DRAWN
    assert again.scene is not None
    assert drawn.scene is not None
    assert again.scene == SceneOutcome(
        status="kept",
        element_count=9,
        without_shape_count=1,
        opening_count=1,
        chunk_count=3,
        byte_count=drawn.scene.byte_count,
    )
    # A current scene is no flag left unapplied: the result says it was kept.
    assert again.warnings == ()
    assert built_at is not None
    assert _scene(ingest_engine, plain.model_id) == built_at
    with ingest_engine.connect() as connection:
        runs = connection.execute(
            text("SELECT status FROM ifc.ingestion_run ORDER BY ingestion_run_id")
        ).scalars()
        assert list(runs) == ["succeeded", "skipped", "skipped"]


def test_building_in_place_applies_no_project_and_says_so(
    ingest_engine: Engine, path: Path
) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")

    drawn = ingest_file(ingest_engine, path, "ARC", viewer=True, project_key="house")

    assert drawn.warnings == (
        f"scene.ifc is already loaded as model {plain.model_id}, so --project and --replaces "
        "were not applied",
    )
    assert drawn.scene is not None
    assert drawn.scene.status == "built"


def test_a_scene_of_an_older_format_is_replaced(
    ingest_engine: Engine, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = ingest_file(ingest_engine, path, "ARC", viewer=True)
    monkeypatch.setattr(viewer_scene, "FORMAT_VERSION", viewer_scene.FORMAT_VERSION + 1)

    again = ingest_file(ingest_engine, path, "ARC", viewer=True)

    assert again.scene is not None
    assert again.scene.status == "replaced"
    scene = _scene(ingest_engine, first.model_id)
    assert scene is not None
    assert scene["format_version"] == viewer_scene.FORMAT_VERSION
    assert len(_chunks(ingest_engine, first.model_id)) == 3


def test_measuring_and_drawing_in_place_share_one_pass(ingest_engine: Engine, path: Path) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")

    both = ingest_file(ingest_engine, path, "ARC", geometry=True, viewer=True)

    assert (both.status, both.model_id) == ("measured", plain.model_id)
    assert _counted(both) == DRAWN
    with ingest_engine.connect() as connection:
        seconds = connection.execute(
            text("SELECT geometry_seconds FROM ifc.ingestion_run WHERE ingestion_run_id = :r"),
            {"r": both.run_id},
        ).scalar_one()
    scene = _scene(ingest_engine, plain.model_id)
    assert scene is not None
    # One pass: the scene's time is the measures' and the writing's.
    assert scene["build_seconds"] >= seconds > 0


def test_measuring_a_model_whose_scene_is_current_keeps_the_scene(
    ingest_engine: Engine, path: Path
) -> None:
    drawn = ingest_file(ingest_engine, path, "ARC", viewer=True)

    both = ingest_file(ingest_engine, path, "ARC", geometry=True, viewer=True)

    assert (both.status, both.model_id) == ("measured", drawn.model_id)
    assert both.scene is not None
    assert drawn.scene is not None
    assert both.scene == dataclasses.replace(drawn.scene, status="kept")


def test_a_model_measured_meanwhile_still_gets_its_scene(
    ingest_engine: Engine, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")
    answers = iter([False, True])  # not measured when the run starts; measured under the lock
    monkeypatch.setattr(pipeline, "_measured", lambda connection, model_id: next(answers))

    result = ingest_file(ingest_engine, path, "ARC", geometry=True, viewer=True)

    assert (result.status, result.concurrent, result.model_id) == ("skipped", True, plain.model_id)
    assert _counted(result) == DRAWN


# --------------------------------------------------------------------------- failures


def test_a_failed_scene_writes_nothing_and_leaves_the_model_loaded(
    ingest_engine: Engine, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "add_scene", refuse)

    result = ingest_file(ingest_engine, path, "ARC", viewer=True)

    assert result.status == "succeeded"
    assert result.scene == SceneOutcome(status="failed", error="RuntimeError: disk full")
    assert _scene(ingest_engine, result.model_id) is None
    assert _names(ingest_engine, result.model_id)


def test_a_file_that_cannot_be_read_again_fails_the_scene_alone(
    ingest_engine: Engine, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")

    def unreadable(*args: object, **kwargs: object) -> Any:
        raise ExtractError("scene.ifc: not a readable IFC file (gone)")

    monkeypatch.setattr(pipeline, "read_model", unreadable)

    result = ingest_file(ingest_engine, path, "ARC", viewer=True)

    assert (result.status, result.model_id) == ("skipped", plain.model_id)
    assert result.scene == SceneOutcome(
        status="failed", error="ExtractError: scene.ifc: not a readable IFC file (gone)"
    )
    assert _scene(ingest_engine, plain.model_id) is None


def test_deleting_the_model_deletes_its_scene(ingest_engine: Engine, path: Path) -> None:
    """The chunks' storey key is checked at commit, so the cascade can remove both."""
    result = ingest_file(ingest_engine, path, "ARC", viewer=True)

    with ingest_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM ifc.model WHERE model_id = :m"), {"m": result.model_id}
        )

    assert _scene(ingest_engine, result.model_id) is None
    assert _chunks(ingest_engine, result.model_id) == []


def test_a_storey_a_chunk_is_on_cannot_be_deleted_alone(ingest_engine: Engine, path: Path) -> None:
    result = ingest_file(ingest_engine, path, "ARC", viewer=True)
    [storey] = {c["storey_id"] for c in _chunks(ingest_engine, result.model_id)}

    with ingest_engine.connect() as connection:
        # Everything in schema ifc lets go of the storey first, so only the chunks hold it.
        for statement in (
            "UPDATE ifc.element SET storey_id = NULL WHERE storey_id = :s",
            "UPDATE ifc.element SET container_id = NULL WHERE container_id = :s",
            "UPDATE ifc.spatial_element SET parent_id = NULL WHERE parent_id = :s",
            "DELETE FROM ifc.spatial_element WHERE object_id = :s",
        ):
            connection.execute(text(statement), {"s": storey})
        # The key is checked at commit.
        with pytest.raises(IntegrityError, match="fk_scene_chunk_storey_id"):
            connection.commit()


# --------------------------------------------------------------------------- two at once


def _race(engine: Engine, path: Path, model_id: int, *, other_commits: bool) -> IngestResult:
    """Build the scene in place while another transaction builds one and holds the model.

    The other transaction plays the other build: it takes the model row's lock (FOR NO KEY
    UPDATE, so the ingest can still record its run) and writes a scene of the current
    format with no chunks. The build under test triangulates, then waits for the lock;
    once it waits, the other transaction commits or rolls back.
    """
    other = engine.connect()
    transaction = other.begin()
    try:
        other.execute(
            text("SELECT 1 FROM ifc.model WHERE model_id = :m FOR NO KEY UPDATE"), {"m": model_id}
        )
        other.execute(
            text(
                "INSERT INTO ifc_viewer.scene (model_id, format_version, build_seconds, "
                "ifcopenshell_version, origin_x_m, origin_y_m, origin_z_m, element_count, "
                "without_shape_count, opening_count, byte_count) "
                "VALUES (:m, :v, 0, 'other', 0, 0, 0, 0, 0, 0, 0)"
            ),
            {"m": model_id, "v": viewer_scene.FORMAT_VERSION},
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            build = pool.submit(ingest_file, engine, path, "ARC", viewer=True)
            try:
                wait_until_a_session_waits_on_a_lock(engine)
                if other_commits:
                    transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()  # also lets the build finish if the test failed
            result = build.result(timeout=60)
    finally:
        other.close()
    return result


def test_of_two_builds_at_once_the_second_keeps_the_first_ones_scene(
    ingest_engine: Engine, path: Path
) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")

    result = _race(ingest_engine, path, plain.model_id, other_commits=True)

    assert result.scene == SceneOutcome(status="kept")
    scene = _scene(ingest_engine, plain.model_id)
    assert scene is not None
    assert scene["ifcopenshell_version"] == "other"
    assert _chunks(ingest_engine, plain.model_id) == []


def test_a_build_that_waited_writes_its_scene_when_the_other_one_fails(
    ingest_engine: Engine, path: Path
) -> None:
    plain = ingest_file(ingest_engine, path, "ARC")

    result = _race(ingest_engine, path, plain.model_id, other_commits=False)

    assert _counted(result) == DRAWN
    scene = _scene(ingest_engine, plain.model_id)
    assert scene is not None
    assert scene["ifcopenshell_version"] == ifcopenshell.version
