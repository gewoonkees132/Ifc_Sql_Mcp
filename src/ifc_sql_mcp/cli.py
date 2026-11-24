"""Command-line entry point: ``ifc-sql``.

The root callback does the work every command shares (read settings, configure logging)
so individual commands stay focused on their own job.
"""

import dataclasses
import logging
import os
import socket
import sys
import tempfile
import time
import webbrowser
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, TextIO, cast

import anyio
import typer
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from ifc_sql_mcp import __version__
from ifc_sql_mcp.config import LOG_LEVELS, Settings, SettingsError, parse_log_level
from ifc_sql_mcp.db.embedded import EmbeddedError, embedded_database_url
from ifc_sql_mcp.db.engine import (
    make_engine,
    make_reader_engine,
    reader_database_url,
    reader_transaction,
    redact,
)
from ifc_sql_mcp.db.migrate import (
    READER_READS_REVISION_FROM,
    RevisionUnreadableError,
    current_revision,
    head_revision,
    reader_revision,
    upgrade,
)
from ifc_sql_mcp.dq.catalogue import CATALOGUE, CatalogueError, load_catalogue
from ifc_sql_mcp.dq.run import (
    DqResult,
    DqRunError,
    UnknownModelError,
    check_model,
    loaded_model_ids,
)
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.pipeline import (
    IngestError,
    IngestResult,
    ReplacesError,
    SceneOutcome,
    check_discipline,
    ingest_file,
)
from ifc_sql_mcp.logging_config import configure_logging
from ifc_sql_mcp.server import tools
from ifc_sql_mcp.server.app import Limits, argument_checks, build_server
from ifc_sql_mcp.viewer import demo as demo_play
from ifc_sql_mcp.viewer import export as demo_export
from ifc_sql_mcp.viewer.demo_script import SCRIPTS, Script
from ifc_sql_mcp.viewer.feed import Feed, Resolver
from ifc_sql_mcp.viewer.http import create_app
from ifc_sql_mcp.viewer.listener import Listener, listen_connection
from ifc_sql_mcp.viewer.reading import Reader
from ifc_sql_mcp.viewer.security import new_token
from ifc_sql_mcp.watch import Watcher, WatchEvent

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
"""The one address ``ifc-sql view`` listens on; no setting changes it (ADR 14)."""

FEED_START_SECONDS = 10.0
"""How long ``ifc-sql demo`` waits for its listener to listen before it plays."""

SERVE = [str(Path(sys.executable).with_name("ifc-sql")), "serve"]
"""How ``ifc-sql demo`` starts the MCP server: as a client starts it, a process of its own."""

PASSED_ON = (
    "IFC_SQL_DATABASE_URL",
    "IFC_SQL_DATA_DIR",
    "IFC_SQL_READER_PASSWORD",
    "IFC_SQL_LOG_LEVEL",
)
"""The settings ``ifc-sql demo`` hands the ``ifc-sql serve`` it starts, as a client would."""

app = typer.Typer(
    name="ifc-sql",
    help="Load Revit-exported IFC files into PostgreSQL, serve them to LLMs over MCP, and "
    "show them in 3D.",
    no_args_is_help=True,
    add_completion=False,
    # Rich tracebacks can print local variables, which may include connection strings.
    pretty_exceptions_show_locals=False,
)


def resolve_settings(log_level_override: str | None) -> Settings:
    """Combine environment settings with command-line overrides.

    A plain function (not inline in the Typer callback) so it can be tested directly.
    """
    settings = Settings.from_env()
    if log_level_override is not None:
        settings = dataclasses.replace(settings, log_level=parse_log_level(log_level_override))
    return settings


def _database_settings(ctx: typer.Context) -> Settings:
    """The settings of a command that uses the database, with the embedded one started first.

    With IFC_SQL_DATA_DIR, the embedded PostgreSQL is started (or attached to) here, by the
    commands that need it, so that ``--help`` starts nothing (``db/embedded.py``).
    """
    settings: Settings = ctx.obj
    if settings.data_dir is None:
        return settings
    try:
        url = embedded_database_url(settings.data_dir)
    except EmbeddedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    return dataclasses.replace(settings, database_url=url)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"ifc-sql {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help=f"Override IFC_SQL_LOG_LEVEL ({', '.join(LOG_LEVELS)})."),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Read settings, configure logging and hand the settings to the chosen command."""
    try:
        settings = resolve_settings(log_level)
    except SettingsError as exc:
        raise typer.BadParameter(str(exc)) from exc

    configure_logging(settings.log_level)
    ctx.obj = settings


