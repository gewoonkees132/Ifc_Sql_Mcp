# Quick start on Windows

The same steps as the README's quick start, in PowerShell and without `make`. Each
command is one that a `make` target runs. CI runs them on Linux only, so this page has
not been run on Windows; report anything that differs there.

## Install once

```powershell
winget install --id astral-sh.uv -e
winget install --id Docker.DockerDesktop -e   # then start Docker Desktop once
winget install --id Git.Git -e
git clone https://github.com/gewoonkees132/Ifc_Sql_Mcp.git
cd Ifc_Sql_Mcp
uv sync --locked                              # make install, without the git hooks
```

## Start the database and create the schema

```powershell
docker compose up -d --wait                   # make up: PostgreSQL 16, on 127.0.0.1 only
uv run ifc-sql migrate                        # make migrate
```

The defaults (database `ifc` on `localhost:5432`, the development passwords in
`docker-compose.yml` and `src/ifc_sql_mcp/config.py`) need no environment variables. They
are public, which is safe only because PostgreSQL listens on loopback. To use your own,
set them for the session (and in `.env` for `docker compose`):

```powershell
$env:IFC_SQL_DATABASE_URL = "postgresql+psycopg://ifc:your-password@localhost:5432/ifc"
$env:IFC_SQL_READER_PASSWORD = "another-password"
```

## Load and check models

One file:

```powershell
uv run ifc-sql ingest C:\Exports\House.ifc --discipline ARC --project house
uv run ifc-sql dq
```

Or keep a folder in step. Every `.ifc` file Revit saves there is loaded once it has
stopped changing, a new export under the same name replaces its previous version, and
each new model is checked:

```powershell
uv run ifc-sql watch C:\Exports --discipline ARC --project house
```

Stop it with Ctrl+C. Add `--geometry` for exports made without quantities
([revit-export-settings.md](revit-export-settings.md)).

## Ask Claude

In Claude Desktop: Settings, Developer, Edit Config opens
`%APPDATA%\Claude\claude_desktop_config.json`. Add the server, with the full path of
`uv.exe` (`(Get-Command uv).Source` prints it) and of the clone. Backslashes in JSON are
doubled:

```json
{
  "mcpServers": {
    "ifc-sql": {
      "command": "C:\\Users\\you\\.local\\bin\\uv.exe",
      "args": ["run", "--directory", "C:\\Users\\you\\Ifc_Sql_Mcp", "ifc-sql", "serve"],
      "env": {
        "IFC_SQL_DATABASE_URL": "postgresql+psycopg://localhost:5432/ifc",
        "IFC_SQL_READER_PASSWORD": "ifc-reader-dev-only"
      }
    }
  }
}
```

Quit Claude Desktop completely and start it again. The server's log is
`%APPDATA%\Claude\logs\mcp-server-ifc-sql.log`. [docs/mcp.md](mcp.md) describes the
tools.

## Not yet

An embedded database, so that one user needs no Docker, is deferred (audit finding A18,
item 4). PostgreSQL in Docker is still required.
