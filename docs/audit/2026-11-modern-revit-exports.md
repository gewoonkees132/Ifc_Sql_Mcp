# Audit: is ifc-sql-mcp useful to architects working in Revit?

**Date:** 21 November 2026 · **Code audited:** commit `15841f4` (README "docs: write the README")
· **Files:** two modern Revit exports, plus three supporting files · **Harness and evidence:**
[`harness/`](harness/README.md), [`evidence/`](evidence/)

This report does two jobs:

- **[Part A](#part-a--improving-the-tool)** is a prioritised backlog for whoever builds the
  tool. Each finding gives its evidence, why it matters to an architect, and a fix.
- **[Part B](#part-b--the-example-workflow-what-works-and-what-does-not)** walks through an
  architect's real questions, from a Revit export to an answer from Claude, and marks where
  the workflow holds and where it breaks.

Every number below was checked against an independent reading of the IFC file with
IfcOpenShell, the [ground truth](#how-it-was-checked), not against the tool's own output.

---

## 1. Verdict

**The foundation is very good, but the tool cannot yet answer the questions architects most
need answered from a modern Revit export.**

What holds up, on both modern files:

- **Loading.** It is fast, robust and honest. A 73 MB, 28-storey Revit 2022 model loads in
  11.7 s, and nothing it cannot represent is lost without trace.
- **Model structure.** Storeys, elevations, element counts, Revit family/type/ElementId and
  `Pset_*Common` flags (IsExternal, LoadBearing, FireRating) all come back **exactly right**.
- **The MCP layer.** It is quick (median 30 ms per call), safe (read-only, bounded) and
  never crashed.

What breaks:

- **Quantities.** The tool was built against Revit 2011/2013 habits. On Revit 2021 and
  2022 exports it produces **no quantities at all**: no wall areas, no room areas, no door
  sizes. The file either has them in a form the tool ignores (IFC4) or has none (IFC2X3
  with default settings).
- **Wrong numbers.** Three defects give *wrong* numbers, not missing ones:
  - Wall types are double-counted, because openings are counted with them.
  - Revit 2021's duplicated and unscaled base quantities leak into results. A naive sum
    gives 1.16 billion m² of wall instead of 946 m².
  - Rooms exported twice inflate room areas by 32%.
- **Data quality signal.** The check mostly reports the tool's own limitations (1,458
  "errors" in a sound model). It misses what an architect would fix: no quantities, no
  rooms, placeholder materials, no fire ratings.

**With Claude in front of it, the workflow is better than the tool alone.** Across six real
sessions it refused to invent areas, told the architect which Revit export option to tick,
and on the IFC4 house reconstructed the correct 946 m² through a workaround. Where it went
wrong, the cause was in the tool:

- It repeated the doubled count per wall type (A3).
- With no door sizes loaded, it read them from type names. It labelled them as such, but
  they were up to 0.9 m off.

**Recommendation.** Fix the five P0 and P1 data items (A1–A4 and A7) before putting this
in front of architects. They are mostly mapping work in `quantities.py`, `extract.py` and
the views, not redesign. Until then, architects get useful answers today if they export
with **"Export Revit property sets"** ticked; that path was verified on a Revit 2019 file
(see [the workaround](#what-to-tell-architects-today)).

### Scorecard: architect tasks on the two modern files

✅ correct · ⚠️ partly, or only through a workaround · ❌ fails or gives a wrong number ·
— not applicable

| Architect's question | Revit 2021, IFC4 (file A) | Revit 2022, IFC2X3 (file B) |
|---|---|---|
| Which levels, at what elevation? | ✅ 6/6 exact | ✅ 28/28 exact |
| How many elements of each kind, per level? | ✅ exact | ✅ exact |
| How many doors and windows per level? | ✅ exact (real Claude session, Q6) | ✅ exact |
| Which walls are external / load-bearing? | ✅ 28 of 47 | ✅ 3,636 of 6,095 |
| Are fire ratings set? | ✅ none (and 51 detected after the [revision](#step-8-i-changed-it-in-revit-what-changed)) | ✅ none |
| Wall area per type, for the cost plan | ❌ tool: null. ⚠️ Claude: 946 m², via a regex workaround. Naive SQL: 1.16 billion m² | ❌ no quantities in the file. Geometry would give 103,058 m² |
| Material quantities | ❌ areas null; door and window materials lost (IFC4) | ❌ areas null |
| Door schedule with sizes | ⚠️ sizes only via `run_sql` on the stored Qto | ❌ sizes not loaded. Claude read them from type names, up to 0.9 m off |
| Room schedule with areas | — no rooms in file. Supporting file C: ❌ 163/163 null; ⚠️ 4,917–8,091 m² through workarounds | — no rooms in file |
| Find an element by Revit ElementId | ✅ | ✅ |
| Is the model fit for handover? | ⚠️ tool report misleading; Claude's own reading good | ⚠️ same (real session, Q5) |
| What changed since my last export? | ⚠️ hand-written SQL only | — |
| Line it up with the other disciplines | — | ❌ merged into an unrelated project through Revit's default project number `0001` |

---

## 2. What was audited

### The files

No public, building-scale export from Revit 2023–2025 could be found. GitHub code search
only indexes files under 384 KB, and no public sample repository holds one. The newest
public Revit exports are from exporter versions 21 and 22. Between them, the two primary
files cover both schemas an architect exports today, and a small and a large project.

| | File | Source | Written by | Schema, view | Size | Elements | Storeys | Rooms |
|---|---|---|---|---|---:|---:|---:|---:|
| **A** | `Ifc4_Revit_ARC.ifc` | [youshengCode/IfcSampleFiles](https://github.com/youshengCode/IfcSampleFiles) @ `7e69279` | Revit 2021, exporter 21.0.0.383 | IFC4 Reference View 1.2, mm | 13.6 MB | 489 | 6 | 0 |
| **B** | `R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc` | [ThatOpen/engine_web-ifc](https://github.com/ThatOpen/engine_web-ifc) @ `d1962b4`, `tests/ifcfiles/public/ISSUE_098_…` | Revit 2022, exporter 22.0.2.392 | IFC2X3 Coordination View 2.0, m | 72.9 MB | 14,612 | 28 | 0 |
| C | `Ifc4_Revit_MEP.ifc` | IfcSampleFiles @ `7e69279` | Revit 2021, exporter 21.0.0.383 | IFC4 Reference View 1.2 | 29.2 MB | 6,955 | 4 | 163 |
| D | `Ifc4_Revit_ARC_FireRatingAdded.ifc` | IfcSampleFiles @ `7e69279` | Revit 2021 (A, re-exported) | IFC4 Reference View 1.2 | 13.6 MB | 489 | 6 | 0 |
| E | `ARK_NUS_skolebygg.ifc` | engine_web-ifc @ `d1962b4`, `ISSUE_068_…` | Revit 2019, exporter 19.2.20.24 | IFC2X3 CV 2.0 + quantity take-off | 57.3 MB | 5,319 | 5 | 307 |

- **A** is a detached house: Basic Walls, a curtain wall, SIP and concrete walls, furniture.
- **B** is a real 24-storey residential tower with two basements (Italian, 1,458 doors).
- **C, D and E** answer one question each. Neither primary file contains rooms, so C (an
  office with 163 spaces) tests the room schedule. D is A with fire ratings added and tests
  "what changed". E is exported *with* Revit property sets and tests the workaround.

SHA-256 checksums are in [harness/README.md](harness/README.md#the-files). The files are
not stored in this repository (`data/` is git-ignored).

### How it was checked

1. **Baseline.** The tool's own suite: 867 of 869 tests pass with 100% branch coverage.
   The two failures depend on the environment (A17). `make dq-samples` and the 102 sample
   tests reproduce the README's Duplex figures exactly.
2. **Load and check.** Each file went through `ifc-sql ingest` and `ifc-sql dq`, with wall
   time and peak memory measured.
3. **Ground truth.** [`harness/ground_truth.py`](harness/ground_truth.py) reads each file
   with IfcOpenShell and never imports the tool. It records counts per class and storey,
   door and window `OverallWidth/OverallHeight`, `Pset_*Common` flags, material kinds, the
   IFC quantity sets (resolving Revit 2021's duplicates), and wall and slab areas measured
   from the geometry.
4. **MCP calls.** [`harness/architect_workflow.py`](harness/architect_workflow.py) starts
   `ifc-sql serve` over stdio, as Claude Desktop does. It makes 68 tool calls and 5
   resource reads on models A, B and C, covering every tool, 11 typical `run_sql` queries,
   the resources and the prompt. Log: [evidence/mcp_workflow_calls.txt](evidence/mcp_workflow_calls.txt).
5. **Real LLM sessions.** Six architect questions, each in a fresh headless Claude Code
   session ([`harness/run_llm_questions.sh`](harness/run_llm_questions.sh)) that could use
   **only** the `ifc-sql` MCP server: no shell, no files, no resource reader, the same
   position as Claude Desktop. Transcripts: [evidence/llm_sessions.md](evidence/llm_sessions.md).

Environment: 4 vCPU, 16 GB RAM, Linux, PostgreSQL 16.15 (local; the Docker daemon was not
available), Python 3.13 through `uv sync --locked`.

---

## 3. Performance

Performance is not the problem. Everything an architect would wait for is in seconds, and
every MCP call in tens of milliseconds.

| Load (`ifc-sql ingest`) | Size | Objects | Property values | Issues | Wall time | Peak memory |
|---|---:|---:|---:|---:|---:|---:|
| A, Revit 2021 IFC4 | 13.6 MB | 500 | 3,148 | 263 | 2.6 s | 227 MB |
| B, Revit 2022 IFC2X3 | 72.9 MB | 14,642 | 77,938 | 8,224 | 11.7 s | 558 MB |
| C, Revit 2021 IFC4 MEP | 29.2 MB | 7,124 | 29,709 | 1,409 | 5.4 s | 311 MB |
| E, Revit 2019 IFC2X3 | 57.3 MB | 5,633 | 246,050 | 2,063 | 12.1 s | 600 MB |
| B again (same bytes) | | | | | 1.7 s, skipped | 173 MB |

- **B in stages:** extract 6.6 s (IfcOpenShell opens the file in 2.2 s), transform 1.1 s,
  load 2.4 s for 129,650 rows. Memory is about 170 MB plus 5–8 MB per MB of IFC, so a
  300 MB federated export would need roughly 2–2.5 GB.
- **`ifc-sql dq`** on A, B and C together took 2.3 s.
- **MCP calls:** 68 tool calls with 0 errors. Median 0.030 s, p95 0.081 s, slowest 0.29 s
  (a `run_sql` join on A). Paging through all 1,458 doors of B takes 15 calls and 0.93 s.
- **What the LLM has to read** is the real cost (A11). One page of 100 doors from B is
  32,875 characters; a full door list is about 490,000. The room schedule of C (163
  spaces) is 36,644 characters, so at the tool's own cap of 500 spaces it would be about
  112,000. That is over the 50,000 characters Claude Code shows inline, which
  [docs/mcp.md](../mcp.md) says the results are built to stay under.
- **Geometry as a quantity source is affordable.** IfcOpenShell triangulated and measured
  all 9,190 walls, slabs, doors and windows of B in 33.8 s on 4 cores, with no failures. On
  A it took 0.3 s, and its wall faces (964.6 m²) agree with the file's own net side area
  (946.0 m²) to within 2%.
- **Real Claude sessions:** 20–75 s each, $0.07–$0.49 each, $1.42 for all six.

---

## Part A — Improving the tool

Priorities: **P0** gives a wrong or missing number on an architect's core question; **P1**
is a significant gap; **P2** is friction or noise; **P3** is hygiene. Effort: S under a
day, M a few days, L a week or more.

| # | Finding | Priority | Effort |
|---|---|---|---|
| [A1](#a1-the-quantity-sets-modern-revit-writes-are-ignored) | The quantity sets modern Revit writes are ignored: 0 quantities | P0 | M |
| [A2](#a2-revit-2021s-duplicated-and-unscaled-quantities-reach-the-llm) | Revit 2021's duplicated, unscaled quantities reach the LLM | P0 | M |
| [A3](#a3-grouping-by-revit-type-counts-openings-as-walls-and-doors) | Grouping by Revit type counts openings as walls and doors | P0 | S |
| [A4](#a4-door-and-window-sizes-are-not-loaded) | Door and window sizes are not loaded | P1 | S |
| [A5](#a5-no-geometry-quantities-for-files-that-carry-none) | No geometry quantities for files that carry none | P1 | L |
| [A6](#a6-ifc4-material-constituent-sets-are-not-read) | IFC4 material constituent sets are not read | P1 | S |
| [A7](#a7-rooms-exported-twice-are-counted-twice) | Rooms exported twice are counted twice | P1 | S |
| [A8](#a8-unknown-totals-are-reported-as-0) | Unknown totals are reported as 0 | P1 | S |
| [A9](#a9-data-quality-measures-the-loader-not-the-model) | Data quality measures the loader, not the model | P1 | M |
| [A10](#a10-the-llm-cannot-find-the-schema) | The LLM cannot find the schema: 28% of its calls failed | P2 | S |
| [A11](#a11-schedules-cost-too-much-context) | Schedules cost too much context | P2 | M |
| [A12](#a12-projects-and-versions-are-guessed-from-the-file) | Projects and versions are guessed from the file | P2 | M |
| [A13](#a13-findings-that-are-noise) | Findings that are noise: 6,488 on B alone | P2 | S |
| [A14](#a14-modern-stair-part-names-are-not-parsed) | Modern stair part names are not parsed | P2 | S |
| [A15](#a15-no-way-to-compare-two-exports) | No way to compare two exports | P2 | M |
| [A16](#a16-control-characters-in-names) | Control characters in names | P3 | S |
| [A17](#a17-two-tests-depend-on-the-environment) | Two tests depend on the environment | P3 | S |
| [A18](#a18-the-path-from-revit-to-a-question-is-long) | The path from Revit to a question is long | P3 | L |

**Suggested order.** First A3, A8, A2, then A1, in that order: A1 without A2 turns
missing numbers into wrong ones. Next A4, A6, A7, A9 and A10. Then the rest. Add files A,
B and C to a `-m modern` sample suite pinned like the Duplex one, with the figures in this
report as its first assertions.

### A1. The quantity sets modern Revit writes are ignored

**Evidence.**

- `quantities.QUANTITY_RULES` maps only `PSet_Revit_Dimensions`/`Dimensions` and
  `GSA Space Areas`. The module says so itself: "Revit 2011 and 2013 exports contain no
  `Qto_*BaseQuantities`".
- File A carries 1,375 IFC quantities (536 areas, 526 lengths, 313 volumes) in
  `Qto_WallBaseQuantities`, `Qto_SlabBaseQuantities`, `Qto_DoorBaseQuantities` and the
  rest. File C carries `Qto_SpaceBaseQuantities` on all 163 spaces. Both files store
  **0 rows** in `ifc.quantity`. The values sit in `ifc.property` (`from_quantity_set`),
  where no tool reads them.
- Revit's IFC2X3 exports name the same set `BaseQuantities`: 3,939 of them in file E,
  also ignored.

**Why it matters.** `quantity_takeoff`, `space_schedule` and the length, area and volume
columns of `v_element` are empty on every modern export tested. Cost plans, area schedules
and material takeoffs, the main reason an architect would ask a model a question, return
nothing.

**Fix.** Add rules with source `ifc_qto`, ranked above `revit_pset` as ADR 3 intends:

| Set | Quantity |
|---|---|
| `Qto_WallBaseQuantities` | `NetSideArea` (→ `area_m2`), `Length`, `Height`, `NetVolume` |
| `Qto_SlabBaseQuantities` | `NetArea`, `NetVolume`, `Perimeter` |
| `Qto_SpaceBaseQuantities` | `NetFloorArea`, `Height`, `GrossPerimeter`, `GrossVolume` |
| `Qto_DoorBaseQuantities`, `Qto_WindowBaseQuantities` | `Width`, `Height`, `Area` |
| `BaseQuantities` (IFC2X3) | the same names |

Also add `width_m` to the quantity vocabulary. Pin it with file A (total wall net side
area **946.0 m²**) and file C (163 of 163 spaces with an area). **Do A2 first.**

### A2. Revit 2021's duplicated and unscaled quantities reach the LLM

**Evidence.** Revit's exporter 21.0.0.383 writes `NetSideArea` and `NetVolume` **twice** in
one `Qto_WallBaseQuantities`. For wall `#3665`:

```
#3810= IFCQUANTITYLENGTH('Height',$,$,6498.67729846129,$);       -- mm, correct
#3811= IFCQUANTITYLENGTH('Length',$,$,6403.99999999979,$);       -- mm, correct
#3815= IFCQUANTITYAREA('NetSideArea',$,$,41617529.4193447,$);    -- Height x Length in mm²: gross, unscaled
#3816= IFCQUANTITYAREA('NetSideArea',$,$,22.3549896560785,$);    -- the real net area, m²
```

- The first value is the gross face in mm², labelled m². `properties.py` keeps "first in
  the file wins", so the database keeps it and reports `conflicting_property` (94 times on
  A, 266 on C).
- `get_element` therefore shows an architect **`NetSideArea = 34,966,030 m²`** and
  **`NetVolume = 9,790,488,407 m³`** for one ordinary wall, with an SI unit and no warning.
- The obvious `run_sql` sum gives **1,157,891,673.6 m²** of wall on A (true: 946 m²) and
  3.79 billion m² on C.
- The space quantities of C are wrong too: `GrossVolume` is 1,000× too large (a 474 m²
  corridor at 1,304,234 m³), and `GrossPerimeter` is written in feet but labelled mm (a
  14.7 m² room with a 0.055 m perimeter).

**Why it matters.** Once A1 is done, these values would feed `quantity_takeoff` directly. A
cost plan off by a factor of a million is easy to spot. A room volume off by a factor of a
thousand, feeding an HVAC or fire-load estimate, is not.

**Fix.**

- **Resolve duplicates by plausibility, not file order.** Of several values for one
  quantity in one set, keep the one consistent with the set's other quantities:
  `NetSideArea ≤ Length × Height`, `NetVolume ≈ NetSideArea × Width`. Keep the rejected
  value in the issue message, as now.
- **Add data quality rules for physical consistency:**

  | Rule | Check |
  |---|---|
  | `quantity_inconsistent` | wall side area > length × height × 1.05; slab volume ≠ area × thickness ± 10% |
  | `space_volume_inconsistent` | volume ≠ floor area × height ± 10% |
  | `space_perimeter_implausible` | perimeter < 2√(π·area), below what any shape can have |

- **Hide or flag implausible values** in `get_element`, rather than serving them as
  measurements.

### A3. Grouping by Revit type counts openings as walls and doors

**Evidence.**

- Revit names an opening after its host (`Basic Wall:1AR_MUR_CV003…:1044048:1`), and
  `revit_names.py` parses that into the host's family and type. All 3,314 openings in B
  carry their host's type (a wall, door or window), and 48 of 50 in the Duplex do too, so
  this predates the modern files.
- `quantity_takeoff(group_by="revit_type")` does not filter by class. For wall type
  `1AR_MUR_CV003` it reports **2,330 elements**: 1,114 + 51 walls and 1,165 openings. In
  the first, discarded run of session Q1, Claude repeated "2,330" to the architect as the
  number of walls of that type.

**Why it matters.** "How many walls or doors of this type?" is one of the most common
questions, and the answer comes back up to twice too large.

**Fix.**

- **Parser:** store an opening's parsed family and type as `host_revit_family` and
  `host_revit_type`, not `revit_family` and `revit_type`.
- **Views:** leave `IfcFeatureElement` (openings) out of the `revit_type` and `ifc_class`
  groupings by default.
- **Test:** pin B's `1AR_MUR_CV003` at 1,165.

### A4. Door and window sizes are not loaded

**Evidence.**

- `IfcDoor.OverallWidth/OverallHeight` and `IfcWindow.OverallWidth/OverallHeight` are
  attributes, not properties, and `extract._element` does not read them.
- B has them on **1,458 of 1,458 doors and 1,199 of 1,199 windows**, yet the database has
  no width or height for any door or window of B.
- The door panel and lining data (`IfcDoorPanelProperties`, `IfcDoorLiningProperties`) is
  dropped as `unsupported_property_set`, 1,458 times.

**Why it matters.** A door schedule without sizes is not a door schedule.

- In session Q2, Claude produced the counts exactly right (159 doors in 6 types on level
  XX_AR_L01), then took sizes from type names and labelled them as such.
- The type names give leaf sizes; the file's overall sizes differ:

  | Type | Claude (from the name) | File (`OverallWidth`) |
  |---|---:|---:|
  | `PI002 80x210` | 0.80 m | 0.87 m |
  | `PI001 90 x 210 ingresso` | 0.90 m | 1.127 m |
  | `PI003 120 x 210 - US` | 1.20 m | 1.35 m |
  | Sliding `PI005 80x210` | 0.80 m | 1.697 m |

**Fix.**

- Read `OverallWidth` and `OverallHeight` (converted by the length unit) as quantities
  `width_m` and `height_m`, with source `ifc_attribute`.
- Read `OperationType` from `IfcDoorType`/`IfcDoorStyle`.
- Read at least `LiningDepth` and `PanelOperation` from the panel and lining sets.

### A5. No geometry quantities for files that carry none

**Evidence.**

- B has **no quantities of any kind**: no `Qto_*`, no Revit dimensions. Revit wrote it
  with both "Export base quantities" and "Export Revit property sets" off. Neither option
  is needed for coordination, so files like B are what an architect receives whenever
  they are left off.
- The README says quantities come only from the file, and ADR 3 reserves a `geometry`
  source.
- IfcOpenShell measured B's 6,095 walls at 103,058 m² of face, with all 9,190 shapes in
  33.8 s on 4 cores.

**Why it matters.** For files like B, which are typical of what architects send, the only
route to an area is geometry. Without it, every area question ends with "re-export from
Revit".

**Fix.** Add an optional `ifc-sql ingest --geometry` that measures with
`ifcopenshell.geom` in parallel:

| Element | Quantity |
|---|---|
| Walls | side area |
| Slabs | footprint area |
| Spaces | floor area |
| All | volume |

Store the results with source `geometry`, ranked last so file quantities still win, and
report any shape that fails. Record the time per model in `ingestion_run`.

### A6. IFC4 material constituent sets are not read

**Evidence.** Revit's IFC4 exporter gives doors, windows, furniture and families an
`IfcMaterialConstituentSet`. `extract._materials` reads it as `unsupported_material`, so
the element gets **no materials**:

| File | Elements affected | Including |
|---|---:|---|
| A | 104 | every door and window (0 of 17 windows have a material) |
| C | 295 | |

**Fix.** Read the constituents in order, as with `IfcMaterialList`: name, category, and
fraction where given. Also read `IfcMaterialProfileSet` (and its `…Usage`) for members and
columns.

### A7. Rooms exported twice are counted twice

**Evidence.**

- C has **163 `IfcSpace`s for 116 distinct rooms**. 47 are pairs with the same number,
  name, storey and floor area, but heights of 3.8 m vs 2.75 m, the mark of a Revit model
  that exports both Rooms and MEP Spaces. Nothing in the file tells them apart.
- Summing gives 8,090.7 m²; one row per number, name and storey gives 6,124.8 m² (+32%).
- In session Q4, Claude spotted the pairs and settled on 4,917–5,391 m² with its own
  heuristic. Three defensible totals, and the tool gives no basis for choosing.

**Fix.**

- Add a data quality rule `duplicate_space` (same number, name and storey; warning, citing
  both spaces).
- Have `space_schedule` mark and count duplicates (`duplicate_of`), so a total can be given
  with and without them.
- Document that Revit MEP models should be exported with either rooms or spaces.

### A8. Unknown totals are reported as 0

**Evidence.**

- With no element carrying the metric, every `quantity_takeoff` on A, B and C returns
  `"value": null` per group but **`"total": 0`**.
- `space_schedule` on C returns `"total_area_m2": 0.0` next to
  `"spaces_without_area": 163`.

**Why it matters.** A model, or a person scanning JSON, reads `total: 0` as zero square
metres. In Q1 Claude caught it ("the total would come out as 0 m², and that would be
wrong"), but the tool should not depend on that.

**Fix.**

- Make `total` null when `elements_with_value` is 0 for every group.
- Add a `coverage` figure, e.g. "0 of 6,095 walls have area_m2".
- When coverage is 0, put a warning in `note` that names the likely cause: no base
  quantities, no Revit property sets.

### A9. Data quality measures the loader, not the model

**Evidence.** On B, `ifc-sql dq` reports **1,458 errors and 6,766 warnings**:

- **None of the 1,458 errors is something the architect can fix in Revit.** They are door
  panel sets this loader does not read (`unsupported_property_set` is severity "error").
- **The warnings are noise:** U-values not converted, openings named after their host, and
  stair names (A13).
- **No rule reports what is actually missing from the model.** Claude found all of these
  in session Q5 by querying, and they are verified:

  | Missing from model B | Extent |
  |---|---|
  | Quantities | 0 for 14,612 elements |
  | Rooms | 0 |
  | Wall build-ups | 6,024 of 6,095 walls are a single layer of "Default Wall" |
  | Fire ratings | none on any wall or door |
  | Window materials | 1,199 of 1,199 windows use a render material, "Materiale per il rendering 0-127-255" |
  | Project information | placeholder: `0001` / `Project Name` |

- On C, `space_without_area` flags **163 of 163** spaces, although every one has a
  `NetFloorArea` in the file. That is a false positive: the loader, not the model, lacks
  the area (A1).

**Why it matters.** An architect asking "is my model fit for handover?" gets a report
dominated by loader limitations, with the real gaps invisible.

**Fix.**

- **Split findings into two dimensions:** `loader_coverage` ("this tool could not read …")
  and `model_content` ("your model lacks …"). Present them separately in
  `data_quality_report`.
- **Add requirement-style rules,** the kind an IDS or BEP would ask for:

  | Rule | Fires when |
  |---|---|
  | `model_without_quantities` | the model has elements but no quantities |
  | `model_without_spaces` | the model has no spaces |
  | `missing_fire_rating` | a wall, door or slab has no `FireRating` (info by default) |
  | `placeholder_material` | a material name matches Default*, `<Unnamed>` or render-material patterns |
  | `placeholder_project_info` | the project is `0001`, `Project Number` or `Project Name` |
  | `space_area_not_mapped` | a space has an area in a set but none in `quantity` |

- **Later:** accept a buildingSMART IDS file as the requirements to check against.

### A10. The LLM cannot find the schema

**Evidence.**

- Across the six real sessions, **20 of 72 tool calls (28%) failed**, all on schema
  discovery:

  | Failure | Calls | Examples |
  |---|---:|---|
  | Guessed table names that do not exist | 14 | `v_property`, `property_value`, `v_space`, `v_element_property` |
  | Tried `information_schema`, refused | 4 | |
  | Guessed column names | 2 | `ifc_class_norm`, `property_name` |

  Q4 alone lost 7 of its 17 calls this way.
- The schema is offered only as the `ifc://schema` resource. In Claude Desktop the user
  attaches resources; the model does not fetch them. These sessions were set up the same
  way. The refusal message points to that resource, which the model cannot read. In
  Claude Code with its built-in tools on, the model could read it, so there this finding
  is smaller.

**Fix.**

- Add a `describe_schema` tool (all of `ifc://schema`, or one table at a time).
- When a relation or column does not exist, append the list of tables, or the table's
  columns, to the error.
- Mention `property(pset_name, prop_name, value_text, value_num, value_bool)` in the
  `run_sql` description, since nearly every architect question beyond counts needs it.

### A11. Schedules cost too much context

**Evidence.**

- A door schedule built from `find_elements` and `get_element` means paging 15 × 100
  doors, about 490,000 characters (~120k tokens), or one `get_element` per door type.
- `space_schedule` returns about 225 characters per space and caps at 500 spaces, about
  112,000 characters, which breaks the 50,000-character budget [docs/mcp.md](../mcp.md)
  sets.
- No tool returns "these elements with these properties".

**Fix.**

- **A `schedule` tool:** a class, a `group_by` (storey, revit_type), and up to *n* chosen
  properties and quantities, e.g. `Pset_DoorCommon.FireRating` and `width_m`. It would
  return one row per group with counts and distinct values: the way Revit itself
  schedules, a few kilobytes for any model size.
- **Page `space_schedule`** at about 150 spaces, or add a compact mode.

### A12. Projects and versions are guessed from the file

**Evidence.**

- `v_storey_alignment` matches models across disciplines by `IfcProject.Name`. B's
  project is named `0001`, Revit's default project number, and so is the Duplex's. The
  audit database lists B as the fifth model of the Duplex project (`project_model_count = 5`).
- C's project is `Project Number`, another Revit default.
- A re-export (D) becomes a separate model in the same project, so any project-wide query
  counts both versions.

**Why it matters.** Architects re-export weekly and coordinate across disciplines. Mixing
projects, or counting last week's model twice, gives wrong coordination answers without
any sign that something is off.

**Fix.**

- Add `ingest --project <key>` (stored on `model`), and group by it instead of the name.
- Add `ingest --replaces <model_id>` (or a `supersedes_model_id` column) and a
  `v_model_current` view that tools use by default.
- Warn at ingest when the project name is a known Revit placeholder.

### A13. Findings that are noise

**Evidence on B.**

- **`revit_tag_mismatch`, 2,072, all `IfcOpeningElement`.** Revit names an opening after
  its host but tags it with its own id, which the docs already call normal.
- **`measure_not_converted`, 4,416.** These are U-values (`ThermalTransmittance`). The
  file declares the SI derived unit (`IFCDERIVEDUNIT(…, .THERMALTRANSMITTANCEUNIT.)`), so
  each could be labelled W/(m²·K) instead.

**Fix.**

- Do not raise `revit_tag_mismatch` for openings.
- Read `IfcDerivedUnit` and `IfcSIUnit` for the common derived measures: thermal
  transmittance, mass density, power, pressure. Store the unit text and raise nothing.

### A14. Modern stair part names are not parsed

**Evidence.**

- Revit 2021 and 2022 name stair parts `Precast Stair:Stair:1287438 Landing 1` and
  `Assembled Stair:Stair:665557 Run 1`, with the part after the id and a space, not a
  colon. These are reported as `revit_name_unparsed`: 278 on B, 3 on A.
- As a result **all 182 stair flights of B have no Revit type.**

**Fix.** Accept `…:<ElementId> (Run|Landing|Support) <n>` in `revit_names.py`, keeping the
host's family and type.

### A15. No way to compare two exports

**Evidence.** Loading D after A and comparing by GlobalId with hand-written SQL shows the
change exactly:

- `Pset_WallCommon.FireRating` added on 42 walls and `Pset_CurtainWallCommon.FireRating`
  on 9 curtain walls (value `M2`).
- 4 openings regenerated with new GlobalIds.

The `data_quality_report` of both versions is identical (120 errors, 143 warnings), since
no rule looks at fire ratings.

**Fix.** Add a `compare_models(old, new)` tool that returns objects added and removed per
class, and properties added, removed and changed per set and name, with counts and a few
examples. The SQL above is already the core of it.

### A16. Control characters in names

**Evidence.** B's wall types include `1AR_MUR_PV001\u001f\u001f\u001f - Interrato_Interrato`.
The `\u001f` characters pass through to `revit_type`, groupings and LLM answers, where
Claude flagged them in Q1.

**Fix.** Report them as an issue, and strip C0 control characters from names, keeping the
raw value in `property`.

### A17. Two tests depend on the environment

**Evidence.** On PostgreSQL 16.15 (Ubuntu) two tests fail and nothing else does:

- `test_the_functions_sqlglot_types_are_the_reviewed_ones` pins the catalogue at 2,644
  executable functions; this build has 2,643.
- `test_ingest_refuses_a_missing_file_as_a_usage_error` expects "does not exist" on one
  line, but Rich wraps the error box at the terminal width, and the long temporary path
  pushes the phrase across two lines.

**Fix.**

- **Function test:** assert that the allowlisted functions are a subset of the catalogue
  and that the reviewed list is unchanged, not the catalogue's exact size.
- **CLI test:** set `COLUMNS=200`, or strip the box before matching.

### A18. The path from Revit to a question is long

**Evidence.** An architect on Windows needs all of the following before asking a question:

- Installs: uv, Docker Desktop, and `make` (or the commands it runs).
- Steps: `make up`, `make migrate`, an `ifc-sql ingest` per file with the right
  `--discipline`, then `ifc-sql dq`.
- Configuration: hand-edited JSON in Claude Desktop's config.
- On every Revit change: re-export and re-ingest.

None of the docs say which Revit export options the tool depends on. The README's
limitation "IFC4 untested" is now answered by this audit.

**Fix, in order of value:**

1. A **"Revit export settings" page** (the [list below](#what-to-tell-architects-today)).
2. An **`ingest_file` MCP tool**, or a watched folder, so a new export is picked up without
   a terminal.
3. A **Windows-native quick start** (PowerShell, no `make`).
4. Longer term, an **embedded database option**, so Docker is not required for one user.

---

## Part B — The example workflow: what works and what does not

The persona is an architect on the tower (file B, model 6) and on the house (file A,
model 5). They use Claude Desktop with the `ifc-sql` server and ask in plain language.
Steps 3–7 quote the real sessions in [evidence/llm_sessions.md](evidence/llm_sessions.md);
the other steps use the tools directly.

### Step 0: export from Revit

**Works, but this choice decides everything downstream.** The two files show the spread:

- **File A** (IFC4 Reference View) has IFC base quantities and no Revit property sets.
- **File B** (IFC2X3 CV 2.0) has neither.
- **File E** (Revit 2019, "Export Revit property sets" ticked) has Revit's `Dimensions`
  set, and it is the only file on which the tool's quantities work today.

### Step 1: load and check

```bash
ifc-sql ingest R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc --discipline ARC
# loaded … as model 6 (ARC): 14,642 objects, 77,938 property values, 8,224 issues   (11.7 s)
ifc-sql dq 6
# checked model 6 … in dq run 6: 1,458 error, 6,766 warning, 0 info findings
```

- ✅ **Works:** fast, idempotent (the same file again: skipped in 1.7 s), and every gap
  recorded.
- ⚠️ **Does not work:** "1,458 errors" alarms the architect, and none of them is theirs to
  fix (A9).

### Step 2: "What's in this model?"

✅ **Works, exactly.**

- `list_models` gives schema, view, authoring application (`Autodesk Revit 2022 (ENG)`),
  unit, and counts.
- `list_storeys` gives all 28 levels, `XX_AR_B02_pf` at −8.570 m to `XX_AR_L24_pf` at
  81.240 m, with element counts identical to IfcOpenShell's, and 0 elements without a
  storey.
- On A it gets six levels including `Level 1 Living Rm.` at −0.550 m, with millimetres
  converted correctly.

### Step 3: "How many doors and windows are on each level?" (session Q6, model 5)

✅ **Works, exactly, in 4 calls, 20 s, $0.07.**

| Level | Doors | Windows |
|---|---:|---:|
| Level 1 Living Rm. (−0.55 m) | 2 | 8 |
| Level 1 (0.00 m) | 4 | 0 |
| Level 2 (3.00 m) | 10 | 7 |
| Roof Line (6.00 m) | 0 | 2 |

All identical to the ground truth. Claude also added a useful observation: Level 1's
window openings are hosted by walls whose windows sit on the living-room level. The same
question on B (doors per level, `run_sql`) matches all 28 levels.

### Step 4: "Wall area per type, for the cost plan"

On **B** (session Q1): ❌ **the tool has nothing to give; ✅ Claude handles it well.**

> I can't give you those numbers, because the model doesn't contain them. … So the total
> would come out as 0 m², and that would be wrong. … export the IFC from Revit again with
> **"Export base quantities"** turned on.

That is correct and the right advice. The ground truth from geometry is 103,058 m² (A5).
In the session's first, discarded run, Claude added a count per type taken from
`quantity_takeoff`, "1AR_MUR_CV003 … 2,330", which is twice the real 1,165 (A3).

On **A** (session Q3): ⚠️ **right answer, by a route no architect should depend on.**

> The 47 walls in model 5 have a total net wall area of **about 946.0 m²** … The export
> wrote `Qto_WallBaseQuantities.NetSideArea` twice on every wall, with two different
> values, and the database kept the wrong one.

946.03 m² matches the ground truth (946.04 from the file, 964.6 from geometry) for every
wall type. Claude got there by **regex-parsing the "dropped" value out of the data quality
messages** in `v_dq_finding`. The query most people, and most models, would write sums
the stored values and returns 1,157,891,673.6 m² (A1, A2).

### Step 5: "Door schedule for level XX_AR_L01: type, count, width, height, fire rating" (session Q2)

⚠️ **Counts exact; sizes wrong.**

- ✅ **Counts:** 159 doors in 6 types (77, 25, 21, 15, 14, 7), and "fire rating: not in
  model". Both match the ground truth.
- ❌ **Sizes:** the tool has no width or height for any door (A4), so Claude read them from
  type names and said so: "the dimensions below are read from the Revit type names. They
  are not measured values". The file's `OverallWidth` differs by up to 0.9 m (the sliding
  door type `PI005 80x210` is 1.697 m wide). The double-leaf doors got "unknown".

### Step 6: "Total net floor area of the rooms, and the five largest" (session Q4, file C)

⚠️ **Rooms found, areas only through workarounds, total ambiguous.**

- ❌ **The tool:** `space_schedule` returns 163 rooms with numbers, names and levels, and
  **every area null** (A1). The `space_without_area` rule blames the model for it (A9).
- ⚠️ **Claude's workaround:** it found the areas in `Qto_SpaceBaseQuantities` through
  `run_sql`, after 7 failed guesses at table names (A10). It noticed the duplicated rooms
  and answered "4,900–5,400 m² is the honest range" (A7), with the five largest rooms
  (Corridor 49 at 474.3 m², Corridor 83, EDP I 54, Corridor 58, MZR 20).
- ✅ **It caught the exporter's bad values:** "Volumes are 1,000 times too large … Perimeters
  are wrong too." (A2).

### Step 7: "Is model 6 fit to hand over to the contractor?" (session Q5)

✅ **Claude's answer is excellent; ⚠️ the tool's own report is not.**

> **Not yet, in my view.** … The automated check counts 1,458 errors and 6,766 warnings.
> Almost none of those are problems in your model, though. The real gaps are things the
> check doesn't flag.

- **Real gaps Claude found, all verified:** "Default Wall" single layers on 6,024 walls,
  no quantities, no rooms, render materials on windows, placeholder project information.
  It explained each tool finding correctly as a loader limitation or normal Revit
  behaviour.
- **One overstatement:** "no classification". In fact 104 doors carry Uniformat
  `C1020120`; it never queried that table.

The analysis an architect needs came from the LLM's own querying, not from the data
quality engine (A9).

### Step 8: "I changed it in Revit: what changed?"

⚠️ **Possible, but only by hand.**

- File D (A with fire ratings) loads as a new model.
- A GlobalId join in `run_sql` shows exactly what changed: `FireRating` added to 42 walls
  and 9 curtain walls (`M2`), and 4 openings regenerated.
- There is no tool for it (A15). The data quality report does not change at all, and
  project-wide queries now count the house twice (A12).

### Step 9: "Line the tower up with the structural and MEP models"

❌ **Wrong without warning.** `v_storey_alignment` put the Italian tower in the same
project as the buildingSMART Duplex, because both are called `0001` (A12).

### What works, and what does not

**Works now:**

- Model structure: levels, elevations, counts per class and level.
- Revit family, type and ElementId lookups.
- Yes/no properties (`IsExternal`, `LoadBearing`, `FireRating` present or not).
- Version diffs, through SQL.
- A model health review, when Claude does the querying.

**Does not work yet on modern Revit exports:**

- Any area, volume or length: walls, slabs, rooms, materials.
- Door and window sizes.
- IFC4 door and window materials.
- Counts per Revit type (doubled by openings).
- Room totals in models with both rooms and spaces.
- Cross-model or cross-version questions.

### What to tell architects today

Until A1–A4 land, these settings and habits get the most from the tool. The first item was
verified on file E: with Revit property sets exported, the tool filled 4,836 areas,
including all 1,057 walls and all 307 rooms, and the room areas (30,344.7 m²) equal the
file's own `BaseQuantities.NetFloorArea` for 307 of 307 rooms.

1. In Revit's IFC export setup, tick **"Export Revit property sets"**. It is the only
   quantity source the tool reads today. Tick **"Export base quantities"** as well, for
   when A1 lands.
2. **Export rooms.** In models that hold both Rooms and MEP Spaces, export one kind, not
   both.
3. Fill in **Project Information** (number and name) with real values, so models are not
   matched by Revit's defaults.
4. Ask freely about counts, levels and properties. Treat any area, volume or size the tool
   gives from a Revit 2021 export as unverified until A2 is fixed. A quick check is area ≤
   length × height, and volume ≈ area × height.

---

## Appendix: reproducing the audit

[harness/README.md](harness/README.md) has the commands: getting the files (with
checksums), loading them as models 5–9 in this order, the ground truth, the MCP driver,
and the six Claude sessions. The evidence from this run is in [evidence/](evidence/):

- `ground_truth_*.json`, the IfcOpenShell readings
- `mcp_workflow_calls.txt`, every MCP call with time and size
- `llm_sessions.md`, every tool call and final answer of the six sessions
