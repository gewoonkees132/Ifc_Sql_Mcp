"""IFC → PostgreSQL → MCP pipeline for Revit-exported building models."""

from importlib.metadata import version

# Read from the installed distribution so pyproject.toml stays the single source of truth.
__version__ = version("ifc-sql-mcp")
