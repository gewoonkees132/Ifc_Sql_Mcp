"""``ifc-sql demo --export``: the scripted demo as a static site (ADR 14, decision 7).

The demo plays as with ``--check``. When every step is as the script says, :func:`export`
writes the page and everything it reads into a folder, which any static server can
serve: the page's ``api.js`` reads these files in place of ``ifc-sql view``'s routes, and
plays the calls back a pause apart.

==========================================  ============================================
File                                        Holds
==========================================  ============================================
``index.html``                              The page, marked static, its content security
                                            policy in a ``<meta>`` tag.
``static/…``                                The page's own files and three.js, as ``view``
                                            serves them under ``/static/``.
``data/models.json``                        ``GET /api/models``, as if the script's models
                                            alone were loaded.
``data/scenes/{model_id}.json``             ``GET /api/models/{id}/scene``: a manifest.
``data/scenes/{model_id}/{chunk_no}.glb``   A chunk.
``data/events.json``                        The script's calls, as the feed resolved them,
                                            and how to play them back: a
                                            :class:`Recording`.
``data/elements.json``                      Every object a scene can draw: its class, name
                                            and storey, for a click (:class:`Elements`).
``data/details.json``                       ``get_element`` of each object a call of the
                                            script looked up (:class:`Details`).
==========================================  ============================================

Every value from an IFC file goes into a JSON value: the files are named by model id and
chunk number, and the page is the one ``view`` serves, with three tags of ours. Only a
script whose files' licence allows a copy is exported, the Duplex
(:attr:`~ifc_sql_mcp.viewer.demo_script.Script.credit`), and only its models, whatever
else the database holds.
"""

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from ifc_sql_mcp import __version__
from ifc_sql_mcp.server import tools
from ifc_sql_mcp.server.tools import IfcText
from ifc_sql_mcp.viewer.demo import Played
from ifc_sql_mcp.viewer.demo_script import Credit, Script
from ifc_sql_mcp.viewer.feed import Entry, Status, demo_entry
from ifc_sql_mcp.viewer.http import (
    STATIC,
    Catalogue,
    Manifest,
    catalogue,
    manifest,
    page_files,
    page_text,
)
from ifc_sql_mcp.viewer.reading import Reader
from ifc_sql_mcp.viewer.security import content_security_policy

MODELS = "data/models.json"
SCENE = "data/scenes/{model_id}.json"
CHUNK = "data/scenes/{model_id}/{chunk_no}.glb"
EVENTS = "data/events.json"
ELEMENTS = "data/elements.json"
DETAILS = "data/details.json"
LAYOUT = (MODELS, SCENE, CHUNK, EVENTS, ELEMENTS, DETAILS)
"""The data files, as ``api.js``'s static source asks for them."""

STATIC_MARK = ('<html lang="en">', '<html lang="en" data-source="static">')
"""How the page knows it is the static copy: ``main.js`` reads ``data-source``."""

_CHARSET = '<meta charset="utf-8">'
_TITLE = "<title>ifc-sql view</title>"

BRIEF_NOTE = (
    "This copy of the demo holds the class, name and storey of every element, and "
    "everything stored only for the objects its questions looked up. Run the demo on your "
    "own computer (make demo) to see everything about any element."
)
"""What the drawer says of an object the static copy holds no details of."""

# The objects a scene can draw: elements, openings aside, and spaces. Their storey as
# get_element gives it.
OBJECTS = f"""
SELECT d.global_id, d.ifc_class, d.name, d.storey_name
FROM ({tools.OBJECT_DETAIL}
      WHERE o.model_id = :model_id
        AND (o.object_kind = 'element' AND NOT coalesce(e.is_feature, false)
             OR o.ifc_class_base = 'IfcSpace')) AS d
ORDER BY d.global_id
"""  # noqa: S608 - put together from the constant query of get_element

GLB = """
SELECT glb FROM ifc_viewer.scene_chunk WHERE model_id = :model_id AND chunk_no = :chunk_no
"""


class ExportError(Exception):
    """The demo cannot be exported; the message says why."""


class Recording(BaseModel):
    """``data/events.json``: the script's calls, to play back."""

    title: str = Field(description="The script's title.")
    status: Status = Field(description="The feed as the page shows it: recorded.")
    entries: list[Entry] = Field(description="The script's calls, as the feed resolved them.")
    pause_seconds: float = Field(description="Seconds between two calls played back.")
    credit: Credit = Field(description="What the page says of the files, as their licence asks.")