@app.command()
def migrate(
    ctx: typer.Context,
    revision: Annotated[
        str, typer.Option(help="Revision to upgrade to; the default is the latest.")
    ] = "head",
) -> None:
    """Create or upgrade the ifc schema: tables, views and the read-only role."""
    settings = _database_settings(ctx)
    database = redact(settings.database_url)
    logger.info("migrating", extra={"database": database, "target": revision})
    try:
        upgrade(settings, revision)
        reached = current_revision(settings)
    except OperationalError as exc:
        # Connection problems are the common failure; say where we tried, without the
        # password, instead of printing a traceback.
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"database is at revision {reached}")


def _require_latest_revision(settings: Settings, command: str) -> None:
    """Exit with 1 unless the database is at the latest migration, which ``command`` needs."""
    _check_revision(current_revision(settings), redact(settings.database_url), command)


def _check_revision(revision: str | None, database: str, command: str) -> None:
    if revision != head_revision():
        found = "not migrated" if revision is None else f"at revision {revision}"
        typer.echo(
            f"the database at {database} is {found}, but {command} needs revision "
            f"{head_revision()}: run `ifc-sql migrate` first",
            err=True,
        )
        raise typer.Exit(1)


def _discipline(value: str) -> str:
    try:
        return check_discipline(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _summary(file_name: str, result: IngestResult) -> str:
    """One line for stdout saying what the run did, and with --viewer what its scene did."""
    model = f"model {result.model_id} ({result.discipline})"
    scene = "" if result.scene is None else f"; {_scene_summary(result.scene)}"
    if result.status == "succeeded":
        counts = result.row_counts
        return (
            f"loaded {file_name} as {model}: {counts['object']:,} objects, "
            f"{counts['property']:,} property values, {counts['ingest_issue']:,} issues "
            f"(run {result.run_id}){scene}"
        )
    if result.status == "measured":
        counts = result.row_counts
        return (
            f"measured the shapes of {file_name}, already loaded as {model}: "
            f"{counts['quantity']:,} quantities and {counts['ingest_issue']:,} issues added "
            f"(run {result.run_id}); check it again with ifc-sql dq {result.model_id}{scene}"
        )
    skipped = f"run {result.run_id}, skipped"
    loaded = (
        f"{file_name} was loaded as {model} by another ingest running at the same time"
        if result.concurrent
        else f"{file_name} is already loaded as {model}"
    )
    # A scene written into schema ifc_viewer is not "nothing written".
    if result.scene is not None and result.scene.status in ("built", "replaced"):
        return f"{loaded}{scene} ({skipped})"
    return f"{loaded}; nothing written ({skipped}){scene}"


def _scene_summary(scene: SceneOutcome) -> str:
    """What ingest --viewer did with the scene, and what it draws."""
    if scene.status == "failed":
        return "its scene was not built"
    verb = {"built": "scene built", "replaced": "scene rebuilt", "kept": "scene kept"}
    return (
        f"{verb[scene.status]}: {scene.element_count:,} elements drawn, "
        f"{scene.without_shape_count:,} without shape, {scene.opening_count:,} openings not "
        f"drawn, {scene.chunk_count:,} chunks, {scene.byte_count / 1e6:.1f} MB"
    )


def _scene_failed(result: IngestResult) -> bool:
    """Say on stderr why the scene was not built; True if it was not."""
    if result.scene is None or result.scene.status != "failed":
        return False
    typer.echo(
        f"the scene of model {result.model_id} was not built: {result.scene.error}; the model "
        "is loaded, so run the same command again once that is fixed",
        err=True,
    )
    return True


@app.command()
def ingest(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="The IFC file to load."),
    ],
    discipline: Annotated[
        str,
        typer.Option(
            "--discipline",
            callback=_discipline,
            help="Discipline of the model: 2 to 8 capital letters, e.g. ARC, MEP, ELE, PLU.",
        ),
    ],
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="The project this model belongs to, e.g. the project's number. Models with "
            "the same project are compared across disciplines. Without it, the file's own "
            "project number or name is used, unless it is a Revit default.",
        ),
    ] = None,
    replaces: Annotated[
        int | None,
        typer.Option(
            "--replaces",
            min=1,
            metavar="MODEL_ID",
            help="The model this file is a new export of. That model stays loaded, but only "
            "this one counts in project-wide views. Its --project is kept unless given.",
        ),
    ] = None,
    geometry: Annotated[
        bool,
        typer.Option(
            "--geometry",
            help="Also measure the shapes, for files exported without quantities: wall side "
            "areas, slab and space footprints, solid volumes. They rank after the file's "
            "own quantities. Takes about a minute for a 15,000-object file on 4 cores. A "
            "file already loaded without it is measured into the same model.",
        ),
    ] = False,
    viewer: Annotated[
        bool,
        typer.Option(
            "--viewer",
            help="Also build the model's 3D scene for the viewer: every element and space "
            "triangulated, in chunks per storey. On a large file this takes longer than the "
            "load itself (ADR 14 has the times); with --geometry, both share one pass. A "
            "file already loaded gets its scene in place.",
        ),
    ] = False,
) -> None:
    """Load one IFC file. A file whose exact bytes are already loaded is skipped.

    Exit codes: 0 when the file was loaded or already was; 1 when the ingest failed
    (unreadable file, database unreachable or not migrated, load error) or its scene
    could not be built (the model stays loaded); 2 for a usage error (no such file,
    invalid discipline, blank project, no model to replace).
    """
    settings = _database_settings(ctx)
    database = redact(settings.database_url)
    logger.info("ingesting", extra={"file": str(path), "discipline": discipline})
    engine = make_engine(settings.database_url)
    try:
        _require_latest_revision(settings, "ingest")
        result = ingest_file(
            engine,
            path,
            discipline,
            project_key=project,
            replaces=replaces,
            geometry=geometry,
            viewer=viewer,
        )
    except ReplacesError as exc:
        raise typer.BadParameter(str(exc), param_hint="'--replaces'") from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="'--project'") from exc
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    except IngestError as exc:
        typer.echo(f"ingest of {path.name} failed (run {exc.run_id}): {exc}", err=True)
        raise typer.Exit(1) from exc
    except OSError as exc:
        typer.echo(f"cannot read {path}: {exc.strerror or exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()
    typer.echo(_summary(path.name, result))
    for warning in result.warnings:
        typer.echo(f"warning: {warning}", err=True)
    if _scene_failed(result):
        raise typer.Exit(1)


def _dq_summary(result: DqResult) -> str:
    """One line for stdout saying what a run found, the model's gaps apart from the tool's."""
    counts = result.severity_counts

    def triple(scope: str) -> str:
        c = result.scope_counts[scope]
        return f"{c['error']:,} / {c['warning']:,} / {c['info']:,}"

    return (
        f"checked model {result.model_id} ({result.discipline}, {result.file_name}) in dq run "
        f"{result.run_id}: {counts['error']:,} error, {counts['warning']:,} warning, "
        f"{counts['info']:,} info findings (model content {triple('model_content')}, "
        f"loader coverage {triple('loader_coverage')})"
    )


@app.command()
def dq(
    ctx: typer.Context,
    model_ids: Annotated[
        list[int] | None,
        typer.Argument(
            metavar="MODEL_ID",
            help="Models to check, by model_id; every loaded model when none is given.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Check models against the data quality rules. Every check is a new, recorded run.

    The rule catalogue is synchronised into ifc.dq_rule first. A failed run does not stop
    the others. Exit codes: 0 when every model was checked (or none is loaded); 1 when a
    run failed, the rule catalogue is invalid, or the database is unreachable or not
    migrated; 2 for a usage error (no model with a given id).
    """
    settings = _database_settings(ctx)
    database = redact(settings.database_url)
    engine = make_engine(settings.database_url)
    failed = False
    try:
        _require_latest_revision(settings, "dq")
        rules = load_catalogue(CATALOGUE)
        with engine.begin() as connection:
            loaded = loaded_model_ids(connection)
            unknown = [model_id for model_id in model_ids or [] if model_id not in loaded]
            if unknown:
                # Raised inside the transaction, so the sync below is not committed either.
                raise typer.BadParameter(f"no model with id {unknown[0]}", param_hint="MODEL_ID")
            sync_rules(connection, rules)
        # Each id once, in the order given.
        targets = list(dict.fromkeys(model_ids)) if model_ids else loaded
        if not targets:
            typer.echo("no models are loaded; nothing to check")
        for model_id in targets:
            try:
                result = check_model(engine, model_id, rules)
            except DqRunError as exc:
                typer.echo(f"dq of model {model_id} failed (dq run {exc.run_id}): {exc}", err=True)
                failed = True
                continue
            except UnknownModelError as exc:
                typer.echo(f"model {model_id} was deleted before it was checked: {exc}", err=True)
                failed = True
                continue
            typer.echo(_dq_summary(result))
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    except CatalogueError as exc:
        typer.echo(f"invalid rule catalogue: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()
    if failed:
        raise typer.Exit(1)


@app.command()
def watch(
    ctx: typer.Context,
    folder: Annotated[
        Path,
        typer.Argument(
            exists=True, file_okay=False, dir_okay=True, help="The folder Revit exports into."
        ),
    ],
    discipline: Annotated[
        str,
        typer.Option(
            "--discipline",
            callback=_discipline,
            help="Discipline of every model in the folder, e.g. ARC.",
        ),
    ],
    project: Annotated[
        str | None,
        typer.Option("--project", help="The project every model in the folder belongs to."),
    ] = None,
    geometry: Annotated[
        bool, typer.Option("--geometry", help="Also measure the shapes, as ingest --geometry.")
    ] = False,
    viewer: Annotated[
        bool, typer.Option("--viewer", help="Also build each model's scene, as ingest --viewer.")
    ] = False,
    interval: Annotated[
        float, typer.Option("--interval", min=0.5, help="Seconds between two looks.")
    ] = 5.0,
    once: Annotated[
        bool,
        typer.Option("--once", help="Load and check what is in the folder now, then stop."),
    ] = False,
) -> None:
    """Load each IFC file saved into FOLDER, then check it; a new export replaces the old.

    A file is loaded once its size and time have not changed between two looks, and a
    file whose name was loaded before is loaded as that model's new version
    (--replaces). Stop with Ctrl+C. Exit codes: 0 when stopped (or after --once with no
    failure); 1 when a file failed to load or check under --once, or the database is
    unreachable or not migrated; 2 for a usage error.
    """
    settings = _database_settings(ctx)
    database = redact(settings.database_url)
    engine = make_engine(settings.database_url)
    failed = False
    try:
        _require_latest_revision(settings, "watch")
        rules = load_catalogue(CATALOGUE)
        with engine.begin() as connection:
            sync_rules(connection, rules)
        watcher = Watcher(
            engine,
            folder,
            discipline,
            rules,
            project_key=project,
            geometry=geometry,
            viewer=viewer,
        )
        if not once:
            typer.echo(f"watching {folder} every {interval:g} s; stop with Ctrl+C", err=True)
        while True:
            for event in watcher.poll(settle=not once):
                failed = _report(event) or failed
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("stopped watching", err=True)
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()
    if failed and once:
        raise typer.Exit(1)


def _report(event: WatchEvent) -> bool:
    """Print what the watch did with one file; True when something failed."""
    if event.ingest is not None:
        typer.echo(_summary(event.path.name, event.ingest))
        for warning in event.ingest.warnings:
            typer.echo(f"warning: {warning}", err=True)
    if event.check is not None:
        typer.echo(_dq_summary(event.check))
    if event.error is not None:
        typer.echo(f"{event.path.name} failed: {event.error}", err=True)
        return True
    return False


def _require_latest_revision_as_reader(engine: Engine, database: str, command: str) -> None:
    """Like ``_require_latest_revision``, as ``ifc_reader``: serve and view have no other role."""
    try:
        with engine.connect() as connection:
            revision = reader_revision(connection)
    except RevisionUnreadableError as exc:
        typer.echo(
            f"the database at {database} is at a revision before {READER_READS_REVISION_FROM}, "
            f"whose version table ifc_reader may not read, but {command} needs revision "
            f"{head_revision()}: run `ifc-sql migrate` first",
            err=True,
        )
        raise typer.Exit(1) from exc
    _check_revision(revision, database, command)


@app.command()
def serve(ctx: typer.Context) -> None:
    """Serve the loaded models to an MCP client over stdio, read-only.

    An MCP client starts this command and exchanges JSON-RPC messages with it over stdin
    and stdout; logs go to stderr. It connects only as the read-only role ifc_reader, with
    IFC_SQL_READER_PASSWORD (the credentials in IFC_SQL_DATABASE_URL are not used), and
    checks the database before it serves. Exit codes: 0 when the client closes the
    connection; 1 when the database is unreachable or not at the latest migration.
    """
    settings = _database_settings(ctx)
    database = redact(reader_database_url(settings.database_url, settings.reader_password))
    engine = make_reader_engine(settings)
    try:
        # Fail before serving: a client shows a server that does not start as failed,
        # while one that starts but fails every call looks healthy.
        _require_latest_revision_as_reader(engine, database, "serve")
        server = build_server(engine, feed=settings.viewer_feed)
        feed = {"viewer_feed": "on"} if settings.viewer_feed else {}
        logger.info("serving", extra={"database": database, "transport": "stdio", **feed})
        server.run("stdio")
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()


def _check_view_choice(engine: Engine, project: str | None, model_ids: list[int]) -> None:
    """Exit with 2 unless ``--project`` and ``--model`` name what is loaded."""
    if project is not None and model_ids:
        raise typer.BadParameter("give --project or --model, not both", param_hint="'--project'")
    limits = Limits()
    with reader_transaction(
        engine,
        statement_timeout_ms=limits.statement_timeout_ms,
        lock_timeout_ms=limits.lock_timeout_ms,
    ) as connection:
        models = tools.list_models(connection, include_replaced=True).models
    known = {model.model_id for model in models}
    unknown = [model_id for model_id in model_ids if model_id not in known]
    if unknown:
        raise typer.BadParameter(f"no model with id {unknown[0]}", param_hint="'--model'")
    current = {model.project_group for model in models if model.replaced_by_model_id is None}
    if project is not None and project not in current:
        raise typer.BadParameter(
            f"no current model belongs to project {project!r}; without --project, the page "
            "lists the projects",
            param_hint="'--project'",
        )


@app.command()
def view(
    ctx: typer.Context,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="The project to show first: its current models, every discipline together. "
            "By default, the project of the newest model with a scene; the page can switch.",
        ),
    ] = None,
    model_ids: Annotated[
        list[int] | None,
        typer.Option(
            "--model",
            min=1,
            metavar="MODEL_ID",
            help="Show these models instead, by model_id; repeat it for several.",
            show_default=False,
        ),
    ] = None,
    port: Annotated[
        int,
        typer.Option(
            "--port", min=0, max=65535, help="The port on 127.0.0.1; 0, the default, a free one."
        ),
    ] = 0,
    no_open: Annotated[
        bool, typer.Option("--no-open", help="Print the URL, but do not open the browser.")
    ] = False,
) -> None:
    """Show the loaded models in 3D, in the browser, on this computer only (ADR 14).

    It listens on 127.0.0.1 alone, reads as the read-only role ifc_reader, as serve does,
    and prints the page's URL on stdout. The URL holds a token, new on every start, that
    the page needs. A model shows once ingest --viewer has built its scene. The page also
    lists the calls of every MCP server on the database run with IFC_SQL_VIEWER_FEED=1,
    and lights what each found. Stop with Ctrl+C. Exit codes: 0 when stopped; 1 when the
    database is unreachable or not at the latest migration, or the port is taken; 2 for a
    usage error (no such project or model).
    """
    settings = _database_settings(ctx)
    database = redact(reader_database_url(settings.database_url, settings.reader_password))
    engine = make_reader_engine(settings, application="viewer")
    try:
        _require_latest_revision_as_reader(engine, database, "view")
        _check_view_choice(engine, project, model_ids or [])
        with _bound(port) as sock, _listening(engine, database) as (reader, feed):
            _view(engine, sock, database, project, model_ids or [], reader, feed, no_open=no_open)
    except KeyboardInterrupt:
        # uvicorn stops on Ctrl+C, then raises the signal again once it has.
        typer.echo("stopped the viewer", err=True)
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()


def _bound(port: int) -> socket.socket:
    """A socket on 127.0.0.1:``port``, bound here; exit 1 with a message if it is taken.

    Bound here, not by uvicorn, so that a taken port is an error to report: uvicorn calls
    sys.exit(3) when its own bind fails.
    """
    try:
        return socket.create_server((HOST, port))
    except OSError as exc:
        typer.echo(
            f"cannot listen on {HOST}:{port}: {exc.strerror or exc}; pick another --port, "
            "or leave it out for a free one",
            err=True,
        )
        raise typer.Exit(1) from exc


@contextmanager
def _listening(engine: Engine, database: str) -> Iterator[tuple[Reader, Feed]]:
    """The feed's reader and feed for ``engine``, its listener listening until the block ends.

    Its sessions: one for the listener, and at most three for the page's routes and the
    feed's selections together (ADR 14, decision 4).
    """
    reader = Reader(engine)
    feed = Feed(Resolver(reader, argument_checks(build_server(engine))), database=database)
    listener = Listener(lambda: listen_connection(engine), feed)
    listener.start()
    try:
        yield reader, feed
    finally:
        listener.stop()
        feed.close()


def _serving(
    engine: Engine,
    sock: socket.socket,
    database: str,
    reader: Reader,
    feed: Feed,
    *,
    project: str | None,
    model_ids: list[int],
    no_open: bool,
) -> uvicorn.Server:
    """The uvicorn server of the page on ``sock``, after printing its URL and opening it."""
    port = sock.getsockname()[1]
    token = new_token()
    viewer = create_app(
        engine, token, port=port, project=project, model_ids=model_ids, feed=feed, reader=reader
    )
    address = f"http://{HOST}:{port}/"
    logger.info("viewing", extra={"database": database, "address": address})
    # The URL with its token goes to stdout, once: it is the user's to open. Never to stderr
    # or the log.
    url = f"{address}#t={token}"
    typer.echo(url)
    typer.echo(f"showing the models at {address}; stop with Ctrl+C", err=True)
    if not no_open:
        webbrowser.open(url)
    # uvicorn says at INFO what the line above says, with ANSI colours in an extra field.
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    config = uvicorn.Config(
        viewer,
        lifespan="off",
        ws="none",
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=2,
    )
    return uvicorn.Server(config)


def _view(
    engine: Engine,
    sock: socket.socket,
    database: str,
    project: str | None,
    model_ids: list[int],
    reader: Reader,
    feed: Feed,
    *,
    no_open: bool,
) -> None:
    """Serve the viewer on ``sock`` until Ctrl+C, after printing its URL."""
    server = _serving(
        engine,
        sock,
        database,
        reader,
        feed,
        project=project,
        model_ids=model_ids,
        no_open=no_open,
    )
    server.run(sockets=[sock])


def _serve_client(errlog: TextIO) -> Client:
    """A client of ``ifc-sql serve``, started over stdio as a client starts it, feed on."""
    environment = {key: os.environ[key] for key in PASSED_ON if key in os.environ}
    environment["IFC_SQL_VIEWER_FEED"] = "1"
    command, *arguments = SERVE
    server = StdioServerParameters(command=command, args=arguments, env=environment)
    return Client(stdio_client(server, errlog=errlog))


@app.command()
def demo(
    ctx: typer.Context,
    script: Annotated[
        Literal["duplex", "audit"],  # the names of SCRIPTS
        typer.Option(
            "--script",
            help="The questions to play. duplex: the four Duplex models. audit: four of the "
            "audit's Revit exports, local only (scripts/demo.py --script audit loads them).",
        ),
    ] = "duplex",
    step: Annotated[
        bool, typer.Option("--step", help="Wait for Enter before each question.")
    ] = False,
    pause: Annotated[
        float,
        typer.Option("--pause", min=0, metavar="SECONDS", help="Seconds between two questions."),
    ] = 5.0,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Serve no page: play every question, compare each with the script, and "
            "exit 1 on a difference.",
        ),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port", min=0, max=65535, help="The port on 127.0.0.1; 0, the default, a free one."
        ),
    ] = 0,
    no_open: Annotated[
        bool, typer.Option("--no-open", help="Print the URL, but do not open the browser.")
    ] = False,
    export: Annotated[
        Path | None,
        typer.Option(
            "--export",
            metavar="DIR",
            help="Serve no page: play and compare every question as --check does, then "
            "write the page, the scenes and the calls into DIR, a new or empty folder, as a "
            "static site that plays them back --pause seconds apart. The Duplex only.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Play a scripted demo through the MCP server, and light each answer in the viewer.

    No LLM and no MCP client of yours: the demo starts ifc-sql serve over stdio, as a
    client would, with IFC_SQL_VIEWER_FEED=1, and calls its tools; the page of ifc-sql
    view lights what each call found. Its files must be loaded with --viewer and checked:
    scripts/demo.py (--script audit for the audit's) does that, then runs this. The page
    follows the calls: one that lights a model of another project shows that project.
    With --check it serves no page and exits 1 when a step differs from the script; with
    --export DIR it checks the same, then writes the static copy of the demo into DIR.
    Exit codes: 0 when stopped (or checked, or exported); 1 when the database is
    unreachable, not at the latest migration or lacks the files or their scenes, the feed
    or the MCP server does not start, a step differs, or the port is taken; 2 for an
    --export folder that is not empty, or a script whose files may not be published.
    """
    settings = _database_settings(ctx)
    database = redact(reader_database_url(settings.database_url, settings.reader_password))
    played = SCRIPTS[script]
    if export is not None:
        try:
            demo_export.check_script(played)
            demo_export.check_folder(export)
        except demo_export.ExportError as exc:
            raise typer.BadParameter(str(exc), param_hint="'--export'") from exc
    engine = make_reader_engine(settings, application="viewer")
    try:
        _require_latest_revision_as_reader(engine, database, "demo")
        with _listening(engine, database) as (reader, feed):
            try:
                models = demo_play.find_models(reader, played)
            except demo_play.DemoError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from exc
            if not _live(feed):
                typer.echo(
                    f"the feed did not start listening on {database} within "
                    f"{FEED_START_SECONDS:g} seconds",
                    err=True,
                )
                raise typer.Exit(1)
            if check or export is not None:
                steps, agreed = _play(lambda client: _check(played, client, feed, models))
                if not agreed:
                    raise typer.Exit(1)
                if export is not None:
                    _export(reader, played, steps, models, export, pause)
                return
            with _bound(port) as sock:
                server = _serving(
                    engine,
                    sock,
                    database,
                    reader,
                    feed,
                    project=None,
                    model_ids=played.first_models(models),
                    no_open=no_open,
                )
                _play(lambda client: _show(played, client, feed, models, server, sock, pause, step))
    except KeyboardInterrupt:
        typer.echo("stopped the demo", err=True)
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()


def _live(feed: Feed) -> bool:
    deadline = time.monotonic() + FEED_START_SECONDS
    while feed.status().state != "live":
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _play[T](run: Callable[[Client], Awaitable[T]]) -> T:
    """Start ``ifc-sql serve`` and run ``run`` with its client; exit 1 if it does not start."""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:

        async def session() -> T:
            # A text file on every OS; typeshed types Windows' as a wrapper, not a TextIO.
            async with _serve_client(cast(TextIO, errlog)) as client:
                return await run(client)

        try:
            return anyio.run(session)
        except Exception as exc:  # the server stopped or never started: its stderr says why
            errlog.seek(0)
            said = errlog.read().strip().splitlines()
            why = said[-1] if said else str(demo_play.innermost(exc))
            typer.echo(f"the MCP server stopped: {why}", err=True)
            raise typer.Exit(1) from exc


async def _check(
    script: Script, client: Client, feed: Feed, models: dict[str, int]
) -> tuple[list[demo_play.Played], bool]:
    played = await demo_play.play(script, client, feed, models)
    lines, agreed = demo_play.report(script, played)
    for line in lines:
        typer.echo(line)
    return played, agreed


def _export(
    reader: Reader,
    script: Script,
    played: list[demo_play.Played],
    models: dict[str, int],
    folder: Path,
    pause: float,
) -> None:
    """Write the static copy of the demo that played; exit 1 if it cannot be."""
    try:
        exported = demo_export.export(reader, script, played, models, folder, pause_seconds=pause)
    except demo_export.ExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"exported the demo to {folder}: {exported.files} files, {exported.bytes / 1e6:.1f} MB; "
        f"to look at it, serve the folder: uv run python -m http.server -d {folder}"
    )


async def _show(
    script: Script,
    client: Client,
    feed: Feed,
    models: dict[str, int],
    server: uvicorn.Server,
    sock: socket.socket,
    pause: float,
    step: bool,
) -> None:
    """Serve the page while the questions play, a pause apart; then serve until Ctrl+C."""

    async def wait() -> None:
        if step:
            typer.echo("Enter for the next question", err=True)
            await anyio.to_thread.run_sync(sys.stdin.readline)
        else:
            await anyio.sleep(pause)

    async def between(_: demo_play.Played) -> None:
        await wait()

    async def steps() -> None:
        await wait()  # the page loads the models meanwhile
        played = await demo_play.play(script, client, feed, models, between=between)
        _, agreed = demo_play.report(script, played)
        verdict = "as the script says" if agreed else "with differences: run it with --check"
        typer.echo(f"played {len(played)} questions, {verdict}; stop with Ctrl+C", err=True)

    await demo_play.alongside(lambda: server.serve(sockets=[sock]), steps)
