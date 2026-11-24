"""The static copy of the demo (``viewer/export.py``): what it may hold, and where.

The export against a database, through ``ifc-sql demo --export``, is in
``tests/integration/test_cli_demo.py``; the Duplex's, in ``tests/samples``.
"""

import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from ifc_sql_mcp.viewer import export
from ifc_sql_mcp.viewer.demo import Played
from ifc_sql_mcp.viewer.demo_script import AUDIT, DUPLEX
from ifc_sql_mcp.viewer.event import Source
from ifc_sql_mcp.viewer.feed import DEMO, Entry
from ifc_sql_mcp.viewer.http import STATIC, page_text
from ifc_sql_mcp.viewer.reading import Reader
from ifc_sql_mcp.viewer.security import content_security_policy
from ifc_sql_mcp.viewer.selection import Group, ModelObjects, Selection

SOURCE = Source(pid=5, started_at="2026-11-23T10:00:00+00:00")
RECORDED_AT = datetime(2026, 11, 23, 10, 0, 5, tzinfo=UTC)
GLOBAL_ID = "0iEHWY1$XA8eQeeULq4jDb"


def _entry(tool: str, *, demo: bool, objects: dict[int, list[str]] | None = None) -> Entry:
    selection = None
    if objects is not None:
        models = [ModelObjects(model_id=m, global_ids=ids) for m, ids in objects.items()]
        selection = Selection(
            groups=[Group(label=None, colour="#ff5722", count=1, models=models)],
            count=sum(len(ids) for ids in objects.values()),
            truncated=False,
            openings=0,
            not_drawn=0,
            spaces=0,
            layers=[],
            storey=None,
            storey_elevation_m=None,
            zoom=tool == "get_element",
            note=None,
        )
    return Entry(
        id=1,
        received_at="2026-11-23T10:00:01+00:00",
        src=SOURCE,
        source=f"{DEMO} (serve 5)" if demo else "serve 5",
        demo=demo,
        n=1,
        tool=tool,
        summary=tool,
        arguments={},
        clipped=[],
        outcome="ok",
        seconds=0.01,
        rows=None,
        sql=None,
        selection=selection,
    )


def _played(entry: Entry | None, index: int = 1, differences: list[str] | None = None) -> Played:
    return Played(
        index=index,
        step=DUPLEX.steps[index - 1],
        entry=entry,
        outcome="ok",
        measured=None,
        characters=0,
        lit=None,
        differences=differences or [],
    )


# --------------------------------------------------------------------------- the page


def test_the_static_page_is_views_page_marked_static_with_its_policy_in_a_meta_tag() -> None:
    page = page_text()

    copy = export.static_page(page, "The Duplex, step by step")

    head = copy[: copy.index('<script type="importmap">')]
    [policy] = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', head)
    assert html.unescape(policy) == content_security_policy(page, meta=True)
    assert '<meta name="referrer" content="no-referrer">' in head
    assert '<html lang="en" data-source="static">' in copy
    assert "<title>The Duplex, step by step · ifc-sql demo</title>" in copy
    # Nothing else differs: the copy's page is view's, three tags and a title apart.
    undone = (
        copy.replace('<html lang="en" data-source="static">', '<html lang="en">')
        .replace(
            "<title>The Duplex, step by step · ifc-sql demo</title>", "<title>ifc-sql view</title>"
        )
        .replace(head[head.index("<meta http-equiv") : head.index('<meta name="viewport"')], "")
    )
    assert undone == page


def test_the_meta_policy_leaves_out_frame_ancestors_which_browsers_ignore_there() -> None:
    page = page_text()

    header = content_security_policy(page)
    meta = content_security_policy(page, meta=True)

    assert "frame-ancestors 'none'" in header
    assert meta == header.replace("; frame-ancestors 'none'", "")
    # The font is a file of the page, in the copy as in view.
    assert "font-src 'self'" in meta.split("; ")


def test_a_page_without_a_tag_the_copy_rewrites_is_refused() -> None:
    page = page_text().replace("<title>ifc-sql view</title>", "")

    with pytest.raises(ValueError, match="the page holds '<title>ifc-sql view</title>' 0 times"):
        export.static_page(page, "x")


def test_a_title_is_text_in_the_page() -> None:
    copy = export.static_page(page_text(), "<b>walls</b> & doors")

    assert "<title>&lt;b&gt;walls&lt;/b&gt; &amp; doors · ifc-sql demo</title>" in copy


