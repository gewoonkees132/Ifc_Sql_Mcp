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
| [7](0007-quantity-set-plausibility.md) | Physical bounds on quantity sets: a duplicated value is settled by the bounds, a value that breaks one is kept but flagged and becomes no quantity |
| [8](0008-ifc-base-quantities.md) | Reading IFC base quantities: rules per standard set, IFC2X3 `BaseQuantities` read as the class's set, net first with gross as a fallback, a slab's area from `GrossArea` only, and `width_m` |
| [9](0009-schema-discovery-tool.md) | The schema as a `describe_schema` tool next to the resource, and `run_sql` errors about an unknown relation or column that list what exists |
| [10](0010-geometry-quantities.md) | Quantities measured from the geometry with IfcOpenShell, on request (`ingest --geometry`): wall side areas, slab and space footprints, solid volumes, ranked last |
| [11](0011-space-plans.md) | Every space's plan measured at ingest: a space whose plan lies 90 % inside larger ones repeats them; Revit Areas kept apart from rooms |
| [12](0012-measure-in-place.md) | `ingest --geometry` on a file already loaded without it measures it into the same model, once; supersedes ADR 10's "delete and load again" |
| [13](0013-embedded-postgresql.md) | An embedded PostgreSQL 16 (`IFC_SQL_DATA_DIR`, the `embedded` extra) for one user without Docker: the same views and read-only role, no passwords, run on Windows by CI |
