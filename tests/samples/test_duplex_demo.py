"""The scripted Duplex demo on the four real files: every step's figure and selection, pinned.

Deselected by default. Run with::

    make samples && uv run pytest -m sample

The script plays through the SDK's in-process client, on a server with the feed on; the
events go through PostgreSQL to a listener of this process, as ``ifc-sql view``'s does.
The counts are those of ``viewer/demo_script.py``, and docs/viewer.md quotes them.
"""

import dataclasses
import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp_client import connect
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.dq.run import DqResult
from ifc_sql_mcp.ingest.pipeline import IngestResult
from ifc_sql_mcp.server.app import argument_checks, build_server
from ifc_sql_mcp.viewer import demo, export
from ifc_sql_mcp.viewer.demo_script import DUPLEX
from ifc_sql_mcp.viewer.feed import Feed, Resolver
from ifc_sql_mcp.viewer.listener import Listener, listen_connection
from ifc_sql_mcp.viewer.reading import SESSIONS, Reader

pytestmark = [pytest.mark.sample, pytest.mark.anyio]

# Each step's selection, group by group, as docs/viewer.md gives it.
LIT = [
    None,  # 1. list_models lights nothing: the four disciplines are shown
    (21,),  # 2. the ARC walls on Level 1
    (12, 4, 4, 3),  # 3. the external walls, by type
    (6, 4, 2, 2),  # 4. the doors, by type
    (21,),  # 5. the ARC rooms
    (21, 21),  # 6. the MEP model's rooms and their copies
    (78,),  # 7. what lacks a fire rating
    (1,),  # 8. one wall
    (),  # 9. the write, refused
]


@pytest.fixture(scope="module")
def database(
    settings: Settings,
    duplex: tuple[Engine, dict[str, IngestResult]],
    checked: dict[str, DqResult],
) -> Settings:
    """The checked sample database, which every test here reads."""
    engine, _ = duplex
    return dataclasses.replace(
        settings, database_url=engine.url.render_as_string(hide_password=False)
    )


@pytest.fixture
def listening(database: Settings) -> Iterator[tuple[Engine, Reader, Feed]]:
    engine = make_reader_engine(database, application="viewer")
    reader = Reader(engine)
    feed = Feed(Resolver(reader, argument_checks(build_server(engine))))
    listener = Listener(lambda: listen_connection(engine), feed)
    listener.start()
    deadline = time.monotonic() + 10
    while feed.status().state != "live":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    yield engine, reader, feed
    listener.stop()
    engine.dispose()


async def _play(database: Settings, reader: Reader, feed: Feed) -> list[demo.Played]:
    models = demo.find_models(reader, DUPLEX)
    server = make_reader_engine(database)
    try:
        async with connect(build_server(server, feed=True)) as client:
            return await demo.play(DUPLEX, client, feed, models)
    finally:
        server.dispose()


async def test_every_step_of_the_duplex_demo_shows_and_lights_what_the_script_says(
    database: Settings, listening: tuple[Engine, Reader, Feed]
) -> None:
    _, reader, feed = listening

    played = await _play(database, reader, feed)

    assert [item.differences for item in played] == [[]] * len(DUPLEX.steps)
    assert [item.lit for item in played] == LIT
    assert [item.outcome for item in played] == ["ok"] * 8 + ["refused"]
    assert [item.measured for item in played] == [step.expected for step in DUPLEX.steps]
    # The models are found by their files' SHA-256; these are the counts the docs cite.
    assert played[0].measured == [268, 926, 103, 498]
    assert played[3].measured == (14, 4, ["Fire Rating"])
    assert played[4].measured == (21, 422.0)
    # get_element on the wall, compact, in this layout (the ARC file loaded first).
    assert played[7].characters == 16_353
    entries = feed.entries()
    assert all(entry.demo for entry in entries)
    assert [entry.step.index if entry.step else None for entry in entries] == list(range(1, 10))


async def test_the_duplex_demo_selections_are_the_files_own(
    database: Settings, listening: tuple[Engine, Reader, Feed]
) -> None:
    _, reader, feed = listening

    played = await _play(database, reader, feed)

    walls = played[2].entry
    assert walls is not None
    assert walls.selection is not None
    assert [group.label for group in walls.selection.groups] == [
        "Exterior - Brick on Block",
        "Foundation - Concrete (417mm)",
        "Party Wall - CMU Residential Unit Dimising Wall",
        "Foundation - Concrete (435mm)",
    ]
    rooms = played[5].entry
    assert rooms is not None
    assert rooms.selection is not None
    assert [group.label for group in rooms.selection.groups] == [
        "rooms",
        "rooms exported again (duplicate_of)",
    ]
    assert rooms.selection.layers == ["space_copies", "spaces"]
    assert rooms.selection.spaces == 42
    on_level_1 = played[1].entry
    assert on_level_1 is not None
    assert on_level_1.selection is not None
    assert on_level_1.selection.storey == "Level 1"
    wall = played[7].entry
    assert wall is not None
    assert wall.selection is not None
    assert wall.selection.zoom is True


