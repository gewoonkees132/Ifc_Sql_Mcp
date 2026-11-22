# ADR 14: A viewer for the loaded models and the MCP server's calls

- Status: **draft**. It holds the Phase 0 spike's findings; Phases 1–3 of
  [the plan](../plans/2026-11-ifc-viewer.md) complete it.
- Date: 2026-11-22

## Context

The plan asks for a page that shows the loaded models in 3D and lights up what each MCP
tool call found. Phase 0 measured what the design rests on before any code is written:

- what triangulating every element and space costs on the nine reference files;
- whether the models of one project line up in world coordinates;
- whether `pg_notify` works as `ifc_reader` on the embedded database on Windows;
- which three.js release to vendor, and whether BatchedMesh carries file B.

The scripts and their raw output are in [`docs/plans/viewer-phase0/`](../plans/viewer-phase0/).
Every figure below comes from them, on one machine: Windows 11, Intel Core i7-14700F
(20 cores, 28 threads), 32 GB, NVIDIA GeForce RTX 4060 Ti, IfcOpenShell 0.9.0.post1,
PostgreSQL 16.15 (the embedded extra).

## Phase 0: the nine reference files

Every `IfcElement` and `IfcSpace` with a body, triangulated by IfcOpenShell on 4 threads
(the audit's setting), each shape in its local coordinates and moved to world coordinates
with its placement matrix (decision P1). The GLB bytes come from a sketch of `glb.py`:
float32 positions relative to the model's origin, Y-up, uint16 or uint32 indices, one
primitive per material, one node per element with its GlobalId and class.

- **Drawn**: elements with a body of their own, plus aggregates drawn through their parts
  (an `IfcStair` whose flights, members and railings have the bodies).
- **No shape**: elements that are neither, which the page counts as "without shape".
- **Openings**: feature elements, never drawn (IfcOpenShell cuts them from their hosts).
- **Seconds**: the triangulation alone, local coordinates (with `use-world-coords` in
  brackets). Opening the file is not included.
- **GLB**: the elements' chunks, split at 2,000 elements, in MB of 10⁶ bytes; the spaces'
  chunks are listed after the plus sign.

| File | IFC elements | Drawn (own + through parts) | No shape | Openings | Spaces | Triangles | Vertices | Seconds (world) | GLB MB | Chunks | Largest storey: elements, MB | Aggregates drawn through parts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Duplex ARC | 268 | 215 + 3 | 0 | 50 | 21 | 26,348 | 15,038 | 0.56 (0.95) | 0.45 + 0.02 | 4 | Level 1: 101, 0.27 | IfcStair 2, IfcRoof 1 |
| Duplex MEP | 926 | 926 + 0 | 0 | 0 | 42 | 336,202 | 187,713 | 4.09 (14.16) | 4.70 + 0.03 | 3 | Level 1: 497, 2.71 | — |
| Duplex ELE | 103 | 103 + 0 | 0 | 0 | 1 | 30,014 | 15,686 | 0.58 (1.42) | 0.42 + 0.00 | 3 | Level 1: 56, 0.16 (by bytes Level 2: 45, 0.25) | — |
| Duplex PLU | 498 | 498 + 0 | 0 | 0 | 1 | 349,670 | 193,036 | 5.48 (17.75) | 4.65 + 0.00 | 3 | Level 1: 292, 2.88 | — |
| A, house | 489 | 442 + 12 | 0 | 35 | 0 | 351,667 | 185,725 | 10.37 (15.31) | 4.54 | 6 | Level 1: 219, 1.72 | IfcCurtainWall 9, IfcStair 2, IfcRoof 1 |
| B, tower | 14,612 | 11,123 + 174 | 1 | 3,314 | 0 | 1,569,412 | 831,231 | 26.76 (65.89) | 24.80 | 28 | XX_AR_L00_pf: 1,442, 2.38 (by bytes XX_AR_L01: 1,409, 2.53) | IfcCurtainWall 86, IfcStair 84, IfcRamp 3, IfcSlab 1 |
| C, office | 6,955 | 6,787 + 37 | 0 | 131 | 163 | 997,272 | 523,290 | 10.54 (31.70) | 15.42 + 0.35 | 5 | Level 1: 3,314, 5.98 (two chunks; the larger 1,942, 5.42) | IfcCurtainWall 33, IfcStair 4 |
| D, A re-exported | 489 | 442 + 12 | 0 | 35 | 0 | 351,667 | 185,725 | 11.10 (15.51) | 4.54 | 6 | Level 1: 219, 1.72 | as A |
| E, school | 5,319 | 4,459 + 71 | 0 | 789 | 307 | 1,416,789 | 745,569 | 14.27 (57.39) | 19.82 + 0.30 | 6 | Plan 01: 2,659, 9.27 (two chunks; the larger 2,000, 7.87) | IfcCurtainWall 34, IfcRoof 25, IfcStair 11, IfcSlab 1 |

Also measured:

- **No aggregate has a body of its own** in any of the nine files: every one of the 309
  aggregates is drawn through its parts. The rule "an aggregate's own body is left out
  when its parts have bodies" never fires on them; the synthetic fixture tests it.
- **File B's one element without shape** is an `IfcSlab` with a representation
  IfcOpenShell gives no triangles for.
- **On 28 threads**, file B's scene takes 20.15 s. Its extract and transform without a
  scene take 8.8 s on this machine.
- **Peak memory** of the process, scene included: at most 0.86 GB (file B) and 0.82 GB
  (file E).
- **The GLB's JSON** is 22 % of file B's bytes: 5.4 MB of 24.8 MB.

## Phase 0: decisions

### P1. One triangulation, in local coordinates, shared by `--viewer`, `--geometry` and the space plans

IfcOpenShell triangulates in each shape's local coordinates, and `geometry.py` moves the
vertices to world coordinates with the shape's placement matrix
(`ifcopenshell.util.shape.get_shape_matrix`), in numpy. Compared with `use-world-coords`:

- it is 1.4 to 4 times faster on all nine files (file B: 26.8 s against 65.9 s; file E:
  14.3 s against 57.4 s);
- it gives the same triangles, and world coordinates within 1 × 10⁻⁹ m on file B;
- `--geometry`'s measures need local coordinates (a wall's side area is seen along its
  local Y axis), so one pass serves both. On file B, `--geometry` alone takes 27.65 s;
  one shared pass that measures the 8,285 shapes and builds the scene of 11,123 takes
  28.84 s; two passes would take 27.65 + 65.89 = 93.5 s.

