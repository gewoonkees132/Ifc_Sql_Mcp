# Implementing the audit of modern Revit exports

This is the record of how the backlog in
[the November 2026 audit](2026-11-modern-revit-exports.md) was carried out: for each
finding, the GitHub issue, the commits, the decision taken where the audit left a choice,
and the figure measured on the audit's own files before and after the fix.

- **Tracking issue:** [#2](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/2), with one
  sub-issue per finding (A1 → #3 … A18 → #20).
- **Branch:** `ccr-010fe6a4-5gswo3`, starting from the audit commit `0366a46` (which audited
  `15841f4`).
- **Proof on the real files:** `make test-modern` loads the five audit files (pinned,
  checksum-verified, [`scripts/download_audit_samples.py`](../../scripts/download_audit_samples.py))
  and runs [`tests/modern/test_audit_acceptance.py`](../../tests/modern/test_audit_acceptance.py).
  The CI job `samples` runs it, with the Duplex suite, on every push.

## How each finding is proven

The acceptance tests were written **before any fix**, one per finding, from the audit's
ground truth (an IfcOpenShell reading that never imports this package). Each started as
`xfail(strict=True)`, and the reason recorded is the audit's own "before" figure (below).
A fix lands with its test passing and its `xfail` marker removed in the same commit;
strict xfail makes the suite fail if a marker outlives its fix. So:

- the markers left in the test file are the open part of the backlog;
- `git log -p tests/modern/test_audit_acceptance.py` is the history of the backlog;
- [`harness/figures.py`](harness/figures.py) prints the table below from any database
  holding the five files, so "before" and "after" are measured the same way.

Every commit also passes the full gate: `ruff`, `mypy --strict`, the unit and integration
suites at 100% branch coverage, and the Duplex sample suite.

## Before: the code as audited

Measured with `harness/figures.py` on `15841f4` (plus the A17 test fix, which changes no
code). The five files were loaded as the audit's README describes. Every figure equals
the one the audit reports.

| Finding | Figure | Audit's ground truth | Measured |
|---|---|---|---:|
| A1 | A: wall area (v_element.area_m2), m² | 946.0 | None |
| A1 | C: spaces with an area | 163 of 163 | 0 of 163 |
| A2 | A: sum of stored Qto_WallBaseQuantities.NetSideArea, m² | 946.0 | 1157891673.6 |
| A3 | B: elements of wall type 1AR_MUR_CV003 (quantity_takeoff) | 1,165 | 2,330 |
| A4 | B: doors with width_m | 1,458 of 1,458 | 0 |
| A4 | B: windows with width_m | 1,199 of 1,199 | 0 |
| A4 | B: unsupported_property_set findings | 0 | 1,458 |
| A6 | A: windows with a material | 17 of 17 | 0 |
| A6 | C: unsupported_material findings | 0 | 295 |
| A7 | C: duplicate_space findings | 47 | 0 |
| A8 | B: area takeoff by Revit type, total | null | 0 |
| A9 | B: findings error / warning / info | (model gaps, not loader gaps) | 1,458 / 6,766 / 0 |
| A9 | B: model_without_quantities findings | 1 | 0 |
| A9 | C: space_without_area findings | 0 | 163 |
| A12 | B: other models it is aligned with (v_storey_alignment) | 0 | 1 |
| A13 | B: measure_not_converted findings | 0 for U-values | 4,416 |
| A13 | B: revit_tag_mismatch findings on openings | 0 | 2,072 |
| A14 | B: stair flights with a Revit type | 182 of 182 | 0 |
| A16 | B: elements whose revit_type holds a control character | 0 | 764 |

The test suite at the start: 867 passed, 2 failed (both A17), exactly as the audit says.

## Ledger

One row per finding, in the order they were done. "Proof" names the acceptance test, or
the test module for findings that add a tool or a command.

| # | Finding | Issue | Status | Proof |
|---|---|---|---|---|
| A17 | Two tests depend on the environment | [#19](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/19) | done | full suite green on Ubuntu's PostgreSQL 16 and at any terminal width |
| A3 | Grouping by Revit type counts openings as walls and doors | [#5](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/5) | done | `test_a3_a_wall_type_counts_its_walls_not_their_openings`: 2,330 → 1,165 |
| A8 | Unknown totals are reported as 0 | [#10](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/10) | done | `test_a8_an_area_nobody_measured_is_not_zero`: total 0 → null, with a warning |
| A2 | Revit 2021's duplicated, unscaled quantities reach the LLM | [#4](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/4) | done | `test_a2_the_stored_wall_areas_add_up_to_the_real_total`: 1,157,891,673.6 → 946.0 m² |

## Per finding

### A17: two tests depend on the environment

- **Function catalogue.** The test pinned `pg_catalog` at 2,644 executable functions.
  Ubuntu's PostgreSQL 16 build has 2,643. What the review protects is that every
  function sqlglot lets through has been reviewed, so the test now asserts exactly that
  (the typed set is a subset of the reviewed list, and nearly all of the list exists). A
  separate test pins the reviewed list itself.
- **Rich's error box.** The CLI tests now read output through one helper,
  `tests/support/cli_output.plain()`. It strips ANSI codes and box drawing and joins
  wrapped lines, so a phrase cut across two box lines still matches.
- **Before → after:** at `COLUMNS=40`, 5 CLI tests failed; after the fix, all 27 pass at
  40, 80 and 250 columns. Full suite: 867/869 → 870/870.

### A3: grouping by Revit type counts openings as walls and doors

- **Decision.** The audit asked for `host_revit_family`/`host_revit_type`, and for leaving
  openings out of the groupings by default. Both are done, plus a stored
  `element.is_feature` flag. The flag is decided at ingest from the class's inheritance in
  the file's own schema (`IfcFeatureElement` in its ancestry), never from the class name.
  It covers openings, voids, projections and surface features in IFC2X3 and IFC4. Two
  CHECK constraints keep the two kinds of name apart: a feature element never has its own
  `revit_type`, and a non-feature never has a host's.
- **Migration 0010** adds the columns and backfills models already loaded, from their
  stored class, so an existing database gets the same answer as a fresh load. Its
  downgrade restores the old columns. A data test runs it both ways on a loaded model.
  `v_element` v3 exposes the three columns.
- **Tools.** `quantity_takeoff` leaves feature elements out of every grouping unless
  `include_openings` is true, and reports `openings_left_out`. `get_element` shows
  `is_feature` and the host's name parts. `find_elements` with a `revit_type` filter now
  finds only elements of that type. `list_storeys` still counts every `IfcElement`, as IFC
  and the audit's ground truth do.
- **Found on the way.** The Duplex architectural model had the same defect in another
  form. Revit 2011 copies a roof's `Dimensions` onto each of its openings, so 4 openings
  carried the roof's 132.9 m², and the takeoffs counted the roof five times (531.7 m² too
  much). With openings left out, the area by class drops from 2,036.7 to 1,505.0 m² and
  by material from 5,329.2 to 4,797.5 m². Walls (587.7 m²) and plasterboard (931.9 m²)
  do not change. The README, `docs/mcp.md` and the Duplex pins now carry these figures.
- **Before → after on file B:** wall type `1AR_MUR_CV003` counts 2,330 → **1,165**
  elements; 3,314 openings are left out of the takeoff and named in `openings_left_out`.

### A8: unknown totals are reported as 0

- **Takeoff.** `quantity_takeoff` returns `coverage` (elements in the takeoff, how many
  have the metric, and the same in words). `total` is null when none has the metric. The
  `note` then starts with a warning that names the likely cause: a model with no
  quantity of any kind was exported without them ("tick *Export base quantities*"), and
  a model with other quantities points at `get_element`. A partly measured total says
  "Only 90 of 218 elements have area_m2: the total covers those". The material basis
  uses the same coverage, from `v_element`.
- **Space schedule.** `total_area_m2` is null when no space has an area, and a new `note`
  warns of it, or says how many spaces the total leaves out.
- **Descriptions.** The tool descriptions now tell the LLM that a null total means "not
  measured" and must never be reported as zero.
- **Before → after on file B:** wall area by type, `total` 0 → **null**; coverage
  "0 of 11,298 elements have area_m2"; the note names the Revit export option.

### A2: Revit 2021's duplicated and unscaled quantities reach the LLM

- **Measured first.** [`harness/quantity_bounds.py`](harness/quantity_bounds.py) measures
  every quantity set of a file against IFC's own ties: side area ≤ length × height, net
  ≤ gross, volume ≤ area × height or thickness, perimeter ≥ a circle's. On files A, C
  and E, the correct values stay within 1.1 of their bound. The defective ones are 10³
  to 10⁹ away. That set the allowances: a factor of 2 on geometric bounds, 5 % on net
  against gross ([ADR 7](adr/0007-quantity-set-plausibility.md)).
- **Duplicates.** Of two values for one quantity, the one that keeps the bounds wins.
  The `conflicting_property` message says which bound chose it ("the only value within
  Length x Height"). File order decides only when the bounds cannot.
- **Single implausible values** stay in `ifc.property` as written, flagged
  `is_implausible` (migration 0011). They make no quantity, raise an
  `implausible_quantity` warning that names the bound, and `get_element` marks them.
- **Found beyond the audit.** Running the bounds on every set exposed three Revit
  defects the audit had not listed:
  - Revit 2021 writes a slab's `NetArea` as the whole solid's surface: 219 m² for a
    105.9 m² floor.
  - Revit 2019 and 2021 write slab and space `GrossVolume` as m² × mm (1,000 too large).
  - Revit 2021 gives a sloped roof slab a `GrossArea` a tenth of the roof.

  The first one changes A1: slab area comes from `GrossArea`, never `NetArea`.
- **One bound corrected on the evidence.** A first version bounded a slab's `NetVolume`
  by `GrossArea` × `Width`, and flagged the sound volume of that roof, whose `GrossArea`
  is the wrong value. Now only `GrossVolume`, which Revit computes from `GrossArea`, is
  bounded that way, and `GrossArea` is checked against `NetVolume` / `Width`. A unit test
  pins the roof.
- **Rules.** `wall_area_inconsistent` (5 %), `space_volume_inconsistent` and
  `space_perimeter_implausible` compare the canonical quantities. The audit's single
  `quantity_inconsistent` became two rules, walls now and slabs with `width_m` in A1,
  because a rule id has one meaning. The audit's space-volume check was two-sided
  (±10 %); here it is upwards only. A room's volume follows its ceiling, and 16 of the
  Duplex's 21 rooms are 7–26 % below area × unbounded height, correctly.
- **Before → after** (files A, C, E):

  | Figure | Before | After |
  |---|---:|---:|
  | A: `sum(value_num)` of `Qto_WallBaseQuantities.NetSideArea` | 1,157,891,673.6 m² | **946.0 m²** |
  | C: the same | 3,790,262,224.1 m² | 3,546.7 m² |
  | A, C: duplicated wall values kept in file order (the unscaled one) | 94, 266 | 0, 0 |
  | Values flagged implausible, A / C / E | — | 67 / 338 / 407 (no wall in any file) |
