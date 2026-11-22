"""Write a scene's chunk as glTF 2.0 binary (GLB), from plain records, with fixed bytes.

The page loads each chunk with three.js's ``GLTFLoader`` into BatchedMeshes (ADR 14). The
format is the smallest that does that:

* **Y up, metres.** IFC is Z up, so a position ``(x, y, z)`` is written as
  ``(x, z, -y)``, less the scene's origin. The positions are float32, relative to the
  origin, so a Revit file placed at survey coordinates far from 0 does not jitter.
* **POSITION and indices only.** No normals: three.js shades a primitive without them
  flat. No textures and no compression, so the page needs no WASM decoder.
* **One node per element**: ``name`` is its GlobalId and ``extras`` its IFC class. An
  aggregate's parts are its children, so selecting a stair selects its flights.
* **One primitive per material**, with its own vertices. BatchedMesh copies a
  primitive's whole vertex buffer, so primitives that shared one would be copied once
  each (ADR 14, P6).

The bytes depend only on the input: materials are numbered in the order they first
appear, the JSON is written with sorted keys, and nothing records when or where it was
written. ``scene.py`` puts the nodes in order.
"""

import json
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ifc_sql_mcp.ingest.raw import DEFAULT_RGBA, RawMesh

MAGIC = b"glTF"
VERSION = 2
_JSON = 0x4E4F534A
_BIN = 0x004E4942
_FLOAT = 5126
_UNSIGNED_SHORT = 5123
_UNSIGNED_INT = 5125
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
# glTF forbids the largest value of the index type, so 16-bit indices reach 65,534.
_MAX_SHORT_VERTICES = 65_535

type Rgba = tuple[float, float, float, float]
type Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Node:
    """One element or space in a chunk.

    Attributes:
        global_id: Its GlobalId, the node's name.
        ifc_class: Its IFC class, in the node's ``extras``.
        mesh: Its body; ``None`` for an aggregate drawn through its parts.
        children: The parts of an aggregate, each a node of its own.
    """

    global_id: str
    ifc_class: str
    mesh: RawMesh | None = None
    children: tuple["Node", ...] = ()


def write_glb(nodes: Sequence[Node], origin: tuple[float, float, float]) -> bytes:
    """Return the GLB of ``nodes``, positions relative to ``origin``.

    Args:
        nodes: The chunk's top-level nodes, in the order to write them.
        origin: The scene's origin, in the file's world coordinates (Z up), in metres.

    Returns:
        The bytes of one GLB file: a 12-byte header, the JSON chunk, and a binary chunk
        when any node has a mesh.
    """
    writer = _Writer(np.asarray(origin, dtype=np.float64))
    roots = [writer.add(node) for node in nodes]
    return writer.finish(roots)


def _pad(data: bytes, fill: bytes) -> bytes:
    """Pad ``data`` to a multiple of 4 bytes, as GLB chunks and accessors need."""
    return data + fill * (-len(data) % 4)


