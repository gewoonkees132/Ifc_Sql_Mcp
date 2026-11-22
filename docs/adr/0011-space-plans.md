# ADR 11: Rooms exported twice found by their plans, and Revit Areas apart from rooms

- Status: accepted; refines [ADR 10](0010-geometry-quantities.md) and audit finding A7
- Date: 2026-11-22
- Re-check finding: [R1](../audit/2026-11-recheck.md#r1-room-totals-count-the-same-floor-two-to-four-times)

## Context

A Revit model that holds both Rooms and MEP Spaces exports each room twice, and Revit
exports its area plans as spaces too. Each is an `IfcSpace`. Summing a room schedule then
counts the same floor more than once.

Audit finding A7 marked a copy by a key: the same number, name, storey and floor area.
The re-check measured every space's plan with IfcOpenShell (`harness/space_truth.py`) and
showed the key is not enough:

- **File C:** the key finds 47 of the 75 copies. The others have another name
  (`41 WC` / `41 WC H`), are split into parts (corridor 49 into 49a–d), or have another
  number. "Each room once" came to 6,124.8 m², against 4,721.4 m².
- **Duplex MEP:** the "MEP Space" copies have their own numbers and names
  (`A101-M Foyer MEP Space`). The key finds none of the 21.
- **File E:** 25 of its spaces are Revit Areas, 22,905.9 m² of gross area added to
  7,438.8 m² of rooms.

On all of these files, whenever two rooms overlap on a storey, one lies at least 90 %
inside the other. No two rooms overlap only partly.

## Decision

1. **Measure every space's plan at every ingest**, not only with `--geometry`
   (`geometry.measure_space_plans`). The plan is the body's triangles seen from above,
   in world coordinates, joined into one shape.
   - Spaces are few: their plans take 2.6 s on C's 163 and 1.7 s on E's 307.
   - A space whose body gives no plan raises `geometry_not_measured`.
2. **Compare the plans storey by storey** (`ingest/footprints.py`, pure shapely).
   - The largest plan is taken first, then file order.
   - A space whose plan lies at least 90 % inside the spaces taken before it repeats
     them, and points at the one it overlaps most.
   - This is the ground truth's own rule, so the totals match it.
3. **Keep the result on the space**: `spatial_element.footprint_m2` and `repeats_id`
   (migration 0019). `v_space_schedule` v3 takes `duplicate_of` from `repeats_id`, and
   keeps A7's key for a space without a plan.
4. **Read Revit's category** of a space: `Pset_SpaceCommon.Category`, else Revit's
   `Other` or `PSet_Revit_Other` set. It is stored in `spatial_element.revit_category`.
   - Spaces of category `Areas` are area plans. They are left out of the comparison and
     out of `space_schedule`'s totals.
   - They are still listed, with their own count and total.

## Consequences

- **Each room once:** C 4,721.4 m², Duplex MEP 386.3 m², E 7,438.8 m², all the ground
  truth's figures.
  - `duplicate_space` finds 75 copies on C and 21 on the Duplex MEP model.
  - The architectural Duplex is unchanged (422.047 m², no copy).
- **Every ingest now triangulates the spaces.** It is the only geometry a plain ingest
  computes; walls and slabs still wait for `--geometry`.
- **Models loaded before migration 0019** have no plans, so their schedule keeps A7's
  key until the file is loaded again.
- **Which space of a pair is the copy** follows plan size, then file order. A total
  does not depend on it; which row is marked does.

## Alternatives considered

- **Widen A7's key**, for example by dropping the name. That finds 11 more copies on C,
  but not the split corridor nor the pairs with other numbers, and it still finds none
  on the Duplex MEP model. Dropping the number as well marks mirrored apartments of the
  Duplex as copies.
- **Ask the architect to export one kind.** This is still the advice
  ([revit-export-settings.md](../revit-export-settings.md)). It does not help with a
  file already exported, and the tool should not give a wrong total in the meantime.
- **Compare plans in the database.** Plain PostgreSQL has no polygon operations, and
  PostGIS would be a second extension to install for one comparison made once per load.
