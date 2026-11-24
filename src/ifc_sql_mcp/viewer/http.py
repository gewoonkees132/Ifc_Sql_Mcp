"""``ifc-sql view``'s web app: the page, its files and the scenes (ADR 14, decision 2).

:func:`create_app` returns the app; ``cli.py`` binds the port and runs it with uvicorn.

======================================================  =========================================
Route                                                   Returns
======================================================  =========================================
``GET /``                                               The page, ``index.html``. No token.
``GET /static/{path}``                                  The page's own files, no others. No token.
``GET /api/models``                                     ``tools.list_models``, each model's scene,
                                                        its projects, and the set shown first.
``GET /api/models/{id}/scene``                          The scene's manifest: its origin, counts
                                                        and chunks.
``GET /api/models/{id}/scene/{chunk_no}.glb``           One chunk, as ``model/gltf-binary``.
``GET /api/models/{id}/objects/{global_id}``            ``tools.get_element``, clipped as usual.
``GET /api/feed``                                       The feed: its status and the calls kept.
``GET /api/events``                                     The same as server-sent events, as they
                                                        arrive; ``Last-Event-ID`` resumes.
======================================================  =========================================

Every ``/api/`` route reads through ``engine``, which connects as ``ifc_reader``, in a
:func:`~ifc_sql_mcp.db.engine.reader_transaction` with the MCP server's limits. The work
runs in a thread, through one :class:`~ifc_sql_mcp.viewer.reading.Reader` with the
feed's selections: at most :data:`~ifc_sql_mcp.viewer.reading.SESSIONS` sessions at once,
since ``ifc_reader`` allows 20, shared with every MCP call on the cluster. No route runs
SQL that a request or an event brought; ids arrive as path parameters and reach SQL as
bind parameters. The feed's routes read only memory: the calls the listener received.

The page's files are read once, when the app is made, and served from memory with their
media types named here: Windows can map ``.js`` to ``text/plain`` in its registry, which
``nosniff`` would make the browser refuse.
"""

import hashlib
import logging
import re
from collections.abc import AsyncIterator, Callable, Container, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import anyio
import anyio.to_thread
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, RowMapping, text
from sqlalchemy.exc import DBAPIError
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ifc_sql_mcp.db.engine import describe_error
from ifc_sql_mcp.db.schema import GLOBAL_ID_PATTERN
from ifc_sql_mcp.server import tools
from ifc_sql_mcp.server.app import Limits
from ifc_sql_mcp.server.tools import IfcText
from ifc_sql_mcp.viewer.feed import Entry, Feed, Status
from ifc_sql_mcp.viewer.reading import SESSIONS, Reader
from ifc_sql_mcp.viewer.security import EVENTS_PATH, Security, content_security_policy

logger = logging.getLogger(__name__)

STATIC = Path(__file__).with_name("static")
"""The page and its files, three.js under ``vendor/three/``."""

PAGE = "index.html"

MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",  # the explainer's still of the Duplex
    ".woff2": "font/woff2",  # Nunito, vendored
    "": "text/plain; charset=utf-8",  # the vendored licences and the SHA256SUMS beside them
}
"""The files of ``STATIC`` that are served, by suffix; any other is not."""

GLB_MEDIA_TYPE = "model/gltf-binary"

POLL_SECONDS = 0.2
"""How often an open event stream looks for new calls in the feed."""

PING_SECONDS = 15
"""A comment line on an idle stream this often, so that nothing between closes it."""

NO_FEED = Status(
    state="off",
    database=None,
    since=None,
    message="this view has no feed",
    received=0,
    dropped={},
    demo=False,
)

_GLOBAL_ID = re.compile(GLOBAL_ID_PATTERN)
_PLAIN_FILE_NAME = re.compile(r"[\w.\-]+")


class NotFoundError(LookupError):
    """What a route asked for does not exist; the message says what to do instead."""


# --------------------------------------------------------------------------- results


class SceneSummary(BaseModel):
    """What a model's scene draws, for the page's list of models."""

    format_version: int
    built_at: datetime
    element_count: int = Field(description="Elements drawn, aggregates drawn by their parts too.")
    without_shape_count: int
    opening_count: int = Field(description="Feature elements, never drawn.")
    chunk_count: int
    byte_count: int
    summary: str = Field(
        description='e.g. "268 elements: 218 drawn, 0 without shape, 50 openings not drawn".'
    )


class ViewerModel(tools.ModelSummary):
    """A model as ``list_models`` describes it, with its scene."""

    scene: SceneSummary | None = Field(description="Null: the model has no scene.")
    scene_hint: str | None = Field(description="Without a scene: the command that builds one.")


