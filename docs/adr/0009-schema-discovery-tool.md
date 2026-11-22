# ADR 9: The schema as a tool, and errors that name what exists

- Status: accepted; amends [ADR 6](0006-mcp-server.md) (decision 8)
- Date: 2026-11-21
- Audit finding: [A10](../audit/2026-11-modern-revit-exports.md#a10-the-llm-cannot-find-the-schema)

## Context

ADR 6 offered the schema as the `ifc://schema` resource only. It argued that a client
without resources "loses nothing essential", because `run_sql` errors point to the
resource. The audit tested that claim in six real sessions, run the way Claude Desktop
runs them: the user attaches resources, so the model cannot read one. **20 of 72 tool
calls (28 %) failed**, all on finding the schema:

- 14 guessed relation names: `v_property` (5 times), `property_value`,
  `v_property_value`, `v_element_property`, `v_space` and `space`;
- 4 queries of `information_schema`, which the validator refuses;
- 2 guessed column names: `ifc_class_norm` on `v_element`, `ifc_class` on `v_takeoff`.

Each error named only what was wrong. The refusal of `information_schema` pointed to a
resource the model could not open. One session spent 7 of its 17 calls this way.

## Decision

1. **A `describe_schema` tool** reads the same live catalogue as the resource. Without
   arguments it lists every view and table with its comment and column names, views
   first: 8,295 characters on the Duplex database, a third of the resource. With a
   relation it gives that one in full: column types, nullability, comments and a
   table's keys. The name may be qualified (`ifc.property`), quoted or in any case. The
   resource stays, for clients that attach it.
2. **An unknown relation or column gets its answer in the error.** `run_sql` runs the
   query in a savepoint. When PostgreSQL reports `undefined_table` (42P01) or
   `undefined_column` (42703), it rolls back to the savepoint, reads the catalogue and
   appends what exists:
   - for a relation: the closest names (`difflib`), every relation of the schema, and
     the columns of `property`;
   - for a column: the closest names, and the columns of the relations the query reads
     (sqlglot finds them, leaving out CTEs). A qualifier that names a table's alias
     narrows that list to the table.
   PostgreSQL's own hint is kept.
3. **The descriptions lead there.** The server instructions and the `run_sql`
   description name `describe_schema`. The `run_sql` description also names
   `property(object_id, pset_name, prop_name, value_text, value_num, value_bool, unit)`,
   which nearly every question beyond counts needs. The validator's refusal of another
   schema or a system catalogue says "call describe_schema".

## Consequences

- `tests/integration/test_schema_discovery.py` replays the 20 failed calls of the
  sessions. Each error now names the relation or column to use, and none points to the
  resource.
- The savepoint costs two statements on every query. A failed query costs two catalogue
  reads more. Both take well under a millisecond next to a query's tens.
- `describe_schema` returns only the schema's own documentation, not text from IFC
  files, and its description says so. It is the one tool whose description does not
  warn about IFC text.

## Alternatives considered

- **The whole schema in the server instructions.** That is 26,541 characters in every
  conversation, most of them unused. It would also be wrong after a migration until the
  client reconnects.
- **Views named after the guesses (`v_property`, `v_space`).** These would answer this
  audit's sessions and miss the next model's guesses. An error that lists the real names
  works for any guess.
- **Pre-checking every name with sqlglot before execution.** This would duplicate
  PostgreSQL's name resolution, with its search path and its CTE scoping, on every
  query. Asking the catalogue only after PostgreSQL has refused a name keeps one
  authority.
