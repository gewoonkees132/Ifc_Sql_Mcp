"""Measuring shapes for ``ingest --geometry`` (audit finding A5), on real triangulations.

The model (``tests/support/geometry_model.py``) has a 5 x 3 m wall with a 1 x 2 m opening,
a 4 x 4 m slab, a 3 x 2 m space, a column and a beam that cannot be triangulated.
"""

import math
from pathlib import Path
from types import SimpleNamespace

import ifcopenshell
import ifcopenshell.util.shape
import numpy as np
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
    UNPLANNED_SPACE,
    WALL,
    write_measured_model,
)

from ifc_sql_mcp.ingest import geometry
from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.quantities import geometry_quantities
from ifc_sql_mcp.ingest.raw import DEFAULT_RGBA, RawModel, RawShape


def _read(tmp_path: Path) -> RawModel:
    return read_model(write_measured_model(tmp_path / "measured.ifc"), geometry=True)


def _by_name(raw: RawModel) -> dict[str | None, RawShape]:
    names = {e.global_id: e.name for e in raw.elements} | {
        s.global_id: s.name for s in raw.spatial_elements
    }
    return {names[shape.global_id]: shape for shape in raw.shapes}


def test_each_shape_is_measured_in_metres(tmp_path: Path) -> None:
    shapes = _by_name(_read(tmp_path))

    assert set(shapes) == {WALL, SLAB, SPACE, COLUMN}
    wall = shapes[WALL]
    # The face is 5 x 3 m less the 1 x 2 m opening, which IfcOpenShell cuts out.
    assert (wall.side_area_m2, wall.footprint_area_m2, wall.volume_m3) == pytest.approx(
        (13.0, 1.0, 2.6)
    )
    assert (shapes[SLAB].footprint_area_m2, shapes[SLAB].volume_m3) == pytest.approx((16.0, 4.0))
    assert (shapes[SPACE].footprint_area_m2, shapes[SPACE].volume_m3) == pytest.approx((6.0, 15.0))
    assert shapes[COLUMN].volume_m3 == pytest.approx(0.48)


def test_a_body_that_cannot_be_triangulated_is_reported(tmp_path: Path) -> None:
    raw = _read(tmp_path)

    [issue] = raw.issues
    assert issue.code == IssueCode.GEOMETRY_NOT_MEASURED
    assert issue.message == (
        f"IfcBeam {BEAM!r} has a body that could not be triangulated; no geometry quantity"
    )
    assert raw.geometry_seconds is not None
    assert raw.geometry_seconds >= 0


def test_without_geometry_nothing_is_measured(tmp_path: Path) -> None:
    raw = read_model(write_measured_model(tmp_path / "measured.ifc"))

    assert (raw.shapes, raw.geometry_seconds, raw.issues) == ((), None, ())


@pytest.mark.parametrize(("result", "why"), [(math.nan, "non-finite"), (None, "boom")])
def test_a_measure_that_fails_is_reported_and_the_rest_stay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: float | None, why: str
) -> None:
    def volume(shape: object) -> float:
        if result is None:
            raise RuntimeError("boom")
        return result

    monkeypatch.setattr(ifcopenshell.util.shape, "get_volume", volume)

    raw = _read(tmp_path)

    assert raw.shapes == ()
    failed = [i for i in raw.issues if "could not be measured" in i.message]
    assert len(failed) == 4
    assert all(why in issue.message for issue in failed)


def test_a_model_where_nothing_triangulates_reports_each_body(tmp_path: Path) -> None:
    path = write_measured_model(tmp_path / "beam.ifc", only_unmeasurable=True)

    raw = read_model(path, geometry=True)

    assert (raw.shapes, raw.footprints) == ((), ())
    # The beam has no geometry quantity; the space (also measured) has none, and no plan.
    assert [(issue.code, issue.message) for issue in raw.issues if "plan" in issue.message] == [
        (
            IssueCode.GEOMETRY_NOT_MEASURED,
            f"IfcSpace {UNPLANNED_SPACE!r} has a body that gives no plan; a copy of it is "
            "found by its number, name, storey and area only",
        )
    ]
    assert len(raw.issues) == 3


def test_a_model_with_nothing_to_measure_takes_no_time_to_say_so(tmp_path: Path) -> None:
    empty = ifcopenshell.file(schema="IFC4")
    shapes, issues, seconds = geometry.measure_shapes(empty)
    plans, plan_issues, plan_seconds = geometry.measure_space_plans(empty)

    assert (shapes, issues, plans, plan_issues) == ((), [], (), [])
    assert seconds >= 0
    assert plan_seconds >= 0


