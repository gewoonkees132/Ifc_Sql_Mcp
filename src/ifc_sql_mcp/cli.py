"""Command-line entry point: ``ifc-sql``.

The root callback does the work every command shares (read settings, configure logging)
so individual commands stay focused on their own job.
"""

import dataclasses
import logging
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.exc import OperationalError

from ifc_sql_mcp import __version__
from ifc_sql_mcp.config import LOG_LEVELS, Settings, SettingsError, parse_log_level
from ifc_sql_mcp.db.engine import make_engine, redact
from ifc_sql_mcp.db.migrate import current_revision, head_revision, upgrade
from ifc_sql_mcp.ingest.pipeline import IngestError, IngestResult, check_discipline, ingest_file
from ifc_sql_mcp.logging_config import configure_logging

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
        revision = current_revision(settings)
        if revision != head_revision():
            found = "not migrated" if revision is None else f"at revision {revision}"
            typer.echo(
                f"the database at {database} is {found}, but ingest needs revision "
                f"{head_revision()}: run `ifc-sql migrate` first",
                err=True,
            )
            raise typer.Exit(1)
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
