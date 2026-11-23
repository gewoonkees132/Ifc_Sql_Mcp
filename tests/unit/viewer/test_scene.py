"""Building a scene (``viewer/scene.py``): what is drawn, the counts, the origin, the chunks."""

import dataclasses
from pathlib import Path

import numpy as np
import pytest
import raw_builders
from geometry_model import FLIGHTS, SPACE, SPACE_COPY, WALL, write_measured_model
from glb_file import read_glb
from test_glb import mesh

from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.raw import RawMesh, RawModel
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import transform
from ifc_sql_mcp.viewer.glb import Node, write_glb
from ifc_sql_mcp.viewer.scene import (
    Scene,
    SceneObject,
    build_scene,
    scene_objects,
    storeys_upwards,
)

L1 = "0level1________________"[:22]
L2 = "0level2________________"[:22]


def gid(name: str) -> str:
    """A 22-character GlobalId-like name for a test object."""
    return f"0{name}".ljust(22, "_")[:22]


def box(name: str, x: float = 0.0, step_id: int = 1, z: float = 0.0) -> RawMesh:
    """A one-metre triangle at ``x``, ``z``."""
    return mesh(gid(name), [(x, 0, z), (x + 1, 0, z), (x, 1, z + 1)], [(0, 1, 2)], step_id=step_id)


def element(name: str, storey: str | None = L1, aggregate: str | None = None) -> SceneObject:
    return SceneObject(
        gid(name), "IfcWall", "element", storey, gid(aggregate) if aggregate else None
    )


def names(scene: Scene, chunk: int) -> list[str]:
    """The top-level node names of one chunk, without their padding."""
    glb = read_glb(scene.chunks[chunk].glb)
    roots = glb.gltf["scenes"][0].get("nodes", [])
    return [glb.gltf["nodes"][i]["name"].rstrip("_")[1:] for i in roots]


def test_the_counts_add_up_to_the_elements() -> None:
    objects = [
        element("wall"),
        element("beam"),  # no body: without shape
        SceneObject(gid("opening"), "IfcOpeningElement", "opening", L1),
        SceneObject(gid("room"), "IfcSpace", "space", L1),
    ]
    meshes = [box("wall"), box("opening"), box("room")]

    scene = build_scene(objects, meshes)

    assert (scene.element_count, scene.without_shape_count, scene.opening_count) == (1, 1, 1)
    assert [(c.kind, c.storey_global_id, c.element_count) for c in scene.chunks] == [
        ("elements", L1, 1),
        ("spaces", L1, 1),
    ]
    assert names(scene, 0) == ["wall"]  # the opening's mesh is never drawn
    assert scene.byte_count == sum(len(c.glb) for c in scene.chunks)


def test_a_body_without_a_triangle_is_not_drawn() -> None:
    hollow = mesh(gid("beam"), [(0, 0, 0)], [])

    scene = build_scene([element("beam")], [hollow])

    assert (scene.element_count, scene.without_shape_count, scene.chunks) == (0, 1, ())


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_a_body_with_a_vertex_that_is_not_finite_is_not_drawn(bad: float) -> None:
    broken = mesh(gid("beam"), [(0, 0, 0), (1, 0, 0), (0, bad, 1)], [(0, 1, 2)], step_id=2)
    objects = [element("wall"), element("beam"), SceneObject(gid("room"), "IfcSpace", "space", L1)]
    room = mesh(gid("room"), [(0, 0, 0), (1, 0, 0), (bad, 1, 1)], [(0, 1, 2)], step_id=3)

    scene = build_scene(objects, [box("wall", x=4.0), broken, room])

    # The rest of the scene is built, its origin from the wall alone.
    assert (scene.element_count, scene.without_shape_count) == (1, 1)
    assert scene.origin == (4.0, 0.0, 0.0)
    assert [names(scene, i) for i in range(len(scene.chunks))] == [["wall"]]


def test_parts_are_nested_under_their_aggregate_whose_own_body_is_left_out() -> None:
    objects = [
        element("stair"),
        element("flight1", None, "stair"),
        element("flight2", None, "stair"),
        element("bare"),  # an aggregate whose parts have no body: drawn by its own body
        element("rail", L1, "bare"),
    ]
    meshes = [box("stair"), box("flight1"), box("flight2"), box("bare")]

    scene = build_scene(objects, meshes)

    glb = read_glb(scene.chunks[0].glb)
    nodes = glb.nodes_by_name()
    stair = nodes[gid("stair")]
    assert "mesh" not in stair
    assert [glb.gltf["nodes"][i]["name"] for i in stair["children"]] == [
        gid("flight1"),
        gid("flight2"),
    ]
    assert "mesh" in nodes[gid("bare")]
    assert gid("rail") not in nodes
    # The stair, its two flights and the bare aggregate; the rail has no shape.
    assert (scene.element_count, scene.without_shape_count) == (4, 1)
    assert scene.chunks[0].element_count == 4


