# ADR 4: Idempotent ingest

- Status: accepted
- Date: 2026-11-16

## Context

`ifc-sql ingest <file> --discipline ARC` will be run by hand, by `make ingest-samples`, by
scripts that load a folder, and sometimes twice at once. The same file arrives again
under another name, and a model is re-exported with changes. The ingest therefore has to:

1. never load the same file twice, however often or concurrently it is run;
2. never leave half a model behind;
3. keep a record of every attempt, including the ones that failed or did nothing;
4. keep what the transform reported (the issues), because the data quality stage needs
   it after the file may be gone.

`model`, `ingestion_run` and their constraints were created in Phase 2 with this in mind;
this record states how the pipeline (`ingest/pipeline.py`) uses them.

## Decision

### 1. A file is identified by the SHA-256 of its bytes

`model.file_sha256` is `UNIQUE`. The file is hashed before anything else, and a model
with that hash means the file is already loaded. Not the file name (the same bytes can
be copied under another name, and a new export can overwrite an old file of the same
name), not the project GlobalId (it differs between disciplines of one building and
stays the same across versions), and not a timestamp.

The discipline is not part of the identity: it describes the model rather than the
file. Ingesting loaded bytes with another discipline is skipped like any repeat, with a
warning that says so. Changing it means `UPDATE ifc.model SET discipline = …`, or
deleting the model and ingesting again.

### 2. A repeat is a `skipped` run, and nothing is parsed

If the hash is found, the run is recorded as `skipped` with that `model_id`, the command
says which model holds the file, and it exits 0. The file is read once to hash it and
never parsed. A second `make ingest-samples` writes four skipped runs and nothing else.

### 3. The run row lives outside the load transaction

1. Insert the run as `running`, in its own transaction, and commit it.
2. Extract and transform, outside any transaction. Parsing takes seconds and needs no
   database.
3. In one transaction, load every row of the model and mark the run `succeeded`, with
   `row_counts` per table. The run says succeeded exactly when the model committed.
4. On any error, that transaction has rolled back, so nothing of the model exists. The
   run is marked `failed`, with the error (exception type and message, no traceback),
   in a new transaction, and the command exits 1.

`finished_at` uses `clock_timestamp()`, so `finished_at - started_at` covers the parse
and the load. An empty file, or a STEP file that stops before `END-ISO-10303-21;`, fails
in extract: IfcOpenShell would otherwise load the part of a truncated file it could read
as a smaller model that looks valid.

### 4. A changed file is a new model version

Any change to the bytes gives a new hash and therefore a new model, with its own
`model_id` and the same GlobalIds (`UNIQUE (model_id, global_id)` allows that). Nothing
is merged, upserted or diffed. Both versions stay until one is deleted, and comparing
them is a join on `global_id`. Revit writes the export time into the `FILE_NAME` header
(`'2011-09-07T12:28:29'` in the architectural model), so exporting an unchanged model
again also gives a new version.

### 5. Deleting a model removes everything loaded from it; its run history stays

`DELETE FROM ifc.model WHERE model_id = …` cascades to its objects, their attributes,
materials, issues and data quality runs. `ingestion_run.model_id` is `ON DELETE SET NULL`,
so the runs survive and still show the file name and hash. The same file can then be
ingested again, as a new model.

### 6. A concurrent ingest of the same file loses on the unique constraint

Two ingests of one file can both find no model and both parse it. The model row is the
first row the load inserts. The second ingest to insert it waits on the unique index
until the first commits, then fails with a unique violation on `uq_model_file_sha256`.
The pipeline recognises exactly that constraint and records its run as `skipped`,
pointing at the winner's model, and the command exits 0 with a message that says a
concurrent ingest loaded the file. The end state equals running the two one after the
other: one model, one `succeeded` run, one `skipped` run. If the winner fails instead,
its row disappears with its rollback, and the waiting ingest goes on to load the file
itself. Two tests in `tests/integration/test_ingest.py` reproduce both endings with a
real blocked transaction, not a mock.

### 7. Transform issues are stored with the model (`ifc.ingest_issue`)

Issues exist only while the pipeline runs. Migration 0005 adds `ifc.ingest_issue`,
written in the load transaction:

| Column | |
|---|---|
| `model_id`, `issue_index` | Primary key: the model, and the issue's 1-based position in the order the pipeline reported it (deterministic, so a file always numbers its issues the same way). |
| `code` | `IssueCode` value, e.g. `quantity_not_a_measure`. A format check rather than a list, so a new code needs no migration. |
| `object_id` | The object concerned, if it was loaded (cascade with it). NULL for model-level issues and for objects skipped for a malformed GlobalId. |
| `global_id` | The GlobalId as written in the file, kept even when there is no `object_id`. |
| `message` | What was dropped or changed, with the offending value. |

**Recommendation for Phase 5:** map issue codes to rules in `rules/dq_rules.yaml`, and
let the `dq` command turn `ingest_issue` rows into `dq_result` findings with
`INSERT … SELECT`. The rules that check stored values (placeholders, empty strings) run
as SQL over `property`. An issue records what the transform did; a finding is an
evaluation against a rule. Keeping them apart keeps ingest independent of the rule
catalogue.

## Consequences

- Re-running an ingest is always safe, which makes scripts and `make` targets simple.
  The cost of a repeat is one read of the file to hash it.
- A process killed between "running" and "failed/succeeded" (power cut, `kill -9`, Ctrl-C)
  leaves its run `running` forever. Nothing reaps such rows. They are easy to find
  (`status = 'running'` with an old `started_at`), and the model itself was never
  committed. The same holds when the database becomes unreachable during the load: the
  run cannot be marked failed, and the command reports the connection error.
- A losing concurrent ingest parses the file for nothing. For these files that is at
  most a few seconds.
- Several versions of one discipline can be loaded at once, and every view shows all of
  them. A query that wants one version per discipline has to choose it, for example
  the latest by `ingested_at`.
- Byte-identical copies under different names are one model; the second name only
  appears in the skipped run.
- Issues are deleted with their model, while the run history is kept.

## Alternatives considered

- **Identity by file name or project GlobalId**, upserting objects by GlobalId. This
  is what keeps a "current state" in place. But it destroys the previous version, needs
  rules for deleted elements, and lets two exports of different content pass for one.
  Versions as separate models are simpler and lose nothing.
- **An advisory lock on the hash** (`pg_advisory_xact_lock`) before the existence check,
  so a concurrent ingest waits instead of parsing. It saves seconds of wasted parsing,
  but it is a second mechanism next to the unique constraint, which is needed anyway
  and already gives the right outcome.
- **Report the race loser as `failed`.** That is accurate about this run, but it would
  make a harmless race look like an error in the run history and turn exit codes red
  in scripts, while the file is in fact loaded.
- **Run row inside the load transaction.** Simpler, but a failed run would be rolled
  back with the model and leave no trace, which defeats the audit log.
- **Issues, other ways:**
  - *Re-run the transform in Phase 5.* That needs the file, and only its base name is
    stored. It may have moved or changed, and parsing costs seconds per file.
  - *Counts only, in `ingestion_run.row_counts`.* The counts are already logged, but
    without the object and the message a finding cannot say what is wrong where.
  - *A jsonb list on `ingestion_run`.* That is the wrong owner, since run history
    outlives the model and issues describe its content. It also has no foreign key to
    the object (ADR 2's argument against jsonb).
  - *Write `dq_result` rows during ingest.* That needs a `dq_run` and the rule
    catalogue, which belong to Phase 5, and it couples ingest to them.