class ModelSet(BaseModel):
    """Models the page shows together: a project's, or those ``--model`` named."""

    label: IfcText
    project_group: IfcText | None
    model_ids: list[int] = Field(description="Oldest first; the first one's origin is 0, 0, 0.")


class Catalogue(BaseModel):
    """``GET /api/models``: every model, the projects they make, and the set shown first."""

    models: list[ViewerModel] = Field(description="Every model, replaced ones too, oldest first.")
    truncated: bool
    projects: list[ModelSet] = Field(
        description="The current models by project, the newest model's with a scene first."
    )
    shown: ModelSet


class FeedSnapshot(BaseModel):
    """``GET /api/feed``: the listener's status and the calls kept, oldest first."""

    status: Status
    entries: list[Entry]


class Chunk(BaseModel):
    """One chunk of a scene, as the manifest lists it."""

    chunk_no: int
    kind: str = Field(description="elements, spaces, space_copies or areas.")
    storey_id: int | None
    storey_name: IfcText | None
    storey_elevation_m: float | None
    element_count: int
    byte_count: int
    sha256: str = Field(description="The chunk's ETag.")


class Manifest(BaseModel):
    """``GET /api/models/{id}/scene``: what the page needs to load a model's scene."""

    model_id: int
    discipline: str
    format_version: int
    built_at: datetime
    origin_m: list[float] = Field(
        description="x, y, z of the scene's origin in the file's world coordinates (Z up), "
        "in metres. The chunks' positions are relative to it, Y up."
    )
    element_count: int
    without_shape_count: int
    opening_count: int
    byte_count: int
    summary: str
    chunks: list[Chunk]


# --------------------------------------------------------------------------- pure rules


def scene_summary(element_count: int, without_shape_count: int, opening_count: int) -> str:
    """The scene's counts in one line; together they are the model's elements."""
    total = element_count + without_shape_count + opening_count
    return (
        f"{total:,} elements: {element_count:,} drawn, {without_shape_count:,} without shape, "
        f"{opening_count:,} openings not drawn"
    )


def scene_hint(file_name: str, discipline: str) -> str:
    """The command that builds the scene of a model loaded without ``--viewer``."""
    name = file_name if _PLAIN_FILE_NAME.fullmatch(file_name) else f'"{file_name}"'
    return f"run ifc-sql ingest {name} --discipline {discipline} --viewer"


def projects(models: Sequence[tools.ModelSummary], scenes: Container[int]) -> list[ModelSet]:
    """The current models by project, the set of the newest model with a scene first.

    Models of one ``project_group`` go together, oldest first, as the page shows them; a
    model of no project is a set of its own, since ``list_models`` says never to put two
    of those together. Sets with a scene come first, the one whose newest scene is newest
    leading; then the others, the newest model's first.
    """
    sets: dict[tuple[str, str | int], ModelSet] = {}
    for model in models:
        if model.replaced_by_model_id is not None:
            continue
        if model.project_group is None:
            key: tuple[str, str | int] = ("model", model.model_id)
            label = f"{model.file_name} (model {model.model_id})"
        else:
            key, label = ("project", model.project_group), model.project_group
        entry = sets.setdefault(
            key, ModelSet(label=label, project_group=model.project_group, model_ids=[])
        )
        entry.model_ids.append(model.model_id)

    def newest(entry: ModelSet) -> tuple[int, int]:
        drawn = [model_id for model_id in entry.model_ids if model_id in scenes]
        return max(drawn, default=0), max(entry.model_ids)

    return sorted(sets.values(), key=newest, reverse=True)


def shown(
    sets: Sequence[ModelSet],
    models: Sequence[tools.ModelSummary],
    *,
    project: str | None,
    model_ids: Sequence[int],
) -> ModelSet:
    """The set the page shows first: ``--model``'s models, ``--project``'s, or the first set.

    ``ifc-sql view`` checks both options when it starts; a model deleted since is left out
    here, and a project left with no current model shows nothing.
    """
    if model_ids:
        known = {model.model_id for model in models}
        named = [model_id for model_id in dict.fromkeys(model_ids) if model_id in known]
        label = "models " + ", ".join(str(model_id) for model_id in named)
        return ModelSet(label=label, project_group=None, model_ids=named)
    if project is not None:
        chosen = [entry for entry in sets if entry.project_group == project]
        return chosen[0] if chosen else ModelSet(label=project, project_group=project, model_ids=[])
    if sets:
        return sets[0]
    return ModelSet(label="no models are loaded", project_group=None, model_ids=[])


