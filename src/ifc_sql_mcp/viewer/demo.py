"""``ifc-sql demo``: a script of questions, played through the real MCP server (ADR 14).

No LLM and no MCP client of the user's: the demo is the client. It starts ``ifc-sql
serve`` with ``IFC_SQL_VIEWER_FEED=1`` over stdio, through the SDK's client, as
``scripts/mcp_smoke.py`` does, and calls each step's tool. ``serve`` publishes the call
as every call is published, ``view``'s listener receives it, and the feed resolves what
it lights: the same path a real client's calls take. For each step the demo waits until
the feed has the call, then compares it with the script:

* the call ended as the step says (``ok``, or ``refused`` for the write);
* the figure the step reads from the result is the one it expects;
* the selection lights the step's count of objects, group by group.

:func:`play` does that through any client: ``ifc-sql demo`` gives it the stdio one, the
tests the SDK's in-process client. ``--check`` reports and exits 1 on a difference;
without it, ``view``'s page is served while the steps play, a pause apart.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
import anyio.to_thread
from mcp import Client
from sqlalchemy import text

from ifc_sql_mcp.viewer.demo_script import Script, Step
from ifc_sql_mcp.viewer.event import Source
from ifc_sql_mcp.viewer.feed import DemoStep, Entry, Feed
from ifc_sql_mcp.viewer.reading import Reader

logger = logging.getLogger(__name__)

WAIT_SECONDS = 15.0
"""How long a step waits for its call to reach the feed."""


class DemoError(Exception):
    """The demo cannot play: its files are not loaded, or the feed is silent."""


@dataclass
class Played:
    """One step as it played.

    Attributes:
        index: The step's number, from 1.
        step: The step.
        entry: The feed's entry for its call, or ``None`` if none arrived.
        outcome: How the call ended.
        measured: What the step's figure read from the result.
        characters: The result's text, in characters: what an LLM would have read.
        lit: The selection's count, group by group.
        differences: Where the call differs from the script; empty when it does not.
    """

    index: int
    step: Step
    entry: Entry | None
    outcome: str
    measured: object
    characters: int
    lit: tuple[int, ...] | None
    differences: list[str] = field(default_factory=list)

    def line(self, steps: int) -> str:
        """The step in one line, for ``--check``'s report."""
        lit = "nothing (feed only)" if self.lit is None else _lit(self.lit)
        verdict = "ok" if not self.differences else "DIFFERS: " + "; ".join(self.differences)
        return (
            f"{self.index}/{steps} {self.step.title} [{self.step.tool}, {self.outcome}, "
            f"{self.characters:,} characters]: {self.step.figure}; lights {lit}: {verdict}"
        )


def find_models(reader: Reader, script: Script) -> dict[str, int]:
    """The model of each of the script's files, found by its SHA-256.

    The current model of that file: a file whose exact bytes are loaded is loaded once. A
    replaced model is never one of them: the audit's script names D, not the A it replaces.

    Raises:
        DemoError: A file is not loaded; the message says how to load it.
    """
    hashes = [sample.sha256 for sample in script.samples]
    found: dict[str, int] = reader.read(
        lambda connection: dict(
            connection.execute(
                text(
                    "SELECT m.file_sha256, m.model_id FROM ifc.model AS m "
                    "WHERE m.file_sha256 = ANY(:hashes) AND NOT EXISTS ("
                    "SELECT 1 FROM ifc.model AS later WHERE later.replaces_model_id = m.model_id)"
                ),
                {"hashes": hashes},
            ).all()
        )
    )
    missing = [sample.file_name for sample in script.samples if sample.sha256 not in found]
    if missing:
        command = "scripts/demo.py"
        if script.name != "duplex":
            command += f" --script {script.name}"
        raise DemoError(
            f"the demo needs {', '.join(missing)} loaded with --viewer, and checked: run "
            f"`uv run python {command}`, which downloads, loads and checks them"
        )
    return {sample.key: found[sample.sha256] for sample in script.samples}


