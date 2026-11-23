"""The GLB writer (``viewer/glb.py``): the frame, the nodes, the axes and fixed bytes."""

import struct

import numpy as np
import pytest
from glb_file import read_glb

from ifc_sql_mcp.ingest.raw import DEFAULT_RGBA, RawMesh
from ifc_sql_mcp.viewer.glb import Node, write_glb

RED = (1.0, 0.0, 0.0, 1.0)
GLASS = (0.2, 0.4, 0.6, 0.3)
WALL = "0wall__________________"[:22]
STAIR = "0stair_________________"[:22]
FLIGHT = "0flight________________"[:22]


def mesh(
    global_id: str,
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    material_ids: list[int] | None = None,
    materials: tuple[tuple[float, float, float, float], ...] = (RED,),
    step_id: int = 1,
) -> RawMesh:
    """A mesh as IfcOpenShell's buffers give it: float64 vertices, int32 faces."""
    return RawMesh(
        global_id=global_id,
        step_id=step_id,
        vertices=np.asarray(vertices, dtype=np.float64).tobytes(),
        faces=np.asarray(faces, dtype=np.int32).reshape(-1, 3).tobytes(),
        material_ids=np.asarray(
            material_ids if material_ids is not None else [0] * len(faces), dtype=np.int32
        ).tobytes(),
        materials=materials,
    )


TRIANGLE = mesh(WALL, [(10.0, 20.0, 30.0), (11.0, 20.0, 30.0), (10.0, 21.0, 32.0)], [(0, 1, 2)])
ORIGIN = (1.0, 2.0, 3.0)


def test_the_header_has_the_magic_the_version_and_the_length() -> None:
    data = write_glb([Node(WALL, "IfcWall", TRIANGLE)], ORIGIN)

    assert data[:4] == b"glTF"
    assert struct.unpack_from("<II", data, 4) == (2, len(data))


def test_chunks_are_aligned_to_four_bytes_and_the_json_parses() -> None:
    glb = read_glb(write_glb([Node(WALL, "IfcWall", TRIANGLE)], ORIGIN))

    assert glb.gltf["asset"] == {"generator": "ifc-sql-mcp", "version": "2.0"}
    assert len(glb.binary) % 4 == 0
    # Three float32 positions (36 bytes) and three uint16 indices, padded to 8 bytes.
    assert [view["byteLength"] for view in glb.gltf["bufferViews"]] == [36, 8]


def test_y_is_up_and_the_origin_is_subtracted() -> None:
    glb = read_glb(write_glb([Node(WALL, "IfcWall", TRIANGLE)], ORIGIN))

    # IFC (x, y, z) less the origin becomes glTF (x, z, -y).
    assert glb.accessor(0).tolist() == [[9.0, 27.0, -18.0], [10.0, 27.0, -18.0], [9.0, 29.0, -19.0]]
    assert glb.accessor(1).ravel().tolist() == [0, 1, 2]


def test_the_position_accessor_has_its_bounds() -> None:
    glb = read_glb(write_glb([Node(WALL, "IfcWall", TRIANGLE)], ORIGIN))

    position = glb.gltf["accessors"][0]
    assert (position["min"], position["max"]) == ([9.0, 27.0, -19.0], [10.0, 29.0, -18.0])
    assert glb.gltf["bufferViews"][0]["byteStride"] == 12


