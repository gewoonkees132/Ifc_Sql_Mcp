"""The scripted audit demo on the real Revit exports: every step's figure and selection, pinned.

Deselected by default. Run with::

    uv run python scripts/download_audit_samples.py && uv run pytest -m modern

The script plays through the SDK's in-process client, on a server with the feed on, over
the ``modern`` fixture's database: the five files loaded in the audit's order, D as A's
new version (``--replaces``). The events go through PostgreSQL to a listener of this
process, as ``ifc-sql view``'s do. The counts are those of ``viewer/demo_script.py``;
docs/viewer.md quotes them. No step needs a scene: a selection is resolved from schema
``ifc``, and the scenes are pinned by ``test_audit_scenes.py``.
"""

import time
from collections.abc import Iterator

import pytest
from audit_files import Modern
from mcp_client import connect
from sqlalchemy import Engine

from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.server.app import argument_checks, build_server
from ifc_sql_mcp.viewer import demo
from ifc_sql_mcp.viewer.demo_script import AUDIT
from ifc_sql_mcp.viewer.feed import Feed, Resolver
from ifc_sql_mcp.viewer.listener import Listener, listen_connection
from ifc_sql_mcp.viewer.reading import Reader

pytestmark = [pytest.mark.modern, pytest.mark.anyio]

# Each step's selection, group by group, as docs/viewer.md gives it.
LIT = [
    (88, 75),  # 1. file C: its rooms, and the copies of rooms
    (282, 25),  # 2. file E: its rooms, and the Revit Areas
    (77, 25, 21, 15, 14, 7),  # 3. file B: the doors of XX_AR_L01, by type
    (4, 51),  # 4. file D: 4 openings added, 51 objects whose properties changed
]


@pytest.fixture
def listening(modern: Modern) -> Iterator[tuple[Engine, Reader, Feed]]:
    engine = make_reader_engine(modern.settings, application="viewer")
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


async def _play(modern: Modern, reader: Reader, feed: Feed) -> list[demo.Played]:
    models = demo.find_models(reader, AUDIT)
    server = make_reader_engine(modern.settings)
    try:
        async with connect(build_server(server, feed=True)) as client:
            return await demo.play(AUDIT, client, feed, models)
    finally:
        server.dispose()


async def test_every_step_of_the_audit_demo_shows_and_lights_what_the_script_says(
    modern: Modern, listening: tuple[Engine, Reader, Feed]
) -> None:
    _, reader, feed = listening

    played = await _play(modern, reader, feed)

    assert [item.differences for item in played] == [[]] * len(AUDIT.steps)
    assert [item.lit for item in played] == LIT
    assert [item.outcome for item in played] == ["ok"] * 4
    assert [item.measured for item in played] == [step.expected for step in AUDIT.steps]
    # Each result's text, in this layout: A to E loaded first, as scripts/demo.py loads them.
    assert [item.characters for item in played] == [41_471, 45_746, 4_181, 2_129]
    entries = feed.entries()
    assert all(entry.demo for entry in entries)
    assert [entry.step.index if entry.step else None for entry in entries] == [1, 2, 3, 4]


def test_the_audit_demo_finds_the_models_by_their_files(modern: Modern) -> None:
    engine = make_reader_engine(modern.settings, application="viewer")
    try:
        models = demo.find_models(Reader(engine), AUDIT)
    finally:
        engine.dispose()

    assert models == {key: modern.model(key) for key in ("b", "c", "d", "e")}
    assert AUDIT.first_models(models) == [modern.model("c")]


async def test_the_audit_demo_selections_are_the_files_own(
    modern: Modern, listening: tuple[Engine, Reader, Feed]
) -> None:
    _, reader, feed = listening

    played = await _play(modern, reader, feed)

    rooms, areas, doors, changes = (item.entry for item in played)
    assert rooms is not None
    assert rooms.selection is not None
    assert [group.label for group in rooms.selection.groups] == [
        "rooms",
        "rooms exported again (duplicate_of)",
    ]
    assert (rooms.selection.spaces, rooms.selection.count) == (163, 163)
    assert rooms.selection.layers == ["space_copies", "spaces"]
    # Every group lies in the model the call asked about.
    assert {m.model_id for g in rooms.selection.groups for m in g.models} == {modern.model("c")}

    assert areas is not None
    assert areas.selection is not None
    assert [group.label for group in areas.selection.groups] == ["rooms", "Revit Areas"]
    assert areas.selection.layers == ["areas", "spaces"]
    # The Areas keep the third colour, after rooms and copies, though E has no copies.
    assert [group.colour for group in areas.selection.groups] == ["#ff5722", "#43a047"]

    assert doors is not None
    assert doors.selection is not None
    assert doors.selection.storey == "XX_AR_L01"
    assert doors.selection.count == 159
    # The types of test_audit_acceptance.py's L01_DOORS, the largest first, as the schedule.
    assert [group.label for group in doors.selection.groups] == [
        "0AR_POR_PI000 - INT 1A:0AR_POR_PI002- 80x210",
        "0AR_POR_PI000 - INT 1A ingresso:0AR_POR_PI001 - 90 x 210 ingresso",
        "0AR_POR_PI000 - INT 2A - US:0AR_POR_PI003 - 120 x 210 - US",
        "0AR_POR_PI000 - INT scorrevole:0AR_POR_PI005- 80x210",
        "0AR_POR_PE002 - doppia anta:0AR_POR_PE002 - doppia anta",
        "0AR_POR_PI000 - INT 1A:0AR_POR_PI004- 90x210",
    ]

    assert changes is not None
    assert changes.selection is not None
    assert [group.label for group in changes.selection.groups] == ["added", "properties changed"]
    # The 4 openings are never drawn; the 51 are 42 walls and 9 curtain walls of D.
    assert (changes.selection.count, changes.selection.openings) == (55, 4)
    assert changes.selection.note == (
        f"4 objects of model {modern.model('a')} are not in model {modern.model('d')}: "
        "counted, not drawn"
    )
    assert {m.model_id for g in changes.selection.groups for m in g.models} == {modern.model("d")}
