# Architecture decision records

Each record states one decision, the context that forced it, its consequences and the
alternatives that were rejected. Records are not rewritten when the decision changes: a
new record supersedes the old one.

| ADR | Decision |
|---|---|
| [1](0001-postgresql.md) | PostgreSQL as the store, accessed through SQLAlchemy Core and psycopg 3 |
| [2](0002-properties-eav-vs-jsonb.md) | Properties as a typed EAV table on a shared object supertable |
| [3](0003-quantity-sources-and-precedence.md) | Quantity sources and precedence: `ifc_qto` > `revit_pset` > `geometry` |
| [4](0004-idempotent-ingest.md) | Idempotent ingest: files identified by SHA-256, every attempt recorded, issues stored with the model |
| [5](0005-data-quality-rules.md) | Data quality rules in a checked YAML catalogue, synchronised into `dq_rule` and evaluated in SQL, one recorded run per model |
| [6](0006-mcp-server.md) | An MCP server of eight read-only tools on the read-only role, a new session per call, and `run_sql` behind four layers: validator, cursor, read-only transaction, privileges |