def test_an_aggregate_drawn_through_parts_of_parts_counts_once() -> None:
    objects = [
        element("ramp"),
        element("landing", None, "ramp"),
        element("slab", None, "landing"),
        element("loop1", L1, "loop2"),  # a cycle reaches no root: neither is drawn
        element("loop2", L1, "loop1"),
    ]

    scene = build_scene(objects, [box("slab"), box("loop1"), box("loop2")])

    assert (scene.element_count, scene.without_shape_count) == (3, 2)
    assert names(scene, 0) == ["ramp"]


def test_spaces_copies_and_areas_get_chunks_of_their_own_after_the_elements() -> None:
    objects = [
        SceneObject(gid("area"), "IfcSpace", "area", L1),
        SceneObject(gid("copy"), "IfcSpace", "space_copy", L1),
        SceneObject(gid("room"), "IfcSpace", "space", L1),
        SceneObject(gid("hollow"), "IfcSpace", "space", L1),  # no body: not drawn
        element("wall"),
    ]
    meshes = [box("area"), box("copy"), box("room"), box("wall")]

    scene = build_scene(objects, meshes)

    assert [c.kind for c in scene.chunks] == ["elements", "spaces", "space_copies", "areas"]
    assert [names(scene, i) for i in range(4)] == [["wall"], ["room"], ["copy"], ["area"]]


def test_chunks_follow_the_storeys_given_then_the_others_then_no_storey() -> None:
    l3, l4 = gid("level3"), gid("level4")
    objects = [
        element("a", L2),
        element("b", None),
        element("c", L1),
        element("d", L2),
        element("e", l4),
        element("f", l3),
    ]
    meshes = [box(name) for name in "abcdef"]

    scene = build_scene(objects, meshes, storeys=[L1, L2])

    assert [(c.storey_global_id, names(scene, i)) for i, c in enumerate(scene.chunks)] == [
        (L1, ["c"]),
        (L2, ["a", "d"]),
        (l3, ["f"]),  # not listed: by GlobalId
        (l4, ["e"]),
        (None, ["b"]),
    ]


def test_a_storey_above_the_element_cap_is_split_keeping_an_aggregate_whole() -> None:
    objects = [
        element("a"),
        element("stair"),
        element("flight", None, "stair"),
        element("b"),
        element("c"),
    ]
    meshes = [box("a"), box("flight"), box("b"), box("c")]

    scene = build_scene(objects, meshes, max_elements=3)

    assert [(c.element_count, names(scene, i)) for i, c in enumerate(scene.chunks)] == [
        (3, ["a", "stair"]),
        (2, ["b", "c"]),
    ]
    assert {c.storey_global_id for c in scene.chunks} == {L1}


def test_a_chunk_above_the_byte_cap_is_halved_until_it_fits_or_holds_one_node() -> None:
    objects = [element(name) for name in "abcde"]
    meshes = [box(name, x=i) for i, name in enumerate("abcde")]
    origin = build_scene(objects, meshes).origin
    nodes = [Node(m.global_id, "IfcWall", m) for m in meshes]
    # Room for two nodes, not three: five are halved into a, b and c, d, e, then c and d, e.
    cap = max(len(write_glb(nodes[:2], origin)), len(write_glb(nodes[3:], origin)))
    assert len(write_glb(nodes[2:], origin)) > cap

    scene = build_scene(objects, meshes, max_bytes=cap)
    tiny = build_scene(objects[:2], meshes[:2], max_bytes=10)

    assert [names(scene, i) for i in range(len(scene.chunks))] == [["a", "b"], ["c"], ["d", "e"]]
    assert all(len(c.glb) <= cap for c in scene.chunks)
    # A single node above the cap is a chunk of its own.
    assert [names(tiny, i) for i in range(len(tiny.chunks))] == [["a"], ["b"]]