def test_nodes_are_named_by_global_id_and_parts_are_children_of_their_aggregate() -> None:
    flight = Node(
        FLIGHT, "IfcStairFlight", mesh(FLIGHT, [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
    )
    stair = Node(STAIR, "IfcStair", None, (flight,))

    glb = read_glb(write_glb([stair, Node(WALL, "IfcWall", TRIANGLE)], ORIGIN))

    nodes = glb.nodes_by_name()
    assert glb.gltf["scenes"] == [{"nodes": [0, 2]}]
    assert nodes[STAIR] == {"name": STAIR, "extras": {"ifc_class": "IfcStair"}, "children": [1]}
    assert glb.gltf["nodes"][1]["name"] == FLIGHT
    assert nodes[FLIGHT]["extras"] == {"ifc_class": "IfcStairFlight"}
    assert [nodes[name]["mesh"] for name in (FLIGHT, WALL)] == [0, 1]


def test_each_material_is_a_primitive_with_only_its_own_vertices() -> None:
    """BatchedMesh copies a primitive's whole vertex buffer, so none is shared (P6)."""
    square = mesh(
        WALL,
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        material_ids=[0, 1],
        materials=(RED, GLASS),
    )

    glb = read_glb(write_glb([Node(WALL, "IfcWall", square)], (0.0, 0.0, 0.0)))

    [entry] = glb.gltf["meshes"]
    red, glass = entry["primitives"]
    assert [glb.accessor(p["attributes"]["POSITION"]).tolist() for p in (red, glass)] == [
        [[0, 0, 0], [1, 0, 0], [1, 0, -1]],
        [[0, 0, 0], [1, 0, -1], [0, 0, -1]],
    ]
    assert [glb.accessor(p["indices"]).ravel().tolist() for p in (red, glass)] == [
        [0, 1, 2],
        [0, 1, 2],
    ]
    materials = glb.gltf["materials"]
    assert [
        materials[p["material"]]["pbrMetallicRoughness"]["baseColorFactor"] for p in (red, glass)
    ] == [
        list(RED),
        list(GLASS),
    ]
    assert "alphaMode" not in materials[red["material"]]
    assert materials[glass["material"]]["alphaMode"] == "BLEND"


def test_a_colour_is_one_material_and_a_missing_one_is_light_grey() -> None:
    first = mesh(WALL, [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], material_ids=[-1])
    second = mesh(FLIGHT, [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], material_ids=[3])
    third = mesh(STAIR, [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])

    glb = read_glb(
        write_glb(
            [
                Node(WALL, "IfcWall", first),
                Node(FLIGHT, "IfcSlab", second),
                Node(STAIR, "IfcSlab", third),
            ],
            (0.0, 0.0, 0.0),
        )
    )

    colours = [m["pbrMetallicRoughness"]["baseColorFactor"] for m in glb.gltf["materials"]]
    assert colours == [list(DEFAULT_RGBA), list(RED)]
    assert [m["primitives"][0]["material"] for m in glb.gltf["meshes"]] == [0, 0, 1]


def test_a_primitive_of_more_than_65535_vertices_has_32_bit_indices() -> None:
    count = 65_536 * 3  # 65,536 triangles that share no vertex
    vertices = np.zeros((count, 3))
    vertices[:, 0] = np.arange(count)
    big = RawMesh(
        global_id=WALL,
        step_id=1,
        vertices=vertices.tobytes(),
        faces=np.arange(count, dtype=np.int32).tobytes(),
        material_ids=np.zeros(count // 3, dtype=np.int32).tobytes(),
        materials=(RED,),
    )

    glb = read_glb(
        write_glb([Node(WALL, "IfcWall", big), Node(STAIR, "IfcSlab", TRIANGLE)], ORIGIN)
    )

    indices = [glb.gltf["accessors"][m["primitives"][0]["indices"]] for m in glb.gltf["meshes"]]
    assert [i["componentType"] for i in indices] == [5125, 5123]  # uint32, then uint16
    assert glb.accessor(glb.gltf["meshes"][0]["primitives"][0]["indices"])[-1, 0] == count - 1


def test_an_empty_chunk_and_a_node_without_triangles_are_valid() -> None:
    empty = read_glb(write_glb([], ORIGIN))
    hollow = read_glb(write_glb([Node(WALL, "IfcWall", mesh(WALL, [], []))], ORIGIN))

    assert (empty.gltf["scenes"], "nodes" in empty.gltf, empty.binary) == ([{}], False, b"")
    assert hollow.gltf["nodes"] == [{"name": WALL, "extras": {"ifc_class": "IfcWall"}}]
    assert "meshes" not in hollow.gltf
    assert hollow.binary == b""


def test_the_same_nodes_give_the_same_bytes() -> None:
    nodes = [Node(WALL, "IfcWall", TRIANGLE), Node(STAIR, "IfcStair")]

    assert write_glb(nodes, ORIGIN) == write_glb(list(nodes), ORIGIN)


def test_a_position_that_is_not_a_number_is_refused() -> None:
    broken = mesh(WALL, [(np.nan, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])

    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        write_glb([Node(WALL, "IfcWall", broken)], ORIGIN)
