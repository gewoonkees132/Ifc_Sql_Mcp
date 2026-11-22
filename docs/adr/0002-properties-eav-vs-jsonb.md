# ADR 2: Properties as a typed EAV table on a shared object supertable

- Status: accepted
- Date: 2026-11-16

## Context

Most of the information in a Revit IFC export sits in property sets, not in IFC
attributes. The sets are open-ended. Their names and contents depend on the family,
category, discipline and Revit version: `Pset_WallCommon`, `PSet_Revit_Dimensions`,
`PSet_Revit_Type_Construction`, a door type set named after the door instance, and so
on. The architectural Duplex model alone has about 5,200 single-value properties.

Each value has an IFC type (`IfcLabel`, `IfcLengthMeasure`, `IfcBoolean`, ...) and,
for measures, a unit that has to be converted to SI because the sample files declare
different units. Revit adds quirks:

- placeholder values (`Manufacturer = 'Manufacturer'`) and empty strings;
- type data copied onto instances (`PSet_Revit_Type_*`);
- the `id` key that `ifcopenshell.util.element.get_psets()` injects into each set.

These are the queries the data has to support:

- filter objects by a property (`IsExternal = true`, `Width > 0.2`);
- show all properties of one object, as the MCP `get_element` tool does;
- find which property sets and names exist and how their values are distributed;
- run set-based data-quality rules over all values (placeholders, empty values,
  inconsistent types).

Properties belong to physical elements and to spatial elements, because spaces carry
OmniClass and area parameters.

## Decision

### 1. One row per property value, with typed value columns

```
property(object_id, pset_name, prop_name,      -- primary key
         value_type,                           -- IFC type: IfcLabel, IfcLengthMeasure, ...
         value_text | value_num | value_bool,  -- at most one is set
         unit,                                 -- SI unit of value_num
         is_type_property)
index (pset_name, prop_name)
```

CHECK constraints keep each row coherent:

- at most one value column is set;
- `value_type` is present exactly when a value is;
- `unit` appears only with a number;
- numbers are finite.

Empty strings and placeholders are stored verbatim, because finding them is
data-quality work (Phase 5), not a load error.

### 2. Properties attach to a shared `object` supertable

The supertable holds one row per IFC product (site, building, storey, space, wall,
duct...). `spatial_element` and `element` extend it one-to-one (class-table
inheritance). `property`, `quantity`, `classification` and `dq_result` reference
`object(object_id)`. Spatial elements and physical elements therefore share one id
space and one GlobalId uniqueness rule per model.

### 3. Type properties are materialised on each instance, flagged

Values a type defines appear on every instance with `is_type_property = true`. That
covers values inherited from an `IfcTypeObject` and the `PSet_Revit_Type_*` sets that
Revit 2011 copies onto walls, which have no type object at all.

### 4. Quantities are not properties

Canonical quantities (`length_m`, `area_m2`, ...) have stricter rules:

- a fixed vocabulary;
- non-negative values;
- several sources with a precedence order.

They get their own `quantity` table. The raw Revit dimension values stay in `property`
as well, so lineage and data-quality rules can compare the two.

## Consequences

- **Filtering is plain SQL** on indexed columns. Numeric comparisons are numeric, with
  no casts that fail halfway through a scan on one malformed value:

  ```sql
  SELECT o.name
  FROM ifc.property AS p JOIN ifc.object AS o USING (object_id)
  WHERE p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'IsExternal' AND p.value_bool;
  ```

- **Data-quality rules are short set-based queries.** The placeholder defect is
  `WHERE value_text = prop_name`.
- **Integrity is enforced by the database.** Every value has a real foreign key to its
  object, and deleting a model cascades through `object` to all its properties.
  Polymorphic `object_kind + object_id` keys could not have a foreign key.
- **One object, one key.** MCP tools can take an `object_id` and work for walls and
  rooms alike.
- **The cost is rows and joins.**
  - There is one row per value rather than one per object.
  - Reassembling an object's property sets needs an aggregate (`jsonb_object_agg` over
    its rows, one query for `get_element`).
  - Reading an element's attributes is a join of `object` and `element`, which the
    `v_element` view hides.
- **Type values are duplicated** across instances, which is small at these volumes and
  saves a hop on every query.
- **Not enforced by a constraint:** that each object has exactly one subtype row
  matching `object_kind`. The loader writes both in one transaction. Enforcing it would
  need a discriminator column in every subtype plus composite foreign keys, or
  triggers.

## Alternatives considered

- **One `jsonb` column per object** (`psets jsonb`, GIN-indexed), e.g.
  `{"Pset_WallCommon": {"IsExternal": true}}`. It is compact and returns an object's
  properties in one read. Its drawbacks:
  - It loses per-value metadata unless every leaf becomes
    `{"value": …, "type": …, "unit": …}`, which makes every path deeper.
  - It cannot have CHECK constraints per value.
  - `->>` returns text, so numeric filters need casts that fail at query time on bad
    data.
  - Questions across objects ("which sets exist, how often") need `jsonb_each`
    laterals.

  For LLM-written SQL in particular, operators and casts are an easy place to be
  subtly wrong. Rejected as the primary store. `jsonb` is still used where it fits:
  `ingestion_run.row_counts`, an audit record that is never filtered on.
- **Hybrid: EAV plus a jsonb copy.** Two representations of the same facts have to be
  kept in sync, for a read speed-up nobody needs at this size. If `get_element` ever
  needs it, a view or materialised view can build the jsonb from `property`.
- **Wide tables with a column per property.** Impossible with open-ended sets.
  Reasonable only for a curated subset, which is what the views do for quantities.
- **Polymorphic key `(object_kind, object_id)` instead of a supertable.** One fewer
  table, but no foreign keys, so no cascades and nothing to stop orphaned properties.
- **A separate `element_type` table with its own properties.** More normalised, but
  Revit 2011 walls have no type object (their type data is on the instance anyway).
  "All properties of X" would then always be a union of instance and type rows.
