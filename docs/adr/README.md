# Architecture decision records

Each record states one decision, the context that forced it, its consequences and the
alternatives that were rejected. Records are not rewritten when the decision changes: a
new record supersedes the old one.

| ADR | Decision |
|---|---|
| [1](0001-postgresql.md) | PostgreSQL as the store, accessed through SQLAlchemy Core and psycopg 3 |
| [2](0002-properties-eav-vs-jsonb.md) | Properties as a typed EAV table on a shared object supertable |