@pytest.mark.parametrize("viewer", [False, True])
def test_a_space_seen_only_edge_on_from_above_gets_no_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, viewer: bool
) -> None:
    """With or without the viewer's pass, which makes the plans from its own shapes."""
    monkeypatch.setattr(geometry, "plan_from_triangles", lambda verts, faces: None)

    raw = read_model(write_measured_model(tmp_path / "measured.ifc"), viewer=viewer)

    assert raw.footprints == ()
    assert [issue.message for issue in raw.issues] == [
        f"IfcSpace {SPACE!r} has a body that gives no plan; a copy of it is found by its "
        "number, name, storey and area only"
    ]


def test_every_space_gets_its_plan_without_geometry(tmp_path: Path) -> None:
    raw = read_model(write_measured_model(tmp_path / "measured.ifc"))
    names = {s.global_id: s.name for s in raw.spatial_elements}

    [footprint] = raw.footprints
    assert names[footprint.global_id] == SPACE
    assert footprint.plan.area == pytest.approx(6.0)


# --------------------------------------------------------------------------- quantities


def test_a_walls_area_is_its_side_a_slabs_and_spaces_their_footprint() -> None:
    shapes = [
        RawShape("0wall__________________"[:22], 13.0, 1.0, 2.6),
        RawShape("0slab__________________"[:22], 1.0, 16.0, 4.0),
        RawShape("0space_________________"[:22], 7.5, 6.0, 15.0),
        RawShape("0column________________"[:22], 1.2, 0.16, 0.48),
        RawShape("0open_mesh_____________"[:22], 0.5, 0.0, -0.1),
        RawShape("0not_loaded____________"[:22], 1.0, 1.0, 1.0),
    ]
    classes = {
        "0wall__________________"[:22]: "IfcWall",
        "0slab__________________"[:22]: "IfcSlab",
        "0space_________________"[:22]: "IfcSpace",
        "0column________________"[:22]: "IfcColumn",
        "0open_mesh_____________"[:22]: "IfcSlab",
    }

    records = geometry_quantities(shapes, classes)

    assert {(r.global_id[:6], r.name, r.value, r.source_ref) for r in records} == {
        ("0wall_", "area_m2", 13.0, "geometry:side_area"),
        ("0wall_", "volume_m3", 2.6, "geometry:volume"),
        ("0slab_", "area_m2", 16.0, "geometry:footprint_area"),
        ("0slab_", "volume_m3", 4.0, "geometry:volume"),
        ("0space", "area_m2", 6.0, "geometry:footprint_area"),
        ("0space", "volume_m3", 15.0, "geometry:volume"),
        ("0colum", "volume_m3", 0.48, "geometry:volume"),
    }
    assert {r.source for r in records} == {"geometry"}


# --------------------------------------------------------------------------- the viewer's pass


def _scene_file(tmp_path: Path) -> Path:
    return write_measured_model(tmp_path / "scene.ifc", scene=True)


def test_one_pass_gives_the_measures_plans_and_issues_of_the_two_passes(tmp_path: Path) -> None:
    """ADR 14, P1: the viewer's pass in local coordinates serves --geometry as well."""
    path = _scene_file(tmp_path)

    shared = read_model(path, geometry=True, viewer=True)
    alone = read_model(path, geometry=True)

    def measures(raw: RawModel) -> list[tuple[str, float, float, float]]:
        return sorted(
            (s.global_id, s.side_area_m2, s.footprint_area_m2, s.volume_m3) for s in raw.shapes
        )

    # Both measure in local coordinates, so the figures are the same to the last bit.
    assert measures(shared) == measures(alone)
    assert len(shared.shapes) == 9  # wall, slab, space, column, four flights, the copy
    # The plans come from world coordinates: the matrix's and IfcOpenShell's own.
    assert [f.global_id for f in shared.footprints] == [f.global_id for f in alone.footprints]
    assert [f.plan.area for f in shared.footprints] == pytest.approx(
        [f.plan.area for f in alone.footprints], abs=1e-9
    )
    assert shared.issues == alone.issues
    assert shared.geometry_seconds == shared.scene_seconds
    assert alone.meshes == ()
    assert alone.scene_seconds is None


