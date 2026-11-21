# Acceptance figures after the fixes

`python -I docs/audit/harness/figures.py <url>` on databases loaded with code `d7c4072`
(the last fix, A18), 2026-11-21. The before table is [figures_before.md](figures_before.md).

## The five files and the Duplex, loaded plainly

Loaded as the before database was: `ifc-sql ingest` for files A to E and the Duplex
architectural model, then `ifc-sql dq`. There is one difference, which A12 makes
possible: D, the re-export of A, is loaded with `--replaces` A.

| Finding | Figure | Audit's ground truth | Measured |
|---|---|---|---:|
| A1 | A: wall area (v_element.area_m2), m² | 946.0 | 946.0 |
| A1 | C: spaces with an area | 163 of 163 | 163 of 163 |
| A2 | A: sum of stored Qto_WallBaseQuantities.NetSideArea, m² | 946.0 | 946.0 |
| A3 | B: elements of wall type 1AR_MUR_CV003 (quantity_takeoff) | 1,165 | 1,165 |
| A4 | B: doors with width_m | 1,458 of 1,458 | 1,458 |
| A4 | B: windows with width_m | 1,199 of 1,199 | 1,199 |
| A4 | B: unsupported_property_set findings | 0 | 0 |
| A6 | A: windows with a material | 17 of 17 | 17 |
| A6 | C: unsupported_material findings | 0 | 0 |
| A5 | B: walls with area_m2 (loaded with --geometry) | 6,095 of 6,095 | 0 |
| A5 | B: their area, m² (loaded with --geometry) | 103,057.7 | None |
| A7 | C: duplicate_space findings | 47 | 47 |
| A8 | B: area takeoff by Revit type, total | null | None |
| A9 | B: findings error / warning / info | (model gaps, not loader gaps) | 0 / 743 / 8,079 |
| A9 | B: the same, by scope | (model gaps apart from loader gaps) | model content 0 / 743 / 8,079; loader coverage 0 / 0 / 0 |
| A9 | B: model_without_quantities findings | 1 | 1 |
| A9 | B: placeholder_project_info findings | 1 | 1 |
| A9 | C: space_without_area findings | 0 | 0 |
| A11 | B: door schedule of XX_AR_L01, characters | < 10,000 | 3,177 (159 doors, 6 types) |
| A12 | B: other models it is aligned with (v_storey_alignment) | 0 | 0 |
| A13 | B: measure_not_converted findings | 0 for U-values | 0 |
| A13 | B: revit_tag_mismatch findings on openings | 0 | 0 |
| A14 | B: stair flights with a Revit type | 182 of 182 | 182 |
| A15 | D vs A: FireRating added on walls / curtain walls | 42 / 9 | 42 / 9 |
| A16 | B: elements whose revit_type holds a control character | 0 | 0 |

The A5 rows are 0 here by design: a plain ingest measures no geometry. The A8 row stays
null for the same reason: file B carries no quantities.

## Files A and B loaded with `--geometry` (A5)

A separate database, with `ifc-sql ingest --geometry` for A and B only.

| Finding | Figure | Audit's ground truth | Measured |
|---|---|---|---:|
| A5 | B: walls with area_m2 (loaded with --geometry) | 6,095 of 6,095 | 6,095 |
| A5 | B: their area, m² (loaded with --geometry) | 103,057.7 | 103,057.7 |
| A1 | A: wall area (v_element.area_m2), m² | 946.0 | 946.0 (the file's own value still takes precedence) |

Measuring took 0.7 s for A's 288 shapes and 59.5 s for B's 8,285, on 4 cores. One of B's
bodies could not be triangulated and is reported as `geometry_not_measured`.