class Brief(BaseModel):
    """One object as a click on the static page names it."""

    ifc_class: str
    name: IfcText | None
    storey_name: IfcText | None


class BriefModel(BaseModel):
    """The objects of one model's scene."""

    discipline: str
    objects: dict[str, Brief] = Field(description="By GlobalId.")


class Elements(BaseModel):
    """``data/elements.json``: every object a scene can draw, by model id and GlobalId."""

    note: str = Field(description="What the drawer says of an object without details.")
    models: dict[int, BriefModel]


class Details(BaseModel):
    """``data/details.json``: ``get_element`` of the objects the calls looked up."""

    models: dict[int, dict[str, tools.ElementDetail]] = Field(
        description="By model id, then GlobalId: GlobalIds are unique per model only."
    )


@dataclass(frozen=True, slots=True)
class Exported:
    """What :func:`export` wrote.

    Attributes:
        folder: Where.
        files: How many files.
        bytes: Their bytes, all together.
    """

    folder: Path
    files: int
    bytes: int


def check_folder(folder: Path) -> None:
    """Refuse a folder that holds anything: an export never overwrites or deletes a file.

    Raises:
        ExportError: ``folder`` is a file, or a folder that is not empty.
    """
    if folder.is_file():
        raise ExportError(f"{folder} is a file: --export needs a new or empty folder")
    if folder.is_dir() and any(folder.iterdir()):
        raise ExportError(
            f"{folder} is not empty: --export writes only into a new or empty folder, so "
            "that no file of an older export stays behind"
        )


def check_script(script: Script) -> Credit:
    """The script's credit.

    Raises:
        ExportError: The script has none: its files may not be published.
    """
    if script.credit is None:
        raise ExportError(
            f"the {script.name} script's files stay on this computer: their licences do not "
            "allow a copy (DATA_ATTRIBUTION.md); --export takes --script duplex"
        )
    return script.credit


def static_page(page: str, title: str) -> str:
    """The page as the static copy serves it: marked static, its policy in a ``<meta>`` tag.

    No server of ours serves the copy, so the policy that ``view`` sends as a header goes
    into the page, without ``frame-ancestors``, which browsers ignore there; and so does
    ``no-referrer``.

    Args:
        page: ``index.html``, with LF line endings.
        title: The page's title: the script's.

    Raises:
        ValueError: The page lacks a tag this function rewrites.
    """
    policy = html.escape(content_security_policy(page, meta=True))
    tags = (
        f"{_CHARSET}\n"
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n'
        '<meta name="referrer" content="no-referrer">'
    )
    page = _once(page, *STATIC_MARK)
    page = _once(page, _TITLE, f"<title>{html.escape(title)} · ifc-sql demo</title>")
    return _once(page, _CHARSET, tags)


def recording(
    script: Script,
    played: Sequence[Played],
    *,
    pause_seconds: float,
    recorded_at: datetime,
) -> Recording:
    """The script's calls as the feed resolved them, and the status the page shows."""
    # The first call arrives before the demo knows its serve, and is labelled after.
    entries = [demo_entry(item.entry) for item in played if item.entry is not None]
    when = recorded_at.astimezone(UTC)
    message = (
        f"Recorded on {when:%Y-%m-%d %H:%M} UTC by ifc-sql demo --export (ifc-sql-mcp "
        f"{__version__}), and played back from this copy's files, one call every "
        f"{pause_seconds:g} s."
    )
    status = Status(
        state="recorded",
        database=None,
        since=when.isoformat(timespec="milliseconds"),
        message=message,
        received=len(entries),
        dropped={},
        demo=True,
    )
    return Recording(
        title=script.title,
        status=status,
        entries=entries,
        pause_seconds=pause_seconds,
        credit=check_script(script),
    )


def looked_up(entries: Sequence[Entry]) -> list[tuple[int, str]]:
    """The objects of the ``get_element`` calls among ``entries``, as (model id, GlobalId)."""
    found: dict[tuple[int, str], None] = {}
    for entry in entries:
        if entry.tool != "get_element" or entry.selection is None:
            continue
        for group in entry.selection.groups:
            for model in group.models:
                found.update(dict.fromkeys((model.model_id, gid) for gid in model.global_ids))
    return list(found)


