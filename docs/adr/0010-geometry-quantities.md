# ADR 10: Quantities measured from the geometry, on request

- Status: accepted; implements decision 8 of [ADR 3](0003-quantity-sources-and-precedence.md)
- Date: 2026-11-21
- Audit finding: [A5](../audit/2026-11-modern-revit-exports.md#a5-no-geometry-quantities-for-files-that-carry-none)

## Context

ADR 3 reserved the quantity source `geometry` and ranked it last, but nothing wrote it.
Revit writes quantities only when "Export base quantities" or "Export Revit property
sets" is ticked, and neither is needed for coordination. The audit's tower, file B, has
neither: none of its 6,095 walls has an area, so every area question ended with
"re-export from Revit". Its shapes are in the file. The audit triangulated them with
IfcOpenShell (`docs/audit/harness/ground_truth.py --geometry`) and measured 103,057.7 m²
of wall face. On file A, which does carry quantities, the same measure gave 964.6 m²
against the file's 946.0 m² of `NetSideArea`.

## Decision

1. **On request only: `ifc-sql ingest --geometry`.** Triangulating B takes about a
   minute on 4 cores, against seconds to read it, and most exports carry quantities.
2. **IfcOpenShell measures the shapes, in parallel** (`ingest/geometry.py`, one thread
   per CPU), in each shape's local coordinates, as the audit did:
   - the side area, seen along the local Y axis (Revit's wall axis runs along X);
   - the footprint area, seen from above;
   - the enclosed volume.

   IfcOpenShell cuts openings out of their walls, so a wall's side area is net of its
   openings.
3. **The classes measured are those modelled as solids**: walls, slabs, roofs,
   coverings, columns, beams, members, plates, footings, stair and ramp flights,
   railings and spaces. Doors, windows and furniture are assemblies of open meshes,
   whose volume would be meaningless. A door's size is an attribute already (A4).
4. **Which measure becomes which quantity** is decided by the transform
   (`quantities.geometry_quantities`):
   - a wall's `area_m2` is its side area;
   - a slab's and a space's `area_m2` is the footprint;
   - every measured solid gets `volume_m3`.

   A measure that is not positive makes no quantity.
5. **Source `geometry`, ranked last** (ADR 3): a quantity the file carries always wins.
   The raw measures are not stored separately. `source_ref` says which measure was used
   (`geometry:side_area`).
6. **Failures are reported, not fatal.** A body IfcOpenShell cannot triangulate or
   measure raises `geometry_not_measured` (loader coverage). The run records how long
   measuring took in `ingestion_run.geometry_seconds` (migration 0018).

## Consequences

- File B's 6,095 walls get 103,057.7 m² of `area_m2`, the audit's figure. Its area
  takeoff by Revit type has a total: type `1AR_MUR_CV003` comes to 25,114.3 m² over
  1,165 walls, the audit's figure too.
- File A keeps its 946.0 m² of walls from the file. Its 964.6 m² of geometry rows sit
  beside them, 2 % apart, where a query can compare the two.
- `ingest/geometry.py` is the second module that touches IfcOpenShell, after `extract`.
- A file already loaded is identified by its bytes (ADR 4). Measuring it later means
  deleting the model and loading the file again with `--geometry`.

## Alternatives considered

- **Always measure.** This would cost a minute on every large ingest, for files that
  usually carry their quantities.
- **Measure at query time.** There is no IfcOpenShell in the MCP server or the read-only
  role, and a tool call has a 5-second statement timeout.
- **Store the raw measures as properties.** That would be a second place to look for an
  area. One canonical quantity with its source, ranked as ADR 3 ranks them, keeps one.
