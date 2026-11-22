# Quick start on Windows

The README's quick start in PowerShell, without `make`. Each command is one that a
`make` target runs. CI runs these steps on Windows on every push (the `windows` job in
`.github/workflows/ci.yml`), on the embedded database below.

## Install once

```powershell
winget install --id astral-sh.uv -e
winget install --id Git.Git -e
git clone https://github.com/gewoonkees132/Ifc_Sql_Mcp.git
cd Ifc_Sql_Mcp
uv sync --locked --extra embedded             # make install, without the git hooks
```

## Choose a database

### For one user: the embedded database, no Docker

`ifc-sql` runs PostgreSQL 16 itself, in a folder of yours. Set the folder once, for your
user, then open a new PowerShell window and create the schema:

```powershell
[Environment]::SetEnvironmentVariable("IFC_SQL_DATA_DIR", "$env:LOCALAPPDATA\ifc-sql\data", "User")
# a new window, so the variable is set
uv run ifc-sql migrate
```

The first command that needs the database starts it, and the last one to finish stops
it. `ingest`, `watch` and the MCP server can run at once.

It listens only on 127.0.0.1 and takes no password, so it is for a computer only you
use ([SECURITY.md](../SECURITY.md#the-embedded-database)). It is the same PostgreSQL as
the Docker one, with the same read-only role for Claude ([ADR 13](adr/0013-embedded-postgresql.md)).

### Shared, or on a server: PostgreSQL in Docker

```powershell
winget install --id Docker.DockerDesktop -e   # then start Docker Desktop once
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

Set `IFC_SQL_DATA_DIR` or `IFC_SQL_DATABASE_URL`, not both.

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
doubled. With the embedded database:

```json
{
  "mcpServers": {
    "ifc-sql": {
      "command": "C:\\Users\\you\\.local\\bin\\uv.exe",
      "args": ["run", "--directory", "C:\\Users\\you\\Ifc_Sql_Mcp", "ifc-sql", "serve"],
      "env": {
        "IFC_SQL_DATA_DIR": "C:\\Users\\you\\AppData\\Local\\ifc-sql\\data"
      }
    }
  }
}
```

With Docker, give the server the database instead:

```json
      "env": {
        "IFC_SQL_DATABASE_URL": "postgresql+psycopg://localhost:5432/ifc",
        "IFC_SQL_READER_PASSWORD": "ifc-reader-dev-only"
      }
```

Quit Claude Desktop completely and start it again. The server's log is
`%APPDATA%\Claude\logs\mcp-server-ifc-sql.log`. [docs/mcp.md](mcp.md) describes the
tools.
