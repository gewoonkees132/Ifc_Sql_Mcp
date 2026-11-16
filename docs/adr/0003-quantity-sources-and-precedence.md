# ADR 3: Quantity sources and precedence

- Status: accepted
- Date: 2026-11-16

## Context

Takeoffs and schedules need lengths, areas and volumes, and an IFC file can say them in
several places:

- **IFC quantity sets** (`IfcElementQuantity`). The standard ones are the
  `Qto_*BaseQuantities`, which none of the four Duplex files contains. Their spaces do
  carry one quantity set from the GSA (US General Services Administration) workflow:
  `GSA Space Areas`, with a single `IfcQuantityArea` called `GSA BIM Area`.
- **Revit parameters** exported as property sets: `PSet_Revit_Dimensions` (Revit 2011)
  or `Dimensions` (Revit 2013), with `Length`, `Area`, `Volume`, `Perimeter` and
  `Unbounded Height`. They are typed measures (`IfcLengthMeasure`, `IfcAreaMeasure`...)
  in the file's units, which are metres, millimetres or feet depending on the file.
- **Plain numbers.** Revit writes some dimensions as `IfcReal`, which IFC defines as
  unitless: pipe fittings' `Length`, for example (44 in MEP, 61 in PLU).
- **The geometry**, from which a quantity could be computed. The pipeline does not read
  geometry.

The figures per file are in [revit-ifc-quirks.md](../revit-ifc-quirks.md), sections 1
and 10 (`make quirks` prints them).

ADR 2 already decided that quantities get their own table with a fixed vocabulary,
separate from `property`. This record decides which values go in it and which one wins
when there are several.

## Decision

1. **Five canonical quantities**, named with their unit: `length_m`, `area_m2`,
   `volume_m3`, `perimeter_m` and `height_m`. Values are converted to SI at ingest,
   with the same unit handling as properties.
2. **A closed list of mappings.** `quantities.QUANTITY_RULES` names every
   (set, property) pair that becomes a quantity. Nothing outside it does, so a property
   that merely looks like an area never turns into one.
3. **Every source is kept.** `quantity` has one row per object, quantity and source,
   with `source_ref` naming the exact property (`PSet_Revit_Dimensions.Area`). The raw
   value also stays in `property`; a test checks that every `source_ref` points at an
   existing property row.
4. **Precedence `ifc_qto` > `revit_pset` > `geometry`**, applied by the view
   `v_quantity` (`DISTINCT ON` per object and quantity). `v_element`,
   `v_space_schedule` and `v_takeoff` read quantities only through it.
   - `ifc_qto` first: a quantity set is IFC's own way to state a quantity, written by
     the exporter on purpose and defined by the standard rather than by a vendor.
   - `revit_pset` second: the number the designers saw in Revit.
   - `geometry` last: a value this project would compute should never override one the
     authors wrote.
5. **GSA BIM Area is `area_m2` from `ifc_qto`.** It is a real `IfcQuantityArea`, so it
   ranks first. In all 65 spaces of the four files it agrees with the Revit `Area`
   parameter to within 1.1 × 10⁻¹⁴ m² (`make quirks`). After `make ingest-samples`,
   those 65 space areas are the only cases where `v_quantity` has two sources to choose
   from; the largest difference is 1.07 × 10⁻¹⁴ m² (the query is in
   [data-model.md](../data-model.md#quantities-after-loading)). The choice therefore
   changes no number. It decides which value is *cited*.
6. **Unitless `IfcReal` dimensions never become quantities.** The file does not say
   their unit, and in the millimetre file the difference between guessing metres and
   guessing millimetres is a factor of 1,000. They are reported as
   `quantity_not_a_measure`, and the value stays in `property`. A rule only produces a
   quantity when the converted value carries the rule's SI unit.
7. **Negative values are not quantities.** The column holds magnitudes (`value >= 0`),
   so a negative mapped value is reported as `negative_quantity` and left out. Negative
   values that are not quantities (`Top Offset`, `Invert Elevation`) are legitimate and
   stay in `property`.
8. **`geometry` is reserved, not implemented.** The CHECK constraint allows it and
   `v_quantity` ranks it, but nothing writes it, and the four files load no `geometry`
   rows. Reserving it now means computing quantities later is a new writer, not a
   migration, and its rank is settled before anyone is tempted to let computed values
   win.

## Consequences

- Questions about quantities have one answer per object (`v_quantity`), and its
  provenance is one column away (`source`, `source_ref`). Comparing sources is a
  `GROUP BY` on `ifc.quantity`.
- After `make ingest-samples`, `v_quantity` picks 65 values from `ifc_qto` and 1,182 from
  `revit_pset`, out of 1,312 `quantity` rows.
- Elements without a mapped parameter have no quantity. The 105 fittings
  (`IfcFlowFitting`, 44 in MEP and 61 in PLU) whose `Length` is an `IfcReal` have no
  `length_m`, so a length takeoff of ducts and pipes leaves them out. Their
  `ingest_issue` rows say so, and the raw values can still be read from `property`.
- Wall `area_m2` is Revit's `Area`, one face of the wall. `v_takeoff` builds layer areas
  and volumes on that (ADR 2, `data-model.md`).
- A file with `Qto_*BaseQuantities` (from another exporter, or Revit with base
  quantities switched on) would get no `ifc_qto` rows from them until they are added to
  `QUANTITY_RULES`. Once they are, they outrank the Revit parameters without any change
  to the views.

## Alternatives considered

- **Revit parameters first.** For Revit files they are the numbers the authors saw. But the ranking would
  then depend on the authoring tool, while `ifc_qto` first is the same rule for every
  exporter. With the GSA area the only overlap, the choice changes no value today.
- **Compute everything from geometry.** One consistent definition of area and volume
  for every element and exporter. It needs a geometry kernel and seconds to minutes per
  model, and the results would differ from the numbers the designers see in Revit. Kept
  as a later, lowest-ranked source.
- **Store only the winning value.** Smaller, but the lineage is gone and a source that
  disagrees can no longer be found. The `quantity` table is small (1,312 rows for the
  four files), so keeping every source costs little.
- **Convert `IfcReal` dimensions with the project's length unit.** It would fill the
  missing fitting lengths, but on a guess that IFC itself contradicts, and a wrong
  guess is silent. A reported gap is easier to fix than a wrong number.
