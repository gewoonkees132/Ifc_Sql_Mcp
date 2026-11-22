"""Measuring shapes for ``ingest --geometry`` (audit finding A5), on real triangulations.

The model (``tests/support/geometry_model.py``) has a 5 x 3 m wall with a 1 x 2 m opening,
a 4 x 4 m slab, a 3 x 2 m space, a column and a beam that cannot be triangulated.
"""

import math
from pathlib import Path

import ifcopenshell.util.shape
import pytest
from geometry_model import BEAM, COLUMN, SLAB, SPACE, UNPLANNED_SPACE, WALL, write_measured_model

from ifc_sql_mcp.ingest import geometry
from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.issues import IssueCode
from ifc_sql_mcp.ingest.quantities import geometry_quantities
from ifc_sql_mcp.ingest.raw import RawModel, RawShape


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


def test_a_space_seen_only_edge_on_from_above_gets_no_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geometry, "plan_from_triangles", lambda verts, faces: None)

    raw = read_model(write_measured_model(tmp_path / "measured.ifc"))

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
