"""Command-line entry point: ``ifc-sql``.

The root callback does the work every command shares (read settings, configure logging)
so individual commands stay focused on their own job.
"""

import dataclasses
from typing import Annotated

import typer

from ifc_sql_mcp import __version__
from ifc_sql_mcp.config import LOG_LEVELS, Settings, SettingsError, parse_log_level
from ifc_sql_mcp.logging_config import configure_logging

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