async def play(
    script: Script,
    client: Client,
    feed: Feed,
    models: Mapping[str, int],
    *,
    between: Callable[[Played], Awaitable[None]] | None = None,
    wait_seconds: float = WAIT_SECONDS,
) -> list[Played]:
    """Play every step of ``script`` through ``client``; return how each one went.

    ``between`` runs after each step but the last: a pause, or a wait for Enter.
    """
    played: list[Played] = []
    demo_source: Source | None = None
    for index, step in enumerate(script.steps, start=1):
        after = max((entry.id for entry in feed.entries()), default=0)
        arguments = step.call_arguments(models)
        mine = _matcher(step, arguments, demo_source)
        # Before the call: its notification can arrive before its result does.
        feed.expect(
            mine,
            DemoStep(index=index, steps=len(script.steps), title=step.title, figure=step.figure),
        )
        result = await client.call_tool(step.tool, arguments)
        text_content = " ".join(getattr(block, "text", "") for block in result.content)
        structured: Mapping[str, Any] = (
            {"error": text_content} if result.is_error else (result.structured_content or {})
        )
        entry = await anyio.to_thread.run_sync(feed.wait, mine, after, wait_seconds)
        if entry is not None and demo_source is None:
            demo_source = entry.src
            feed.mark_demo(entry.src)
        played.append(_compare(index, step, entry, structured, text_content, models))
        if between is not None and index < len(script.steps):
            await between(played[-1])
    return played


async def alongside(
    serve: Callable[[], Awaitable[None]], steps: Callable[[], Awaitable[None]]
) -> None:
    """Run ``serve`` and ``steps`` together; when ``serve`` ends (Ctrl+C), stop the steps.

    The page stays served after the last step, until Ctrl+C.
    """
    async with anyio.create_task_group() as group:

        async def serving() -> None:
            await serve()
            group.cancel_scope.cancel()

        group.start_soon(serving)
        await steps()


def innermost(exc: BaseException) -> BaseException:
    """The error itself: the client's task groups wrap it in exception groups."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def report(script: Script, played: Sequence[Played]) -> tuple[list[str], bool]:
    """``--check``'s report, a line per step and a last line; and whether all agree."""
    lines = [item.line(len(script.steps)) for item in played]
    differing = sum(1 for item in played if item.differences)
    if differing:
        lines.append(f"{differing} of {len(played)} steps differ from the script")
    else:
        lines.append(f"all {len(played)} steps as the script says")
    return lines, differing == 0


def _matcher(
    step: Step, arguments: Mapping[str, Any], source: Source | None
) -> Callable[[Entry], bool]:
    """Picks the step's call out of the feed: from the demo's own serve, once that is known."""

    def mine(entry: Entry) -> bool:
        return _same_call(entry, step, arguments) and (source is None or entry.src == source)

    return mine


def _same_call(entry: Entry, step: Step, arguments: Mapping[str, Any]) -> bool:
    """True when ``entry`` is the call the step made: its tool and its arguments."""
    if entry.tool != step.tool:
        return False
    if step.sql is not None:
        sql = arguments["sql"]
        return (
            entry.sql is not None and entry.sql.chars == len(sql) and sql.startswith(entry.sql.text)
        )
    return all(entry.arguments.get(name) == value for name, value in arguments.items())


def _compare(
    index: int,
    step: Step,
    entry: Entry | None,
    result: Mapping[str, Any],
    text_content: str,
    models: Mapping[str, int],
) -> Played:
    differences = []
    outcome = entry.outcome if entry is not None else ("error" if "error" in result else "ok")
    try:
        measured = step.measure(result, models)
    except (KeyError, IndexError, TypeError) as exc:
        measured = f"unreadable ({type(exc).__name__})"
    if _plain(measured) != _plain(step.expected):
        differences.append(f"the figure is {measured!r}, not {step.expected!r}")
    lit: tuple[int, ...] | None = None
    if entry is None:
        differences.append(f"no call reached the feed within {WAIT_SECONDS:g} seconds")
    else:
        if entry.outcome != step.outcome:
            differences.append(f"the call ended {entry.outcome}, not {step.outcome}")
        if entry.selection is not None:
            lit = tuple(group.count for group in entry.selection.groups)
        if lit != step.lit:
            expected = "nothing (feed only)" if step.lit is None else _lit(step.lit)
            found = "nothing (feed only)" if lit is None else _lit(lit)
            differences.append(f"it lights {found}, not {expected}")
    return Played(
        index=index,
        step=step,
        entry=entry,
        outcome=outcome,
        measured=measured,
        characters=len(text_content),
        lit=lit,
        differences=differences,
    )


def _plain(value: object) -> object:
    """``value`` as JSON would hold it: tuples are lists."""
    return json.loads(json.dumps(value))


def _lit(counts: Sequence[int]) -> str:
    if not counts:
        return "nothing"
    total = sum(counts)
    objects = "object" if total == 1 else "objects"
    groups = "" if len(counts) == 1 else f" in {len(counts)} groups ({', '.join(map(str, counts))})"
    return f"{total:,} {objects}{groups}"