The space plans that every ingest makes come from the same pass when `--viewer` is given.

### P2. `--viewer` stays opt-in

File B's scene (26.8 s on 4 threads, 20.2 s on 28) triples its ingest (8.8 s for extract
and transform). Every documented demo path passes `--viewer`.

### P3. Chunks: at most 2,000 elements or 16 MB

The element cap splits two storeys: file C's Level 1 (3,314 elements) and file E's Plan 01
(2,659). The byte cap is never reached; the largest chunk is 7.87 MB (file E).

### P4. The models of a project are shown together

- **The four Duplex models line up in world coordinates.** All four place `IfcSite` at
  0, 0, 0. Each of ARC's 21 rooms coincides with one of MEP's spaces (intersection over
  union 1.0 for all 21). ELE and PLU each hold one space, "R301 Roof", whose plan
  surrounds the union of ARC's rooms evenly, by 0.208 m on all four sides.
- **A and D line up exactly**: the 442 shapes they share by GlobalId have the same
  bounding boxes (deviation 0.0 m).

### P5. three.js r187, unminified

- r187 (2026-10-30) is the newest release at least two weeks old. Its tarball's SHA-512
  matches npm's integrity field.
- r186 and r187 no longer publish `build/three.module.min.js` and
  `build/three.core.min.js`, so the plan's premise, a minified build under pre-commit's
  500 KB, no longer holds. r187 is vendored unminified, as the user decided:
  `three.module.js` (658,900 bytes) imports `./three.core.js` (1,474,799 bytes).
  The vendor folder is excluded from `check-added-large-files`, as the plan already says.
