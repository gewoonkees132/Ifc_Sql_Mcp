# Re-check: the audit's architect workflow on the fixed tool

**Date:** 22 November 2026 · **Code re-checked:** commit `513fe97` (all of A1–A18 done) ·
**Files, harness and questions:** the [audit's](2026-11-modern-revit-exports.md), unchanged ·
**Evidence:** [`evidence/recheck/`](evidence/recheck/) · **Tracking issue:** [#22](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/22)

The [implementation log](2026-11-implementation.md) proves each of A1–A18 against its own
acceptance figure. This re-check asks a broader question: **with all eighteen fixes in,
does the architect's workflow of the audit's [Part B](2026-11-modern-revit-exports.md#part-b--the-example-workflow-what-works-and-what-does-not)
now hold?** It repeats Part B as the audit ran it, on the same five files, with the same
MCP driver and the same six questions put to Claude, and compares the answers with
ground truth read by IfcOpenShell alone.

It also looks for what the audit missed. Four new findings, R1 to R4, are listed below
with the evidence and the fix. One of them corrects a figure in the audit itself
([erratum](#erratum-to-the-audit)).

---

## 1. Verdict

**The workflow holds on every question the audit scored, except for room totals.**

- **Every ❌ in the audit's scorecard is gone.** Wall areas, material quantities, door
  sizes, the handover check, what changed between exports, and alignment across
  disciplines now come from the tool, not from workarounds.
- **The sessions are shorter and cheaper.** The six real Claude sessions made 29 tool
  calls, against 72. 2 of them failed, against 20, and both errors named the column to use.
  They cost $1.01 in all, against $1.42.
- **One wrong number remains: room totals.** Revit writes the same floor more than once,
  as Rooms and MEP Spaces, or as Rooms and area plans. The "each room once" total that A7
  added catches only the copies with an identical name. On file C it still counts
  1,403 m² twice. On file E the tool counts Revit's area plans as rooms and reports
  30,344.7 m² for 7,438.8 m² of rooms. This is **R1**, the one P0 of the re-check.
- **Three smaller gaps.**
  - Ceilings' IFC base quantities are not read (R2).
  - Enumerated property values are dropped, which leaves 724 "errors" on file C that are
    loader gaps, not the model's (R3).
  - The no-quantities warning sends the architect back to Revit, although
    `ingest --geometry` can measure the file they already have (R4).

The audit's one deferred item, A18's embedded database, is still open. It is carried into
this round as **A18.4**.

### Scorecard: the audit's architect tasks, before → after

✅ correct · ⚠️ partly, or only through a workaround · ❌ fails or gives a wrong number ·
— not applicable. "Before" is the audit's scorecard; "after" is this re-check.

| Architect's question | A, Revit 2021 IFC4: before | after | B, Revit 2022 IFC2X3: before | after |
|---|---|---|---|---|
| Which levels, at what elevation? | ✅ 6/6 | ✅ 6/6 | ✅ 28/28 | ✅ 28/28 |
| How many elements of each kind, per level? | ✅ | ✅ | ✅ | ✅ |
| How many doors and windows per level? | ✅ | ✅ in 3 calls (Q6) | ✅ | ✅ |
| Which walls are external / load-bearing? | ✅ | ✅ | ✅ | ✅ |
| Are fire ratings set? | ✅ | ✅, and `missing_fire_rating` names every element without one | ✅ | ✅ (Q5) |
| Wall area per type, for the cost plan | ❌ null; ⚠️ 946 m² by regex | ✅ **946.0 m²** by type, from the tool (Q3) | ❌ no quantities | ✅ with `--geometry`: **103,057.7 m²**; ⚠️ the tool's warning does not say so (R4) |
| Material quantities | ❌ areas null, door and window materials lost | ✅ areas; 17/17 windows with a material | ❌ areas null | ✅ with `--geometry` (6,533 elements, mostly on placeholder materials, which the check reports) |
| Door schedule with sizes | ⚠️ only via `run_sql` | ✅ `schedule`, sizes from the file | ❌ sizes from type names, up to 0.9 m off | ✅ **one call**: 159 doors in 6 types, widths as IfcOpenShell reads them (Q2) |
| Room schedule with areas (file C) | ❌ 163/163 null | ⚠️ 163/163 areas, but the total counts 1,403 m² twice (R1) | — | — |
| Find an element by Revit ElementId | ✅ | ✅ | ✅ | ✅ |
| Is the model fit for handover? | ⚠️ report misleading | ✅ the report lists the model's own gaps | ⚠️ 1,458 "errors", none the architect's | ✅ **0 errors**; Claude's answer comes from the report (Q5) |
| What changed since my last export? | ⚠️ hand-written SQL | ✅ `compare_models`: 42 + 9 fire ratings, 4 + 4 openings | — | — |
| Line it up with the other disciplines | — | — | ❌ merged with the Duplex through `0001` | ✅ joins no project, with a warning; `--project` groups it |

---

## 2. How it was checked

The audit's appendix, repeated on the fixed code:

1. **Gate.** `pytest`: 1,057 passed at 100 % branch coverage. `pytest -m "sample or
   modern"`: 126 passed, with no `xfail` left. Lint and `mypy --strict` clean.
2. **Load.** An empty, migrated database, loaded in the audit's order: the four Duplex
   files as models 1–4, then A, B, C, D and E as models 5–9, with `ifc-sql dq` on all nine.
   One difference, which A12 makes possible: D, the re-export of A, is loaded with
   `--replaces 5`. B was also loaded with `--geometry` into a database of its own.
3. **The audit's figures.** [`harness/figures.py`](harness/figures.py) on this database
   gives every A1–A18 figure the [implementation log](2026-11-implementation.md#after-every-finding-done)
   reports. A5's figures need the `--geometry` load and match there: 25,114.3 m² for wall
   type `1AR_MUR_CV003`, over 1,165 walls.
4. **MCP calls.** [`harness/architect_workflow.py`](harness/architect_workflow.py), the same
   driver: 68 tool calls and 4 resource reads on models 5, 6 and 7, with **0 errors**,
   median 0.04 s and slowest 0.62 s ([log](evidence/recheck/mcp_workflow_calls.txt)).
5. **Real Claude sessions.** [`harness/run_llm_questions.sh`](harness/run_llm_questions.sh),
   the same six questions, each in a fresh headless session that can use only the
   `ifc-sql` server ([transcripts](evidence/recheck/llm_sessions.md)).
6. **New ground truth**, for what the audit had not measured:
   - [`harness/space_truth.py`](harness/space_truth.py) measures every space's footprint
     with IfcOpenShell and shapely, and adds up the rooms each once
     ([figures](evidence/recheck/space_truth.jsonl)).
   - [`harness/quantity_sets.py`](harness/quantity_sets.py) lists the quantity sets each
     class carries ([output](evidence/recheck/quantity_sets.txt)).

Environment as in the audit: 4 vCPU, PostgreSQL 16.15, Python 3.12 through `uv sync
--locked`.

---

## 3. The six sessions, before → after

| | Question | Tool calls | Failed calls | Cost | Answer |
|---|---|---:|---:|---:|---|
| Q1 | Total wall area of the tower (B) and its five largest types | 10 → **5** | 3 → **0** | $0.14 → $0.20 | Refuses to invent areas, as before. The counts per type are now right (1,165 for `CV003`, not 2,330). It still tells the architect only to re-export (R4). |
| Q2 | Door schedule of `XX_AR_L01` (B) | 14 → **4** | 4 → **0** | $0.22 → $0.13 | ✅ 159 doors in 6 types. Widths come from the file's `OverallWidth`: the sliding door is 1.697 m, where the type name says 0.80 m. Before, the sizes were read from type names. |
| Q3 | Net wall area of the house (A) by type | 10 → **4** | 3 → **1** | $0.23 → $0.11 | ✅ **946.04 m²**, the 8 types identical to the ground truth, with no workaround. Before, it took a regex over data quality messages. |
| Q4 | Net floor area of the rooms (C), and the five largest | 17 → **6** | 7 → **0** | $0.49 → $0.35 | ⚠️ Claude found that the tool's "each room once" total of 6,124.8 m² still counts rooms twice, and settled on 4,533 m², one of the two sets. The ground truth, each room once, is **4,721.4 m²** (R1). |
| Q5 | Is the tower (B) fit to hand over? | 17 → **7** | 3 → **0** | $0.28 → $0.17 | ✅ The answer follows the report: 0 errors, and warnings that are the model's own (no quantities, no rooms, 6,024 "Default Wall" materials, no fire ratings, placeholder project information, 731 names with control characters). The audit's overstatement ("no classification") is gone. |
| Q6 | Doors and windows per level of the house (A) | 4 → **3** | 0 → **1** | $0.07 → $0.05 | ✅ Exact. Its first query named a column that does not exist; the error listed the right one and it went on. |
| | **All six** | **72 → 29** | **20 → 2** | **$1.42 → $1.01** | |

The audit's transcripts do not record the model. These sessions ran on the `claude`
CLI's default model, so part of the difference may come from the model. The failed calls
do not: all 20 of the audit's were unknown names (A10). Both failures here were unknown
names too, and each error listed the name to use.

---

## Part R — What the re-check found

| # | Finding | Priority | Effort | Issue |
|---|---|---|---|---|
| [R1](#r1-room-totals-count-the-same-floor-two-to-four-times) | Room totals count the same floor two to four times | P0 | M | [#23](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/23) |
| [R2](#r2-ceilings-ifc-base-quantities-are-not-read) | Ceilings' IFC base quantities are not read | P1 | S | [#24](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/24) |
| [R3](#r3-enumerated-property-values-are-dropped) | Enumerated property values are dropped: 724 "errors" on C | P2 | S | [#25](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/25) |
| [R4](#r4-the-no-quantities-warning-does-not-mention---geometry) | The no-quantities warning does not mention `--geometry` | P2 | S | [#26](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/26) |
| [A18.4](#a184-the-embedded-database-still-deferred) | The embedded database, deferred in the audit | P3 | L | [#27](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/27) |

### R1. Room totals count the same floor two to four times

**Evidence.** IfcOpenShell measured each space's footprint ([`space_truth.py`](harness/space_truth.py)).
Wherever two spaces overlap on a storey, one lies at least 90 % inside the other: Revit
wrote the same floor twice. There are no partial overlaps between rooms.

| File | Spaces | Tool: total | Tool: each room once (A7) | Ground truth: each room once |
|---|---:|---:|---:|---:|
| C, Revit 2021 MEP | 163 | 8,090.7 m² | 6,124.8 m² (47 copies) | **4,721.4 m²** (75 copies) |
| E, Revit 2019 | 307 | 30,344.7 m² | 30,344.7 m² (0 copies) | **7,438.8 m²**: 282 rooms; the other 25 spaces are Revit Areas |
| Duplex MEP, Revit 2011 | 42 | 797.8 m² | 797.8 m² (0 copies) | **386.3 m²** (21 copies) |

- **C** holds Rooms and MEP Spaces for the same floor. A7's key (number, name, storey
  and area) finds the 47 pairs whose names agree. It misses the other 28:
  - pairs whose names differ: `41 WC` / `41 WC H`, `32 Laboratory I` / `32 Laboratory !`,
    `53 EDP III` / `53 EDP II`;
  - rooms that the other set splits: `57 Staff Rom` into `57a` and `57b`, and corridor 49
    (474.3 m²) into 49a–d;
  - a pair with different numbers: `69 Meeting Room` inside `54 EDP I`.

  The footprints settle every case. Each of the 75 copies lies at least 90 % inside
  spaces of the other set, and no two rooms overlap only partly, in C or in E. The floor
  covered, each square metre once, is 4,719.5 m². The rooms' own areas, each once, add up
  to 4,721.4 m².
- **E** writes its area plans, area scheme `BTA` (gross area), as 25 `IfcSpace`s. Revit
  marks them: `Pset_SpaceCommon.Category` is `Areas`, against `Rooms` for the other 282.
  Their 22,905.9 m² are added to the rooms', and the room total is four times too large.
- **Duplex MEP**, the README's own sample, has 21 "MEP Space" copies (`A101-M Foyer MEP
  Space` over `A101 Foyer`). They share neither number nor name, so A7 finds none.

**Why it matters.** "Total net floor area" is one of the first figures an architect asks
for. Q4 shows what happens. Claude saw that the tool's total was wrong, had no basis for a
better one, and picked one of the two sets, 4,533 m². The audit's own advice,
[What to tell architects today](2026-11-modern-revit-exports.md#what-to-tell-architects-today),
quotes E's 30,344.7 m² as the rooms' area ([erratum](#erratum-to-the-audit)).

**Fix.**
- Measure each space's footprint at ingest, in world coordinates. A space whose footprint
  lies at least 90 % inside larger spaces on its storey repeats them:
  `v_space_schedule.duplicate_of` points at the one it overlaps most. Use A7's key only
  where a space has no shape.
- Read Revit's `Category`. Spaces that Revit marks as `Areas` are area plans, not rooms.
  List them as such, keep them out of the room totals, and report them in their own
  `revit_area_spaces` figure.
- **Acceptance:** each room once, C 4,721.4 m² and Duplex MEP 386.3 m². E 7,438.8 m² over
  282 rooms, with its 25 Revit Areas (22,905.9 m²) apart.

### R2. Ceilings' IFC base quantities are not read

**Evidence.** [`quantity_sets.py`](evidence/recheck/quantity_sets.txt) lists the sets each
class carries:
- every ceiling of A (2) and of C (43) has `Qto_CoveringBaseQuantities.NetArea`;
- E's 259 coverings have IFC2X3 `BaseQuantities.GrossCeilingArea`.

The tool maps neither, so A and C have 0 coverings with an area. Their `NetArea` equals
the measured footprint to the square centimetre: 154.3 m² on A and 1,755.4 m² on C. On
E, `GrossCeilingArea` equals Revit's `Dimensions.Area` on all 259, 6,879.0 m². The
implementation log said of A1 that "no file shows" covering values; these three do.

**Why it matters.** Ceiling and floor-finish areas go straight into a cost plan, and the
IFC4 export Revit recommends for coordination carries no other source for them.

**Fix.** Map `Qto_CoveringBaseQuantities`: `NetArea` to `area_m2`, with `GrossArea` as
the fallback, and `Width` (the covering's thickness) to `width_m`. Read IFC2X3
`BaseQuantities` on a covering as that set, with Revit's `GrossCeilingArea` as a further
fallback. **Acceptance:** A 2/2 coverings with an area (154.3 m²), C 43/43 (1,755.4 m²),
and E unchanged at 6,879.0 m².

### R3. Enumerated property values are dropped

**Evidence.**
- File C's data quality report has **724 errors**, every one `unsupported_property`:
  `'Pset_DuctSegmentTypeCommon'.'Shape' is an IfcPropertyEnumeratedValue, which is not
  read`.
- So 724 of C's 837 duct segments lose their shape (round, rectangular, …).
- [revit-ifc-quirks.md §9](../revit-ifc-quirks.md#9-property-values-are-shared-between-elements)
  says other kinds of property "would be reported". This is the first file that has one.

**Why it matters.** It is A9's defect again, on another file. The most severe findings
of an MEP model describe the loader, not the model, and the values are gone.

**Fix.** Read an `IfcPropertyEnumeratedValue` as a text value. A single value is stored
as itself; several are joined with `", "` in the file's order. The value type is the
type of the enumeration value (`IfcLabel`). **Acceptance:** C has 0
`unsupported_property` findings, and its 724 duct segments carry their `Shape`.

### R4. The no-quantities warning does not mention `--geometry`

**Evidence.**
- On B, `quantity_takeoff`'s warning, the `model_without_quantities` finding and
  `space_schedule`'s note all give one way to get areas: tick an option in Revit and
  export again.
- In Q1 and Q5, Claude passed exactly that on.
- A5 added a second way that needs no new export: `ifc-sql ingest --geometry` measures
  103,057.7 m² of wall from this very file. None of the three texts mentions it.

**Why it matters.** The architect may not own the Revit model, for example a consultant's
export or an archived file. The tool can answer, but it does not say how.

**Fix.** Add the second way to the three texts: load the file again with `ifc-sql ingest
--geometry` to measure its shapes. **Acceptance:** on B, the takeoff warning and the
finding both name `--geometry`.

### A18.4. The embedded database, still deferred

From the audit, deferred by the first round: an architect on Windows still needs Docker
Desktop for a database that only they use. The fix the audit asked for still stands. The
implementation log noted what makes it hard: the read-only role (ADR 6) and the
PostgreSQL SQL of every view. An embedded **PostgreSQL** keeps both.
[`pixeltable-pgserver`](https://pypi.org/project/pixeltable-pgserver/) (Apache-2.0)
ships PostgreSQL 16.15 binaries as a wheel for Linux, macOS and Windows, the same version
CI and this audit use. **Acceptance:** `ifc-sql migrate`, `ingest`, `dq` and `serve` run
without Docker, on a CI job on Windows, with the steps of [docs/windows.md](../windows.md).

### Other observations (no action)

- **Page size.** One page of 100 doors from `find_elements` on B is 37,765 characters,
  against 32,875 before: each door now carries its size and how it opens. A door
  schedule does not need it. `schedule` gives B's `XX_AR_L01` in 3,177 characters.
- **`space_schedule` on C** is 39,012 characters for its first page of 150 spaces. That
  is under the 50,000 the docs keep results within.

---

## Erratum to the audit

[What to tell architects today](2026-11-modern-revit-exports.md#what-to-tell-architects-today)
says that on file E "the tool filled 4,836 areas, including all 1,057 walls and all 307
rooms, and the room areas (30,344.7 m²) equal the file's own
`BaseQuantities.NetFloorArea` for 307 of 307 rooms".

The area per space is right. But 25 of those 307 spaces are Revit Areas, area scheme
`BTA`, not rooms. E has **282 rooms, of 7,438.8 m²**. The audit's conclusion stands:
exporting Revit property sets is what gave E its quantities. The total does not. The
audit is left as written, as the record of what was found then. This erratum and R1
correct it.

---

## Reproducing the re-check

Load the files as in [harness/README.md](harness/README.md#running-it), with D loaded
with `--replaces 5`. Then:

```bash
H=docs/audit/harness
.venv/bin/python -I $H/architect_workflow.py .venv/bin/ifc-sql workflow.json 5 6 7
OUT=out $H/run_llm_questions.sh && .venv/bin/python -I $H/parse_transcript.py out/q*.jsonl
.venv/bin/python -I $H/space_truth.py data/audit/Ifc4_Revit_MEP.ifc data/audit/ARK_NUS_skolebygg.ifc \
  data/samples/Duplex_MEP_20110907.ifc data/samples/Duplex_A_20110907.ifc
.venv/bin/python -I $H/quantity_sets.py data/audit/Ifc4_Revit_MEP.ifc
```