# --------------------------------------------------------------------------- the app


@dataclass(frozen=True, slots=True)
class PageFile:
    """One file of the page, held in memory."""

    body: bytes
    media_type: str
    etag: str

    @classmethod
    def of(cls, body: bytes, media_type: str) -> "PageFile":
        """The file of ``body``, its SHA-256 as its ``ETag``."""
        return cls(body, media_type, f'"{hashlib.sha256(body).hexdigest()}"')


def page_text() -> str:
    """The page, ``index.html``, with LF line endings.

    The browser hashes the import map after turning CRLF into LF, as git on Windows may
    have written the file; so the page is served, and hashed, with LF.
    """
    return (STATIC / PAGE).read_text(encoding="utf-8").replace("\r\n", "\n")


def page_files(folder: Path) -> dict[str, PageFile]:
    """The page's files under ``folder``, by their path below it, the page itself apart.

    What ``view`` serves under ``/static/``, and the static demo copies.
    """
    files: dict[str, PageFile] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix in MEDIA_TYPES and path.name != PAGE:
            relative = path.relative_to(folder).as_posix()
            files[relative] = PageFile.of(path.read_bytes(), MEDIA_TYPES[path.suffix])
    return files


def _matches(request: Request) -> list[str]:
    """The entity tags of ``If-None-Match``, without quotes or the weak prefix."""
    header = request.headers.get("if-none-match", "")
    return [tag.strip().removeprefix("W/").strip('"') for tag in header.split(",") if tag.strip()]


