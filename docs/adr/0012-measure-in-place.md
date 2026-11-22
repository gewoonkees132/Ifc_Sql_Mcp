# ADR 12: `ingest --geometry` on a file already loaded measures it in place

- Status: accepted; supersedes the last consequence of [ADR 10](0010-geometry-quantities.md)
- Date: 2026-11-22
- Re-check finding: [R4](../audit/2026-11-recheck.md#r4-the-no-quantities-warning-does-not-mention---geometry)

## Context

When a model has no quantities, the tool's warnings named one way to get them: tick an
option in Revit and export again. R4 adds the second way, which needs no new export:
measure the shapes with `ifc-sql ingest --geometry`.

That advice reaches the architect through the takeoff warning, the space schedule and
the `model_without_quantities` finding. In every case the file is already loaded. A file
is identified by its bytes (ADR 4), so loading it again was skipped, and
`--geometry` was "not applied". ADR 10 said that measuring a loaded file meant deleting
its model and loading it again. That loses the model's id, its versions and its data
quality runs, and the tool has no command for it.

## Decision

1. **`ingest --geometry` on a file already loaded without it measures it in place**
   (`pipeline._measure_in_place`, `load.add_geometry`). The file is read and measured as
   a load would, and only two things are written to its model:
   - the quantities of source `geometry`;
   - the `geometry_not_measured` issues the model does not have yet, numbered after its
     last issue.
2. **The run is recorded as `succeeded`**, with the model's id and its
   `geometry_seconds`. `IngestResult.status` is `measured`, and the CLI says so.
3. **A model is measured once.** A model with a succeeded run that measured geometry is
   skipped, as before. The model row is locked while the quantities are added, so two
   runs on one file cannot both add them.
4. **`--project` and `--replaces` are still not applied** to a file already loaded. A
   warning names them.
5. **The data quality run is not repeated.** As after any ingest, `ifc-sql dq` checks the
   model again.

## Consequences

- The advice in the three texts works on the file the architect has loaded.
- A model loaded by an earlier version of the tool gets only its geometry. Whatever else
  a newer version would read from the file (ceilings' areas since R2, say) needs a fresh
  load.
- `ingestion_run` can hold two succeeded runs for one model: the load and the measure.

## Alternatives considered

- **Delete the model and load it again** (ADR 10). This loses the model's id, versions
  and history, and needs SQL the architect should not have to write.
- **Load the file again as a new version** that replaces the old one. The bytes are the
  same, and `model.file_sha256` is unique: one file, one model.
- **Say "delete the model first" in the warning.** This keeps a step that the tool can
  do itself.
