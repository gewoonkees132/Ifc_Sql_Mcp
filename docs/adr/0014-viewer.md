# ADR 14: A viewer for the loaded models and the MCP server's calls

- Status: accepted. It holds the Phase 0 spike's findings, decision 1 (Phase 1: scenes
  in the pipeline), decisions 2, 3 and 6 (Phase 2: `ifc-sql view` and the page),
  decisions 4 and 5 (Phase 3: the feed and the scripted demo) and decision 7 (Phase 5:
  the static demo on GitHub Pages). Phase 4 added the audit's script to decision 5; the
  colours of a fixed kind, and a `run_sql` with no id column among the calls that light
  nothing by nature, to decision 4; and the page following the calls to decision 6.
- Date: 2026-11-22; accepted 2026-11-23; Phases 4 and 5's additions 2026-11-23
- Amends: [ADR 1](0001-postgresql.md) (a second schema, `ifc_viewer`);
  [ADR 6](0006-mcp-server.md) decisions 2 and 10 (the feed's notification, decision 4);
  [ADR 13](0013-embedded-postgresql.md) decision 3 (`view` and `demo` attach to the
  embedded database)

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

Rendered with three.js r185.1 (the same BatchedMesh API as r187; Phase 2 measured the
page itself with r187, in decision 6 and [viewer.md](../viewer.md#measured)) in the browser of this machine (Chromium, ANGLE on Direct3D 11), 1280 × 800,
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

## Decisions

### 1. A scene per model, built at ingest, in its own schema

`ifc-sql ingest --viewer` triangulates the model while the file is open and stores the
result. The database keeps the file's name, size and SHA-256, never its bytes, so this is
the only time the geometry can be had.

**Storage: schema `ifc_viewer`** (migration 0020), outside `ifc`, because a scene is
derived from the file and can be rebuilt at any time:

- `scene`, one row per model (`ON DELETE CASCADE` from `ifc.model`): `format_version`,
  `built_at`, `build_seconds`, `ifcopenshell_version`, the origin (`origin_x_m`, `_y_m`,
  `_z_m`, float64) and the counts: `element_count` (drawn), `without_shape_count`,
  `opening_count`, `byte_count`.
- `scene_chunk`, primary key `(model_id, chunk_no)`: `storey_id`, `kind` (`elements`,
  `spaces`, `space_copies` or `areas`), `element_count`, `glb` (`bytea`) and `glb_sha256`.
  - `storey_id` references `ifc.spatial_element` deferrably, checked at commit as the
    references inside schema `ifc` are, so one `DELETE` on `ifc.model` removes the model
    and its scene, while a storey that a chunk is on cannot be deleted on its own.
  - `glb_sha256` is a generated column, `encode(sha256(glb), 'hex')`: the database
    computes the chunk's future ETag, so the two cannot disagree.
  - A CHECK requires the GLB's magic bytes, `glTF`.
- `ifc_reader` gets `USAGE` on the schema and `SELECT` on the two tables, as explicit
  grants: `ifc-sql view` will read them as that role. No default privileges, so a later
  table there is unreadable unless its migration grants it.
- `run_sql`'s validator reads only schema `ifc`, so a query cannot pull megabytes of
  geometry through the MCP server, and `describe_schema` does not list the scenes.
- The downgrade drops the schema.

**What is drawn.** Every `IfcElement` with a body, except feature elements: IfcOpenShell
cuts the openings out of their hosts (`disable-opening-subtractions` stays off). Every
`IfcSpace` with a body too, in chunks the page shows only when a selection holds one: the
rooms in `spaces`, a space that repeats a room (`spatial_element.repeats_id`) in
`space_copies`, and Revit area plans (category `Areas`) in `areas`. Of two objects that
share a GlobalId, only the one the transform keeps is triangulated (the first spatial
element, else the first element, by STEP id), so the other's body is never drawn under
its GlobalId. The plans and the measures follow the same rule, with or without
`--viewer`.

**Aggregates.** When an element aggregates parts that are drawn (`IfcRelAggregates`), the
parts' nodes are children of the aggregate's node, and the aggregate's own body, if any,
is left out: selecting a stair's GlobalId selects its flights. No aggregate in the nine
files has a body of its own; the synthetic fixture tests the rule.

**The counts** add up to the elements of `ifc.element`:

- drawn: the elements with a body of their own, and the aggregates drawn through their
  parts;
- without shape: the elements that are neither, among them those whose body has a NaN or
  infinite vertex, which would otherwise fail the whole scene;
- openings: the feature elements, never drawn.

**Format: glTF 2.0 binary**, written by our own pure module, `viewer/glb.py`:

- Y up, metres. Positions are float32, relative to the scene's origin: the centre of the
  drawn elements' bounding box, rounded to whole metres (of the spaces', for a model with
  no element drawn). File B's origin is 1,507,997 m, 5,039,599 m, 37 m.
- POSITION and indices only: no normals, textures or compression, so the page needs no
  WASM decoder.
- One node per element: `name` is its GlobalId, `extras` its IFC class.
- One primitive per IfcOpenShell material, with its colour and transparency, and with
  **its own vertices** (P6). The positions' buffer view has a `byteStride` of 12, as glTF
  requires when several accessors share one.
- Fixed bytes: the meshes are taken by STEP id, whatever order IfcOpenShell's threads
  finish them in, and the JSON is written with sorted keys.

**Chunks follow the storeys**: at most 2,000 elements (aggregates included) or 16 MiB
each (P3). An aggregate stays whole, on its own storey. A run of elements above the byte
cap is halved until it fits. The chunks are numbered kind by kind and storey by storey from
the lowest; within a storey the elements come by GlobalId.

**In the pipeline.**

- `extract.read_model(…, viewer=True)` makes the one pass of P1:
  `geometry.triangulate_scene` triangulates every element and space in local coordinates,
  moves the vertices to world coordinates with the placement matrix, and gives the space
  plans and, with `--geometry`, the measures from the same shapes.
- The scene is written after the load commits, in a transaction of its own that locks the
  model row. Of two builds at once, the second finds the first's scene and keeps it. An
  ingest that loses the race to load the file stores its scene for the winner's model,
  which holds the same records. `built_at` is `clock_timestamp()` when the scene is
  written, after the wait for the lock, not the transaction's start.
- On a file already loaded, `--viewer` builds the scene in place, as ADR 12 measures in
  place. The run is the skipped run every repeat records; nothing of the model is written.
- A scene of the current `format_version` is kept, and the summary says so; an older one
  is replaced when `ingest --viewer` runs on its file. Nothing rebuilds a scene on its own.
- A scene that fails writes nothing and exits 1; the model stays loaded. When the
  triangulation itself raises, the space plans and the measures come from the two passes
  of an ingest without `--viewer`.
- `watch --viewer` passes the flag on.

**Not an ingestion run, and no data quality rule.** A scene writes nothing to schema
`ifc`; its own row says when it was built and how long it took. The page shows its counts.

**The nine files**, as `ingest --viewer` builds them (pinned by
`tests/samples/test_duplex_scenes.py` and `tests/modern/test_audit_scenes.py`; the bytes
within 5 %, because OpenCASCADE may triangulate a curved face differently on Linux):

| File | Drawn (own + through parts) | No shape | Openings | Chunks: elements, spaces, copies, areas | Bytes: elements + spaces |
|---|---:|---:|---:|---|---:|
| Duplex ARC | 218 (215 + 3) | 0 | 50 | 4, 3, 0, 0 | 462,272 + 18,184 |
| Duplex MEP | 926 (926 + 0) | 0 | 0 | 3, 3, 3, 0 | 4,703,808 + 35,152 |
| Duplex ELE | 103 (103 + 0) | 0 | 0 | 3, 1, 0, 0 | 423,568 + 1,064 |
| Duplex PLU | 498 (498 + 0) | 0 | 0 | 3, 1, 0, 0 | 4,648,992 + 1,064 |
| A, house | 454 (442 + 12) | 0 | 35 | 6, 0, 0, 0 | 4,545,068 |
| B, tower | 11,297 (11,123 + 174) | 1 | 3,314 | 28, 0, 0, 0 | 25,154,068 |
| C, office | 6,824 (6,787 + 37) | 0 | 131 | 5, 3, 3, 0 | 15,418,460 + 353,244 |
| D, A re-exported | 454 (442 + 12) | 0 | 35 | 6, 0, 0, 0 | 4,545,068 |
| E, school | 4,530 (4,459 + 71) | 0 | 789 | 6, 4, 0, 4 | 20,214,868 + 299,356 |

Every count is Phase 0's. The elements' bytes are 0.0 to 2.9 % more than the sketch's (file
B 1.4 %): each primitive now has its own vertices, and the JSON carries an accessor per
primitive and the stride.

### 2. `ifc-sql view`: the only port

`ifc-sql serve` stays stdio-only, as [ADR 6](0006-mcp-server.md) decision 1 says. The
page is served by a separate command, `ifc-sql view [--project GROUP] [--model ID …]
[--port N] [--no-open]`, which runs no MCP and serves no tool but `list_models` and
`get_element`.

- **The database as `serve` reads it.** It connects as `ifc_reader` (sessions named
  `ifc-sql-mcp-viewer`) and checks the migration revision first, exiting 1 on an older
  one. Each request runs in a thread, at most three at once (`ifc_reader` allows 20
  sessions, shared with every MCP call on the cluster), inside `reader_transaction` with
  the MCP server's limits.
- **What it shows first.** The current models of one `project_group`: by default the
  project of the newest model that has a scene, so that a file loaded later without
  `--viewer` does not leave the page empty; `--project` names another, `--model` names
  models. The page has a picker. Models without a scene are listed, greyed out, with the
  `ifc-sql ingest … --viewer` command that builds theirs. `view` checks both options when
  it starts, and exits 2 on one that names nothing loaded.
- **uvicorn on a socket of its own.** `view` binds 127.0.0.1 itself
  (`socket.create_server`, port 0 by default), so a taken port is an `OSError` it
  reports, exiting 1; uvicorn's own bind would `sys.exit(3)`. uvicorn then runs on that
  socket with no lifespan, no websockets, no access log, its logging through the
  project's stderr logger (its INFO lines silenced), and two seconds for a graceful stop.
  Ctrl+C stops it with exit code 0.
- **The page works without a feed.** It shows the models; Phase 3 adds the calls.
- **The app** is `viewer/http.py`'s `create_app(engine, token, port=…, project=…,
  model_ids=…)`, on Starlette, with six routes ([viewer.md](../viewer.md#how-it-is-built)).
  The rules that can live in Python do: the projects and their order, the set shown
  first, the counts' wording, the hint. The page lays them out.

### 3. What the listening port must hold

`viewer/security.py` stands in front of every route, outside Starlette's own error
handling, so that a 500 carries the headers too:

- **127.0.0.1 only**, with no setting to change it.
- **The Host.** Only `127.0.0.1:<port>` and `localhost:<port>`; anything else, two Host
  headers included, gets 421 before any route. That stops DNS rebinding. It is our own
  dozen lines, not the MCP SDK's `transport_security`, which allows any port and lets a
  request without an Origin through.
- **GET and HEAD only**; anything else gets 405.
- **The token.** `secrets.token_urlsafe(32)` on every start, compared with
  `hmac.compare_digest`. The URL carries it in its fragment (`#t=…`), which reaches no
  server, log or Referer; the page moves it to `sessionStorage` and drops the fragment
  with `history.replaceState`; `fetch` sends `Authorization: Bearer`. Every `/api/` route
  needs it (401 otherwise); `/` and `/static/` hold no data and do not. No cookie, and no
  `?t=`, which Phase 3's SSE route alone will take.
