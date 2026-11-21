# Acceptance figures before the fixes

`python -I docs/audit/harness/figures.py <url>` on a database loaded with code `0ffa380` (= `15841f4` plus the A17 test fix), 2026-11-21.

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
