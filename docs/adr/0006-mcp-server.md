# ADR 6: An MCP server of read-only tools on the read-only role, with run_sql behind four layers

- Status: accepted
- Date: 2026-11-17

## Context

The models are loaded (ADR 4) and checked (ADR 5). Phase 6 serves them to LLMs through
the Model Context Protocol, from a process an MCP client such as Claude Desktop or Claude
Code starts on the user's machine. The design is shaped by these requirements:

1. **The common questions are answered reliably without SQL**: what is in a model, its
   storeys, which elements match, everything about one element, takeoffs, the room
   schedule, the data quality. Several of them have a trap an LLM writing SQL falls into
   (below), so their answer should be written once and tested.
2. **The other questions can still be asked**, in SQL, which makes LLM-written SQL
   untrusted input: it must not change anything, harm the database or other sessions,
   or run or return without bound.
3. **Content from IFC files is untrusted.** Names, property values and finding messages
   come from files anyone can write, and reach the LLM through tool results.
4. **Results go into a context window**: bounded, structured, with units, and errors
   that say what to do. Claude Code warns above 10,000 tokens of tool output and saves
   a text result of more than 50,000 characters to a file instead of showing it to the
   model ([its documentation](https://code.claude.com/docs/en/mcp)).
5. **stdout is the protocol.** Over stdio, every byte on stdout must be a JSON-RPC
   message.
6. **Least privilege**: the server holds no more access than it needs.

The traps, for the record: `v_takeoff` counts an element once per material and gives a
layered material the element's area once per layer, so its totals are not element
totals; "the latest successful data quality run" has to be chosen the way
`v_model_summary` chooses it; and a model never checked has NULL counts, which an
aggregate turns into 0, "no findings".

## Decision

### 1. The official MCP Python SDK, over stdio only

The server is the SDK's `MCPServer` (package `mcp`, version 2.3, pinned below 3),
started by `ifc-sql serve`. The SDK speaks both protocol eras: the initialize handshake
(revision 2025-11-25) and the 2026-07-28 negotiation, and the tests run both. It derives
each tool's input schema from its typed signature and its output schema from the
pydantic model it returns, and it already withholds the text of an unexpected exception
from the client.

stdio is the only transport. The server is meant for one user's client on one machine,
and the HTTP transport would bring authentication, origin checks and a listening port
with it, none of which this phase needs (SECURITY.md lists what is not protected).

### 2. One role: the server connects only as `ifc_reader`

No tool writes, so the server never holds a writable connection, not even at startup:

- **Credentials.** `ifc-sql serve` builds its URL with `reader_database_url()`: host,
  port and database from `IFC_SQL_DATABASE_URL`, user `ifc_reader`, password
  `IFC_SQL_READER_PASSWORD`. The owner's password in that URL is never used and can be
  left out, so a client's configuration file need not hold it.
- **Migration 0009** grants `ifc_reader` SELECT on `public.alembic_version`, so the
  server can refuse a database at an old revision, as ingest and dq do, without the
  owner's credentials. Testing the boundary for this ADR also showed that PostgreSQL
  grants every role TEMPORARY on every database: `ifc_reader` could create and fill a
  temporary table in a read-write transaction. 0009 revokes TEMPORARY from PUBLIC; the
  owner keeps it.
- **A new session per call.** The engine has no pool (`NullPool`): each tool call opens
  a session and closes it, so nothing one call leaves in its session can reach the next.
  That matters because a session-level advisory lock outlives the transaction that took
  it, even when the transaction is rolled back (measured for a committed one; PostgreSQL
  documents the rollback case). A new session cost
  13.1 ms (median of 20) against 1.4 ms from a pool on the development machine, which is
  nothing next to an LLM's tool call.
- **One transaction per call**, begun `REPEATABLE READ READ ONLY` by the client (on top
  of the role's `default_transaction_read_only`), so a tool that counts and then fetches
  a page sees one snapshot. Inside it, `SET LOCAL` sets `statement_timeout` (5 s),
  `lock_timeout` (1 s), `search_path` (`ifc`) and `jit` (off), whatever the role's
  defaults say. It always ends in a rollback, which also undoes a setting a query
  changed for the rest of the session. JIT is off because `ifc.model` has four rows,
  fewer changes than autovacuum waits for before it analyzes a table, so the planner
  assumed 210 models and costed `v_model_summary` at about 1.06 million, above the
  100,000 at which PostgreSQL JIT-compiles. EXPLAIN ANALYZE showed 425 ms of compiling
  for a query that runs in about 60 ms: `list_models` took 0.645 s, and 0.059 s without JIT.

### 3. Fail fast at startup, report per call afterwards

Before it reads its first message, `ifc-sql serve` connects as `ifc_reader` and checks
the revision. An unreachable database, a wrong reader password, a database not migrated
or at an older revision: the command prints the reason to stderr, without a password,
and exits 1. A client shows a server that does not start as failed and keeps its stderr
(Claude Desktop writes it to `mcp-server-NAME.log`), while a server that started but
failed every call would look healthy and give the LLM an error per question. An outage
after startup is reported per call ("the database cannot be reached right now"), and
the next call simply connects again.

### 4. Seven fixed tools, and run_sql for the rest

| Tool | Answers |
|---|---|
| `list_models` | What is loaded, with counts and the data quality status. |
| `list_storeys` | A model's storeys, elevations and how many elements and spaces each holds. |
| `find_elements` | Which elements match a class, storey, Revit family or type, or part of a name; paginated. |
| `get_element` | Everything about one object: identity, location, quantities with source, properties by set, classifications, materials, findings. |
| `quantity_takeoff` | One metric totalled per storey, class, Revit type or material. |
| `space_schedule` | The room schedule. |
| `data_quality_report` | The latest check: counts per rule and a sample of findings. |
| `run_sql` | Anything else, in SQL. |

Each fixed tool is one query shape with bind parameters. Its traps are handled once:
`quantity_takeoff` totals elements from `v_element` (each element once) for storey,
class and Revit type, and materials from `v_takeoff` for material, and every result
names its basis and explains it; "never checked" is a status, with null counts, never
zero. `group_by` and `metric` are an enum in the input schema, and the code looks them
up in fixed tables before it puts any SQL together, so no argument is formatted into
SQL. The tool descriptions are written for the LLM: what comes back, in which units,
the limits, and which tool to use instead.

`run_sql` covers the long tail (which walls are external, a property's values across a
model), with the `ifc://schema` resource as its map.

### 5. run_sql: four layers, each tested on its own against PostgreSQL

| Layer | What it stops | Holds even if |
|---|---|---|
| 1. Validator (`sql_guard`, sqlglot) | Everything but one read-only query on schema `ifc` with allowed functions, with a reason the LLM can act on | — |
| 2. Executor (`DECLARE … CURSOR` over the extended protocol, no parameters) | A second statement, anything but SELECT or VALUES, a data-modifying WITH, SELECT INTO; fetching more than `max_rows + 1` rows | sqlglot and PostgreSQL parse a text differently |
| 3. Transaction (read-only, `SET LOCAL` limits, new session, rollback) | Writes a SELECT can make (FOR UPDATE, `lo_create`), long queries, waits for locks, state left for the next call | the query was let through |
| 4. Privileges of `ifc_reader` | Any write to any table, sequence or file, temporary tables | the client turned the read-only transaction off |

The tests send each refused statement past the layers above the one under test: past
the validator to the executor, and with `SET TRANSACTION READ WRITE` to the privileges.

**The validator** runs before the session opens, so a refused query never reaches the
database. Its rules, in order: at most 20,000 characters and no NUL; it parses with
sqlglot's `postgres` dialect (otherwise sqlglot's reason, with line and column); exactly
one statement; that statement is SELECT, WITH … SELECT, TABLE, a set operation or
VALUES; no INSERT, UPDATE, DELETE, MERGE or COPY anywhere, including inside WITH; no
SELECT … INTO; no locking clause; only schema `ifc` (a qualified name must be in `ifc`,
and an unqualified `pg_*` name, which PostgreSQL resolves in `pg_catalog` first, must be
a CTE; all 140 catalogue relations start with `pg_`); functions from the allowlist; no
schema-qualified calls. The query that runs is the text the LLM wrote, never sqlglot's
regeneration of it, so a parser difference can only make the database refuse a query.

**SHOW and EXPLAIN are refused.** Neither answers a question about a building; sqlglot
parses both as an opaque command whose contents cannot be checked; EXPLAIN ANALYZE
executes the statement it explains; and the executor could not run them anyway, since
DECLARE takes only a query.

**Functions: an allowlist, not a denylist.** Of the 2,644 functions in `pg_catalog`
that `ifc_reader` may execute, sqlglot parses 173 into typed nodes: standard aggregates,
maths, text, dates, JSON and window functions, none with a side effect. The validator
allows those, and of the 2,467 others, which it parses as anonymous calls, only the 55 in
`ALLOWED_UNTYPED_FUNCTIONS` (JSON, arrays, regular expressions, a few date and number
helpers). A test pins the 173 names, so a sqlglot or PostgreSQL upgrade that changes the
set fails until it is reviewed. A denylist would have to name every function the role
should not run, among thousands, plus any an extension adds. Checked as `ifc_reader` in a
read-only transaction, the risky ones behave like this:

| Function | As `ifc_reader` |
|---|---|
| `pg_read_file`, `pg_ls_dir`, `pg_stat_file`, `lo_import`, `pg_reload_conf` | refused by privileges |
| `lo_create`, `nextval` | refused (read-only transaction; sequence privilege) |
| `pg_sleep` | runs, until the statement timeout |
| `set_config('statement_timeout', '0', true)` | runs; changes the setting for the rest of the transaction, not for the statement it runs in |
| `pg_advisory_lock` | runs; the lock outlives the transaction |
| `pg_terminate_backend`, `pg_cancel_backend` | run against other `ifc_reader` sessions, which every server shares (`pg_cancel_backend` cancelled another session's query) |
| `query_to_xml` | runs the SQL text it is given, which the validator never sees |
| `pg_notify`, `txid_current` | run |

All of them are outside the allowlist. A query that calls `set_config` to lift its own
timeout is still cancelled when the timeout runs out (a test shows it), and no later
statement benefits: the one after the FETCH is the cursor's CLOSE, and the rollback
undoes the setting.

**The executor** sends the text to psycopg with no parameters, so nothing in it is read
as a bind parameter: SQLAlchemy's `text()` would read `':x'` inside a literal as one, and
psycopg would read `'%s'` once parameters were passed. A test runs `SELECT ':x', '%s',
'%', 'a::b'`. A server-side cursor makes PostgreSQL compute at most `max_rows + 1` rows:
the test's query divides by zero on row 7 and still succeeds with `max_rows` 5.

### 6. Results: structured, compact, bounded

- **Structured content and the same data as text.** Every tool returns a pydantic model
  as structured content, which the SDK validates against the published output schema,
  and the same JSON, compact, as text content for clients that only read text. The SDK
  would indent the text: 28 to 50 % more characters on the Duplex files (`get_element` on
  an ARC wall: 14,902 characters compact, 22,342 indented).
- **Every list is capped and says so** (`truncated`): 200 models, storeys or takeoff
  groups, pages of at most 100 elements, 500 properties or 100 findings per element,
  500 spaces, 20 sample findings per rule.
- **Text from an IFC file is clipped at 2,000 characters**, with a note at the end
  saying how long it was. The longest real value in the four Duplex files, a product
  specification, has 904. The type `IfcText` clips on construction, so no field can
  forget it.
- **run_sql** returns columns (name and PostgreSQL type) and rows as arrays: at most
  `max_rows` (default 100, at most 500) and at most 30,000 characters of rows, with
  `truncated_by` (`max_rows` or `size`) and a note saying what to do. A value is clipped
  like any text; an array or JSON value longer than that becomes its JSON text, clipped.
  JSON conversion: a `numeric` that a float holds exactly is a number, otherwise a string
  of its digits; NaN and the infinities are the strings PostgreSQL writes; timestamps are
  ISO 8601; an interval is an ISO 8601 duration; `bytea` is `\x…` hex.

### 7. Errors say what to do, and leak nothing

- An unknown id, an argument that does not fit, a refused query, a timeout, a lock wait,
  a database error and an unreachable database are **tool errors** (`isError`), whose
  text names the problem and the next step, for example `no model with id 99;
  list_models shows the loaded models`. A database error carries PostgreSQL's primary
  message, its hint and the SQLSTATE: `column "storey_nme" does not exist Hint: Perhaps
  you meant to reference the column "v_element.storey_name".`
- The messages are built from the SQLSTATE and the diagnostic fields, never from a
  connection error's text, so neither the URL nor a password can appear in one.
- Anything unexpected is a generic tool error (`internal error in list_models; the
  server's log has the details`); the traceback goes to the log only.
- Arguments that break the input schema are refused by the SDK before any code runs.
  Resources raise the SDK's errors (not found: -32602, anything else: -32603); the
  prompt raises -32602 for a model id that is not a positive number.

### 8. Two resources and a prompt, next to the tools

- **`ifc://schema`**: every table and view of `ifc` with its columns, types, comments,
  keys and foreign keys, generated from `pg_catalog` on every read, so it cannot drift
  from the database. **Markdown**, because its reader is an LLM preparing a query: a
  table per relation reads like documentation, and it is the shortest form. The same
  content as JSON, which repeats every key for each column, is 27 % longer compact
  (29,633 characters against 23,307) and 75 % longer indented. A program wanting the
  catalogue as data can query it with its own credentials.
- **`ifc://models/{model_id}/summary`**: one model as `list_models` describes it, in
  **JSON**, because it is a record a client may parse.

  In MCP, resources are context the user or application attaches and tools are what the
  model calls. Both resources therefore have a tool equivalent: `list_models` holds every
  summary, and `run_sql` errors point to `ifc://schema` but every question can be asked
  without it. A client without resource support loses nothing essential.
- **`model_briefing(model_id)`**: tells the LLM which tools and resources to use, in
  which order, to brief a reader on a model, and how to report. It holds no data, so it
  cannot be stale, and the only value it interpolates is the model id, checked to be a
  positive number.

### 9. Untrusted content stays data

Text from an IFC file is only ever a value in a data field of a result. It is never
written into a description, the server's instructions, the prompt or an error message
built by the server (the one exception is PostgreSQL's own error text, which can quote a
value from the query). The instructions, the tool descriptions and the prompt tell the
LLM that this text is data and not instructions. This lowers the risk; it cannot remove
it, since the LLM reads the text either way. What contains it is that the server can do
nothing harmful whatever the LLM is persuaded to try (SECURITY.md).

### 10. One log line per call; the SQL text only at DEBUG

Every tool call logs one `key=value` line on stderr: `tool`, `outcome` (`ok`, `invalid`,
`refused`, `timeout`, `database_error`, `unavailable`, `error`), `seconds`, `rows` or
`error`. A resource read logs `uri` and `chars`. Arguments the SDK refuses are logged by
the SDK, one line naming the fields.

For `run_sql` the line carries a fingerprint (the first 16 hex digits of the SHA-256 of
the text) and its length, and the full text is logged at DEBUG only. A query can be
20,000 characters, and it carries whatever the conversation put into it, including what
the user typed. A log file that keeps all of it by default holds more than an operator
expects. The fingerprint still links repeated queries and failures, and
`IFC_SQL_LOG_LEVEL=DEBUG` gives the full audit trail when one is needed. sqlglot's own
logger is limited to errors, because its warnings quote the statement they could not
parse.

## Consequences

- An LLM gets the usual answers in one tested call each (tens of milliseconds on the
  Duplex files; `data_quality_report` on MEP's 31,974 findings takes 0.21 s), and the
  rest through `run_sql`.
- Least privilege holds end to end: the process needs only the reader password, the
  database refuses every write whatever the client does, and the privilege tests now
  include temporary tables and the version table.
- The limits are constants in the code (`Limits`, the `MAX_*` names), not settings.
- `ifc_reader` has `CONNECTION LIMIT 20`, and every call opens a session: more than 20
  calls at once, across all servers using the role, fail until others finish.
- The allowlist will refuse a legitimate function now and then; the refusal lists what
  is allowed, and a reviewed addition to `ALLOWED_UNTYPED_FUNCTIONS` fixes it.
- A sqlglot upgrade can change what parses or how; it is pinned below 31, and a test
  pins the functions it types.
- Every call costs a new session (about 12 ms more than a pooled connection).
- There is no authentication and no network transport (SECURITY.md).

## Alternatives considered

- **Only run_sql.** The fewest tools, but every briefing would depend on the LLM
  writing the traps above correctly, every time, untested.
- **Many fine-grained tools** (one per view, one per property question). A long tool
  list costs every request context and makes the choice harder for the LLM; the fixed
  tools cover what every briefing needs, and SQL covers the rest.
- **Connect as the owner, with read-only transactions.** Read-only transactions are a
  client setting, which a query or a bug can switch off; privileges are enforced by the
  database. It would also put the owner's password in every client's configuration.
- **A pool with `DISCARD ALL` on check-in.** It would clear session state for less than
  a connection costs, but advisory locks, settings and prepared statements would then
  depend on that event firing on every path; a session that ends clears them for sure.
- **A function denylist.** Rejected above: it could never be complete.
- **Executing sqlglot's regenerated SQL, or letting sqlglot add a LIMIT.** The query
  that runs would no longer be the one the LLM wrote, and any difference between
  sqlglot's output and the input would be a new place for a wrong answer to come from.
- **Wrapping the query in `SELECT * FROM (…) LIMIT n`** instead of a cursor. It does
  not stop a second statement (with no parameters psycopg uses the simple protocol,
  which runs several), and the order of the inner query is not guaranteed to survive.
- **Logging the full SQL at INFO**, or logging nothing about it. Rejected in decision 10.
- **`ifc://schema` as JSON.** Rejected in decision 8.
- **Reporting an unreachable database per call from the start.** The client would show
  a healthy server that fails every question (decision 3).
- **The HTTP transport.** Not needed for one local user, and it brings what decision 1
  lists.