def _peaks(owner: Engine, stop: threading.Event, peaks: Counter[str]) -> Callable[[], None]:
    def sample() -> None:
        with owner.connect() as connection:
            while not stop.is_set():
                for name, count in connection.execute(
                    text(
                        "SELECT application_name, count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() GROUP BY 1"
                    )
                ):
                    peaks[name] = max(peaks[name], count)
                time.sleep(0.002)

    return sample


async def test_a_demo_run_holds_at_most_one_listener_three_readers_and_one_call(
    database: Settings, listening: tuple[Engine, Reader, Feed]
) -> None:
    """ifc_reader has CONNECTION LIMIT 20: view holds 1 + 3 at most, serve 1 more per call."""
    _, reader, feed = listening
    owner = create_engine(database.database_url, poolclass=NullPool)
    peaks: Counter[str] = Counter()
    stop = threading.Event()
    sampler = threading.Thread(target=_peaks(owner, stop, peaks))
    sampler.start()
    try:
        played = await _play(database, reader, feed)
    finally:
        stop.set()
        sampler.join()
        owner.dispose()

    assert len(played) == 9
    assert peaks["ifc-sql-mcp-viewer-feed"] == 1
    assert peaks["ifc-sql-mcp-viewer"] <= SESSIONS
    # A call's own session is closed before its notification's opens.
    assert peaks["ifc-sql-mcp-server"] <= 1


def test_demo_check_plays_through_a_real_serve_over_stdio(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IFC_SQL_DATA_DIR", raising=False)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", database.database_url)
    monkeypatch.setenv("IFC_SQL_READER_PASSWORD", database.reader_password)

    result = CliRunner().invoke(cli.app, ["demo", "--check"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 10
    assert lines[0].startswith("1/9 What is loaded? [list_models, ok, ")
    assert lines[2].endswith("lights 23 objects in 4 groups (12, 4, 4, 3): ok")
    assert lines[-1] == "all 9 steps as the script says"


async def test_the_duplex_demo_exports_as_the_static_copy_pages_publishes(
    database: Settings, listening: tuple[Engine, Reader, Feed], tmp_path: Path
) -> None:
    _, reader, feed = listening
    played = await _play(database, reader, feed)
    models = demo.find_models(reader, DUPLEX)
    site = tmp_path / "site"

    exported = export.export(reader, DUPLEX, played, models, site)

    def read(path: str) -> Any:
        return json.loads((site / path).read_text(encoding="utf-8"))

    # The page and its 16 files, 4 data files, and 4 scenes of 7, 9, 4 and 4 chunks.
    assert exported.files == 1 + 16 + 4 + 4 + 24
    ids = [models[key] for key in ("arc", "mep", "ele", "plu")]
    assert [model["model_id"] for model in read(export.MODELS)["models"]] == ids
    scenes = [read(export.SCENE.format(model_id=model_id)) for model_id in ids]
    assert [len(scene["chunks"]) for scene in scenes] == [7, 9, 4, 4]
    entries = read(export.EVENTS)["entries"]
    assert [
        None if e["selection"] is None else tuple(g["count"] for g in e["selection"]["groups"])
        for e in entries
    ] == LIT
    assert [entry["step"]["index"] for entry in entries] == list(range(1, 10))
    # Every element a scene can draw, and the spaces: ARC's 218 drawn and 21 rooms.
    elements = read(export.ELEMENTS)["models"]
    assert [len(elements[str(model_id)]["objects"]) for model_id in ids] == [239, 968, 104, 499]
    # The one object the script looks up: the wall of step 8.
    details = read(export.DETAILS)["models"]
    assert {model: list(objects) for model, objects in details.items()} == {
        str(models["arc"]): ["0iEHWY1$XA8eQeeULq4jDb"]
    }
    wall = details[str(models["arc"])]["0iEHWY1$XA8eQeeULq4jDb"]
    assert wall["name"] == "Basic Wall:Interior - Furring (152 mm Stud):190774"
