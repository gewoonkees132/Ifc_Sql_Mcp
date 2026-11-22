"""Phase 0 sketch of viewer/glb.py: one GLB from plain mesh records.

Y-up, metres, float32 positions relative to an origin, POSITION and indices only, one
primitive per material, one node per element (name = GlobalId, extras = ifc_class),
parts nested under their aggregate. Deterministic: sorted by STEP id, sort_keys JSON.
"""

import json
import struct
from dataclasses import dataclass, field

import numpy as np

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


@dataclass
class Mesh:
    """One element's triangles in world coordinates, Z-up, with its materials."""

    step_id: int
    global_id: str
    ifc_class: str
    verts: np.ndarray  # (n, 3) float64, world, Z-up
    faces: np.ndarray  # (m, 3) int
    material_ids: np.ndarray  # (m,) int
    materials: list[tuple[float, float, float, float]]  # rgba per material slot
    parent: str | None = None


@dataclass
class Node:
    """A glTF node: an element, with its mesh or with its parts as children."""

    global_id: str
    ifc_class: str
    mesh: Mesh | None
    children: list["Node"] = field(default_factory=list)


def _pad(data: bytes, fill: bytes = b"\x00") -> bytes:
    return data + fill * (-len(data) % 4)


def write_glb(roots: list[Node], origin: tuple[float, float, float]) -> bytes:
    """Return the GLB bytes of ``roots`` (sorted already)."""
    ox, oy, oz = origin
    positions: list[bytes] = []
    indices: list[bytes] = []
    pos_offset = 0
    idx_offset = 0
    accessors: list[dict] = []
    meshes: list[dict] = []
    nodes: list[dict] = []
    materials: list[dict] = []
    material_index: dict[tuple[float, float, float, float], int] = {}
    scene_roots: list[int] = []

    def material(rgba: tuple[float, float, float, float]) -> int:
        key = tuple(round(c, 4) for c in rgba)
        if key not in material_index:
            m: dict = {
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(key),
                    "metallicFactor": 0,
                    "roughnessFactor": 1,
                },
                "doubleSided": True,
            }
            if key[3] < 1:
                m["alphaMode"] = "BLEND"
            material_index[key] = len(materials)
            materials.append(m)
        return material_index[key]

    def add(node: Node) -> int:
        nonlocal pos_offset, idx_offset
        entry: dict = {"name": node.global_id, "extras": {"ifc_class": node.ifc_class}}
        mesh = node.mesh
        if mesh is not None and len(mesh.faces):
            v = mesh.verts
            yup = np.empty_like(v, dtype=np.float32)
            yup[:, 0] = v[:, 0] - ox
            yup[:, 1] = v[:, 2] - oz
            yup[:, 2] = -(v[:, 1] - oy)
            data = yup.tobytes()
            pos_accessor = len(accessors)
            accessors.append(
                {
                    "bufferView": 0,
                    "byteOffset": pos_offset,
                    "componentType": 5126,
                    "count": len(yup),
                    "type": "VEC3",
                    "min": [float(x) for x in yup.min(axis=0)],
                    "max": [float(x) for x in yup.max(axis=0)],
                }
            )
            positions.append(data)
            pos_offset += len(data)
            primitives = []
            small = len(yup) < 65536
            for slot in sorted({int(m) for m in mesh.material_ids}):
                tri = mesh.faces[mesh.material_ids == slot].reshape(-1)
                raw = tri.astype(np.uint16 if small else np.uint32).tobytes()
                raw = _pad(raw)
                accessors.append(
                    {
                        "bufferView": 1,
                        "byteOffset": idx_offset,
                        "componentType": 5123 if small else 5125,
                        "count": len(tri),
                        "type": "SCALAR",
                    }
                )
                indices.append(raw)
                idx_offset += len(raw)
                rgba = (
                    mesh.materials[slot]
                    if 0 <= slot < len(mesh.materials)
                    else (0.8, 0.8, 0.8, 1.0)
                )
                primitives.append(
                    {
                        "attributes": {"POSITION": pos_accessor},
                        "indices": len(accessors) - 1,
                        "material": material(rgba),
                    }
                )
            entry["mesh"] = len(meshes)
            meshes.append({"primitives": primitives})
        index = len(nodes)
        nodes.append(entry)
        if node.children:
            entry["children"] = [add(child) for child in node.children]
        return index

    for root in roots:
        scene_roots.append(add(root))

    pos_blob = b"".join(positions)
    idx_blob = b"".join(indices)
    bin_blob = pos_blob + idx_blob
    gltf: dict = {
        "asset": {"version": "2.0", "generator": "ifc-sql-mcp spike"},
        "scene": 0,
        "scenes": [{"nodes": scene_roots}],
        "nodes": nodes,
    }
    if meshes:
        gltf.update(
            {
                "meshes": meshes,
                "materials": materials,
                "accessors": accessors,
                "bufferViews": [
                    {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_blob), "target": 34962},
                    {
                        "buffer": 0,
                        "byteOffset": len(pos_blob),
                        "byteLength": len(idx_blob),
                        "target": 34963,
                    },
                ],
                "buffers": [{"byteLength": len(bin_blob)}],
            }
        )
    json_bytes = _pad(json.dumps(gltf, sort_keys=True, separators=(",", ":")).encode(), b" ")
    bin_bytes = _pad(bin_blob)
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_blob else 0)
    out = struct.pack("<III", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(json_bytes), JSON_CHUNK) + json_bytes
    if bin_blob:
        out += struct.pack("<II", len(bin_bytes), BIN_CHUNK) + bin_bytes
    return out


def json_bytes_of(glb: bytes) -> int:
    """Return the length of a GLB's JSON chunk."""
    return struct.unpack_from("<I", glb, 12)[0]
