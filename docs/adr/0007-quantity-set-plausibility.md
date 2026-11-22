# ADR 7: Physical bounds on quantity sets

- Status: accepted
- Date: 2026-11-21
- Audit finding: [A2](../audit/2026-11-modern-revit-exports.md#a2-revit-2021s-duplicated-and-unscaled-quantities-reach-the-llm)

## Context

ADR 3 decided which quantity sets become canonical quantities. It took the file's values
as measurements. The audit of Revit 2019 to 2022 exports found that some are not, and
the measurement below this record confirmed it on every set of files A, C and E.

| Defect | Revit | Value / what it should be |
|---|---|---|
| A wall's `NetSideArea` and `NetVolume` written twice, the first unscaled (gross face in mm², labelled m²) | 2021 | 10⁶ and 10⁹ |
| A slab's `NetArea` is the area of the whole solid: top, bottom and edges | 2021 | about 2 |
| A slab's `GrossVolume` is `GrossArea` (m²) × `Width` (mm) | 2019, 2021 | 1,000 |
| A sloped roof slab's `GrossArea` | 2021 | 0.09 |
| A space's `GrossVolume` is floor area (m²) × height (mm) | 2019, 2021 | 1,000 |
| A space's `GrossPerimeter` is in feet, labelled with the file's millimetres | 2021 | 1/304.8 |

`properties.py` kept the first of two values, so file order decided. The obvious
`run_sql` sum over the walls of file A gave 1,157,891,673.6 m² for a 946 m² house.

Within a quantity set, IFC's own definitions tie the values together. A side area cannot
exceed length × height. A net value cannot exceed its gross one. No shape of area A has
a perimeter below 2√(πA). Checking every set of the three files against those ties
([`quantity_bounds.py`](../audit/harness/quantity_bounds.py) in the audit harness), the correct
values never pass a bound by more than a factor of 1.1, and the defective ones by 10³
to 10⁹, except the two slab-area cases, which are factors of 2 and 10.

## Decision

1. **Bounds are checked per quantity set, at ingest, before any value becomes a
   quantity** (`ingest/plausibility.py`). Only `IfcElementQuantity` sets are checked;
   Revit's parameter sets are not, since their names carry no IFC definition.
2. **Two allowances.** A net value may pass its gross by 5 % (`NET_TOLERANCE`): net is
   gross minus openings, so more is a definition broken, not a shape. A value against a
   geometric bound (length × height, area × thickness, area × height, a circle's
   perimeter) may pass it by a factor of 2 (`FACTOR`). That is far outside any correct
   value measured (≤ 1.1, a gable wall), and far inside the unit errors.
3. **A duplicated value is settled by the bounds first.** Of several values for one
   quantity, the one value that keeps every bound wins. Only if the bounds do not
   single one out does file order decide, as before. The `conflicting_property` message
   says which bound chose it.
4. **A single value that breaks a bound is kept but flagged.** It stays in
   `ifc.property` as written, so the file can be read back, with `is_implausible` set.
   No quantity is made from it, and an `implausible_quantity` issue (a warning) names
   the bound and its value. `get_element` marks it `implausible`.
5. **A value flagged by one bound bounds nothing after it.** The order of `BOUNDS`
   matters and is part of this decision. Net against gross comes first. A slab's
   `GrossVolume` is checked against `GrossArea` × `Width` before `GrossArea` is checked
   against `NetVolume` / `Width`. A slab's `NetVolume` is not bounded by `GrossArea`,
   because on a sloped roof `GrossArea` is the wrong one.
6. **Finer inconsistencies are findings, not rejections.** Three SQL rules compare the
   canonical quantities with tight allowances: `wall_area_inconsistent` (5 %),
   `space_volume_inconsistent` (10 %, upwards only) and `space_perimeter_implausible`.

## Consequences

- File A's stored wall areas add up to 946.0 m², the file's own total. On file C,
  163 space volumes and 163 perimeters are flagged. So are 6 slab areas and 6 slab
  volumes. On file E, 307 space volumes and 100 slab volumes are flagged. No wall
  value is flagged in any of the three files.
- A defect the bounds cannot see still passes. Revit 2021's slab `NetArea` is the
  solid's surface on every slab, so for slabs this project takes area from `GrossArea`
  and never from `NetArea` (ADR 3's rules, finding A1).
- Models loaded before migration 0011 have `is_implausible = false` and kept their
  duplicates in file order. Deleting the model and loading the file again applies the
  bounds.

## Alternatives considered

- **Keep file order and only report.** It is what the audit found. The first value
  was the wrong one in every duplicated wall of files A and C (94 and 266).
- **Drop implausible values.** The file could no longer be read back as written, and
  the evidence would be gone. A flag keeps both.
- **One tolerance for everything.** At 5 %, 10 sound gable walls of file E would be
  flagged and their areas lost. At a factor of 2, a net area twice its gross would pass.
- **Correct the values** (divide by 1,000, convert feet). The factor is a guess about
  one exporter's bug. A flagged gap is easier to fix than a silently "repaired" number.
