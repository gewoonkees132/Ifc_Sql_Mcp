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
