# ADR 5: Data quality rules in a checked YAML catalogue, evaluated in SQL

- Status: accepted
- Date: 2026-11-16

## Context

Phase 2 created `dq_rule`, `dq_run` and `dq_result`, and `v_model_summary` already counts
the findings of a model's latest successful run. Phase 4 stores what the transform could
not represent in `ifc.ingest_issue` ([ADR 4](0004-idempotent-ingest.md)). Phase 5 has to
fill those tables, and the design is shaped by these requirements:

1. **Rules are reviewed like code.** A reader should see every rule, its severity and
   exactly what it selects in one place, and a typo must fail at once instead of turning
   into a rule that silently finds nothing.
2. **Every transform issue becomes a finding**, and a new `IssueCode` cannot be forgotten.
3. **Stored values are checked with SQL.** Revit's placeholders (`BarCode = 'BarCode'`,
   `'N/A'`, empty strings) are kept verbatim for this purpose
   ([revit-ifc-quirks.md](../revit-ifc-quirks.md), section 8).
4. **Evaluation is set-based.** One run over the MEP model writes 31,974 findings.
5. **Results are kept per run**, so quality can be compared over time, and a finding keeps
   naming the rule it broke. `dq_result.rule_id` therefore references `dq_rule` without
   ON DELETE CASCADE, deliberately.
6. **The catalogue will change**, and the database must stay truthful when it does.
7. **An LLM reads the result**: `dq_rule.description` and each finding's message must
   say in plain words what is wrong.

## Decision

### 1. One YAML file inside the package, with two kinds of rule

`src/ifc_sql_mcp/rules/dq_rules.yaml` holds 31 rules. Each has `rule_id`, `description`,
`severity` and `dimension`, plus exactly one of:

- `issue_code`: the rule's findings are the model's `ingest_issue` rows with that code.
  The 26 issue codes have one rule each, named after the code.
- `sql`: a SELECT of `object_id` and `message` for the model in `:model_id`. Five rules:
  placeholders that repeat the parameter name, `'N/A'`, empty strings, elements without
  a storey and spaces without an area.

The file ships in the package, like the view files, so an installed wheel can run
`ifc-sql dq` without a checkout.

`dq/catalogue.py` checks the file strictly when it loads it: YAML syntax, keys written
twice in one mapping (PyYAML would silently keep the last), unknown and missing keys,
values that are not text (YAML reads an unquoted `no` as false), the rule id format,
duplicate ids, severities and dimensions outside the CHECKs on `dq_rule`, both or
neither evaluation key, issue codes that do not exist or are mapped twice, and SQL that
does not use `:model_id` or uses any other parameter. Without `:model_id` a rule would
report every model's objects. SQLAlchemy reads `':word'` inside a string literal as a
parameter, so any other parameter is refused too. A unit test fails until every
`IssueCode` has exactly one rule.

### 2. Severity says what a finding means for answers

| Severity | Meaning |
|---|---|
| `error` | Something in the file is not in the database: an object was skipped, or a value was dropped or left NULL, and nothing stored stands in for it. |
| `warning` | What is stored may mislead: a value was chosen between two, assumed, could not be used as intended, or is a placeholder that looks like real data. |
| `info` | Stored as written and plainly not a value (`'N/A'`, an empty string). It misleads no one who reads it, but it is a gap worth counting. |

The dimension says what kind of defect it is: `completeness` (something expected is
missing), `validity` (a value breaks a format, type, range or rule of IFC), `consistency`
(two pieces of data disagree), `uniqueness` (something that must occur once occurs more
often). Each finding copies its rule's severity, so a later change to the rule does not
rewrite history.

### 3. The catalogue is synchronised into `dq_rule` before every run

`ifc-sql dq` first makes `dq_rule` match the file, in its own transaction:

| Change to the YAML | Effect on `dq_rule` |
|---|---|
| New rule | Inserted. |
| Description, severity or dimension edited | Updated in place. Earlier findings keep the severity they were found with. |
| Rule removed, and findings refer to it | Kept, with `retired_at` set (migration 0007). Later runs do not evaluate it. |
| Rule removed, and nothing refers to it | Deleted. This also clears a retired rule once its last findings have been deleted with their model. |
| Retired rule back in the file | `retired_at` cleared. |