- Its `GLTFLoader.js` (119,861 bytes) imports `BufferGeometryUtils.js` (39,153) and
  `SkeletonUtils.js` (11,535); `OrbitControls.js` is 40,918 bytes. With the licence, the
  vendored files come to 2,346,247 bytes.
- Its BatchedMesh has `addGeometry`, `addInstance`, `setMatrixAt`, `setColorAt` (with a
  `Vector4`, alpha included) and `setVisibleAt`: what decision 6 needs.

### P6. BatchedMesh from the start, confirmed

Rendered with three.js r185.1 (the same BatchedMesh API as r187; Phase 2 measures again
with r187) in the browser of this machine (Chromium, ANGLE on Direct3D 11), 1280 × 800,
the camera orbiting for 10 s:

| Scene | As | Draw calls | Frames per second | JS heap |
|---|---|---:|---:|---:|
| B's largest storey (1,483 primitives) | plain meshes | 1,753 | 143.8 | 28 MB |
| B's largest storey | two BatchedMeshes | 3 | 144.3 | 36 MB |
| All of B (28 chunks, 12,774 primitives) | plain meshes | 14,492 | 22.5 | 212 MB |
| All of B | two BatchedMeshes per chunk | 76 | 143.8 | 141 MB |

- 144 frames per second is the display's refresh rate, the cap.
- This is a desktop GPU, not the laptop the plan names; a laptop figure is still to be
  recorded (Phase 2). JS heap is `performance.memory`, which leaves out GPU memory.
- BatchedMesh copies each primitive's whole vertex buffer. In the sketch an element's
  primitives share one POSITION accessor, so the batches held 1,040,891 vertices for
  831,231 in the file. `glb.py` therefore gives each primitive its own vertices.

### P7. NOTIFY works as `ifc_reader`, on the embedded database on Windows

On PostgreSQL 16.15 from the `embedded` extra, with `ifc_reader` and its
`default_transaction_read_only`:

- `SELECT pg_notify(…)` in a committed READ ONLY transaction is delivered, also through
  `make_reader_engine`'s `engine.begin()`; in a rolled-back one it is not;
- a payload of 7,999 bytes is accepted and one of 8,000 refused (SQLSTATE 22023,
  "payload string too long"), counted in UTF-8 bytes: 3,999 `é` pass, 4,000 do not;
- a notify through a new session takes 15.2 ms (median of 30; 15.2 to 17.5 ms over three
  runs), against 15.5 ms for a bare new session: its cost is the session;
- a listener thread (autocommit, `LISTEN`, `notifies(timeout=1)`) receives 200 events
  from one sender in order, reconnects after `pg_terminate_backend`, and reconnects
  within 0.2 s after `pg_ctl restart -m fast`, on the same port.

Two things the implementation must handle:

- **psycopg's default connect timeout is 130 s** (`psycopg/conninfo.py`). The listener
  passes `connect_timeout=5`, as `make_reader_engine` does.
- **The embedded server, stopped and started again, comes back on another port**:
  pgserver picks a free one on Windows. `serve` and `view` both resolve the URL once, at
  start, so after such a stop both must be restarted. The docs say so.

Not run: NOTIFY on Linux. There is no Linux machine or Docker here. Phase 3's listener
tests run against PostgreSQL 16 on Linux in CI.

## Decisions 1–7

To be recorded by Phases 1 to 3, as the plan's "Design, to record in ADR 14" gives them,
with the changes above.

## Alternatives considered

To be completed with the decisions. Phase 0 rejected:

- **Triangulating in world coordinates** (`use-world-coords`): 1.4 to 4 times slower, and
  `--geometry` would need a second pass in local coordinates (P1).
- **three.js r185.1, the newest release with minified builds** (0.75 MB in place of
  2.1 MB): it would pin a release four months older than the rule allows (P5).
