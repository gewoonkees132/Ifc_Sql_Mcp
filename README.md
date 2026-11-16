# ifc-sql-mcp

[![CI](https://github.com/gewoonkees132/Ifc_Sql_Mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/gewoonkees132/Ifc_Sql_Mcp/actions/workflows/ci.yml)

Load IFC files exported from Autodesk Revit into a normalised PostgreSQL database and
expose the data to LLMs through an MCP server.

> Status: under active development. This README is a placeholder until the pipeline is
> complete; the full write-up (architecture, data model, data-quality findings, MCP tools)
> will replace it.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make up        # start PostgreSQL 16 (docker compose) and wait until it is healthy
make migrate   # create the ifc schema: tables, views and the read-only role
make test      # unit + integration tests with coverage (needs `make up`)
make lint      # ruff lint + format check
make help      # every target
```

Download the pinned, checksum-verified sample models (not stored in this repo):

```bash
make samples   # or: uv run python scripts/download_samples.py arc
```

The schema is described in [docs/data-model.md](docs/data-model.md); design decisions are
recorded in [docs/adr/](docs/adr/).

## Licences

Code: [MIT](LICENSE). Sample data: CC BY 4.0, see [DATA_ATTRIBUTION.md](DATA_ATTRIBUTION.md).
