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
| A1 | The quantity sets modern Revit writes are ignored | [#3](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/3) | done | `test_a1_modern_quantity_sets_become_quantities`: A wall area null → 946.0 m², C spaces 0 → 163 of 163 |
| A4 | Door and window sizes are not loaded | [#6](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/6) | done | `test_a4_doors_and_windows_have_their_sizes`: 0 → 1,458 of 1,458 doors, `PI005` 1.697 m wide |
| A6 | IFC4 material constituent sets are not read | [#8](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/8) | done | `test_a6_ifc4_doors_and_windows_have_their_materials`: A windows 0 → 17 of 17 |
| A7 | Rooms exported twice are counted twice | [#9](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/9) | done | `test_a7_rooms_exported_twice_are_found`: 0 → 47; 6,124.8 m² without duplicates |
| A9 | Data quality measures the loader, not the model | [#11](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/11) | done | `test_a9_data_quality_reports_what_the_model_lacks`, `test_a9_the_loaders_gaps_are_reported_apart_from_the_models`: B: errors that are loader gaps 1,458 → 0; no quantities, no rooms, placeholder project and materials, 8,079 missing fire ratings: none reported → all reported |
| A10 | The LLM cannot find the schema | [#12](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/12) | done | `tests/integration/test_schema_discovery.py`: of the sessions' 20 failed calls, errors that name what to use 0 → 20 |
| A11 | Schedules cost too much context | [#13](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/13) | done | `test_a11_a_door_schedule_fits_in_a_few_kilobytes`: B's L01 door schedule, ~490,000 characters by paging → 4,181; 159 doors in 6 types, every width as IfcOpenShell reads it |
| A12 | Projects and versions are guessed from the file | [#14](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/14) | done | `test_a12_a_placeholder_project_number_joins_no_project`, `test_a12_a_new_export_replaces_the_old_one_in_project_wide_views`: B aligned with other models 1 → 0; A next to its re-export D 2 models → 1 |
| A13 | Findings that are noise | [#15](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/15) | done | `test_a13_normal_revit_behaviour_is_not_reported`: B's U-value and opening-tag findings 6,488 → 0; its warnings 6,778 → 290 |
| A14 | Modern stair part names are not parsed | [#16](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/16) | done | `test_a14_every_stair_flight_has_its_revit_type`: B's stair flights with a Revit type 0 → 182 of 182; `revit_name_unparsed` 278 / 3 / 61 → 0 / 0 / 2 on B / A / E |
| A16 | Control characters in names | [#18](https://github.com/gewoonkees132/Ifc_Sql_Mcp/issues/18) | done | `test_a16_revit_types_hold_no_control_characters`: B's elements with a control character in `revit_type` 731 → 0, each reported |

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

### A1: the quantity sets modern Revit writes are ignored

- **Mapping** ([ADR 8](adr/0008-ifc-base-quantities.md), table in
  [revit-ifc-quirks.md §14](../revit-ifc-quirks.md#14-revit-2019-to-2022-write-ifc-base-quantities-some-of-them-wrong)):
  - rules for walls, slabs, spaces, doors, windows, columns, beams, members, stair flights
    and railings;
  - IFC2X3 `BaseQuantities` is read as the `Qto_` set of the object's class;
  - net first, gross as a silent fallback;
  - a new canonical quantity, `width_m`. Migration 0012 adds it to the CHECK and the
    comments, `v_quantity` v2 is replaced in place (it has three readers), `v_element` v4
    has the column, and `find_elements` returns `width_m` and `height_m`.
- **Deviation from the audit's table, on evidence.** The audit proposed slab
  `NetArea → area_m2`. Revit 2021 writes it as the whole solid's surface, on every slab
  of A and C (219.0 m² for a 105.9 m² floor), so a slab's area comes from `GrossArea`
  only. Coverings, roofs, plates and openings are not mapped: no file shows their values
  correct.
- **Rule** `slab_volume_inconsistent`: volume outside 0.75–1.1 × area × thickness.
  Sound slabs in all three files sit between 0.91 and 1.00. Outside that range there are
  only defects: a 150 mm foundation slab with a `Width` of 25.4 m, pile caps of
  600 × 600 × 900 mm with a `Width` of 0.6 m and a third of their volume, and stair
  landings 1.6 m "thick". The lower bound is 0.75, not the audit's 0.9, because a slab
  with a shaft loses volume against its gross area.
- **Before → after**

  | Figure | Before | After |
  |---|---:|---:|
  | A: wall area (`v_element`), m² | null | **946.0** |
  | C: spaces with an area | 0 of 163 | **163 of 163** (8,090.7 m², duplicates included: A7) |
  | E: room areas, m² | 30,344.7 (Revit parameters) | 30,344.7 (now from `NetFloorArea`, the same) |
  | A / C / E: `ifc_qto` quantities | 0 / 0 / 0 | 776 / 4,617 / 8,980 |

### A4: door and window sizes are not loaded

- **Attributes as properties.** A door's and a window's `OverallWidth` and
  `OverallHeight`, and how it opens (`OperationType`, `ConstructionType`,
  `PartitioningType` on the occurrence or its type), are read as properties of a set
  named after the declaring entity (`IfcDoor.OverallWidth`). They are typed by the
  schema's declaration, so a millimetre file's widths are converted like any length.
- **Predefined sets.** `IfcDoorLiningProperties`, `IfcDoorPanelProperties`, the window
  ones and the rest are read attribute by attribute, as a set named after the class; a
  second set of one class (a double door's second panel) gets a number. `NOTDEFINED` is
  no value, as for `PredefinedType`. Only an attribute that refers to another entity
  (`ShapeAspectStyle`) is still reported as `unsupported_property_set`.
- **Quantities.** The overall size becomes `width_m` and `height_m` with a new source,
  `ifc_attribute` (migration 0013). `v_quantity` v3 ranks it after `revit_pset` and
  before `geometry`. Attributes are written whatever the export options, so the A8
  warning ("tick *Export base quantities*") now looks at base quantities and Revit
  parameters only.
- **Duplex.** The architectural model's 14 doors and 24 windows gain their sizes (76
  quantities), and its door styles their `OperationType` (14 rows). The pins and the
  quirks document carry the new counts.
- **Before → after on file B**

  | Figure | Before | After |
  |---|---:|---:|
  | Doors with `width_m` and `height_m` | 0 of 1,458 | **1,458 of 1,458** |
  | Windows with `width_m` and `height_m` | 0 of 1,199 | **1,199 of 1,199** |
  | Width of type `PI005 80x210` (Claude read 0.80 m from the name) | — | **1.697 m** |
  | `unsupported_property_set` findings | 1,458 | **0** |

### A6: IFC4 material constituent sets are not read

- **Read.** `IfcMaterialConstituentSet` (a door's frame and panel, a window's frame and
  glazing) and `IfcMaterialProfileSet` (with its usage) give one row per material in the
  order the file lists them, without thickness, like a material list. `v_takeoff`
  already treats such rows as "the element's whole area to each material".
- **Scope decision.** The issue asked for the constituent's name and fraction "where
  given". In all 399 constituent sets of files A and C, Revit names each constituent
  after its material and gives no fraction. A column of copies would add nothing, so
  none was added, and the extract docstring records why. With every `IfcMaterialSelect`
  kind of IFC2X3 and IFC4 now read, the `unsupported_material` branch is defensive only.
  The rule stays, for models loaded earlier.
- **Migration 0014** corrects the two `element_material_layer` column comments that the
  LLM reads.
- **Before → after**

  | Figure | Before | After |
  |---|---:|---:|
  | A: windows with a material | 0 of 17 | **17 of 17** |
  | A / C: `unsupported_material` findings | 104 / 295 | **0 / 0** |

### A7: rooms exported twice are counted twice

- **The key** is number, name, storey **and floor area** (to the square centimetre),
  following the audit's own description of C's pairs. Without the area, the rule found 5
  "duplicates" in file E that are different spaces sharing a number: two shafts numbered
  02055 (1.24 and 1.57 m²), and Revit Areas of 67.8 and 3,087.4 m². Leaving those out of a
  total would lose real area. With the area in the key, E has none.
- **`v_space_schedule` v2** (migration 0015) adds `duplicate_of`, the first space with
  the same key. `space_schedule` gives each space's `duplicate_of`, `duplicate_spaces`,
  and `total_area_without_duplicates_m2` next to the plain total, and its `note` says
  that a total counts those rooms twice.
- **Rule** `duplicate_space` (warning, uniqueness) names both spaces. Its description,
  and `docs/data-quality.md`, tell the architect to export rooms or spaces, not both.
- **Before → after on file C**

  | Figure | Before | After |
  |---|---:|---:|
  | `duplicate_space` findings | 0 | **47** |
  | Total area, every space | 8,090.7 m² (after A1) | 8,090.7 m² |
  | Total area, each room once | — | **6,124.8 m²** (116 rooms) |

### A9: data quality measures the loader, not the model

- **Scope** (migration 0016): `dq_rule.scope` is `model_content` or `loader_coverage`,
  NOT NULL and checked, so the catalogue must state it for every rule, and the loader
  refuses a rule without one. Eight rules are loader coverage: the five
  `unsupported_*` and `unit_not_convertible`, `measure_not_converted`,
  `revit_name_unparsed`, plus the new `space_area_not_mapped`. `v_dq_finding` v2 shows the
  scope, `ifc-sql dq` prints both counts after the total, and `data_quality_report`
  gains `findings_by_scope`, a `scope` filter, and lists model-content rules first.
- **Six requirement rules**, as the audit listed them: `model_without_quantities`,
  `model_without_spaces` (architectural models only), `missing_fire_rating` (info),
  `placeholder_material` (one finding per material, with how many elements use it),
  `placeholder_project_info` and `space_area_not_mapped`.
- **Decisions.**
  - `model_without_quantities` ignores a door's or window's overall size (A4's
    `ifc_attribute` quantities): every Revit export writes those, so they say nothing
    about "Export base quantities".
  - `missing_fire_rating` reads only the class's own `Pset_*Common`. That is where IFC
    puts `FireRating`, and IfcOpenShell's `get_psets` counts exactly the same
    elements (below). A rating only in a Revit parameter set is not one a handover
    checker reads.
  - Reading a buildingSMART IDS file as the requirements is left for later, as the
    audit says.
- **Checked against IfcOpenShell.** Elements without `FireRating`: B 8,079 of 8,079, A
  107 of 107, D 56 of 107 (D is A with ratings added), Duplex ARC 78 of 92. Every one
  equals the rule's count. On B, `placeholder_material` finds "Default Wall" on 6,024
  elements and "Materiale per il rendering 0-127-255" on 1,199, the audit's figures.
- **Duplex.** All four files keep project number `0001`, so `placeholder_project_info`
  adds one warning to each. Their loader-coverage count is 0 in every file.
- **Before → after on file B** (`harness/figures.py`, with the Duplex ARC model loaded
  as in the before database)

  | Figure | Before | After |
  |---|---:|---:|
  | Errors / warnings / info | 1,458 / 6,766 / 0 | **0** / 6,778 / 8,079 |
  | Model content (e / w / i) | — | **0 / 2,084 / 8,079** |
  | Loader coverage (e / w / i) | — | **0 / 4,694 / 0** (4,416 U-values not converted, 278 stair names: A13, A14) |
  | `model_without_quantities` / `model_without_spaces` | 0 / 0 | **1 / 1** |
  | `placeholder_project_info` | 0 | **1** (`0001` / `Project Name`) |
  | `placeholder_material` | 0 | **9** materials (6,024 walls use "Default Wall") |
  | `missing_fire_rating` | 0 | **8,079** (info) |
  | C: `space_without_area` | 163 | **0** (A1) |

  The 1,458 errors were door panel sets, gone with A4. The remaining loader-coverage
  warnings are what A13 and A14 fix.

### A10: the LLM cannot find the schema

- **`describe_schema`** ([ADR 9](../adr/0009-schema-discovery-tool.md)) reads the live
  catalogue, as the resource does. Without arguments it gives every relation's comment
  and column names (8,295 characters, a third of `ifc://schema`). Given one relation it
  gives that relation in full. The server now has nine tools.
- **Errors that answer.** `run_sql` runs each query in a savepoint. An unknown relation
  (42P01) gets the closest names, every relation and `property`'s columns. An unknown
  column (42703) gets the closest names and the columns of the relations the query reads,
  narrowed by its qualifier. The `information_schema` refusal, and the run_sql
  description and server instructions, name `describe_schema`.
- **Decision.** The audit's sessions guessed `v_property` five times, so a view of that
  name was considered and rejected. It would fit these sessions and miss the next
  model's guesses; an error that lists the real names fits any guess (ADR 9).
- **Proof: the sessions, replayed.** `test_every_failed_call_of_the_audit_sessions_now_names_what_exists`
  runs the 20 failed calls of `evidence/llm_sessions.md` against the server: 14 unknown
  relations, 4 `information_schema` queries and 2 unknown columns. The record cuts four
  of the queries at 273 characters; each one fails on a name before the cut.

  | The 20 failed calls | Before | After |
  |---|---:|---:|
  | Errors that name the relation or column to use | 0 (PostgreSQL gave no hint for either column) | **20** |
  | Errors that point to the resource the model could not read | 4 | **0** |

### A11: schedules cost too much context

- **`schedule`** is a tenth tool. It takes one class, 1 to 3 `group_by` keys (`storey`,
  `revit_type`, `ifc_class`), up to 8 fields and an optional storey. A field is a
  canonical quantity or `PsetName.PropName`. Per group and field it gives the count with
  a value, the 10 most common values with their counts, and the exact min, max and sum.
- **Decisions.**
  - *Numbers are told apart to 6 significant digits.* On file B, the 77 doors of type
    `PI002` came back as 36 "distinct" widths, all 0.87 m: Revit writes
    0.870000000000002, 0.870000000000012, and so on. Min, max and sum stay exact.
  - *A size budget.* A wall schedule of B by storey and type has 276 groups, about
    75,000 characters. A schedule therefore stops at 200 groups or 30,000 characters,
    run_sql's budget, and always shows its first group. `note` says how to narrow it.
  - *A field no element has is not an error.* "Fire rating: not in the model" is the
    answer to a schedule of B's doors, so the field comes back empty. `note` names the
    closest properties the elements do have (`Pset_DoorCommon.IsExternal`, …).
  - Spaces are spatial elements, not elements, so `schedule` does not list them and its
    note sends the reader to `space_schedule`.
- **`space_schedule` pages** at 150 spaces (`limit`, `offset`, `next_offset`), and its
  totals and counts now cover every space of the model on every page. The old cap of 500
  spaces returned at once is gone, and so is `truncated`.
- **Ground truth.** [`harness/door_schedule.py`](harness/door_schedule.py) reads the
  door schedule of a storey with IfcOpenShell. On B's `XX_AR_L01` it gives 159 doors in
  6 types (77, 25, 21, 15, 14, 7), the audit's figures, and each type's widths. The
  acceptance test compares the tool with it type by type.
- **Before → after**

  | Figure | Before | After |
  |---|---:|---:|
  | B: door schedule of `XX_AR_L01` (type, count, width, height, fire rating) | ~490,000 characters paging `find_elements` + `get_element` (the audit's estimate) | **4,181** in one call |
  | B: door schedule of all 1,458 doors by type | the same | **3,759** |
  | C: room schedule, first answer | all 163 spaces, 42,353 characters | **39,012** for a page of 150, with the totals of all 163 |
  | E: room schedule, first answer | all 307 spaces, 87,014 characters (the cap was 500, ~112,000) | **42,703** for a page of 150, with the totals of all 307 |

### A12: projects and versions are guessed from the file

- **Migration 0017** adds three columns to `ifc.model`, plus a view:
  - `project_key`, set by `ifc-sql ingest --project`;
  - `project_group`, generated: `project_key`, else the project number, else its name,
    never a Revit placeholder;
  - `replaces_model_id`, set by `ifc-sql ingest --replaces`: unique, never the model
    itself, `SET NULL` when the old model is deleted;
  - `v_model_current`, the models no later model replaces.

  `v_storey_alignment` v2 groups current models by `project_group`. `list_models` shows
  each model's project group and versions, and leaves replaced models out unless
  `include_replaced`.
- **Decisions.**
  - *The name stands in for a placeholder number.* The issue said a placeholder never
    forms a project by itself. The four Duplex files are numbered `0001` and named
    `Duplex Apartment`; grouping them by their name keeps them one project without a
    `--project`. B (`0001` / `Project Name`) and C (`Project Number` / `Project Name`)
    have only placeholders and join no project.
  - *A replacement keeps the project.* `--replaces` without `--project` takes the
    replaced model's key, so a weekly re-export needs one option.
  - *A replaced model stays loaded.* It drops out of project-wide views and the default
    model list, but every tool still reads it by its id, for comparison (A15).
  - *Options on a file already loaded* are not applied. The model is identified by its
    bytes (ADR 4), and changing it from a repeat ingest would be a hidden edit. The CLI
    says so in a warning.
- **The warning at ingest** names the placeholder and what decided the group.
  `placeholder_project_info` (A9) reports the same on every check.
- **Before → after** (`harness/figures.py`; the modern suite loads D with `--replaces` A)

  | Figure | Before | After |
  |---|---:|---:|
  | B: other models it is aligned with (`v_storey_alignment`) | 1 (the Duplex, as project `0001`) | **0** |
  | Project `001-00`: models compared | 2 (A and its re-export D) | **1** (D) |
  | Duplex: models in its project | 4 | 4 (grouped as `Duplex Apartment`) |

### A13: findings that are noise

- **Openings.** `parse_revit_name` takes `named_after_host`, which the transform sets for
  every feature element. Revit names an opening after the wall it cuts and tags it with
  its own id, so a Tag that differs from the name's ElementId is normal there, and is
  not reported. Every other element is checked as before.
- **Derived units.** Extract reads `IfcDerivedUnit`. A derived unit gets an SI factor
  when it is coherent: every element an SI unit without a prefix, the kilogram
  excepted. Revit's U-value unit, kg·K⁻¹·s⁻³, is W/(m²·K) exactly. Thermal
  transmittance, mass density, power, pressure, force, flow rate, velocity, current,
  voltage, frequency, light and time measures then get their SI unit (`W/(m2*K)`,
  `kg/m3`, `W`, `Pa`, …) and no issue. A kilowatt is scaled by 1,000.
- **Decisions.**
  - *Stored as written when unsure, as before.* The four base dimensions drop a value
    whose unit has no SI factor. A derived measure whose unit is undeclared or
    incoherent keeps its number, without a unit, and is reported.
  - *A mass in grams is scaled to kilograms.* IfcOpenShell's `get_unit_scale` gives a
    kilogram as 1,000 (grams). The SI unit is the kilogram.
  - *Temperatures are left out.* Revit declares degrees Celsius, which an offset, not a
    factor, separates from kelvin.
  - *Units are written in ASCII* (`W/(m2*K)`), like `m2` and `m3`.
- **Why 4,416 findings were 2,208 values.** Each U-value was reported twice, from the
  occurrence and from its type, and both resolve to one stored value: 1,023 doors,
  1,182 windows and 3 walls.
- **Before → after** (`harness/figures.py`)

  | Figure | Before | After |
  |---|---:|---:|
  | B: `measure_not_converted` for U-values | 4,416 | **0**; 2,208 U-values stored in `W/(m2*K)` |
  | B: `revit_tag_mismatch` on openings | 2,072 | **0** |
  | B: warnings (model content / loader coverage) | 6,778 (2,084 / 4,694) | **290** (12 / 278: the stair names of A14) |
  | E: `measure_not_converted` | 1,624 | **0**: power, pressure, flow, U-values and voltage get their unit |

### A14: modern stair part names are not parsed

- **Rule.** A last name segment `<ElementId> <part> <n>` is a stair part, where the part
  word is `Run`, `Landing`, `Support` or `Stringer`. The part keeps its stair's family,
  type and ElementId, as the Revit 2011 parts (`…:151086:1`) already do. Its Tag is not
  compared: on all 343 such parts of A, B, D and E, the Tag is the part's own id.
- **Decisions.**
  - *`Stringer` added on evidence.* The audit named `Run`, `Landing` and `Support`. File
    E (Revit 2019) also writes `Assembled Stair:Stair:4959975 Stringer 10`, 23 times.
  - *No other word is guessed.* `…:1287438 Flight 1` stays reported, because no file
    shows it. So do E's two `Model Text:350mm Arial 2` proxies, which end in no
    ElementId.
- **Before → after**

  | Figure | Before | After |
  |---|---:|---:|
  | B: stair flights with a Revit type | 0 of 182 | **182 of 182** (`Precast Stair` : `Stair`) |
  | `revit_name_unparsed` on B / A / E | 278 / 3 / 61 | **0 / 0 / 2** |
  | B: warnings left (all scopes) | 290 after A13 | **12**, all model content |

### A16: control characters in names

- **Strip and report.** `revit_names.py` removes C0 control characters and DEL from the
  family and type it parses. They are invisible, and nobody types them. A new issue,
  `control_character_in_name` (warning, model content), names the characters and the
  cleaned parts. `object.name` keeps the IFC Name as written, as the issue asked.
- **Decisions.**
  - *Reported once, on the element that owns the name.* An opening or a stair part
    repeats its host's name, so it is cleaned but not reported again. On B that is
    731 walls, not 764: 33 openings in those walls repeat their host.
  - *A family or type of control characters alone is no name.* It is reported as
    `revit_name_unparsed`, like any other name without a clear `Family:Type`.
- **Before → after on file B**

  | Figure | Before | After |
  |---|---:|---:|
  | Elements whose `revit_type` holds a control character | 764 (731 after A3 moved the openings' parts to `host_*`) | **0** |
  | `control_character_in_name` findings | — | **731** |
  | Wall types whose name carried them into groupings and answers | 2 (`PV001`, 571 walls; `PV007`, 160) | **0** |