def test_the_static_source_asks_for_the_files_the_export_writes() -> None:
    api = (STATIC / "api.js").read_text(encoding="utf-8")
    source = api[api.index("export function staticSource()") :]
    # The paths of the static source, its template literals' ${…} as {}.
    asked = {
        re.sub(r"\$\{[^}]*\}", "{}", path)
        for path in re.findall(r"""['`](data/[^'`]*)['`]""", source)
    }
    scene = "data/scenes/{}"
    asked = {path.replace("${scene(modelId)}", scene) for path in asked}
    written = {re.sub(r"\{\w+\}", "{}", path) for path in export.LAYOUT}

    # scene() makes the stem; the manifest and the chunks add their endings to it.
    assert asked == {
        "data/models.json",
        "data/events.json",
        "data/elements.json",
        "data/details.json",
        scene,
    }
    assert "`${scene(modelId)}.json`" in source
    assert "`${scene(modelId)}/${encodeURIComponent(chunkNo)}.glb`" in source
    assert written == {
        "data/models.json",
        "data/events.json",
        "data/elements.json",
        "data/details.json",
        f"{scene}.json",
        f"{scene}/{{}}.glb",
    }


# --------------------------------------------------------------------------- what may be published


def test_only_a_script_whose_files_may_be_published_is_exported() -> None:
    assert export.check_script(DUPLEX) is DUPLEX.credit

    with pytest.raises(export.ExportError) as refused:
        export.check_script(AUDIT)

    assert str(refused.value) == (
        "the audit script's files stay on this computer: their licences do not allow a copy "
        "(DATA_ATTRIBUTION.md); --export takes --script duplex"
    )


def test_the_duplex_is_credited_as_its_licence_asks() -> None:
    credit = DUPLEX.credit

    assert credit is not None
    assert "buildingSMART International" in credit.text
    assert "CC BY 4.0" in credit.text
    assert "converted to glTF by IfcOpenShell triangulation, openings cut" in credit.text
    # The page links only https addresses.
    assert credit.source_url.startswith("https://github.com/buildingsmart-community/")
    assert credit.licence_url == "https://creativecommons.org/licenses/by/4.0/"


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_a_new_or_empty_folder_takes_an_export(tmp_path: Path, state: str) -> None:
    folder = tmp_path / "site"
    if state == "empty":
        folder.mkdir()

    export.check_folder(folder)


def test_a_folder_that_holds_anything_is_refused(tmp_path: Path) -> None:
    (tmp_path / "old.glb").write_bytes(b"glTF")

    with pytest.raises(export.ExportError, match="is not empty: --export writes only into a new"):
        export.check_folder(tmp_path)


def test_a_file_is_no_folder_to_export_into(tmp_path: Path) -> None:
    path = tmp_path / "site"
    path.write_text("x", encoding="utf-8")

    with pytest.raises(export.ExportError, match="is a file: --export needs a new or empty"):
        export.check_folder(path)


def test_a_play_that_differs_from_the_script_is_not_exported(tmp_path: Path) -> None:
    played = [_played(_entry("list_models", demo=True)), _played(None, 2, ["no call"])]

    with pytest.raises(export.ExportError, match=r"steps \[2\] differ from the script"):
        # Refused before it reads anything.
        export.export(
            cast(Reader, None),
            DUPLEX,
            played,
            {},
            tmp_path / "site",
        )

    assert not (tmp_path / "site").exists()


# --------------------------------------------------------------------------- the recording


def test_the_recording_says_when_it_was_made_and_labels_every_call() -> None:
    # The demo knows its serve only once the first call has arrived, and labels it after.
    first, second = _entry("list_models", demo=False), _entry("find_elements", demo=True)

    recorded = export.recording(
        DUPLEX, [_played(first), _played(second, 2)], recorded_at=RECORDED_AT
    )

    assert recorded.title == "The Duplex, step by step"
    # Nothing plays by itself: the copy keeps no pause between calls.
    assert "pause_seconds" not in recorded.model_dump()
    assert recorded.credit == DUPLEX.credit
    assert [(entry.demo, entry.source) for entry in recorded.entries] == [
        (True, "scripted demo, no LLM (serve 5)"),
        (True, "scripted demo, no LLM (serve 5)"),
    ]
    status = recorded.status
    assert (status.state, status.database, status.received, status.demo) == (
        "recorded",
        None,
        2,
        True,
    )
    assert status.since == "2026-11-23T10:00:05.000+00:00"
    assert status.message is not None
    assert status.message.startswith(
        "Recorded on 2026-11-23 10:00 UTC by ifc-sql demo --export (ifc-sql-mcp "
    )
    assert status.message.endswith("): the answers of the real tools, kept in this copy's files.")
    # The page lists the questions to pick from, every one of the script's.
    assert [question.title for question in status.questions] == [s.title for s in DUPLEX.steps]
    assert status.questions[2].headline == "23 walls"


def test_the_details_are_those_of_the_objects_get_element_looked_up() -> None:
    entries = [
        _entry("find_elements", demo=True, objects={1: ["2O2Fr$t4X7Zf8NOew3FLOH"]}),
        _entry("get_element", demo=True, objects={1: [GLOBAL_ID]}),
        _entry("get_element", demo=True, objects={2: [GLOBAL_ID]}),  # unique per model only
        _entry("get_element", demo=True, objects={1: [GLOBAL_ID]}),
        _entry("get_element", demo=True),  # one that failed lights nothing
    ]

    assert export.looked_up(entries) == [(1, GLOBAL_ID), (2, GLOBAL_ID)]