def test_the_origin_is_the_centre_of_the_drawn_elements_rounded_to_metres() -> None:
    objects = [element("a"), element("b"), SceneObject(gid("room"), "IfcSpace", "space", L1)]
    meshes = [box("a", x=100.2, z=-3.0), box("b", x=104.0, z=7.6), box("room", x=900.0)]

    scene = build_scene(objects, meshes)

    # x 100.2..105, y 0..1, z -3..8.6: the room, far off, does not count.
    assert scene.origin == (103.0, 0.0, 3.0)
    glb = read_glb(scene.chunks[0].glb)
    assert glb.accessor(0)[0].tolist() == [np.float32(100.2 - 103.0), -3.0 - 3.0, -0.0]


def test_without_elements_the_origin_comes_from_the_spaces_and_else_is_zero() -> None:
    rooms = [SceneObject(gid("room"), "IfcSpace", "space", L1)]

    spaces_only = build_scene(rooms, [box("room", x=10.0)])
    nothing = build_scene([element("beam")], [])

    assert spaces_only.origin == (10.0, 0.0, 0.0)
    assert (nothing.origin, nothing.chunks, nothing.byte_count) == ((0.0, 0.0, 0.0), (), 0)
    assert (nothing.element_count, nothing.without_shape_count) == (0, 1)


def test_the_same_objects_give_the_same_bytes_whatever_order_the_meshes_come_in() -> None:
    objects = [element(name) for name in "abcdef"] + [element("g", None, "a")]
    meshes = [box(name, x=i, step_id=i + 1) for i, name in enumerate("abcdefg")]
    # As IfcOpenShell's worker threads might finish them.
    shuffled = [meshes[i] for i in (4, 6, 0, 3, 5, 1, 2)]

    assert build_scene(objects, meshes) == build_scene(objects, shuffled)


def test_a_global_id_with_two_meshes_keeps_the_lower_step_id_and_strays_are_left_out() -> None:
    first, second = box("a", x=0.0, step_id=5), box("a", x=50.0, step_id=9)

    scene = build_scene([element("a")], [second, box("stray"), first])

    assert scene.origin == (0.0, 0.0, 0.0)
    assert names(scene, 0) == ["a"]


# --------------------------------------------------------------------------- from the records


def _fixture(tmp_path: Path) -> tuple[RawModel, ModelRecords]:
    raw = read_model(write_measured_model(tmp_path / "scene.ifc", scene=True), viewer=True)
    return raw, transform(raw)


def test_the_objects_are_placed_as_the_database_places_them(tmp_path: Path) -> None:
    raw, records = _fixture(tmp_path)
    names = {o.global_id: o.name for o in records.objects}
    level = next(o.global_id for o in records.objects if o.ifc_class == "IfcBuildingStorey")

    objects = {names[o.global_id]: o for o in scene_objects(raw, records)}

    assert {name: o.kind for name, o in objects.items() if o.kind != "element"} == {
        f"{WALL}:1": "opening",
        SPACE: "space",
        SPACE_COPY: "space_copy",
    }
    assert {o.storey_global_id for o in objects.values()} == {level}
    for stair, flights in FLIGHTS.items():
        assert objects[stair].aggregate_global_id is None
        assert {objects[f].aggregate_global_id for f in flights} == {objects[stair].global_id}


def test_an_area_plan_and_a_space_off_any_storey_are_placed_as_well(tmp_path: Path) -> None:
    raw, records = _fixture(tmp_path)
    names = {o.global_id: o.name for o in records.objects}
    by_name = {names[s.global_id]: s for s in records.spatial_elements}
    room, copy, site = by_name[SPACE], by_name[SPACE_COPY], by_name["Site"]
    spatial = [
        dataclasses.replace(s, revit_category="Areas")
        if s is room
        # Under the site, which is on no storey; and a loop, which reaches none.
        else dataclasses.replace(s, parent_global_id=site.global_id)
        if s is copy
        else dataclasses.replace(s, parent_global_id=copy.global_id)
        if s is site
        else s
        for s in records.spatial_elements
    ]

    objects = scene_objects(raw, dataclasses.replace(records, spatial_elements=tuple(spatial)))

    placed = {names[o.global_id]: (o.kind, o.storey_global_id) for o in objects}
    assert placed[SPACE][0] == "area"
    assert placed[SPACE_COPY] == ("space_copy", None)


def test_the_storeys_go_upwards_and_those_without_an_elevation_last() -> None:
    storeys = tuple(
        raw_builders.spatial(name, "IfcBuildingStorey", elevation=elevation)
        for name, elevation in (("Roof", 6.0), ("L1", 0.0), ("L2", 3.1), ("A", None))
    )
    records = transform(raw_builders.model(storeys))
    names = {o.global_id: o.name for o in records.objects}

    assert [names[g] for g in storeys_upwards(records)] == ["L1", "L2", "Roof", "A"]
