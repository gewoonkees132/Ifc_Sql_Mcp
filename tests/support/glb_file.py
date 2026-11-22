"""Read a GLB back, checking its container as the glTF 2.0 specification lays it out.

Shared by the unit tests of ``viewer/glb.py`` and the tests that read scenes from the
database. It checks the frame (header, chunk types, lengths, alignment) and returns the
JSON and the binary chunk; the tests check the content.
"""

import json
import struct
from dataclasses import dataclass
from typing import Any

import numpy as np

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


@dataclass(frozen=True)
class Glb:
    """A parsed GLB: its JSON and its binary chunk (empty when it has none)."""

    gltf: dict[str, Any]
    binary: bytes

    def accessor(self, index: int) -> np.ndarray[Any, Any]:
        """The values of one accessor, shaped (count, components)."""
        accessor = self.gltf["accessors"][index]
        view = self.gltf["bufferViews"][accessor["bufferView"]]
        dtype = {5126: np.float32, 5123: np.uint16, 5125: np.uint32}[accessor["componentType"]]
        width = {"VEC3": 3, "SCALAR": 1}[accessor["type"]]
        start = view["byteOffset"] + accessor["byteOffset"]
        count = accessor["count"] * width
        values = np.frombuffer(self.binary, dtype=dtype, count=count, offset=start)
        return values.reshape(-1, width)

    def nodes_by_name(self) -> dict[str, dict[str, Any]]:
        """Every node, by its name (a GlobalId)."""
        return {node["name"]: node for node in self.gltf.get("nodes", [])}


def read_glb(data: bytes) -> Glb:
    """Parse ``data`` as GLB, asserting what the specification requires of the frame."""
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert (magic, version, length) == (b"glTF", 2, len(data))
    offset = 12
    chunks: list[tuple[int, bytes]] = []
    while offset < len(data):
        size, kind = struct.unpack_from("<II", data, offset)
        assert size % 4 == 0
        chunks.append((kind, data[offset + 8 : offset + 8 + size]))
        offset += 8 + size
    assert offset == len(data)
    assert chunks[0][0] == JSON_CHUNK
    gltf = json.loads(chunks[0][1].decode("utf-8"))
    binary = b""
    if len(chunks) > 1:
        assert [kind for kind, _ in chunks[1:]] == [BIN_CHUNK]
        binary = chunks[1][1]
        assert len(binary) >= gltf["buffers"][0]["byteLength"]
    return Glb(gltf=gltf, binary=binary)
