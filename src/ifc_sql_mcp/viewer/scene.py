"""Turn a model's meshes into its scene: the origin, the counts, and chunks storey by storey.

The scene is what ``ifc-sql view`` shows of a model (ADR 14). This module decides what is
drawn and how it is cut into chunks; ``glb.py`` writes each chunk. Both are pure.

**What is drawn.** Every element with a body, except feature elements (openings), which
IfcOpenShell has already cut out of the elements they belong to. Every space with a body
too, in chunks of their own, which the page shows only when a selection holds one. A
space that repeats a room (``spatial_element.repeats_id``) and a Revit area plan
(category ``Areas``) get chunks of their own kinds, so they never z-fight with the rooms.

**Aggregates.** When an element aggregates parts that are drawn (an ``IfcStair``'s
flights, an ``IfcCurtainWall``'s plates and members), the parts become children of the
aggregate's node, and the aggregate's own body, if any, is left out: selecting the
aggregate's GlobalId then selects what is drawn of it. None of the nine reference files
has an aggregate with a body of its own; the synthetic fixture tests the rule.

**The counts**, which the page shows, add up to the elements of ``ifc.element``:

* drawn (``element_count``): the elements with a body of their own, and the aggregates
  drawn through their parts;
* without shape: the elements that are neither, those whose body has no triangle or a
  vertex that is not a finite number among them;
* openings: the feature elements, never drawn.

The Duplex ARC model has 218 drawn (215 with a body and 3 aggregates: two stairs and a
roof), none without shape and 50 openings: its 268 elements.

**Chunks follow the storeys**, so the page can load and toggle storey by storey: each
storey's elements, and those on no storey, go in chunks of at most
:data:`MAX_CHUNK_ELEMENTS` elements and :data:`MAX_CHUNK_BYTES` bytes. An aggregate and
its parts stay in one chunk, on the aggregate's storey. Phase 0 set the caps: the element
cap splits two storeys of the nine files (file C's Level 1 and file E's Plan 01), and the
largest chunk is 7.87 MB, under the byte cap. The chunks are numbered kind by kind, and
storey by storey from the lowest; within a storey the elements come in the records'
order, by GlobalId.

**The origin** is the centre of the drawn elements' bounding box, rounded to whole
metres (of the spaces', for a model with no element drawn). The chunks' positions are
relative to it, in float32, and the page lines models up by subtracting one origin from
another.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from ifc_sql_mcp.ingest.footprints import REVIT_AREAS
from ifc_sql_mcp.ingest.raw import RawMesh, RawModel
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import SPACE_CLASS, STOREY_CLASS
from ifc_sql_mcp.viewer.glb import Node, write_glb

FORMAT_VERSION = 1
"""The version of the scene's format. A scene of an older version is replaced when
``ingest --viewer`` runs on its file again; a current one is kept."""

MAX_CHUNK_ELEMENTS = 2_000
"""At most this many elements (or spaces) in one chunk, aggregates included."""

MAX_CHUNK_BYTES = 16 * 1024 * 1024
"""At most this many bytes in one chunk, unless it holds a single aggregate or element."""

type ObjectKind = Literal["element", "opening", "space", "space_copy", "area"]
type ChunkKind = Literal["elements", "spaces", "space_copies", "areas"]

CHUNK_KINDS: dict[ObjectKind, ChunkKind] = {
    "element": "elements",
    "space": "spaces",
    "space_copy": "space_copies",
    "area": "areas",
}
"""The chunk each kind of object is drawn in, in the order the chunks are numbered."""


@dataclass(frozen=True, slots=True)
class SceneObject:
    """An element or space of the model, as the scene needs it.

    Attributes:
        global_id: Its GlobalId.
        ifc_class: Its exact IFC class.
        kind: An element, an opening (a feature element), a room, a space that repeats a
            room, or a Revit area plan.
        storey_global_id: The storey it is on, if any: ``element.storey_id``, or the
            storey above a space.
        aggregate_global_id: The element it is a part of (IfcRelAggregates), if any.
    """

    global_id: str
    ifc_class: str
    kind: ObjectKind
    storey_global_id: str | None
    aggregate_global_id: str | None = None


@dataclass(frozen=True, slots=True)
class SceneChunk:
    """One part of a scene: some of one storey's elements or spaces, as GLB.

    Attributes:
        storey_global_id: The storey; ``None`` for the objects on no storey.
        kind: What the chunk holds.
        element_count: How many elements or spaces it draws, aggregates included.
        glb: The chunk, written by ``glb.write_glb``.
    """

    storey_global_id: str | None
    kind: ChunkKind
    element_count: int
    glb: bytes


@dataclass(frozen=True, slots=True)
class Scene:
    """What the viewer shows of one model.

    Attributes:
        origin: The point the chunks' positions are relative to, in the file's world
            coordinates (Z up), in metres.
        element_count: Elements drawn, aggregates drawn through their parts included.
        without_shape_count: Elements that are not drawn, openings left out.
        opening_count: Feature elements, never drawn.
        chunks: The chunks, in the order they are numbered: kind by kind, storey by storey.
    """

    origin: tuple[float, float, float]
    element_count: int
    without_shape_count: int
    opening_count: int
    chunks: tuple[SceneChunk, ...]

    @property
    def byte_count(self) -> int:
        """Bytes of all the chunks together."""
        return sum(len(chunk.glb) for chunk in self.chunks)


def storeys_upwards(records: ModelRecords) -> list[str]:
    """The model's storeys from the lowest elevation up; those without one last, by GlobalId."""
    storeys = {o.global_id for o in records.objects if o.ifc_class_base == STOREY_CLASS}
    return [
        record.global_id
        for record in sorted(
            (r for r in records.spatial_elements if r.global_id in storeys),
            key=lambda r: (r.elevation_m is None, r.elevation_m or 0, r.global_id),
        )
    ]