def export(
    reader: Reader,
    script: Script,
    played: Sequence[Played],
    models: Mapping[str, int],
    folder: Path,
    *,
    pause_seconds: float,
    recorded_at: datetime | None = None,
) -> Exported:
    """Write the static copy of ``script``'s demo, played as ``played``, into ``folder``.

    Args:
        reader: How to read: as ``ifc_reader``, read-only.
        script: The script played. It must have a credit.
        played: How each step played: every step as the script says.
        models: The model of each of the script's files.
        folder: A new or empty folder.
        pause_seconds: Seconds between two calls played back.
        recorded_at: When the demo played; now by default.

    Raises:
        ExportError: The script may not be published, the folder is not empty, a step
            differs from the script, or a model has no scene.
    """
    check_folder(folder)
    differing = [item.index for item in played if item.differences]
    if differing:
        raise ExportError(f"steps {differing} differ from the script: nothing exported")
    recorded = recording(
        script,
        played,
        pause_seconds=pause_seconds,
        recorded_at=recorded_at or datetime.now(UTC),
    )
    ids = list(dict.fromkeys(models[sample.key] for sample in script.samples))
    data = reader.read(
        lambda connection: _read(connection, ids, script.first_models(models), recorded.entries)
    )
    writer = _Writer(folder)
    writer.text("index.html", static_page(page_text(), script.title))
    for path, file in page_files(STATIC).items():
        writer.write(f"static/{path}", file.body)
    writer.text(MODELS, data.catalogue.model_dump_json())
    for scene in data.manifests:
        writer.text(SCENE.format(model_id=scene.model_id), scene.model_dump_json())
    for (model_id, chunk_no), glb in data.chunks.items():
        writer.write(CHUNK.format(model_id=model_id, chunk_no=chunk_no), glb)
    writer.text(EVENTS, recorded.model_dump_json())
    writer.text(ELEMENTS, data.elements.model_dump_json())
    writer.text(DETAILS, data.details.model_dump_json())
    return Exported(folder=folder, files=writer.files, bytes=writer.bytes)


@dataclass(frozen=True, slots=True)
class _Data:
    catalogue: Catalogue
    manifests: list[Manifest]
    chunks: dict[tuple[int, int], bytes]
    elements: Elements
    details: Details


def _read(
    connection: Connection, ids: list[int], shown: list[int], entries: Sequence[Entry]
) -> _Data:
    """Everything the copy holds of the database, in one read-only transaction."""
    listed = catalogue(connection, model_ids=shown, only=set(ids))
    without = [model.file_name for model in listed.models if model.scene is None]
    if without:
        raise ExportError(
            f"{', '.join(without)} has no scene: load it with --viewer (scripts/demo.py does)"
        )
    manifests = [manifest(connection, model_id) for model_id in ids]
    chunks = {
        (scene.model_id, chunk.chunk_no): bytes(
            connection.execute(
                text(GLB), {"model_id": scene.model_id, "chunk_no": chunk.chunk_no}
            ).scalar_one()
        )
        for scene in manifests
        for chunk in scene.chunks
    }
    elements = Elements(
        note=BRIEF_NOTE,
        models={
            scene.model_id: BriefModel(
                discipline=scene.discipline,
                objects={
                    row["global_id"]: Brief.model_validate(dict(row))
                    for row in connection.execute(
                        text(OBJECTS), {"model_id": scene.model_id}
                    ).mappings()
                },
            )
            for scene in manifests
        },
    )
    details: dict[int, dict[str, tools.ElementDetail]] = {}
    for model_id, global_id in looked_up(entries):
        details.setdefault(model_id, {})[global_id] = tools.get_element(
            connection, object_id=None, model_id=model_id, global_id=global_id
        )
    return _Data(listed, manifests, chunks, elements, Details(models=details))


def _once(page: str, old: str, new: str) -> str:
    if page.count(old) != 1:
        raise ValueError(f"the page holds {old!r} {page.count(old)} times, not once")
    return page.replace(old, new)


class _Writer:
    """Writes files below one folder, counting them."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.files = 0
        self.bytes = 0

    def text(self, path: str, body: str) -> None:
        self.write(path, body.encode("utf-8"))

    def write(self, path: str, body: bytes) -> None:
        target = self.folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self.files += 1
        self.bytes += len(body)
