# Revit export settings

What to tick in Revit's IFC export setup so this tool can answer an architect's
questions, and what it does when an option is off. Every figure is from the audit's five
Revit 2019 to 2022 exports ([the audit](audit/2026-11-modern-revit-exports.md), then
[how it was implemented](audit/2026-11-implementation.md)).

## The settings

| In Revit's IFC export setup | What the tool reads from it | Without it |
|---|---|---|
| **Export base quantities** (Property Sets) | The IFC base quantities (`Qto_*BaseQuantities`, or `BaseQuantities` in IFC2X3): wall, slab and room areas, lengths, heights, thicknesses and volumes. They rank first ([ADR 8](adr/0008-ifc-base-quantities.md)). File A: 946.0 m² of walls, 47 of 47. File C: an area for 163 of 163 spaces. | No areas or volumes, unless Revit property sets are exported or the file is measured with `--geometry` (below). |
| **Export Revit property sets** (Property Sets) | Revit's own parameters, among them `Dimensions` (Area, Volume, Length). They are a second quantity source. File E: 4,836 areas, including all 1,057 walls and all 307 rooms (30,344.7 m²). | Revit's parameters are not in the file. |
| **Export IFC common property sets** (Property Sets) | `Pset_WallCommon`, `Pset_DoorCommon` and the rest: `FireRating`, `IsExternal`, `LoadBearing`, `ThermalTransmittance`. The `missing_fire_rating` rule and the `schedule` tool read them. | No fire ratings, and nothing to tell external from internal walls. |
| **IFC version**: IFC2X3 Coordination View 2.0, or IFC4 Reference View | Both are read and tested (file B is IFC2X3, file A IFC4). | — |

A door's and a window's width and height are written whatever the options, as
attributes of the door or window, and are always loaded (1,458 of 1,458 doors of file B).

## Habits that keep the answers right

- **Fill in Project Information** (Project Number and Project Name). Revit's defaults,
  `0001` and `Project Name`, are shared by unrelated models; the tool then puts the
  model in no project, says so at ingest, and reports `placeholder_project_info`. Or
  give the project with `ifc-sql ingest --project` or `ifc-sql watch --project`
  ([data-model.md](data-model.md#projects-and-versions)).
- **Export Rooms or MEP Spaces, not both.** A model that has both writes each room twice,
  often under another number or name: 75 of file C's 163 spaces repeat another. The tool
  finds the repeats from their plans (`duplicate_space`) and gives a total without them,
  but the room list still holds both. Revit's area plans are exported as spaces too
  (category `Areas`); the tool lists them apart and leaves them out of room totals.
- **Export each model under the same file name every time**, and into one folder. With
  `ifc-sql watch`, a new export of `House.ifc` is then loaded as the new version of the
  previous `House.ifc`, and `compare_models` shows what changed.
- **Name the types in Revit with visible characters only.** Two wall types of file B
  had invisible control characters; the tool removes them from `revit_type` and reports
  `control_character_in_name`.

## A file exported without quantities

Exports are often made for coordination, with none of the property set options ticked:
file B, a 24-storey tower, has no area or volume at all. Then measure the shapes:

```bash
uv run ifc-sql ingest tower.ifc --discipline ARC --geometry
```

IfcOpenShell triangulates the walls, slabs, spaces and other solids. A wall's side
area, a slab's or a space's footprint, and each solid's volume become quantities of
source `geometry`, ranked after anything the file carries
([ADR 10](adr/0010-geometry-quantities.md)). File B's 6,095 walls get 103,057.7 m², in
about a minute on 4 cores. On file A, which has its own quantities, the shapes give
964.6 m² against the file's 946.0 m²; the file's figure is the one used.

## What the tool says when something is missing

- **`data_quality_report`** lists the model's gaps (`model_content`) apart from what
  the tool does not read (`loader_coverage`). The gaps include `model_without_quantities`
  (export with base quantities), `model_without_spaces`, `missing_fire_rating`,
  `placeholder_material` and `placeholder_project_info`
  ([data-quality.md](data-quality.md#scope-the-model-or-the-loader)).
- **`quantity_takeoff`** gives a null total, never 0, when no element has the metric.
  Its note then says to export with base quantities.
