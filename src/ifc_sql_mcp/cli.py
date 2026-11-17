"""Command-line entry point: ``ifc-sql``.

The root callback does the work every command shares (read settings, configure logging)
so individual commands stay focused on their own job.
"""

import dataclasses
import logging
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from ifc_sql_mcp import __version__
from ifc_sql_mcp.config import LOG_LEVELS, Settings, SettingsError, parse_log_level
from ifc_sql_mcp.db.engine import make_engine, make_reader_engine, reader_database_url, redact
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
from ifc_sql_mcp.ingest.pipeline import IngestError, IngestResult, check_discipline, ingest_file
from ifc_sql_mcp.logging_config import configure_logging
from ifc_sql_mcp.server.app import build_server

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="ifc-sql",
    help="Load Revit-exported IFC files into PostgreSQL and serve them to LLMs over MCP.",
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
    settings: Settings = ctx.obj
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
    """One line for stdout saying what the run did."""
    model = f"model {result.model_id} ({result.discipline})"
    if result.status == "succeeded":
        counts = result.row_counts
        return (
            f"loaded {file_name} as {model}: {counts['object']:,} objects, "
            f"{counts['property']:,} property values, {counts['ingest_issue']:,} issues "
            f"(run {result.run_id})"
        )
    skipped = f"run {result.run_id}, skipped"
    if result.concurrent:
        return (
            f"{file_name} was loaded as {model} by another ingest running at the same time; "
            f"nothing written ({skipped})"
        )
    return f"{file_name} is already loaded as {model}; nothing written ({skipped})"


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
) -> None:
    """Load one IFC file. A file whose exact bytes are already loaded is skipped.

    Exit codes: 0 when the file was loaded or already was; 1 when the ingest failed
    (unreadable file, database unreachable or not migrated, load error); 2 for a usage
    error (no such file, invalid discipline).
    """
    settings: Settings = ctx.obj
    database = redact(settings.database_url)
    logger.info("ingesting", extra={"file": str(path), "discipline": discipline})
    engine = make_engine(settings.database_url)
    try:
        _require_latest_revision(settings, "ingest")
        result = ingest_file(engine, path, discipline)
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


def _dq_summary(result: DqResult) -> str:
    """One line for stdout saying what a run found."""
    counts = result.severity_counts
    return (
        f"checked model {result.model_id} ({result.discipline}, {result.file_name}) in dq run "
        f"{result.run_id}: {counts['error']:,} error, {counts['warning']:,} warning, "
        f"{counts['info']:,} info findings"
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
    settings: Settings = ctx.obj
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


def _require_latest_revision_as_reader(engine: Engine, database: str) -> None:
    """Like ``_require_latest_revision``, but as ``ifc_reader``: serve has no other role."""
    try:
        with engine.connect() as connection:
            revision = reader_revision(connection)
    except RevisionUnreadableError as exc:
        typer.echo(
            f"the database at {database} is at a revision before {READER_READS_REVISION_FROM}, "
            f"whose version table ifc_reader may not read, but serve needs revision "
            f"{head_revision()}: run `ifc-sql migrate` first",
            err=True,
        )
        raise typer.Exit(1) from exc
    _check_revision(revision, database, "serve")


@app.command()
def serve(ctx: typer.Context) -> None:
    """Serve the loaded models to an MCP client over stdio, read-only.

    An MCP client starts this command and exchanges JSON-RPC messages with it over stdin
    and stdout; logs go to stderr. It connects only as the read-only role ifc_reader, with
    IFC_SQL_READER_PASSWORD (the credentials in IFC_SQL_DATABASE_URL are not used), and
    checks the database before it serves. Exit codes: 0 when the client closes the
    connection; 1 when the database is unreachable or not at the latest migration.
    """
    settings: Settings = ctx.obj
    database = redact(reader_database_url(settings.database_url, settings.reader_password))
    engine = make_reader_engine(settings)
    try:
        # Fail before serving: a client shows a server that does not start as failed,
        # while one that starts but fails every call looks healthy.
        _require_latest_revision_as_reader(engine, database)
        server = build_server(engine)
        logger.info("serving", extra={"database": database, "transport": "stdio"})
        server.run("stdio")
    except OperationalError as exc:
        typer.echo(f"cannot reach the database at {database}: {exc.orig}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()
