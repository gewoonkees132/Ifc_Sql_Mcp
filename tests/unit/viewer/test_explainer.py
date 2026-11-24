"""The "?" explainer (``viewer/static/index.html``) says only what the code does.

Its copy counts things: the server's tools, the data-quality rules, the demo's questions
and models, the changes it makes to a model. Each count is marked ``data-count``, and
these tests compare it with the code, so the copy cannot drift from it. "Follow one wall"
is checked against the demo's script and the ingest's own name parser.
"""

import re
from collections.abc import Iterator
from html.parser import HTMLParser

import pytest
from sqlalchemy import Engine

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.dq.catalogue import load_catalogue
from ifc_sql_mcp.ingest.revit_names import parse_revit_name
from ifc_sql_mcp.server.app import build_server
from ifc_sql_mcp.viewer.demo_script import DUPLEX
from ifc_sql_mcp.viewer.http import STATIC

PAGE = (STATIC / "index.html").read_text(encoding="utf-8")

WORDS = {
    word: value
    for value, word in enumerate(
        (
            *("zero", "one", "two", "three", "four", "five", "six"),
            *("seven", "eight", "nine", "ten", "eleven", "twelve"),
        )
    )
}


def _value(text: str) -> int:
    """A count as the copy writes it: "45", or "Nine"."""
    return int(text) if text.isdigit() else WORDS[text.lower()]


class _Page(HTMLParser):
    """The explainer's text: each data-count's, and all of it with the counts left out.

    The rest is in chunks, with a "|" between two blocks, so that a number at the end of
    one box and a noun at the start of the next are not read as one count. What is hidden
    from a screen reader (the steps' numbers) is left out.
    """

    VOID = frozenset({"meta", "link", "input", "br", "img", "source"})
    BLOCKS = frozenset({"p", "div", "li", "ul", "ol", "h2", "h3", "h4", "small", "dt", "dd"})

    def __init__(self) -> None:
        super().__init__()
        self.counts: list[tuple[str, str]] = []
        self.rest: list[str] = []
        self._open: list[str | None] = []  # the data-count of each open element, if any
        self._hidden: list[bool] = []  # each open element is aria-hidden
        self._in_dialog = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return
        found = dict(attrs)
        if found.get("id") == "explainer" or self._in_dialog:
            self._in_dialog += 1
        self._open.append(found.get("data-count"))
        self._hidden.append(found.get("aria-hidden") == "true")
        if self._open[-1] is not None:
            self.counts.append((self._open[-1], ""))
        if tag in self.BLOCKS:
            self.rest.append("|")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        self._open.pop()
        self._hidden.pop()
        if tag in self.BLOCKS:
            self.rest.append("|")
        if self._in_dialog:
            self._in_dialog -= 1

    def handle_data(self, data: str) -> None:
        counting = next((name for name in reversed(self._open) if name is not None), None)
        if counting is not None:
            name, text = self.counts[-1]
            self.counts[-1] = (name, text + data)
        elif self._in_dialog and not any(self._hidden):
            self.rest.append(data)


@pytest.fixture(scope="module")
def page() -> _Page:
    parsed = _Page()
    parsed.feed(PAGE)
    return parsed


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    # The server is built, never called: no connection is made.
    settings = Settings.from_env(
        {"IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc@127.0.0.1:1/ifc"}
    )
    engine = make_reader_engine(settings, application="viewer")
    yield engine
    engine.dispose()


def test_every_count_of_the_explainer_is_the_codes(page: _Page, engine: Engine) -> None:
    tools = build_server(engine)._tool_manager.list_tools()
    expected = {
        "tools": len(tools),
        "rules": len(load_catalogue()),
        "questions": len(DUPLEX.steps),
        "models": len(DUPLEX.samples),
        "changes": 0,
    }

    assert {name for name, _ in page.counts} == set(expected)
    for name, text in page.counts:
        assert _value(text.strip()) == expected[name], (name, text)
    # The figures the design pass approved, so that a change to them is seen here first.
    assert expected == {"tools": 11, "rules": 45, "questions": 9, "models": 4, "changes": 0}


def test_no_changes_to_your_models_means_every_tool_only_reads(engine: Engine) -> None:
    tools = build_server(engine)._tool_manager.list_tools()

    assert tools
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_no_count_in_the_explainer_goes_unchecked(page: _Page) -> None:
    text = " ".join(" ".join(page.rest).split())
    # "One model in one go" counts nothing: a count here is none, or two or more.
    number = rf"\b(?:\d+|{'|'.join(word for word in WORDS if word != 'one')})\b"
    nouns = r"(?:tools?|rules?|checks?|questions?|models?|changes?)\b"

    assert re.findall(rf"{number}(?:[ -][\w-]+)? {nouns}", text, re.IGNORECASE) == []
    assert "read-only tools" in text  # the counts were left out, not the text around them


def test_the_wall_the_explainer_follows_is_question_8s() -> None:
    trail = re.search(r'<div class="trail">(.*?)</div>', PAGE, re.DOTALL)
    assert trail is not None
    ifc_name = re.search(r"<code>([^<]*)</code>", trail[1])
    family, type_, element_id = re.findall(r"<b>([^<]*)</b>", trail[1])
    step = DUPLEX.steps[7]

    assert ifc_name is not None
    assert "Question 8 shows everything stored about it." in trail[1]
    assert step.tool == "get_element"
    assert step.expected == ("IfcWallStandardCase", ifc_name[1])
    parsed, issues = parse_revit_name(step.arguments["global_id"], ifc_name[1], element_id)
    assert issues == []
    assert (parsed.family, parsed.type, parsed.element_id) == (family, type_, int(element_id))
