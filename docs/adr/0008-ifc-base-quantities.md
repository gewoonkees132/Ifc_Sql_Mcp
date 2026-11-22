# ADR 8: Reading IFC base quantities

- Status: accepted; amends [ADR 3](0003-quantity-sources-and-precedence.md) (decisions 1
  and 2)
- Date: 2026-11-21
- Audit finding: [A1](../audit/2026-11-modern-revit-exports.md#a1-the-quantity-sets-modern-revit-writes-are-ignored)

## Context

ADR 3 mapped the quantities of Revit 2011 and 2013 exports: Revit's `Dimensions`
parameters and the GSA space area. Its consequences already said that a file with
`Qto_*BaseQuantities` "would get no `ifc_qto` rows from them until they are added". The
audit found that this is every modern export. Revit 2019 to 2022 write IFC base
quantities: `Qto_WallBaseQuantities` and its siblings in IFC4, and one set named
`BaseQuantities` in IFC2X3. The audit's files A and C had 0 quantities, and file B none
at all.

ADR 7 checks those sets against physical bounds. Running the bounds also showed which of
their values Revit gets wrong, and that decides what to map.

## Decision

1. **Six canonical quantities.** `width_m` joins the five, as IFC's `Width`: a door's
   or window's width, a wall's or slab's thickness. A door schedule needs it, and so
   does checking a slab's volume against area × thickness.
2. **Rules per standard set.** The list covers walls, slabs, spaces, doors, windows,
   columns, beams, members, stair flights and railings, as written in
   `quantities.QUANTITY_RULES` and listed in
   [revit-ifc-quirks.md](../revit-ifc-quirks.md#14-revit-2019-to-2022-write-ifc-base-quantities-some-of-them-wrong).
   It is still a closed list (ADR 3, decision 2). Sets the audit's files do not show
   correct (coverings, roofs, plates, openings) are not mapped.
3. **IFC2X3 `BaseQuantities` is read as the class's `Qto_` set.** On an `IfcWall` it is
   `Qto_WallBaseQuantities`, and so on (`QTO_SET_OF_CLASS`). On a class without one, it
   maps to nothing.
4. **Net first, gross as a silent fallback.** A gross value supplies a quantity only when
   its set has no net value, or only an implausible one (ADR 7), and never conflicts
   with the net one. This covers space areas, perimeters and volumes, wall side areas,
   and the volumes of walls, slabs, columns, beams, members and stair flights.
5. **A slab's area is `GrossArea`, never `NetArea`.** Revit 2021 writes `NetArea` as the
   area of the whole solid: 219.0 m² for a 105.9 m² floor (twice the plan plus the
   edges). It does so on every slab of files A and C, including those with no other
   value to catch it ("Pad 2", 320.5 m² for a pad of about 149 m²).
6. **GSA Space Areas becomes a fallback.** It still gives the Duplex's rooms their area,
   and gives way to `NetFloorArea` in a file that has both.
7. **Precedence is unchanged:** `ifc_qto` > `revit_pset` > `geometry` (ADR 3, decision 4).

## Consequences

- File A: 47 of 47 walls have an area, 946.0 m² in all, the file's own total. File C: 163
  of 163 spaces have an area. Their 163 volumes and perimeters are implausible, so
  they have none, which is better than a wrong one.
- File E, exported with Revit property sets as well, keeps the same wall and room
  areas: its `GrossSideArea` equals Revit's `Area` on all 1,041 walls, and its rooms
  still sum to 30,344.7 m². Its slab areas now come from `GrossArea`, which differs from
  Revit's `Area` by more than 1 % on 12 of 79 slabs. Both rows are kept in
  `ifc.quantity`, so the sources can be compared (ADR 3, decision 3).
- The Duplex files have no base quantities, and none of their figures changes.
- A new `Qto_` set (from another exporter, or a later Revit) is one row in the table and
  a test, after checking its values on a real file.

## Alternatives considered

- **Every `Qto_*` set and every quantity in it.** It would map `NetArea` on slabs, wrong
  on every slab of two files, and coverings and roofs no file showed correct.
- **`NetArea` with a plausibility check only.** The check catches `NetArea` only where
  `GrossArea` is present; on "Pad 2" nothing would.
- **Rank gross values below Revit's parameters** (a fourth source). It complicates the
  precedence for a difference that, on the walls of file E, is zero.