def scene_objects(raw: RawModel, records: ModelRecords) -> list[SceneObject]:
    """The model's elements and spaces as the transform accepted them, by GlobalId.

    The storeys and the kinds of space come from the records, so the scene places each
    object as the database does: an element by ``element.storey_id``, a space by its
    parent, a copy by ``spatial_element.repeats_id``, an area plan by its category.
    """
    objects = {record.global_id: record for record in records.objects}
    aggregates: dict[str, str | None] = {}
    for element in raw.elements:
        # A GlobalId used twice keeps its first object, as in the transform.
        aggregates.setdefault(element.global_id, element.aggregate_global_id)
    parents = {record.global_id: record.parent_global_id for record in records.spatial_elements}
    storeys = {key for key, record in objects.items() if record.ifc_class_base == STOREY_CLASS}
    scene = [
        SceneObject(
            global_id=element.global_id,
            ifc_class=objects[element.global_id].ifc_class,
            kind="opening" if element.is_feature else "element",
            storey_global_id=element.storey_global_id,
            aggregate_global_id=aggregates.get(element.global_id),
        )
        for element in records.elements
    ]
    for space in records.spatial_elements:
        if objects[space.global_id].ifc_class_base != SPACE_CLASS:
            continue
        kind: ObjectKind = "space"
        if space.revit_category == REVIT_AREAS:
            kind = "area"
        elif space.repeats_global_id is not None:
            kind = "space_copy"
        scene.append(
            SceneObject(
                global_id=space.global_id,
                ifc_class=objects[space.global_id].ifc_class,
                kind=kind,
                storey_global_id=_storey_above(space.parent_global_id, parents, storeys),
            )
        )
    return scene


def _storey_above(
    parent: str | None, parents: Mapping[str, str | None], storeys: set[str]
) -> str | None:
    """The first storey up the spatial hierarchy from ``parent``, if any."""
    seen: set[str] = set()
    while parent is not None and parent not in seen:
        if parent in storeys:
            return parent
        seen.add(parent)
        parent = parents.get(parent)
    return None


def build_scene(
    objects: Sequence[SceneObject],
    meshes: Iterable[RawMesh],
    *,
    storeys: Sequence[str] = (),
    max_elements: int = MAX_CHUNK_ELEMENTS,
    max_bytes: int = MAX_CHUNK_BYTES,
) -> Scene:
    """Build the scene of a model from its objects and their meshes.

    Args:
        objects: The model's elements and spaces (:func:`scene_objects`), in the order
            to write them.
        meshes: Their bodies, in any order. A mesh of no object is left out, and so is
            one with no triangle or with a coordinate that is NaN or infinite: its object
            counts as without shape. ``geometry.triangulate_scene`` gives one mesh per
            GlobalId; of two, the one with the lower STEP id is kept.
        storeys: The storeys in the order to number their chunks
            (:func:`storeys_upwards`). A storey not listed comes after them, by GlobalId,
            and the objects on no storey come last.
        max_elements: The element cap of a chunk.
        max_bytes: The byte cap of a chunk.

    Returns:
        The scene. The same objects and meshes give the same bytes, whatever the order
        the meshes come in.
    """
    by_global_id: dict[str, RawMesh] = {}
    for mesh in sorted(meshes, key=lambda mesh: mesh.step_id):
        if _drawable(mesh):
            by_global_id.setdefault(mesh.global_id, mesh)
    elements = {o.global_id: o for o in objects if o.kind == "element"}
    parts: dict[str, list[SceneObject]] = defaultdict(list)
    for element in elements.values():
        if element.aggregate_global_id in elements:
            parts[str(element.aggregate_global_id)].append(element)

    def node(obj: SceneObject) -> Node | None:
        children = tuple(
            child for child in (node(part) for part in parts[obj.global_id]) if child is not None
        )
        mesh = by_global_id.get(obj.global_id)
        if children:
            return Node(obj.global_id, obj.ifc_class, None, children)
        return Node(obj.global_id, obj.ifc_class, mesh) if mesh is not None else None

    groups: dict[tuple[ChunkKind, str | None], list[Node]] = defaultdict(list)
    for obj in objects:
        if obj.kind == "opening" or obj.aggregate_global_id in elements:
            continue
        drawn = node(obj) if obj.kind == "element" else _space(obj, by_global_id)
        if drawn is not None:
            groups[(CHUNK_KINDS[obj.kind], obj.storey_global_id)].append(drawn)

    element_nodes = [n for (kind, _), nodes in groups.items() if kind == "elements" for n in nodes]
    space_nodes = [n for (kind, _), nodes in groups.items() if kind != "elements" for n in nodes]
    origin = _origin(element_nodes) or _origin(space_nodes) or (0.0, 0.0, 0.0)
    element_count = sum(_count(n) for n in element_nodes)

    chunks: list[SceneChunk] = []
    for kind, storey in sorted(groups, key=_chunk_order(storeys)):
        for batch in _batches(groups[(kind, storey)], max_elements):
            chunks.extend(_split(batch, storey, kind, origin, max_bytes))
    return Scene(
        origin=origin,
        element_count=element_count,
        without_shape_count=len(elements) - element_count,
        opening_count=sum(1 for obj in objects if obj.kind == "opening"),
        chunks=tuple(chunks),
    )