class _Writer:
    """Collects the nodes, meshes, accessors and binary data of one GLB."""

    def __init__(self, origin: np.ndarray[Any, np.dtype[np.float64]]) -> None:
        self.origin = origin
        self.nodes: list[Json] = []
        self.meshes: list[Json] = []
        self.accessors: list[Json] = []
        self.materials: list[Json] = []
        self.material_index: dict[Rgba, int] = {}
        self.positions = bytearray()
        self.indices = bytearray()

    def add(self, node: Node) -> int:
        """Add ``node`` and its children; return its index."""
        entry: Json = {"name": node.global_id, "extras": {"ifc_class": node.ifc_class}}
        index = len(self.nodes)
        self.nodes.append(entry)
        if node.mesh is not None:
            primitives = list(self._primitives(node.mesh))
            if primitives:
                entry["mesh"] = len(self.meshes)
                self.meshes.append({"primitives": primitives})
        if node.children:
            entry["children"] = [self.add(child) for child in node.children]
        return index

    def _primitives(self, mesh: RawMesh) -> Iterable[Json]:
        """One primitive per material of ``mesh``, each with the vertices it uses."""
        vertices = np.frombuffer(mesh.vertices, dtype=np.float64).reshape(-1, 3) - self.origin
        y_up = np.empty_like(vertices, dtype=np.float32)
        y_up[:, 0] = vertices[:, 0]
        y_up[:, 1] = vertices[:, 2]
        y_up[:, 2] = -vertices[:, 1]
        faces = np.frombuffer(mesh.faces, dtype=np.int32).reshape(-1, 3)
        material_ids = np.frombuffer(mesh.material_ids, dtype=np.int32)
        for slot in np.unique(material_ids):
            used, local = np.unique(faces[material_ids == slot], return_inverse=True)
            rgba = mesh.materials[slot] if 0 <= slot < len(mesh.materials) else DEFAULT_RGBA
            yield {
                "attributes": {"POSITION": self._positions(y_up[used])},
                "indices": self._indices(local.reshape(-1), len(used)),
                "material": self._material(rgba),
            }

    def _positions(self, points: np.ndarray[Any, np.dtype[np.float32]]) -> int:
        self.accessors.append(
            {
                "bufferView": 0,
                "byteOffset": len(self.positions),
                "componentType": _FLOAT,
                "count": len(points),
                "type": "VEC3",
                "min": [float(value) for value in points.min(axis=0)],
                "max": [float(value) for value in points.max(axis=0)],
            }
        )
        self.positions += points.tobytes()
        return len(self.accessors) - 1

    def _indices(self, indices: np.ndarray[Any, Any], vertex_count: int) -> int:
        short = vertex_count <= _MAX_SHORT_VERTICES
        data = indices.astype(np.uint16 if short else np.uint32).tobytes()
        self.accessors.append(
            {
                "bufferView": 1,
                "byteOffset": len(self.indices),
                "componentType": _UNSIGNED_SHORT if short else _UNSIGNED_INT,
                "count": len(indices),
                "type": "SCALAR",
            }
        )
        self.indices += _pad(data, b"\x00")
        return len(self.accessors) - 1

    def _material(self, rgba: Rgba) -> int:
        """The material of one colour, added the first time it is used."""
        key = (round(rgba[0], 4), round(rgba[1], 4), round(rgba[2], 4), round(rgba[3], 4))
        if key not in self.material_index:
            material: Json = {
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(key),
                    "metallicFactor": 0,
                    "roughnessFactor": 1,
                },
            }
            if key[3] < 1:
                material["alphaMode"] = "BLEND"
            self.material_index[key] = len(self.materials)
            self.materials.append(material)
        return self.material_index[key]

    def finish(self, roots: list[int]) -> bytes:
        """Return the GLB's bytes."""
        gltf: Json = {
            "asset": {"generator": "ifc-sql-mcp", "version": "2.0"},
            "scene": 0,
            # glTF allows no empty array: an empty chunk has a scene with no nodes key.
            "scenes": [{"nodes": roots} if roots else {}],
        }
        if self.nodes:
            gltf["nodes"] = self.nodes
        binary = bytes(self.positions + self.indices)
        if self.meshes:
            gltf |= {
                "meshes": self.meshes,
                "materials": self.materials,
                "accessors": self.accessors,
                "bufferViews": [
                    {
                        "buffer": 0,
                        "byteLength": len(self.positions),
                        "byteOffset": 0,
                        "byteStride": 12,
                        "target": _ARRAY_BUFFER,
                    },
                    {
                        "buffer": 0,
                        "byteLength": len(self.indices),
                        "byteOffset": len(self.positions),
                        "target": _ELEMENT_ARRAY_BUFFER,
                    },
                ],
                "buffers": [{"byteLength": len(binary)}],
            }
        text = json.dumps(gltf, sort_keys=True, separators=(",", ":"), allow_nan=False)
        chunks = [(_JSON, _pad(text.encode("utf-8"), b" "))]
        if binary:
            chunks.append((_BIN, _pad(binary, b"\x00")))
        body = b"".join(struct.pack("<II", len(data), kind) + data for kind, data in chunks)
        return MAGIC + struct.pack("<II", VERSION, 12 + len(body)) + body
