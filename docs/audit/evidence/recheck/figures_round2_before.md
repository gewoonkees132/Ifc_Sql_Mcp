# Round 2 figures before the fixes

`python -I docs/audit/harness/figures.py <url>` on a database loaded with code `513fe97` (the code the re-check measured, after A1–A18), run with that code's own environment, 2026-11-22. Loaded as harness/README.md describes: the four Duplex files as models 1–4, then files A to E as models 5–9, D with `--replaces 5`, and `ifc-sql dq` on all nine. The after table is [figures_round2_after.md](figures_round2_after.md).

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
| A7 | C: duplicate_space findings | 47 by A7's key; 75 by the plans (R1) | 47 |
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
| R1 | C: rooms, each once, m² (space_schedule) | 4,721.4 (75 copies) | 6,124.8 (47 copies) |
| R1 | E: room total, m² (space_schedule) | 7,438.8; 25 Revit Areas apart (22,905.9) | 30,344.7; Revit Areas not told apart |
| R1 | Duplex MEP: rooms, each once, m² | 386.3 (21 copies) | 797.8 (0 copies) |
| R2 | A: ceilings with an area | 2 of 2 (154.3 m²) | 0 of 2 (0 m²) |
| R2 | C: ceilings with an area | 43 of 43 (1,755.4 m²) | 0 of 43 (0 m²) |
| R3 | C: unsupported_property findings (errors) | 0 | 724 |
| R3 | C: duct segments with Pset_DuctSegmentTypeCommon.Shape | 724 | 0 |
| R4 | B: takeoff warning / no-quantities finding name ingest --geometry | yes / yes | no / no |
