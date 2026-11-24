"""The page's words (``viewer/static/words.js``) agree with the code they describe.

The JavaScript is not run here (there is no browser in the suite); these tests read its
tables, so that the page cannot name a tool the server does not have, or miss one.
"""

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.server.app import build_server
from ifc_sql_mcp.viewer.http import STATIC
from ifc_sql_mcp.viewer.selection import FAILED

WORDS = (STATIC / "words.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    # The server is built, never called: no connection is made.
    settings = Settings.from_env(
        {"IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc@127.0.0.1:1/ifc"}
    )
    engine = make_reader_engine(settings, application="viewer")
    yield engine
    engine.dispose()


def _table(name: str) -> dict[str, str]:
    """A `const NAME = { key: 'text', … };` table of words.js."""
    match = re.search(rf"export const {name} = \{{(.*?)\n\}};", WORDS, re.DOTALL)
    assert match is not None, name
    return dict(re.findall(r"^\s*(\w+): '([^']*)',$", match[1], re.MULTILINE))


def test_the_page_names_every_tool_of_the_server_by_its_title(engine: Engine) -> None:
    tools = build_server(engine)._tool_manager.list_tools()

    assert _table("TITLES") == {tool.name: tool.title for tool in tools}


def test_the_page_says_how_every_failed_call_ended() -> None:
    assert set(_table("OUTCOMES")) == set(FAILED)
