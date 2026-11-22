# ADR 1: PostgreSQL as the store, accessed through SQLAlchemy Core and psycopg 3

- Status: accepted; amended by [ADR 14](0014-viewer.md): a second schema, `ifc_viewer`
- Date: 2026-11-16

## Context

The pipeline turns Revit IFC exports into normalised tables. Two kinds of client use
them:

- the loader, which writes one model per transaction;
- an MCP server, through which an LLM reads the data, partly with SQL the LLM writes
  itself.

The store therefore has to:

1. **Enforce the data model**, not just hold it: keys, foreign keys with cascades,
   CHECK constraints on formats and value ranges, and references checked at commit.
   A bad load should fail loudly, not leave subtly wrong rows.
2. **Give LLM-written SQL a real security boundary**: a role that can only read one
   schema, with server-side read-only transactions and statement timeouts, independent
   of the application code.
3. **Migrate safely**: transactional DDL, so a failed migration leaves nothing half
   applied.
4. **Describe itself**: comments on tables and columns, which the MCP `ifc://schema`
   resource will serve to the LLM.
5. **Answer the questions people ask of a building model**: schedules, takeoffs,
   "which walls on level 2", grouping and pivoting, which are tabular questions. SQL is
   the query language both analysts and LLMs are most practised in.
6. **Run the same way in development and CI**: a Docker image locally, a service
   container on GitHub Actions.

The data is small. The four Duplex sample files total about 53 MB, and the
architectural model has about 5,200 property values. Analytical throughput is not a
deciding factor.

## Decision

Use **PostgreSQL 16**, with:

- **one dedicated schema**, `ifc`, for all tables and views;
- **SQLAlchemy 2 Core** for table definitions and statements (not the ORM);
- **psycopg 3** as the driver;
- **Alembic** for migrations.

Core rather than the ORM, because:

- the loader does bulk inserts and the readers run set-based SQL and views, so an
  identity map and unit-of-work add nothing;
- Core statements read like the SQL they produce, which keeps the code easy to check
  against the schema;
- the same `Table` objects drive migrations (autogenerate) and queries.

## Consequences

- PostgreSQL features are used freely where they make the model clearer:
  `DISTINCT ON`, `FILTER`, `LATERAL`, `num_nonnulls`, `jsonb`, deferrable constraints,
  `ALTER ROLE … IN DATABASE`, catalogue comments. Portability to another database is
  explicitly not a goal.
- Development and tests need Docker (or another PostgreSQL 16). `make up` starts it;
  the tests create and migrate their own throwaway databases.
- Roles are cluster-wide while migrations are per database. The read-only role is
  therefore created idempotently and kept on downgrade (see `docs/data-model.md`).
- Migration code sometimes drops to the psycopg connection: for DDL that needs quoted
  literals (`psycopg.sql`), for client-side password hashing, and for running SQL files
  verbatim. Those places are documented in `ifc_sql_mcp/db/migrate.py`.

## Alternatives considered

- **SQLite.** No server and trivially portable, but it has:
  - no roles or GRANTs, so read-only access depends on how the file is opened, not on
    a privilege boundary;
  - no server-side statement timeout;
  - no schemas, and weaker typing and constraint options.

  The LLM-facing security requirement (2) rules it out.
- **DuckDB.** Excellent for analytics, and its SQL is close to PostgreSQL's. But it has
  no roles or privileges, and it is an embedded single-writer file: the MCP server
  reading while a load runs means coordinating file locks. Columnar speed does not
  matter at this data size.
- **A graph database (e.g. Neo4j).** IFC is a graph, but the questions this project
  serves are tabular (schedules, takeoffs, filters). Cypher is less familiar to analysts
  and LLMs than SQL, and the relational constraints in requirement (1) would move into
  application code.
- **A document store, or IFC as JSON in one column.** Loading is easy, but it gives up
  integrity constraints and turns every query into path navigation. ADR 2 discusses the
  narrower jsonb variant for properties.
- **An IFC model server (e.g. BIMserver).** It understands IFC natively, but queries
  go through its own API rather than SQL. It is a heavier dependency, and it does not
  give an LLM a constrained SQL surface.