def test_the_viewer_alone_measures_nothing_and_still_gives_the_plans(tmp_path: Path) -> None:
    raw = read_model(_scene_file(tmp_path), viewer=True)
    names = {e.global_id: e.name for e in raw.elements} | {
        s.global_id: s.name for s in raw.spatial_elements
    }

    assert (raw.shapes, raw.geometry_seconds, raw.issues) == ((), None, ())
    assert raw.scene_seconds is not None
    assert sorted(names[f.global_id] for f in raw.footprints) == [SPACE, SPACE_COPY]
    # Every element and space with a body but the beam, which gives no triangle; no opening.
    assert sorted(str(names[m.global_id]) for m in raw.meshes) == sorted(
        [WALL, SLAB, SPACE, COLUMN, STAIR, *FLIGHTS[STAIR], *FLIGHTS[BARE_STAIR], SPACE_COPY]
    )
    assert [m.step_id for m in raw.meshes] == sorted(m.step_id for m in raw.meshes)


def test_meshes_are_moved_to_the_world_coordinates_ifcopenshell_gives(tmp_path: Path) -> None:
    """The placement matrix in numpy agrees with use-world-coords (ADR 14, P1)."""
    path = _scene_file(tmp_path)
    raw = read_model(path, viewer=True)
    model = ifcopenshell.open(str(path))
    flights = {e.id(): e for e in model.by_type("IfcStairFlight") if e.Name in FLIGHTS[BARE_STAIR]}

    world = {
        entity.GlobalId: np.frombuffer(shape.geometry.verts_buffer).reshape(-1, 3)
        for shape, entity in geometry._triangulated(model, flights, 1, world=True)
    }

    moved = [m for m in raw.meshes if m.global_id in world]
    assert len(moved) == 2
    for mesh in moved:
        vertices = np.frombuffer(mesh.vertices).reshape(-1, 3)
        assert np.abs(vertices - world[mesh.global_id]).max() < 1e-9
        # Turned a quarter and moved: x from 10 - 3 to 10, y from 20 to 21.
        assert vertices.min(axis=0).tolist() == pytest.approx([7.0, 20.0, 1.0])
        assert vertices.max(axis=0).tolist() == pytest.approx([10.0, 21.0, 1.2])


def test_a_file_with_no_body_triangulates_nothing(tmp_path: Path) -> None:
    scene = geometry.triangulate_scene(ifcopenshell.file(schema="IFC4"), measure=True)

    assert (scene.meshes, scene.footprints, scene.shapes, scene.issues) == ((), (), (), ())
    assert scene.seconds >= 0


def test_the_viewers_pass_reports_what_it_cannot_measure_or_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_measured_model(tmp_path / "beam.ifc", only_unmeasurable=True)
    monkeypatch.setattr(ifcopenshell.util.shape, "get_volume", lambda shape: math.nan)

    shared = read_model(path, geometry=True, viewer=True)
    measured = read_model(_scene_file(tmp_path), geometry=True, viewer=True)

    assert shared.issues == read_model(path, geometry=True).issues
    assert [i.message for i in shared.issues] == [
        f"IfcSpace {UNPLANNED_SPACE!r} has a body that gives no plan; a copy of it is found "
        "by its number, name, storey and area only",
        f"IfcBeam {BEAM!r} has a body that could not be triangulated; no geometry quantity",
        f"IfcSpace {UNPLANNED_SPACE!r} has a body that could not be triangulated; no geometry "
        "quantity",
    ]
    failed = [i for i in measured.issues if "could not be measured" in i.message]
    assert len(failed) == 9
    assert all("non-finite" in issue.message for issue in failed)


@pytest.mark.parametrize(
    ("diffuse", "transparency", "rgba"),
    [
        ((0.4, 0.5, 0.6), math.nan, (0.4, 0.5, 0.6, 1.0)),
        ((0.65, 0.75, 0.8), 0.8, (0.65, 0.75, 0.8, pytest.approx(0.2))),
        ((1.5, -0.1, 0.5), None, (1.0, 0.0, 0.5, 1.0)),
        ((math.nan, 0.5, 0.5), 0.0, DEFAULT_RGBA),
        (None, 0.0, DEFAULT_RGBA),
        ((0.1, 0.2), 0.0, DEFAULT_RGBA),
    ],
)
def test_a_materials_colour_is_kept_between_0_and_1_and_light_grey_without_one(
    diffuse: tuple[float, ...] | None, transparency: float | None, rgba: tuple[float, ...]
) -> None:
    material = SimpleNamespace(
        diffuse=SimpleNamespace(components=diffuse) if diffuse is not None else None,
        transparency=transparency,
    )

    assert geometry.rgba(material) == rgba