- **Headers on every response:** a content security policy of `default-src 'none';
  script-src 'self' 'sha256-<import map>'; style-src 'self'; img-src 'self'; connect-src
  'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, whose hash
  `create_app` computes from the `index.html` it serves; `X-Content-Type-Options:
  nosniff`; `Referrer-Policy: no-referrer`; `Cache-Control: no-store` on the JSON, and
  `private, no-cache` with the chunk's `glb_sha256` as `ETag` on a chunk, so a reload
  revalidates and gets a 304. No CORS headers.
- **The page is served with LF line endings**, and hashed so: the browser hashes the
  import map after its HTML parser turned CRLF into LF, and git on Windows may check out
  `index.html` with CRLF.
- **The page's files** are read once and served from memory, by an explicit list of
  suffixes and media types: no listing, no path outside the folder, and `.js` as
  `text/javascript` whatever the Windows registry says (with `nosniff`, a module served
  as `text/plain` would not run).
- **IFC text stays data.** The page sets it with `textContent` only, through one helper
  (`dom.js`) that refuses `on…` attributes. A unit test fails on `innerHTML`,
  `outerHTML`, `insertAdjacentHTML`, `document.write`, `srcdoc`,
  `createContextualFragment`, `setAttribute('on…')`, `javascript:`, `eval` or
  `new Function` in the page's own JavaScript. Ids in URLs go through
  `encodeURIComponent`.
- **The token is never logged.** It is printed once, on stdout; a test runs `view` as a
  process and checks its stderr.

### 4. The feed: every tool call, and what it found

`ifc-sql serve` stays stdio-only. With `IFC_SQL_VIEWER_FEED=1` (off by default) it
publishes each tool call as one PostgreSQL notification, and every `ifc-sql view` on the
database receives it: one page shows the calls of every client, the scripted demo's,
Claude Desktop's and Claude Code's, and no link passes through a conversation. The MCP
server keeps its eleven tools.

**Publishing, in `serve`.** After every call, ok or not, once its read-only transaction
has rolled back, `_Calls` builds the event and `db.engine.notify` sends it: a new
session as `ifc_reader`, `BEGIN READ ONLY`, `SELECT pg_notify('ifc_viewer_feed', …)`,
`COMMIT`, closed (P7: PostgreSQL delivers a notification only when its transaction
commits, and allows `NOTIFY` in a read-only one). It amends ADR 6:

- decision 2: with the feed on, each call is followed by a second, committed READ ONLY
  transaction in its own session, which only notifies. It costs a session, 15 ms on the
  Phase 0 machine (P7);
- decision 10: `run_sql`'s text, clipped, reaches whoever listens on the database, and
  PostgreSQL's notification queue (`pg_notify/` in the data folder) holds it until every
  listener has read it.

A notification that fails is logged, without its payload, and changes nothing else: the
client has its result already. With the feed off nothing is sent and no session opened.
A call the SDK refuses before any code runs never reaches `_Calls`, nor the feed.

**The event, version 1** (`viewer/event.py`): JSON, at most 7,999 bytes of UTF-8. The
process (`src: {pid, started_at}`), the call's number in it and when it ended; the tool,
its arguments, the outcome, the seconds and the rows, as the log line has them; for
`run_sql` the SQL as `{text, chars, sha256_16}`; and for `run_sql` and `get_element`,
whose results hold the selection itself, the ids, `{groups, truncated}`. Each text
argument is clipped to 200 bytes and named in `clipped`; the ids are capped at 500 and
4,800 bytes; the SQL text gets what is left, clipped by its encoded bytes. Measured by
`tests/unit/viewer/test_event.py`: 500 object ids of seven digits take 4,076 bytes, and
with 20,000 non-ASCII characters of SQL beside them the event is 7,999 bytes, the text
clipped to 1,821 characters. The plan's "500 ids fit in about 4 KB" was an estimate.
`run_sql`'s ids are read from the rows it returned before any value was clipped, so an
`array_agg` the client got as clipped text still gives its ids: an `object_id` column,
an `object_ids` array column (a group per row, labelled by its first column), or
`global_id` or `global_ids` beside `model_id`. With the feed on, `run_sql`'s description
gains one sentence that says so. Rows with no such column carry no ids at all (Phase 4):
the query is a count or a sum, not a choice of objects, so it lights nothing by nature,
as `list_models` does, and the page keeps what the previous call lit. An id column with
no rows carries ids, none of them: the query found no object, and the page lights none.

**Listening, in `view`** (`viewer/listener.py`): a plain psycopg connection as
`ifc_reader`, autocommit, `LISTEN ifc_viewer_feed`, in a thread that waits a second at a
time and checks a stop flag. Synchronous on purpose: on Windows uvicorn runs a
`ProactorEventLoop`, which psycopg's `AsyncConnection` refuses. It passes
`connect_timeout=5` (psycopg's default is 130 s, P7). On a lost session it says
"interrupted" in the page, waits 0.5, 1, 2, then 5 s between attempts, and listens
again. Its tests ran on Windows against the embedded extra's PostgreSQL 16, as
`ifc_reader`, including a reconnect after `pg_terminate_backend`; CI runs them on Linux.

**Every event is untrusted** (`viewer/feed.py`): any login can notify. `view` drops an
unknown version, any field of the wrong type (strict pydantic), an unknown tool, ids
that are not positive integers, GlobalIds that do not match the pattern, more than 500
ids, and arguments no real call could have: it checks them with the tool's own argument
model, the one the MCP SDK checks a call with, and refuses an argument the tool does not
have. It rate-limits (a burst of 30, then 10 a second), numbers what it keeps itself (the
SSE `id`, so `Last-Event-ID` resumes), labels each call with its process, keeps the last
200, the selections of the last 50, and counts what it dropped by reason. It logs a
reason once, never a payload.

**Selections are resolved in `view`** (`viewer/selection.py`), when the event arrives,
by fixed read-only queries in `server/tools.py` and `server/compare.py` that reuse the
tools' own SQL, with the arguments as bind parameters; no SQL comes from an event. Each
holds at most 20,000 objects, in groups with a colour each (by severity for a data
quality report):

| Tool | What is lit |
|---|---|
| `find_elements` | Every match: `find_element_ids` and `find_elements` share one WHERE clause (`_element_filter`). |
| `schedule` | The same elements, by the schedule's groups, in its order (`_schedule_elements`, `_schedule_groups`). |
| `quantity_takeoff` | By storey, class or Revit type: each group's elements, by the takeoff's own grouping; openings unless `include_openings`. By material: nothing, and why. |
| `get_element` | Its object; the page moves the camera to it. |
| `space_schedule` | Rooms, their copies (`duplicate_of`) and Revit Areas, as `v_space_schedule` marks them, each kind in its own colour. |
| `data_quality_report` | The objects with a finding of the latest run that pass the report's filters: one group per rule asked for, else per worst severity. |
| `compare_models` | The newer model's objects added, and those whose properties changed, by the comparison's own queries, each kind in its own colour; removed ones counted. |
| `run_sql` | The objects among the event's ids that are loaded. Rows with no id column (a count): nothing, and the page leaves the view as it is (Phase 4). |
| `list_models`, `list_storeys`, `describe_schema` | Nothing: the page leaves the view as it is. |

A group's colour is its place in the selection, except where the groups are fixed kinds
(Phase 4): the rooms, copies and Areas of a space schedule, and the added and changed
objects of a comparison, keep the place of their kind, so file E's Areas are the third
colour, as file C's would be, though E has no copies of rooms.

Besides its groups, a selection says how many objects are openings (never drawn), not
drawn at all (storeys), or spaces; which chunk kinds of spaces must show; and the storey,
when every object is on one, above which the page hides the rest.

**Sessions.** `ifc_reader` has `CONNECTION LIMIT 20`, shared with every MCP call on the
cluster. `view` holds one for the listener, and at most three for its routes and its
selections together (one `Reader`, a semaphore of three); `serve` holds one per call, and
with the feed on one more after it, never both at once. `tests/samples` samples
`pg_stat_activity` while the Duplex demo plays and checks these numbers.

### 5. The scripted demo

`ifc-sql demo [--script duplex|audit] [--step | --pause SECONDS] [--check] [--port N]
[--no-open]` plays a script of questions through the real MCP server, with no LLM and no
client of the user's: it is the client. It starts `ifc-sql serve` over stdio with the
SDK's client, as `scripts/mcp_smoke.py` does, with `IFC_SQL_VIEWER_FEED=1`, listens as
`view` does, serves `view`'s page and opens it, and asks each step's question. The calls
take the path a real client's take, `serve` → `NOTIFY` → listener → selection → page, so
the demo checks that path. The page says "scripted demo, no LLM" and numbers the
questions: the demo tells the feed which step it expects before it asks, and labels the
events of its own `serve` once the first arrives.

**The scripts are data** (`viewer/demo_script.py`): each step has a title, a tool, its
arguments, a figure in words with a function that reads it from the result and the value
it must have, and the selection's count group by group. Files are named by SHA-256, and
the models are found by those hashes, never by id: ids depend on the order of loading,
and the docs' figures come from three layouts. The ids are filled into the arguments and
the SQL when it plays. A unit test checks that the Duplex script's hashes are those
`scripts/download_samples.py` pins.

**`--check`** serves no page and opens nothing. It plays every step, waits up to 15 s for
each call to reach the feed, and compares its outcome, its figure and its selection with
the script; it prints a line per step and exits 1 on a difference. CI runs it on the
Duplex in the `samples` job (Linux) and in the `windows` job (the embedded database).
The figures it compares do not depend on ids: step 8 compares the wall's class and name,
not the characters of its result, which change with the digits of its ids.

**The Duplex script**, nine steps, each pinned by `tests/samples/test_duplex_demo.py`:
the four models and their elements (268, 926, 103, 498); the 21 ARC walls on Level 1; the
external walls by type, 12, 4, 4 and 3, from the README's query with `array_agg(object_id)
AS object_ids`; the door schedule, 14 doors in 4 types (6, 4, 2, 2) whose fire rating is
the placeholder `'Fire Rating'`; the 21 ARC rooms, 422.0 m²; the MEP model's 42 spaces,
21 of them copies of rooms; the 78 elements without a fire rating; one wall in full; and
a refused write.

**The audit's script** (Phase 4), four steps, each pinned by
`tests/modern/test_audit_demo.py` on the `modern` tests' database: file C's room schedule,
163 spaces with 75 copies of rooms (8,090.7 m² with them, 4,721.4 m² without), lit as 88
and 75; file E's, whose 25 Revit Areas of 22,905.9 m² light in the third colour beside 282
rooms; file B's doors on `XX_AR_L01`, 159 in 6 types (77, 25, 21, 15, 14, 7), the storeys
above hidden; and `compare_models` on file D, whose fire ratings were added to 42 walls
and 9 curtain walls and whose 4 openings Revit gave new GlobalIds: it lights 4 added and
51 changed, and the 4 added are openings, never drawn. The script names B, C, D and E:
file A is the model D replaces (`--replaces`), and `compare_models` compares D with it
when `old_model_id` is left out, so the demo, which finds only current models, never
looks for it. It is local only: B and E are of uncertain provenance and none of the five
is published, so CI does not play it; the `modern` tests do.

**`scripts/demo.py`** is the one command for a fresh clone, on any OS: it downloads the
Duplex, migrates, loads the four files with `--viewer` (building a scene in place for a
file already loaded), runs `ifc-sql dq`, and runs `ifc-sql demo` with its own options.
`make demo` calls it; `make ingest-samples` already passes `--viewer`. With `--script
audit` (`make demo-audit`) it downloads the audit's five files instead and loads them in
the audit's order, A to E, D with `--replaces` and the model id A's ingest printed. Run
again, `ingest D --replaces A` is a repeat like any other: a file already loaded as the
model that replaces A changes nothing and warns of nothing, where it used to exit 2
because A "is already replaced".

### 6. The page

- **Plain ES modules, no build step, no npm**: `index.html`, `viewer.css`, `main.js`,
  `scene.js`, `details.js`, `api.js` (the one seam for data) and `dom.js`. three.js r187
  is vendored unminified in `viewer/static/vendor/three/` (P5): `three.module.js`,
  `three.core.js`, `GLTFLoader` with the two addons it imports, `OrbitControls`, the MIT
  licence and `SHA256SUMS`. Tests check the sums, that every module the page imports is
  vendored, and that the wheel holds every file. `.gitattributes` keeps git from
  converting the vendored files' line endings.
- **Rendering.** `api.js` fetches a chunk and `GLTFLoader.parseAsync` parses it;
  `scene.js` puts its primitives into two BatchedMeshes, one opaque and one transparent,
  one instance per primitive with its colour (alpha included), and frees the parsed
  meshes. A map from each GlobalId to its instances lets a click light an element, and an
  aggregate through its parts. The view draws only when something changed.
- **Disciplines.** Every model shown is placed at its origin less the first's (P4). A chip
  per discipline hides or shows it. ARC keeps its materials' colours; MEP, ELE and PLU are
  tinted, 60 % of the way to blue, amber and green.
- **Storeys.** A chunk holds one storey, so a storey is hidden by hiding its chunks. The
  page lists the storeys of every model by name, highest first.
- **Spaces.** Rooms are loaded and shown, see-through, on request. Copies and Areas are
  not drawn in Phase 2; Phase 3 draws them when a selection holds one.
- **A click** lights the element in the accent colour, names it in a caption, and opens a
  drawer with `get_element`. "fit", "fit selection" and "only selection" frame and hide.
- **`window.ifcViewer`** exposes `ready`, `ready_ms`, the highlighted count, the feed id
  of the call lit last (`call`), `stats()` and `measure(seconds)`, for Phase 6's browser
  test and for the figures below.
- **The feed** (Phase 3, `feed.js`): the calls from `/api/events`, newest first, with the
  feed's state. A call's selection is drawn in its groups' colours and everything else in
  opaque light grey: no per-instance alpha, and the tower does not fog over. A selection
  of spaces alone hides the rest, since the rooms are inside the walls. The storeys above
  a one-storey selection are hidden, with "Level 2, Roof hidden · show all". The caption
  says who asked, what, the counts and the legend; the server words them, the page counts
  what it could draw.
- **The page follows the calls** (Phase 4). A call whose selection lies wholly outside
  the models shown shows the project of its first model, as the picker lists it (a
  replaced model, alone), and then lights it; the picker follows. A model loaded since the
  page opened is found by asking `/api/models` again. A selection partly in the models
  shown lights in place, and the caption names the models not shown, as before. The demo
  opens on the models its script names first (`Script.shown_first`): the Duplex's four,
  as in Phase 3, or file C for the audit's script, whose four steps are about four
  buildings of four projects. A live client asking about another project gets the same.

**Measured** ([viewer.md](../viewer.md#measured)), on the Phase 0 machine with Microsoft
Edge 158 and r187: file B (28 chunks, 12,774 primitives) is drawn 0.93 s after the page
starts loading, in 76 draw calls, at 144.1 frames per second (the display's cap), with a
68 MB JS heap, 173 MB in the tab's process and 219 MB in the GPU process. Its batches hold
831,231 vertices, the file's own: P6's fix holds. A laptop figure is still missing.

### 7. The static demo on GitHub Pages

`ifc-sql demo --export DIR` plays the script as `--check` does and, only when every step
is as the script says, writes a static copy of the demo into DIR, a new or empty folder:
`view`'s page, marked `data-source="static"`, with its files and three.js; the script's
models as `GET /api/models` would list them were they alone, their manifests and chunks;
`events.json`, the script's calls as the feed resolved them, with the status "recorded",
the pause between two calls and the credit; `elements.json`, the class, name and storey
of every element and space a scene can draw; and `details.json`, `get_element` of the
objects the calls looked up. The files are named by fixed names and ids only; every value
of an IFC file is a JSON value.

**The page is `view`'s.** `api.js` has a second source, which reads those files by paths
relative to the page, so that it works below the repository's folder on Pages, and plays
the recorded calls back one every `--pause` seconds; `main.js` takes it when the page is
marked static, and otherwise nothing changes. The page's paths are relative for `view`
too: served at `/`, they are the same addresses. The content security policy is `view`'s,
as a `<meta>` tag, without `frame-ancestors`, which browsers ignore there; `no-referrer`
is a `<meta>` tag too. The sidebar names the script and credits its files with links to
their source and licence, `https` only. A click names any element from `elements.json`;
the drawer holds everything for the objects in `details.json`, and says so for the rest.

**Only the Duplex.** It is CC BY 4.0; files A, C and D of the audit state no licence, and
B and E were posted to web-ifc's issues. A script names how its files must be credited
(`Script.credit`); the audit's has none, and `--export` refuses a script without one
(exit 2). Only the script's models are written, whatever else the database holds, and
not the database's address. `DATA_ATTRIBUTION.md` says what the copy redistributes and
what changed: "converted to glTF by IfcOpenShell triangulation, openings cut".

**`pages.yml`**, apart from CI, runs on pushes to `main` and by hand: a PostgreSQL 16
service, `scripts/demo.py --export _site`, then `actions/upload-pages-artifact` and
`actions/deploy-pages`, pinned by commit SHA. The workflow keeps `contents: read`; the
deploy job alone gets `pages: write` and `id-token: write`. CI's `samples` job runs the
same export on every push in place of `--check`, which it includes, and keeps the copy as
an artifact, so a pull request shows the copy before `main` publishes it.

**Measured** on the Duplex, from `scripts/demo.py` on a fresh embedded database on the
Phase 0 machine: 49 files, 13.0 MB, of which 10.3 MB are the 24 chunks and 2.3 MB
three.js; `elements.json` is 245 KB. Served below a folder in Microsoft Edge 158, the nine
calls played back light what `--check` resolved, and the browser reports no violation of
the policy.

## Alternatives considered

Phase 0 rejected:

- **Triangulating in world coordinates** (`use-world-coords`): 1.4 to 4 times slower, and
  `--geometry` would need a second pass in local coordinates (P1).
- **three.js r185.1, the newest release with minified builds** (0.75 MB in place of
  2.1 MB): it would pin a release four months older than the rule allows (P5).

Decision 1 rejected:

- **Parsing the IFC file in the browser with web-ifc.** The database keeps no copy of the
  file, so the page would need the user's file again, and a WASM module of several MB.
  The page would also count and group elements differently from the database it shows.
- **IfcOpenShell's glTF serializer.** It is compiled into the locked wheel, but it writes
  only to a file path, writes no `extras`, and makes one file per run, so chunks per
  storey would need one run each.
- **A file cache keyed by SHA-256.** A second store beside the database, with its own
  location, clean-up and backups. In the database, a scene is deleted with its model, and
  `ifc-sql view` needs nothing but the database.
- **Building scenes when `view` starts.** It would need the file, which is not kept, and
  the first page would wait for the triangulation: 27 s for file B on 4 threads.
- **A `scene_seconds` column on `ingestion_run`.** A scene is not a load of the model: it
  is built, kept or replaced on its own, so its own row records its time.

Decisions 2, 3 and 6 rejected:

- **A web server inside `serve`.** The MCP server would hold a port while it is meant to
  speak only stdio (ADR 6, decision 1), and a link to the page would have to pass through
  the conversation. A separate `view` keeps `serve` as it is.
- **The MCP SDK's `transport_security`.** It allows any port of an allowed host, and lets
  a request without an Origin through; the viewer's own check is a dozen lines.
- **A cookie for the token.** Cookies are scoped by host, not port (RFC 6265 §8.5): one
  would reach every other server on 127.0.0.1, and two viewers would overwrite each
  other's.
- **The token in the query (`?t=`).** It would reach the history and any log. Only
  Phase 3's `EventSource`, which cannot send headers, takes it there.
- **Letting uvicorn bind.** A taken port would end the process with `sys.exit(3)` from
  inside uvicorn, and no message of ours.
- **Starlette's `StaticFiles`.** It takes media types from `mimetypes`, which reads the
  Windows registry, and reads the files on every request; the page's files are known
  when the app is made.
- **`GLTFLoader.load` with `setRequestHeader`.** The plan's first idea. The page fetches
  each chunk in `api.js` instead and hands the bytes to `parseAsync`, so all data passes
  one seam, which Phase 5's static source replaces.
- **One Mesh per primitive.** At least 14,492 draw calls on file B, and 22.5 frames per
  second in Phase 0 (P6).
- **A build step (npm, a bundler, minified three.js).** The page would need Node to
  change, and r186 and later publish no minified build anyway (P5).

Decisions 4 and 5 rejected:

- **A listener inside `serve`, and a `show_in_viewer` tool.** `serve` would hold a port
  and a twelfth tool, a link to the page would pass through the conversation, and only
  that server's client would be seen. One notification per call reaches every `view`.
- **MCP Apps first.** Only Claude Desktop could show it: claude.ai cannot reach a local
  stdio server. The plan leaves it out.
- **A static demo only.** It would never check the live path; the scripted demo plays
  through the real `serve`, and Phase 5 exports what it recorded.
- **psycopg's `AsyncConnection` for the listener.** It refuses the `ProactorEventLoop`
  uvicorn runs on Windows.
- **One long-lived session in `serve` for its notifications.** It would hold a session
  for a whole conversation, against ADR 6's session per call, and a session that broke
  would have to be noticed and replaced. A new one per notification costs 15 ms.
- **The calls in a table.** `ifc_reader` cannot write, and should not; `NOTIFY` needs no
  write and no clean-up.
- **The selection in the event, for every tool.** A `find_elements` on file B finds
  thousands of elements, far past 8,000 bytes; and `view` would light whatever an event
  said. It resolves the tools' own filters itself, and takes ids only from the two tools
  whose results are the selection.
- **Running the SQL of a `run_sql` event in `view`** to find its objects. `view` would run
  SQL any login can send it. The LLM selects `object_id` or `object_ids`, and the
  description of `run_sql` says so.
- **Validating the arguments with a copy of the tools' limits.** The copy could drift; the
  SDK's own argument model of each tool is the check a real call passes.
- **Finding the demo's models by id.** Ids depend on the order of loading; SHA-256 does not.
- **A demo through a real LLM in CI.** It would need a key, cost money and answer
  differently from run to run; the script is the same every time.

Phase 4 rejected, for the audit's script and the page:

- **Teaching `find_models` that a file may be a replaced model**, so that the script
  could name A and pass `old_model_id`. It is more code for the same comparison:
  `compare_models` without `old_model_id` compares D with the model it replaces, which is
  how the tool's description offers it to an LLM.
- **Lighting only D's 51 changed objects**, the plan's "51 elements of D", by leaving the
  added ones out. The selection would no longer be the comparison's: Revit's new
  GlobalIds for the 4 openings are a finding of the audit (A15), and the caption counts
  them as openings not drawn.
- **Showing a script's models together**, as Phase 3 did. The audit's four buildings are
  four projects: placed at their origins less the first's, they would be drawn far apart
  or over each other, and "fit" would frame them all.
- **Leaving the project to the user**, with Phase 3's caption "Model 5 is not shown here:
  pick its project above". A call from Claude about another project would light nothing
  visible until the user picks it, and the scripted demo would show nothing at all.
- **Documenting the audit's commands** in place of `scripts/demo.py --script audit`. They
  are eight, and `--replaces` needs the model id that A's ingest printed, read by hand.
  One command is the demo; it reads the id from the ingest's line.
- **Clearing the view on a `run_sql` whose rows hold no id column**, as Phase 3 did. In
  the live check with Claude Code, the LLM lit the Duplex's 23 external walls with one
  query and counted walls by `IsExternal` with the next; the count put the 23 out, and
  the page said "0 found", while the answer said they were shown. Phase 3 could not tell
  that count from a query that found no object: both carried ids with no groups.
- **Keeping what is lit after every call that lights nothing**, failed calls and empty
  finds included. The page would then show an old answer under a new question that found
  nothing; a failed call still clears the view, and its caption says how it ended.

Decision 7 rejected:

- **A page of its own for the static copy**, or a build step that writes one. Two pages
  would drift; with one page and two sources in `api.js`, the copy runs `view`'s code, and
  the live demo checks it.
- **`details/<GlobalId>.json`, a file per object**, the plan's layout. GlobalIds are
  unique per model only, and differ by case alone where Windows and macOS file names do
  not, so two objects could share a file; `details.json` is keyed by model id and
  GlobalId.
- **`get_element` of every object** in place of `elements.json`'s class, name and storey.
  Several megabytes of properties and findings that no step shows, for a demo whose
  questions name one wall; `make demo` shows them all.
- **Exporting the audit's script too.** Its files' licences allow no copy (above); the
  rule is data, a script's credit, not a list of names.
- **Writing into a folder that holds files**, overwriting or emptying it first. A chunk of
  an older export would stay behind, or a file of the user's would go; a new or empty
  folder is the only safe target.
- **Deploying from CI's `samples` job.** Every branch would need the Pages permissions;
  `pages.yml` alone holds them, and only on `main`.
- **`actions/configure-pages` with `enablement`.** It needs a token that may change the
  repository's settings; Pages is switched to GitHub Actions once, by hand.
