# ADR 13: An embedded PostgreSQL for one user, so that Docker is optional

- Status: accepted; adds to [ADR 1](0001-postgresql.md). Decision 3 amended by
  [ADR 14](0014-viewer.md): `ifc-sql view` and `ifc-sql demo` attach to the embedded
  database too.
- Date: 2026-11-22
- Audit finding: [A18](../audit/2026-11-modern-revit-exports.md#a18-the-path-from-revit-to-a-question-is-long),
  item 4, deferred in the first round and done as
  [A18.4](../audit/2026-11-recheck.md#a184-the-embedded-database-still-deferred)

## Context

An architect on Windows needed Docker Desktop to run a database that only they use.
The audit asked for "an embedded database option, so Docker is not required for one
user", and the first round deferred it. Two things rule out another database engine:

- every view is PostgreSQL's SQL;
- the MCP server's safety rests on PostgreSQL's read-only role, `ifc_reader`, and on
  read-only transactions (ADR 6).

## Decision

1. **Embed PostgreSQL itself.** The optional extra `embedded` installs
   [`pixeltable-pgserver`](https://pypi.org/project/pixeltable-pgserver/) (Apache-2.0).
   - It ships PostgreSQL 16.15 binaries as wheels for Linux, macOS and Windows: the
     version CI and the audit use. `db/embedded.py` asks for 16 by name.
   - About 150 MB, so only who asks for it installs it.
2. **One setting, `IFC_SQL_DATA_DIR`**, a folder, in place of `IFC_SQL_DATABASE_URL`.
   Setting both is an error.
3. **Started by the commands that need it.** `migrate`, `ingest`, `dq`, `watch`,
   `serve`, `view` and `demo` start a server on the folder, or attach to the one already
   running there. (`view`, the viewer's page, and `demo`, its scripted questions, were
   added by [ADR 14](0014-viewer.md): like `serve`, they read as `ifc_reader`, and they
   can run while the others do. `demo` hands `IFC_SQL_DATA_DIR` to the `serve` it starts,
   which attaches to the same server.)
   - `--help` and `--version` start nothing.
   - The server stops when the last process attached to it exits. Several commands can
     run at once.
4. **Everything else is unchanged.**
   - The schema lives in a database `ifc`, created on first use, owned by the superuser
     `postgres`.
   - Migrations create `ifc_reader` in it, and `serve` connects as `ifc_reader`, as on
     any other server.
5. **No passwords.** pgserver initialises the cluster with `trust`.
   - On Linux and macOS it listens on a Unix socket in the data folder, which `initdb`
     makes readable by its owner only. When that path is too long for a socket, it
     listens in a folder under the runtime directory (`XDG_RUNTIME_DIR`, else the
     temporary directory).
   - On Windows it listens on a free port of 127.0.0.1.
   - SECURITY.md says what that means.
6. **Proven on Windows.** A CI job runs `docs/windows.md`'s steps there on every push:
   migrate, ingest, dq, watch and the MCP server over stdio. It also runs the embedded
   database's tests.

## Consequences

- One user needs no Docker, no database password and no URL.
- The Windows quick start is run by CI, not only written. The first round's test plan
  left that box unticked.
- A command that starts the server pays its start-up, about 1.5 s (initdb on first use
  included), unless another command or the MCP server is running.
- **On Windows, a server that stopped and starts again listens on another free port**
  (found in the viewer's Phase 0, [ADR 14](0014-viewer.md) P7). `serve` resolves the URL
  once, when it starts, so a `serve` still running after the database stopped under it
  keeps the old port, and its calls fail until the client restarts it. `docs/windows.md`
  says so. `ifc-sql view` resolves the URL once too, and needs the same restart.
- **Trust authentication.**
  - On Linux and macOS, with the socket in the data folder, only its user connects. With
    the socket under the temporary directory, other local users may reach it.
  - On Windows any local user of the computer can connect to the port, as the superuser.
  - The embedded database is therefore for a computer only its user uses. A shared
    computer, or a server, should run PostgreSQL in Docker or as a service, with
    passwords. Passwords for the embedded database, and a private socket in every case,
    are left for later.
- `.github/workflows/ci.yml` installs the extra in the main job, whose coverage gate
  includes `db/embedded.py`.

## Alternatives considered

- **SQLite or DuckDB.** Every view, migration and the read-only role would need a second
  version, and the read-only guarantees of ADR 6 would rest on that engine instead.
- **PGlite** (PostgreSQL compiled to WebAssembly). It is a single connection in a
  JavaScript process, with no roles to speak of.
- **Download PostgreSQL's binaries on first run.** That means a network call at first
  start, and a checksum of our own to keep. A wheel from PyPI is locked in `uv.lock`
  like every other dependency.