def _drawable(mesh: RawMesh) -> bool:
    """Whether a body can be drawn: it has a triangle, and every vertex is finite.

    A NaN or infinite coordinate cannot be rounded into the origin, nor written as JSON
    in the accessor's bounds, so one such vertex would fail the whole model's scene.
    """
    vertices = np.frombuffer(mesh.vertices, dtype=np.float64)
    return bool(mesh.faces) and bool(np.isfinite(vertices).all())


def _space(obj: SceneObject, meshes: Mapping[str, RawMesh]) -> Node | None:
    mesh = meshes.get(obj.global_id)
    return Node(obj.global_id, obj.ifc_class, mesh) if mesh is not None else None


def _count(node: Node) -> int:
    """The objects a node draws: itself and its parts."""
    return 1 + sum(_count(child) for child in node.children)


def _meshes(nodes: Iterable[Node]) -> Iterable[RawMesh]:
    for node in nodes:
        if node.mesh is not None:
            yield node.mesh
        yield from _meshes(node.children)


def _origin(nodes: Sequence[Node]) -> tuple[float, float, float] | None:
    """The centre of the nodes' bounding box, rounded to whole metres; ``None`` if empty."""
    boxes = [
        (points.min(axis=0), points.max(axis=0))
        for points in (
            np.frombuffer(mesh.vertices, dtype=np.float64).reshape(-1, 3) for mesh in _meshes(nodes)
        )
        if len(points)
    ]
    if not boxes:
        return None
    low = np.min([box[0] for box in boxes], axis=0)
    high = np.max([box[1] for box in boxes], axis=0)
    x, y, z = (float(round(value)) for value in (low + high) / 2)
    return x, y, z


def _chunk_order(
    storeys: Sequence[str],
) -> Callable[[tuple[ChunkKind, str | None]], tuple[int, int, int, str]]:
    """Order chunks by kind; then the storeys listed, those not listed, and no storey."""
    position = {storey: index for index, storey in enumerate(storeys)}
    kinds = list(CHUNK_KINDS.values())

    def key(group: tuple[ChunkKind, str | None]) -> tuple[int, int, int, str]:
        kind, storey = group
        if storey is None:
            return kinds.index(kind), 2, 0, ""
        if storey in position:
            return kinds.index(kind), 0, position[storey], ""
        return kinds.index(kind), 1, 0, storey

    return key


def _batches(nodes: Sequence[Node], max_elements: int) -> Iterable[list[Node]]:
    """Cut a storey's nodes, in order, into runs of at most ``max_elements`` objects."""
    batch: list[Node] = []
    count = 0
    for node in nodes:
        size = _count(node)
        if batch and count + size > max_elements:
            yield batch
            batch, count = [], 0
        batch.append(node)
        count += size
    # Each storey has at least one node, so there is always a last batch.
    yield batch


def _split(
    nodes: list[Node],
    storey: str | None,
    kind: ChunkKind,
    origin: tuple[float, float, float],
    max_bytes: int,
) -> list[SceneChunk]:
    """One chunk of ``nodes``, or halves of it, recursively, while it is above the byte cap."""
    glb = write_glb(nodes, origin)
    if len(glb) > max_bytes and len(nodes) > 1:
        half = len(nodes) // 2
        return [
            *_split(nodes[:half], storey, kind, origin, max_bytes),
            *_split(nodes[half:], storey, kind, origin, max_bytes),
        ]
    return [SceneChunk(storey, kind, sum(_count(node) for node in nodes), glb)]