The SQL and issue code of a rule are not stored, so the sync cannot see a change to
them. A rule whose meaning changes gets a new `rule_id` instead, and the old one is
retired with its findings. `LOCK TABLE ifc.dq_rule IN SHARE ROW EXCLUSIVE MODE` makes
two syncs at once take turns. Without it, the second would insert the same new rules and
fail on the primary key; a test stages exactly that race. The lock does not block runs,
whose foreign key checks take a weaker lock.

### 4. One run per model, recorded the way an ingest is

1. Insert the `dq_run` as `running` and commit it.
2. In one transaction, run one `INSERT INTO dq_result … SELECT` per rule over the rule's
   query, then mark the run `succeeded`. No finding passes through Python. A run's
   findings are all there or none are, so `v_model_summary` never counts half a run.
3. On any error that transaction has rolled back. The run is marked `failed` with the
   error, which names the rule (`RuleError: rule … failed: DivisionByZero: division by
   zero`), and the command goes on to the next model and exits 1 at the end.

Every run evaluates every rule and adds new rows. Earlier runs are never changed, so
re-running gives a second run with the same findings, and two runs can be compared.

### 5. Issues become findings in SQL; an unloaded object's GlobalId goes in the message

`run.ISSUE_FINDINGS` selects the model's issues with the rule's code and keeps each
issue's `object_id` and message. An issue can name an object that was never loaded: only
`invalid_global_id` does, for an object whose GlobalId is malformed. `dq_result` has no
GlobalId column, and none is added. For such a finding the message ends with
`(GlobalId '…': no object with it was loaded)`, for these reasons:

- The value is a malformed GlobalId. It matches no object, and it would fail the format
  CHECK every other GlobalId column has, so a column could only be displayed, never
  joined. Displaying it is what the message is for.
- For every other finding the column would be NULL or a copy of `object.global_id`.
- `ingest_issue.global_id` keeps the raw value for anyone who needs it.

### 6. One view for the current findings

`v_dq_finding` (migration 0008) lists the findings of each model's latest successful
run, chosen exactly as `v_model_summary` chooses it, with the rule's dimension and the
object's GlobalId, class and name. Phase 6's `data_quality_report` tool reads it. Earlier
runs stay in the tables.

## Consequences

- Adding a rule means adding a YAML entry, its expected findings on the fixture (a test
  refuses a rule without them) and its counts on the sample files.
- Severity belongs to the rule, not to the finding. A check that needs two severities
  becomes two rules.
- Findings are numerous. One run over the four Duplex files writes 58,171 rows, nearly
  all placeholders, and every re-run adds as many again. Nothing prunes old runs;
  `DELETE FROM ifc.dq_run WHERE …` removes a run with its findings.
- A run does not record which rules it evaluated. When the catalogue changes, a rule
  added later has no findings in earlier runs because it did not exist yet, not because
  the model was clean. `retired_at` dates a removal, but nothing dates an addition, so a
  comparison across a catalogue change has to allow for that.
- A process killed between "running" and "succeeded" leaves its run `running`, as with
  ingest (ADR 4). Its findings were never committed.
- The catalogue's SQL runs as the owning role. That is acceptable because the file is
  code, shipped with and reviewed with the package. The MCP `run_sql` tool, which runs
  queries an LLM writes, gets the read-only role and query validation instead (Phase 6).

## Alternatives considered

- **A data quality framework** (Great Expectations, Soda, dbt tests). It would bring a
  runtime, a configuration language and its own connection handling to run five short
  SELECTs and one template, and its results would live outside the schema, where
  `v_model_summary` and the MCP tools cannot join them.
- **Rules as Python functions.** The findings would pass through Python row by row, and
  a rule would read less like the query it is.
- **One SQL file per rule**, like the view files. The rule's metadata and its query
  would then live in two places; in the YAML a rule fits on one screen.
- **SQL stored in `dq_rule`.** The database would hold code that neither the migrations
  nor the package track, and the catalogue would have two sources.
- **For a removed rule with findings:** delete its findings by cascade, which destroys
  the history results are kept for; or refuse to sync, which would make deleting history
  the price of removing a rule.
- **A `global_id` column on `dq_result`**, rejected in decision 5.
- **Findings written during ingest.** ADR 4 rejected this: it couples ingest to the rule
  catalogue.
- **One current set of findings per model**, upserted on each run. Simpler to read, but
  it gives up comparing quality over time.