def _json(result: BaseModel) -> Response:
    return Response(
        result.model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _error(status: int, message: str) -> Response:
    return JSONResponse({"error": message}, status, headers={"Cache-Control": "no-store"})


MODEL = """
SELECT m.model_id, m.file_name, m.discipline, s.format_version, s.built_at,
       s.origin_x_m, s.origin_y_m, s.origin_z_m, s.element_count, s.without_shape_count,
       s.opening_count, s.byte_count,
       (SELECT count(*) FROM ifc_viewer.scene_chunk AS c WHERE c.model_id = m.model_id)
           AS chunk_count
FROM ifc.model AS m LEFT JOIN ifc_viewer.scene AS s ON s.model_id = m.model_id
WHERE m.model_id = :model_id
"""

SCENES = """
SELECT s.model_id, s.format_version, s.built_at, s.element_count, s.without_shape_count,
       s.opening_count, s.byte_count, count(c.chunk_no) AS chunk_count
FROM ifc_viewer.scene AS s
LEFT JOIN ifc_viewer.scene_chunk AS c ON c.model_id = s.model_id
GROUP BY s.model_id
"""

CHUNKS = """
SELECT c.chunk_no, c.kind, c.storey_id, o.name AS storey_name,
       s.elevation_m AS storey_elevation_m, c.element_count,
       octet_length(c.glb) AS byte_count, c.glb_sha256 AS sha256
FROM ifc_viewer.scene_chunk AS c
LEFT JOIN ifc.object AS o ON o.object_id = c.storey_id
LEFT JOIN ifc.spatial_element AS s ON s.object_id = c.storey_id
WHERE c.model_id = :model_id
ORDER BY c.chunk_no
"""

# The bytes are left out when the browser already holds them: a reload then reads only the
# hash, and answers 304.
CHUNK = """
SELECT glb_sha256,
       CASE WHEN glb_sha256 = ANY(CAST(:etags AS text[])) THEN NULL ELSE glb END AS glb
FROM ifc_viewer.scene_chunk
WHERE model_id = :model_id AND chunk_no = :chunk_no
"""


class _Viewer:
    """The routes, with what they share: the engine, the limits and the page's files."""

    def __init__(
        self,
        reader: Reader,
        page: str,
        *,
        project: str | None,
        model_ids: Sequence[int],
        feed: Feed | None,
    ) -> None:
        self.reader = reader
        self.feed = feed
        self.project = project
        self.model_ids = tuple(model_ids)
        self.limiter = anyio.CapacityLimiter(SESSIONS)
        self.page = PageFile.of(page.encode("utf-8"), "text/html; charset=utf-8")
        self.files = page_files(STATIC)

    # ----------------------------------------------------------------- the page

    async def index(self, request: Request) -> Response:
        return self._file(request, self.page)

    async def static(self, request: Request) -> Response:
        found = self.files.get(request.path_params["path"])
        if found is None:
            return _error(404, "the page has no such file")
        return self._file(request, found)

    @staticmethod
    def _file(request: Request, file: PageFile) -> Response:
        headers = {"ETag": file.etag, "Cache-Control": "no-cache"}
        if file.etag.strip('"') in _matches(request):
            return Response(status_code=304, headers=headers)
        return Response(file.body, media_type=file.media_type, headers=headers)

    # ----------------------------------------------------------------- the data

    async def models(self, request: Request) -> Response:
        return await self._read(
            lambda connection: _json(
                catalogue(connection, project=self.project, model_ids=self.model_ids)
            )
        )

    async def scene(self, request: Request) -> Response:
        model_id: int = request.path_params["model_id"]
        return await self._read(lambda connection: _json(manifest(connection, model_id)))

    async def chunk(self, request: Request) -> Response:
        model_id: int = request.path_params["model_id"]
        chunk_no: int = request.path_params["chunk_no"]
        etags = _matches(request)

        def work(connection: Connection) -> Response:
            row = connection.execute(
                text(CHUNK), {"model_id": model_id, "chunk_no": chunk_no, "etags": etags}
            ).one_or_none()
            if row is None:
                count = scene_row(connection, model_id)["chunk_count"]
                raise NotFoundError(
                    f"model {model_id}'s scene has no chunk {chunk_no}: it has {count} chunks, "
                    f"numbered from 0"
                )
            headers = {"ETag": f'"{row.glb_sha256}"', "Cache-Control": "private, no-cache"}
            if row.glb is None:
                return Response(status_code=304, headers=headers)
            return Response(bytes(row.glb), media_type=GLB_MEDIA_TYPE, headers=headers)

        return await self._read(work)

    async def element(self, request: Request) -> Response:
        model_id: int = request.path_params["model_id"]
        global_id: str = request.path_params["global_id"]
        if not _GLOBAL_ID.fullmatch(global_id):
            return _error(404, "not an IFC GlobalId: 22 characters of 0-9, A-Z, a-z, _ and $")

        def work(connection: Connection) -> Response:
            model_row(connection, model_id)
            detail = tools.get_element(
                connection, object_id=None, model_id=model_id, global_id=global_id
            )
            return _json(detail)

        return await self._read(work)

    # ----------------------------------------------------------------- the feed

    async def feed_snapshot(self, request: Request) -> Response:
        if self.feed is None:
            return _json(FeedSnapshot(status=NO_FEED, entries=[]))
        return _json(FeedSnapshot(status=self.feed.status(), entries=self.feed.entries()))

    async def events(self, request: Request) -> Response:
        """The feed as server-sent events: ``status`` when it changes, ``call`` per call.

        A stream begins with the status and every call kept, or, when the browser comes
        back with ``Last-Event-ID``, with the calls after that one.
        """
        last = request.headers.get("last-event-id", "")
        after = int(last) if last.isdecimal() and len(last) < 10 else 0
        return EventSourceResponse(self._stream(after), ping=PING_SECONDS)

    async def _stream(self, after: int) -> AsyncIterator[ServerSentEvent]:
        sent: dict[str, object] | None = None
        while True:
            status = NO_FEED if self.feed is None else self.feed.status()
            # A status when the page has something new to say, not with every call's count.
            shown = status.model_dump(exclude={"received"})
            if shown != sent:
                yield ServerSentEvent(status.model_dump_json(), event="status")
                sent = shown
            for entry in [] if self.feed is None else self.feed.entries(after):
                yield ServerSentEvent(entry.model_dump_json(), event="call", id=str(entry.id))
                after = entry.id
            if self.feed is None or self.feed.closed:
                return
            await anyio.sleep(POLL_SECONDS)

    # ----------------------------------------------------------------- reading

    async def _read(self, work: Callable[[Connection], Response]) -> Response:
        """Run ``work`` in a thread, in a new read-only transaction, as ``ifc_reader``."""
        return await anyio.to_thread.run_sync(self._transaction, work, limiter=self.limiter)

    def _transaction(self, work: Callable[[Connection], Response]) -> Response:
        try:
            return self.reader.read(work)
        except (NotFoundError, tools.ToolInputError) as exc:
            return _error(404, str(exc))
        except DBAPIError as exc:
            logger.warning("viewer request failed", extra={"error": describe_error(exc)})
            return _error(503, "the database did not answer; the log of ifc-sql view says why")


def catalogue(
    connection: Connection,
    *,
    project: str | None = None,
    model_ids: Sequence[int] = (),
    only: Container[int] | None = None,
) -> Catalogue:
    """``GET /api/models``: every model with its scene, the projects, and the set shown first.

    Args:
        connection: A read-only transaction.
        project: The project shown first, from ``--project``.
        model_ids: The models shown first, from ``--model``; before ``project``.
        only: List these models alone, as if no other were loaded: the static demo's.
    """
    listed = tools.list_models(connection, include_replaced=True)
    summaries = [model for model in listed.models if only is None or model.model_id in only]
    scenes = {row["model_id"]: row for row in connection.execute(text(SCENES)).mappings()}
    models = [
        ViewerModel.model_validate(
            {
                **model.model_dump(),
                "scene": _summary(scenes.get(model.model_id)),
                "scene_hint": None
                if model.model_id in scenes
                else scene_hint(model.file_name, model.discipline),
            }
        )
        for model in summaries
    ]
    sets = projects(summaries, scenes)
    return Catalogue(
        models=models,
        truncated=listed.truncated and only is None,
        projects=sets,
        shown=shown(sets, summaries, project=project, model_ids=model_ids),
    )


def manifest(connection: Connection, model_id: int) -> Manifest:
    """``GET /api/models/{id}/scene``: the scene's origin, counts and chunks.

    Raises:
        NotFoundError: The model does not exist, or has no scene.
    """
    row = scene_row(connection, model_id)
    chunks = connection.execute(text(CHUNKS), {"model_id": model_id}).mappings()
    counts = row["element_count"], row["without_shape_count"], row["opening_count"]
    return Manifest.model_validate(
        {
            **row,
            "origin_m": [row["origin_x_m"], row["origin_y_m"], row["origin_z_m"]],
            "summary": scene_summary(*counts),
            "chunks": [dict(chunk) for chunk in chunks],
        }
    )


def model_row(connection: Connection, model_id: int) -> RowMapping:
    """The model and its scene's row, if any.

    Raises:
        NotFoundError: The model does not exist.
    """
    row = connection.execute(text(MODEL), {"model_id": model_id}).mappings().one_or_none()
    if row is None:
        raise NotFoundError(f"no model with id {model_id}: reload the page for those loaded")
    return row


def scene_row(connection: Connection, model_id: int) -> RowMapping:
    """The model and its scene's row.

    Raises:
        NotFoundError: The model does not exist, or has no scene; the message says how to
            build one.
    """
    row = model_row(connection, model_id)
    if row["format_version"] is None:
        hint = scene_hint(row["file_name"], row["discipline"])
        raise NotFoundError(f"model {model_id} has no scene: {hint}")
    return row


def _summary(row: RowMapping | None) -> SceneSummary | None:
    if row is None:
        return None
    counts = row["element_count"], row["without_shape_count"], row["opening_count"]
    return SceneSummary.model_validate({**row, "summary": scene_summary(*counts)})


def create_app(
    engine: Engine,
    token: str,
    *,
    port: int,
    project: str | None = None,
    model_ids: Sequence[int] = (),
    limits: Limits | None = None,
    feed: Feed | None = None,
    reader: Reader | None = None,
) -> Security:
    """Return the viewer's app, guarded by :class:`~ifc_sql_mcp.viewer.security.Security`.

    Args:
        engine: The engine to read through, from ``make_reader_engine``: ``ifc_reader``.
        token: The token every ``/api/`` request must send.
        port: The port the app is served on, which a request's Host must name.
        project: The project the page shows first, from ``--project``.
        model_ids: The models the page shows first, from ``--model``; before ``project``.
        limits: The limits of each request's transaction; by default the MCP server's.
        feed: The calls the listener receives; without one the page says it has none.
        reader: How to read, shared with ``feed``'s selections so that both together
            hold at most three sessions; by default a new one on ``engine``.

    Returns:
        The ASGI app. Its ``app`` is the Starlette app, whose ``routes`` are the table
        above.
    """
    page = page_text()
    viewer = _Viewer(
        reader or Reader(engine, limits),
        page,
        project=project,
        model_ids=model_ids,
        feed=feed,
    )
    routes = [
        Route("/", viewer.index, methods=["GET"]),
        Route("/static/{path:path}", viewer.static, methods=["GET"]),
        Route("/api/models", viewer.models, methods=["GET"]),
        Route("/api/models/{model_id:int}/scene", viewer.scene, methods=["GET"]),
        Route("/api/models/{model_id:int}/scene/{chunk_no:int}.glb", viewer.chunk, methods=["GET"]),
        Route("/api/models/{model_id:int}/objects/{global_id}", viewer.element, methods=["GET"]),
        Route("/api/feed", viewer.feed_snapshot, methods=["GET"]),
        Route(EVENTS_PATH, viewer.events, methods=["GET"]),
    ]
    return Security(
        Starlette(routes=routes),
        token=token,
        port=port,
        policy=content_security_policy(page),
    )
