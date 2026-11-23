# The viewer: `ifc-sql view`

`ifc-sql view` shows the loaded models in 3D, in your own browser, on your own computer:
every discipline of a project together, storey by storey, with an element's details on a
click. It reads the scenes that `ifc-sql ingest --viewer` built
([ADR 14](adr/0014-viewer.md), decision 1) and nothing else of the files, which the
database does not keep. The design is in ADR 14, decisions 2, 3 and 6; what the port
allows is in [SECURITY.md](../SECURITY.md#the-viewers-port-ifc-sql-view).

![The four Duplex models in ifc-sql view, Roof hidden, a ceiling of the architectural model clicked and its details open](images/viewer-duplex.png)

*The four Duplex models, after `make ingest-samples`, in `uv run ifc-sql view`: the
storey Roof hidden, a ceiling of the ARC model clicked. Microsoft Edge 158 on Windows 11,
2026-11-23.*

Coming in Phase 3 of [the plan](plans/2026-11-ifc-viewer.md): the MCP server's calls in
the page, with the elements each one found lit up, and a scripted demo.

## Start it

The models need their scenes first: `--viewer` at ingest, or on a file already loaded, to
build its scene in place.

```bash
make ingest-samples     # the four Duplex files, with --viewer
make view               # uv run ifc-sql view
```

On Windows, in PowerShell, see [windows.md](windows.md#look-at-the-models-in-3d). The
command prints the page's address on stdout and opens it in your browser:

```
http://127.0.0.1:53358/#t=fIOk_8X-KXsQCZLIly49YiMBCfX-cs87cBYczdvPmG8
```

and on stderr `showing the models at http://127.0.0.1:53358/; stop with Ctrl+C`. Keep it
open while you look; Ctrl+C stops it. The part after `#t=` is a token, new on every
start, that the page needs for every request: a page opened from an earlier start, or
from somewhere else, gets nothing.

| Option | What it does |
|---|---|
| `--project GROUP` | Show this project first: its current models, every discipline together. By default, the project of the newest model that has a scene. The page has a picker for the others. |
| `--model ID` | Show these models instead (repeat it), in that order, each at its origin less the first's. A replaced model may be named, to look at two exports side by side. |
| `--port N` | Listen on this port of 127.0.0.1; 0, the default, takes a free one. A port in use is an error (exit 1). |
| `--no-open` | Print the address, but do not open the browser. |

It needs no other setting than the other commands: `IFC_SQL_DATA_DIR` for the embedded
database, or `IFC_SQL_DATABASE_URL` and `IFC_SQL_READER_PASSWORD`. Like `serve`, it
connects as the read-only role `ifc_reader`, checks first that the database is at the
latest migration, and exits 1 if not; 2 for a `--project` or `--model` that names nothing
loaded. With the embedded database, a database that stopped and started again listens on
another port, so restart `view` then, as `serve` ([ADR 13](adr/0013-embedded-postgresql.md)).

## What the page shows

- **Project.** The current models of one `project_group`, as `list_models` groups them; a
  model of no project is shown on its own. The picker lists every project, the one with
  the newest scene first.
- **Models.** Each with its scene's counts, which add up to the model's elements: "268
  elements: 218 drawn, 0 without shape, 50 openings not drawn" on the Duplex ARC model. A
  model without a scene is greyed out, with the command that builds it, such as `ifc-sql
  ingest revit_quirks.ifc --discipline ELE --viewer`.
- **Disciplines.** A chip per discipline hides or shows its models. ARC keeps its
  materials' colours; MEP is tinted blue, ELE amber and PLU green.
- **Storeys.** One row per storey name across the models ("Level 1" of all four Duplex
  models is one row), highest first, with its elevation and its elements; a row hides or
  shows its chunks. "show all" brings back the hidden ones.
- **Rooms.** The spaces, see-through, loaded when the box is ticked. The spaces that
  repeat a room and Revit's area plans are kept apart in their own chunks, and are never
  drawn yet: Phase 3 draws them when a call selects one.
- **A click** lights the element in orange, names it over the view, and opens a drawer
  with what `get_element` returns: identity, storey and room, quantities with their
  source, property sets, materials, classifications and data quality findings. A click
  on a stair's flight selects the flight; Escape, or a click on nothing, clears it.
- **fit**, **fit selection** and **only selection** frame what is shown, frame the
  selection, or hide everything else.

Every model is placed at its scene's origin less the first model's: the models of a
project line up, as the Duplex's four and the audit's A and D do (ADR 14, P4).

## Measured

`window.ifcViewer.measure(10)`, in the browser's console, orbits the camera for ten
seconds, drawing every frame, and returns what it drew: frames per second, the worst
frame, draw calls, triangles, the batches' vertices and the JS heap. `window.ifcViewer`
also says when the page is `ready`, and after how many milliseconds (`ready_ms`).

On a desktop: Windows 11, Intel Core i7-14700F, NVIDIA GeForce RTX 4060 Ti, 32 GB,
Microsoft Edge 158 (ANGLE on Direct3D 11), a 144 Hz display, the canvas 1,284 × 901 CSS
pixels at a device scale of 1; three.js r187; 2026-11-23. The tab's memory is the private
bytes of its renderer process, and the GPU process's as Windows reports them.

| Scene | Chunks | Primitives (instances) | Vertices | Triangles | Draw calls | Ready after | Frames per second | Worst frame | JS heap | Tab | GPU process |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| File B, the tower (`R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc`) | 28 | 12,774 | 831,231 | 1,598,866 | 76 | 0.93 s | 144.1 | 15 ms | 68 MB | 173 MB | 219 MB |
| The four Duplex models | 13 | 1,822 | 411,473 | 753,614 | 23 | — | 144.2 | 7 ms | 31 MB | — | — |

- 144 frames per second is the display's refresh rate: the cap. Phase 0 measured the same
  76 draw calls and the same cap on file B with a prototype and three.js r185.1 (ADR 14,
  P6); the page's JS heap is half the prototype's 141 MB, because it frees each chunk's
  parsed meshes once they are in the batches.
- The batches hold 831,231 vertices, as many as file B's scene: each primitive has its
  own (P6). The prototype's shared buffers had made it 1,040,891.
- "Ready after" is from the start of the page's load to the last of the 28 chunks drawn,
  25.2 MB read from the embedded database on the same machine.
- **Not measured yet: a laptop.** No laptop was at hand for this phase, so the plan's
  laptop figure is still to be added: open file B on one and add a row with
  `ifcViewer.measure(10)`.

## How it is built

`ifc-sql view` binds 127.0.0.1 itself (so a taken port is an error it reports), and runs
the app of `viewer/http.py` with uvicorn: no lifespan, no access log, uvicorn logging
through the project's stderr logger, two seconds to finish on Ctrl+C.

| Route | Returns |
|---|---|
| `GET /` | The page, `index.html`. No token. |
| `GET /static/{path}` | The page's own files, and no others: no listing, no `..`. No token. |
| `GET /api/models` | `list_models` with every model's scene (or the command that builds one), the projects, and the set shown first. |
| `GET /api/models/{id}/scene` | The manifest: the origin, the counts, and each chunk's number, kind, storey, elements, bytes and SHA-256. |
| `GET /api/models/{id}/scene/{chunk_no}.glb` | The chunk, `model/gltf-binary`, with its SHA-256 as `ETag`: a reload revalidates it and gets a 304. |
| `GET /api/models/{id}/objects/{global_id}` | `get_element`, clipped as the MCP server clips it. |

Every `/api/` route reads as `ifc_reader` in a read-only transaction with the MCP server's
limits (5 s per statement), at most three at once. No route runs SQL that came from a
request.

The page is plain ES modules, with no build step and no npm, in
`src/ifc_sql_mcp/viewer/static/`:

| File | Does |
|---|---|
| `index.html`, `viewer.css` | The layout. The import map is the only inline script. |
| `main.js` | The sidebar, the loading of the chunks (four at a time), the toggles, the selection. |
| `scene.js` | Each chunk into two BatchedMeshes, one opaque and one transparent, one instance per primitive; the map from GlobalId to instances; picking, fitting, `measure`. |
| `details.js` | The drawer. |
| `api.js` | The one seam for data: the token, and `fetch` with `Authorization: Bearer`. |
| `dom.js` | The one way the page makes elements: text as `textContent`, never HTML. |
| `vendor/three/` | three.js r187, unminified, with its MIT licence and `SHA256SUMS`. |

## Upgrading three.js

three.js is vendored and committed, so the page needs no network and no build. To move to
a newer release:

1. **Pick the release**: the newest at least two weeks old (`npm view three time`, or
   <https://registry.npmjs.org/three>). r187 was picked on 2026-11-22 (ADR 14, P5).
2. **Download it into an empty folder, and check it** against npm's integrity field:

   ```bash
   mkdir /tmp/three && cd /tmp/three
   curl -sSfLO https://registry.npmjs.org/three/-/three-0.187.0.tgz
   curl -sSf https://registry.npmjs.org/three/0.187.0 | grep -o '"integrity":"[^"]*"'
   echo "sha512-$(openssl dgst -sha512 -binary three-0.187.0.tgz | openssl base64 -A)"
   tar -xzf three-0.187.0.tgz
   ```

   The two `sha512-…` values must be equal.
3. **Copy the files** into `src/ifc_sql_mcp/viewer/static/vendor/three/`, as they are:
   `package/build/three.module.js` and `three.core.js`; `package/LICENSE`;
   `package/examples/jsm/loaders/GLTFLoader.js` into `addons/loaders/`,
   `utils/BufferGeometryUtils.js` and `utils/SkeletonUtils.js` into `addons/utils/`, and
   `controls/OrbitControls.js` into `addons/controls/`. If `GLTFLoader.js` imports
   another addon (`grep "from '" GLTFLoader.js`), copy that too: a test fails on an import
   that is not vendored. Do not name a folder `build/`: `.gitignore` ignores it.
4. **Write `SHA256SUMS`** again, in that folder (`--text` makes Git Bash on Windows write
   two spaces, as on Linux):

   ```bash
   sha256sum --text LICENSE three.core.js three.module.js addons/*/*.js > SHA256SUMS
   ```

5. **Update the release** in `tests/unit/viewer/test_page.py` (`REVISION = '187'`), on
   this page, in ADR 14 and in the README's licences.
6. **Check**: `uv run pytest tests/unit/viewer`; then open the Duplex and file B with
   `ifc-sql view`, click an element, and record the frames per second above.

`.gitattributes` keeps git from changing the vendored files' line endings on Windows, and
`.pre-commit-config.yaml` keeps the whitespace fixers and the 500 KB limit off them:
r187 publishes no minified build, and `three.core.js` alone is 1.5 MB.
