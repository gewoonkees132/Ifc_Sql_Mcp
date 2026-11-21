# Data model

Everything lives in one PostgreSQL schema, `ifc`: 14 tables, 7 views and a read-only
role. It is created by Alembic migrations:

```bash
make up        # PostgreSQL 16 in Docker
make migrate   # or: uv run ifc-sql migrate
```

The table definitions live in
[`src/ifc_sql_mcp/db/schema.py`](../src/ifc_sql_mcp/db/schema.py) (SQLAlchemy Core),
the views in [`src/ifc_sql_mcp/db/sql/views/`](../src/ifc_sql_mcp/db/sql/views/), and
the migrations in
[`src/ifc_sql_mcp/db/migrations/versions/`](../src/ifc_sql_mcp/db/migrations/versions/).
Every table, view and column carries a `COMMENT`. The MCP `ifc://schema` resource is
generated from those comments ([mcp.md](mcp.md#resources)), so the catalogue documents
itself (`\d+ ifc.element` in psql shows them).

Two decisions shape most of the model. [ADR 1](adr/0001-postgresql.md) covers why
PostgreSQL, and [ADR 2](adr/0002-properties-eav-vs-jsonb.md) covers how properties are
stored and why everything hangs off one `object` table.
[ADR 3](adr/0003-quantity-sources-and-precedence.md) covers where quantities come from,
[ADR 4](adr/0004-idempotent-ingest.md) how files are loaded ([Loading](#loading) below),
[ADR 5](adr/0005-data-quality-rules.md) how models are checked against the data
quality rules, which [data-quality.md](data-quality.md) describes with their findings, and
[ADR 6](adr/0006-mcp-server.md) how the MCP server serves it all to an LLM
([mcp.md](mcp.md)).

## Entity-relationship diagram

```mermaid
erDiagram
    model ||--o{ object : "contains"
    model ||--o{ material : "defines"
    model |o--o{ ingestion_run : "loaded by"
    model ||--o{ dq_run : "checked by"

    object ||--o| spatial_element : "is a"
    object ||--o| element : "is a"
    spatial_element |o--o{ spatial_element : "parent of"
    spatial_element |o--o{ element : "contains"
    spatial_element |o--o{ element : "is storey of"

    object ||--o{ property : "has"
    object ||--o{ quantity : "has"
    object ||--o{ classification : "has"
    element ||--o{ element_material_layer : "made of"
    material ||--o{ element_material_layer : "used in"

    model ||--o{ ingest_issue : "reported"
    object |o--o{ ingest_issue : "about"

    dq_run ||--o{ dq_result : "found"
    dq_rule ||--o{ dq_result : "violated in"
    object |o--o{ dq_result : "about"

    model {
        bigint model_id PK
        text file_name
        text file_sha256 UK "one row per file version"
        bigint file_size_bytes
        text ifc_schema "IFC2X3, IFC4..."
        text mvd
        text authoring_app
        text discipline "ARC, MEP, ELE, PLU..."
        text project_global_id
        text project_name
        text project_long_name
        text length_unit "declared unit; *_m columns are converted"
        timestamptz ingested_at
    }
    ingestion_run {
        bigint ingestion_run_id PK
        bigint model_id FK "SET NULL: history outlives the model"
        text file_name
        text file_sha256
        timestamptz started_at
        timestamptz finished_at
        text status "running, succeeded, failed, skipped"
        jsonb row_counts
        text error
    }
    object {
        bigint object_id PK
        bigint model_id FK
        text global_id "UNIQUE (model_id, global_id)"
        text object_kind "spatial_element or element"
        text ifc_class "IfcWallStandardCase"
        text ifc_class_base "IfcWall"
        text name "verbatim IFC Name"
    }
    spatial_element {
        bigint object_id PK, FK
        bigint parent_id FK
        text long_name
        numeric elevation_m "numeric(9,3)"
    }
    element {
        bigint object_id PK, FK
        bigint container_id FK "storey or space"
        bigint storey_id FK "resolved storey"
        text tag
        boolean is_feature "opening, void, projection"
        text revit_family
        text revit_type
        text host_revit_family "features only"
        text host_revit_type "features only"
        bigint revit_element_id
        text type_name
        text predefined_type
    }
    property {
        bigint object_id PK, FK
        text pset_name PK
        text prop_name PK
        text value_type "IfcLabel, IfcLengthMeasure..."
        text value_text
        float8 value_num
        boolean value_bool
        text unit
        boolean is_type_property
        boolean is_implausible "breaks a bound of its set"
    }
    quantity {
        bigint object_id PK, FK
        text name PK "length_m, area_m2, volume_m3, perimeter_m, height_m, width_m"
        text source PK "ifc_qto, revit_pset, geometry"
        float8 value
        text source_ref
    }
    material {
        bigint material_id PK
        bigint model_id FK "UNIQUE (model_id, name)"
        text name
    }
    element_material_layer {
        bigint element_id PK, FK
        smallint layer_index PK
        bigint material_id FK
        float8 thickness_m
    }
    classification {
        bigint object_id PK, FK
        text system PK
        text code PK
        text title
    }
    ingest_issue {
        bigint model_id PK, FK
        int issue_index PK "order the pipeline reported it"
        text code "quantity_not_a_measure..."
        bigint object_id FK "NULL: model-level, or object not loaded"
        text global_id "as written in the file"
        text message
    }
    dq_rule {
        text rule_id PK
        text description
        text severity
        text dimension
        text scope "model_content or loader_coverage"
        timestamptz retired_at "removed from the catalogue, kept for its findings"
    }
    dq_run {
        bigint dq_run_id PK
        bigint model_id FK
        timestamptz started_at
        timestamptz finished_at
        text status
        text error
    }
    dq_result {
        bigint dq_result_id PK
        bigint dq_run_id FK
        text rule_id FK
        bigint object_id FK
        text severity
        text message
    }
```

## Tables

| Table | Grain | Notes |
|---|---|---|
| `model` | one IFC file version | Identified by `file_sha256`, so loading the same bytes twice is impossible. `discipline` is supplied at ingest; IFC files do not state it. |
| `ingestion_run` | one ingest attempt | Written outside the load transaction, so failed and skipped runs are recorded too. Keeps `file_sha256` because `model_id` is NULL when no model was committed. |
| `object` | one IFC product | Shared supertype of spatial elements and physical elements (class-table inheritance, ADR 2). |
| `spatial_element` | site, building, storey, space | `parent_id` follows `IfcRelAggregates`. |
| `element` | wall, door, duct, chair... | `container_id` is the direct `IfcRelContainedInSpatialStructure` target; `storey_id` is resolved at ingest. |
| `property` | one value per object, set and name | Typed entity-attribute-value table (ADR 2). |
| `quantity` | one value per object, quantity and source | Canonical names whose suffix is the unit. Several sources can coexist. |
| `material` | one material name per model | IFC materials have no GlobalId, so the name is the key within a file. |
| `element_material_layer` | one layer (or list entry) per element | Ordered by `layer_index`. A single `IfcMaterial` is one row without thickness. |
| `classification` | one code per object and system | From `IfcClassificationReference` or from Revit parameters such as OmniClass. |
| `ingest_issue` | one issue the transform reported | What could not be stored as written, and what was done instead. Written with the model, deleted with it; the input for data-quality findings ([ADR 4](adr/0004-idempotent-ingest.md)). `object_id` is NULL when the object was never loaded, and `global_id` keeps what the file said. |
| `dq_rule`, `dq_run`, `dq_result` | rule catalogue, one evaluation, one finding | `dq_rule` is synchronised from `rules/dq_rules.yaml` by every `ifc-sql dq`; a removed rule that findings refer to stays, with `retired_at` set. Results are kept per run so quality can be compared over time. `dq_result.severity` records the severity at run time ([data-quality.md](data-quality.md)). |

### How the Revit quirks map onto the schema

[revit-ifc-quirks.md](revit-ifc-quirks.md) shows how often each quirk occurs in the four
Duplex files and how the extract and transform stages handle it.

| Quirk in Revit exports | Where the schema handles it |
|---|---|
| Revit 2011 and 2013: no `Qto_*` base quantities; quantities sit in `PSet_Revit_Dimensions`. Revit 2019 to 2022: `Qto_*BaseQuantities` (IFC4) or one `BaseQuantities` set (IFC2X3) | `quantity.source` (`revit_pset` or `ifc_qto`), alongside `geometry` rows for the same object; an IFC2X3 `BaseQuantities` set is read as the class's `Qto_` set ([ADR 8](adr/0008-ifc-base-quantities.md)). The raw values also stay in `property`. |
| Names follow `Family:Type:ElementId`; loadable families add an instance label (`Family:Type:Label:ElementId`), and openings and stair parts add a `:n` suffix to their host's name | `object.name` keeps the raw string. `element.revit_family`, `revit_type` and `revit_element_id` hold the parsed parts. `revit_element_id` is indexed but deliberately not unique. A feature element (`is_feature`: an opening, void or projection) is named after the element it cuts, so its parts go to `host_revit_family` and `host_revit_type` instead, and its own `revit_family` and `revit_type` are NULL (two CHECKs keep it so): grouping by Revit type then counts walls, not walls and their openings. |
| Revit 2019 and 2021 write quantity-set values that cannot be measurements: a wall's `NetSideArea` twice, unscaled first (10⁶ too large); a slab's `NetArea` as the whole solid's surface; space volumes 1,000 times too large; perimeters in feet labelled millimetres | `property.is_implausible`: the value is stored as written, but a physical bound of its set flags it, it becomes no `quantity`, and an `implausible_quantity` finding names the bound. A value written twice keeps the one that respects the bounds ([ADR 7](adr/0007-quantity-set-plausibility.md)). |
| A door's and a window's size (`OverallWidth`, `OverallHeight`) and how it opens (`OperationType`) are entity attributes; lining and panel data sit in predefined property sets (`IfcDoorLiningProperties`...) | Stored as properties of a set named after the declaring entity (`IfcDoor.OverallWidth`) or the predefined set's class (`IfcDoorPanelProperties.PanelOperation`), typed and converted by the schema's declaration. The overall size also becomes `width_m` and `height_m` with source `ifc_attribute`, ranked after `revit_pset` (audit finding A4). |
| Walls have no `IfcTypeObject`; type data arrives as `PSet_Revit_Type_*` on the instance | `property.is_type_property`, set for those sets and for values inherited from a type object. |
| Placeholder values (`Manufacturer = 'Manufacturer'`), empty strings | Stored verbatim in `property.value_text` so data-quality rules can find them; nothing rejects them. |
| Layered walls (`IfcMaterialLayerSetUsage`) | `element_material_layer` keeps layer order and thickness; `v_takeoff` splits area and volume per layer. |
| OmniClass Table 13 as a parameter value | Stored as a `classification` row (system `OmniClass Table 13`), and read by `v_space_schedule`. |
| `IfcWallStandardCase` versus `IfcWall` | `object.ifc_class` keeps the exact entity; `ifc_class_base` holds the normalised one for filtering. |
| Storey elevations with float noise (`3.100000000000378`) | `spatial_element.elevation_m` is `numeric(9,3)`. The column itself rounds to the millimetre, so storeys compare equal across models. |

Looking at the four pinned Duplex files turned up a few more facts that the schema
accommodates:

- **Units differ by file.** The files declare metres, millimetres and feet, so every
  `*_m` column is converted at ingest and `model.length_unit` records what the file
  declared.
- **Furniture sits in rooms.** The architectural model contains its furniture in
  spaces, not storeys, hence `container_id` plus a resolved `storey_id`.
- **Material lists exist.** The electrical and plumbing files use `IfcMaterialList`
  and single `IfcMaterial`, so `thickness_m` is nullable.
- **Classifications appear two ways.** The electrical and plumbing files carry
  UniFormat `IfcClassificationReference`s, while OmniClass arrives as a Revit
  parameter.
- **Project GlobalIds don't match.** The architectural model's `IfcProject` GlobalId
  differs from the other three files, so `project_global_id` is stored but cannot
  match models. `v_storey_alignment` matches the disciplines by project name instead.
- **Quantity sets coexist with Revit parameters.** Spaces carry a `GSA Space Areas`
  quantity set (an `IfcElementQuantity`) next to the Revit `Area` parameter, which is
  why a quantity can have more than one source.

## Conventions

- **Keys.** Entity tables (`model`, `object`, `material`, runs, results) have a
  `bigint GENERATED ALWAYS AS IDENTITY` key named `<table>_id`. Dependent attribute
  tables (`property`, `quantity`, `classification`, `element_material_layer`) use their
  natural composite key, because nothing references their rows individually.
  `ingest_issue` has no natural key, so it is keyed by its position in the pipeline's
  deterministic output, `(model_id, issue_index)`. Foreign
  key columns are named after the key they reference, or after their role (`parent_id`,
  `storey_id`, `container_id`).
- **Constraint names** follow one convention (`pk_`, `fk_`, `uq_`, `ix_`, `ck_<table>_…`).
  This lets tests assert that the intended constraint rejected a row.
- **Units** are SI and named in the column (`elevation_m`, `thickness_m`) or in the
  quantity name (`area_m2`).
- **NULL means absent.** Identifier-like text columns reject blank strings
  (`CHECK (col ~ '\S')`). Property values are the exception: they keep what the file says.
- **Finite numbers.** `float8` accepts `NaN` and `±Infinity`, and `NaN` would poison
  `SUM()`. Float columns therefore carry `CHECK (x > '-Infinity' AND x < 'Infinity')`, or
  `x >= 0 AND x < 'Infinity'` for quantities and thicknesses. PostgreSQL sorts `NaN` above
  every other value, so the upper bound rejects it too.
- **Deletes cascade from `model`.** One `DELETE FROM ifc.model WHERE model_id = …`
  removes everything loaded from a file. Run history survives: `ingestion_run.model_id`
  becomes NULL.
- **Sideways references are checked at commit.** These are `parent_id`, `container_id`,
  `storey_id` and `element_material_layer.material_id`, which point between rows of the
  same model. PostgreSQL runs each cascade step as a separate internal statement, so an
  immediate check can see a half-deleted model and fail. These four foreign keys are
  therefore `DEFERRABLE INITIALLY DEFERRED`. A storey or material that is still in use
  cannot be deleted on its own (the commit fails), but a whole model can.
  `test_deleting_a_model_removes_everything_loaded_from_it` failed before this change.
- **What the database does not enforce** is left to the loader (`ingest/load.py`), which
  writes one model in one transaction:
  - an object has exactly one subtype row, matching `object_kind`;
  - `storey_id` points at a storey;
  - every row of an element's graph belongs to the same model.

  Each of these would need triggers or composite keys, which is more machinery than the
  risk warrants.

## Views

Each view is a versioned file (`v_element.v1.sql`) that also carries its `COMMENT`s.
`v_element` is at version 3. Migration 0004 corrected its `revit_type` comment (and the
column's), which wrongly said the type may contain colons; migration 0010 added
`is_feature`, `host_revit_family` and `host_revit_type` (audit finding A3) and moved the
name parts of openings in models already loaded.

| View | One row per | What it adds |
|---|---|---|
| `v_quantity` | object and quantity | Picks one value when several sources supply it, in the order `ifc_qto`, then `revit_pset`, then `geometry`: an IFC quantity set written by the exporter, then Revit's own parameter, then a value computed here. The other views read quantities through it. |
| `v_element` | element | Identity, Revit name parts (a feature element's are its host's, in `host_revit_*`), `is_feature`, storey and elevation, the containing room, the six canonical quantities as columns (`width_m` since v4), and materials in layer order (`Plasterboard / Metal - Stud Layer / Plasterboard`). Openings are rows too: filter `NOT is_feature` to count building elements. |
| `v_space_schedule` | space | Number, name, storey, area, perimeter, volume, height, the OmniClass Table 13 code and title, and `duplicate_of` (v2): an earlier space with the same number, name, storey and floor area, as a model exporting both Revit Rooms and MEP Spaces writes. |
| `v_takeoff` | model × storey × `ifc_class_base` × material | Element count and summed length, area and volume. A layered material covers the element's face once per layer, so its area is element area × layer count and its volume is element area × total thickness. Plasterboard on both faces of a 10 m² wall is 20 m² and 0.32 m³. Materials without layers take the element's own quantities. |
| `v_model_summary` | model | File and project metadata, row counts, and the findings of the latest successful data-quality run. The finding counts are NULL, not 0, for a model never checked. |
| `v_dq_finding` | finding of each model's latest successful data-quality run | The rule's dimension and scope (model content or loader coverage, v2) and the object's GlobalId, class and name next to each finding, for the run `v_model_summary` counts ([data-quality.md](data-quality.md#reading-the-findings)). |
| `v_storey_alignment` | storey and model, for models that share a project name | The storey matched across disciplines on a normalised name, each model's elevation, how many of the project's models have the storey, and the spread of their elevations ([below](#storeys-across-disciplines)). |

`tests/integration/test_views.py` builds a small building that reproduces these cases
and checks each figure. `tests/integration/test_load.py` loads the synthetic fixture and
checks the views on it, and `tests/samples/test_duplex_ingest.py` checks
`v_storey_alignment` on the four Duplex files.

### Storeys across disciplines

Discipline models of one building are exported separately, and each can define its own
levels. `v_storey_alignment` puts them side by side:

- **Same project** means the same `model.project_name` (`IfcProject.Name`, Revit's
  Project Number), compared exactly. The project GlobalId cannot be used because ARC's
  differs from the other three. Models without a project name are left out.
- **Same storey** means the same `storey_key`: the `IfcBuildingStorey` name with every
  run of whitespace replaced by one space, trimmed and lower-cased. `'Level 2'`,
  `' LEVEL  2'` and `'level\t2'` match. `'Level 02'`, `'L2'` and a storey at the same
  elevation under another name do not: guessing those would hide exactly the
  inconsistencies the view is for. Unnamed storeys are left out.
- **One row per storey and model**, with the model's own name and elevation, plus
  `storey_model_count` (how many of the project's models have this key),
  `project_model_count` and `elevation_spread_m` (highest minus lowest elevation). A
  storey lines up when the counts are equal and the spread is 0. Every loaded version of
  a model counts as a model, so load one version per discipline to compare disciplines.

On the four Duplex files, after `make ingest-samples` (`make psql`):

```sql
SELECT storey_key, discipline, storey_name, elevation_m,
       storey_model_count AS models_with_it, project_model_count AS models,
       elevation_spread_m AS spread_m
FROM ifc.v_storey_alignment WHERE project_name = '0001'
ORDER BY elevation_m, storey_key, discipline;
```

```
 storey_key | discipline | storey_name | elevation_m | models_with_it | models | spread_m
------------+------------+-------------+-------------+----------------+--------+----------
 t/fdn      | ARC        | T/FDN       |      -1.250 |              1 |      4 |    0.000
 level 1    | ARC        | Level 1     |       0.000 |              4 |      4 |    0.000
 level 1    | ELE        | Level 1     |       0.000 |              4 |      4 |    0.000
 level 1    | MEP        | Level 1     |       0.000 |              4 |      4 |    0.000
 level 1    | PLU        | Level 1     |       0.000 |              4 |      4 |    0.000
 level 2    | ARC        | Level 2     |       3.100 |              4 |      4 |    0.000
 level 2    | ELE        | Level 2     |       3.100 |              4 |      4 |    0.000
 level 2    | MEP        | Level 2     |       3.100 |              4 |      4 |    0.000
 level 2    | PLU        | Level 2     |       3.100 |              4 |      4 |    0.000
 roof       | ARC        | Roof        |       6.000 |              4 |      4 |    0.000
 roof       | ELE        | Roof        |       6.000 |              4 |      4 |    0.000
 roof       | MEP        | Roof        |       6.000 |              4 |      4 |    0.000
 roof       | PLU        | Roof        |       6.000 |              4 |      4 |    0.000
(13 rows)
```

Level 2 is 3.100 m in all four files although they declare metres, feet and
millimetres. The foundation level exists only in the architectural model. The
mismatches are one `WHERE` away:

```sql
SELECT DISTINCT storey_key FROM ifc.v_storey_alignment
WHERE storey_model_count < project_model_count OR elevation_spread_m <> 0;
-- t/fdn
```

## Loading

```bash
make ingest-samples                          # make samples + make migrate, then the four files
uv run ifc-sql ingest path/to/model.ifc --discipline ARC
```

`ifc-sql ingest` loads one file through `ingest/pipeline.py`. The steps, and why they
come in this order, are in [ADR 4](adr/0004-idempotent-ingest.md):

1. Refuse an invalid discipline (two to eight capital letters, the CHECK on
   `model.discipline`) and a database that is not at the latest migration.
2. Hash the file. If a model has the same SHA-256, record a `skipped` run and stop,
   without parsing.
3. Record a `running` run and commit it.
4. Extract and transform, outside any transaction.
5. In one transaction, insert the model with every row that belongs to it, including
   its `ingest_issue` rows, and mark the run `succeeded` with `row_counts`.
6. On an error the load has rolled back, so nothing of the model exists. The run is
   marked `failed`, with the error.

| Outcome | Run | Exit | Output |
|---|---|---|---|
| Loaded | `succeeded` | 0 | `loaded Duplex_MEP_20110907.ifc as model 2 (MEP): 973 objects, 63,683 property values, 46 issues (run 2)` |
| Already loaded | `skipped` | 0 | `Duplex_MEP_20110907.ifc is already loaded as model 2 (MEP); nothing written (run 6, skipped)` |
| Loaded meanwhile by a concurrent ingest | `skipped` | 0 | `<file> was loaded as model <id> (<discipline>) by another ingest running at the same time; nothing written (run <id>, skipped)` |
| Unreadable file | `failed` | 1 | `ingest of corrupt.ifc failed (run 9): ExtractError: corrupt.ifc: not a readable IFC file (Unable to parse IFC SPF header)` |
| Truncated file | `failed` | 1 | `ingest of truncated.ifc failed (run 10): ExtractError: truncated.ifc: the file ends before END-ISO-10303-21; (truncated)` |
| Database unreachable | none | 1 | `cannot reach the database at postgresql+psycopg://ifc:***@127.0.0.1:1/ifc: connection failed: …` (the password is masked) |
| Database not at the latest migration | none | 1 | ``the database at postgresql+psycopg://ifc:***@localhost:5432/ifc_empty is not migrated, but ingest needs revision 0009: run `ifc-sql migrate` first`` |
| No such file; missing or invalid `--discipline` | none | 2 | Usage error, e.g. `Invalid value for '--discipline': discipline must be 2 to 8 capital letters, e.g. ARC; got 'arc'` |

The outputs are from runs of the command in this container, except the concurrent one,
whose wording is checked by `tests/integration/test_cli_ingest.py`. An empty file and a
database error during the load also end as `failed` runs.

The summary goes to stdout and errors to stderr. Logs go to stderr as `key=value` lines:
one per stage with its duration, and one per issue code. These are the MEP lines from
`make ingest-samples`, without the timestamp and logger fields:

```
msg=extracted run_id=2 objects=973 seconds=1.463
msg=transformed run_id=2 properties=63683 issues=46 seconds=0.473
msg="transform issues" run_id=2 code=conflicting_property count=2
msg="transform issues" run_id=2 code=quantity_not_a_measure count=44
msg=loaded run_id=2 model_id=2 rows=66868 seconds=1.358
```

### How rows are inserted

`ingest/load.py` writes one model in the caller's transaction. Records carry natural
keys (GlobalId, material name), so the loader needs generated ids before it can write
the rows that reference them:

- **INSERT … RETURNING** for `model`, `object` and `material`. Each row comes back with
  its natural key and its new id, so the mapping never depends on the order of the
  returned rows.
- **COPY** for the other seven tables, through the psycopg connection under the
  SQLAlchemy one, in the same transaction.
- References within the model (`parent_id`, `container_id`, `storey_id`,
  `material_id`) are deferred to commit, so the order of rows does not matter. The
  tests run `SET CONSTRAINTS ALL IMMEDIATE` to check them inside a transaction they roll
  back.

COPY was chosen by measurement on the largest table of the samples, MEP's 63,683
property rows. `make bench-load` (`scripts/bench_load.py`) runs each variant five times
in a throwaway database, in transactions that are rolled back. One run in this
development container printed:

| Measurement | Median (s) |
|---|---:|
| `load()`, whole model, with the checks COMMIT runs | 1.47 |
| property rows: SQLAlchemy Core executemany | 3.50 |
| property rows: SQLAlchemy insertmanyvalues (RETURNING) | 6.56 |
| property rows: COPY (`load.copy_rows`) | 1.29 |
| property rows: COPY, table without its foreign key | 0.40 |
| property rows: COPY, table without constraints or indexes | 0.08 |

- **COPY wins: 1.29 s against 3.50 s.** With psycopg 3, SQLAlchemy's executemany hands
  the rows to psycopg's own `executemany`, which sends one INSERT per row in pipeline
  mode. SQLAlchemy switches to multi-row INSERTs (insertmanyvalues) only for statements
  with RETURNING, and that variant was the slowest here.
- **The integrity rules account for most of what remains.** Dropping the property
  table's foreign key cuts COPY to 0.40 s, so most of its time is PostgreSQL checking
  each row's `object_id`. Dropping every constraint and index leaves 0.08 s to move the
  bytes. Those checks are the point of the schema (ADR 1), so they stay.

Timings for the four files come from one `make ingest-samples` run on an empty
database, in the same container. The stage times are the logged `seconds`; the run time
is `finished_at - started_at` in `ingestion_run`:

| File | Rows written | Extract (s) | Transform (s) | Load (s) | Run (s) |
|---|---:|---:|---:|---:|---:|
| `Duplex_A_20110907.ifc` | 14,899 | 0.31 | 0.07 | 0.34 | 0.72 |
| `Duplex_MEP_20110907.ifc` | 66,868 | 1.46 | 0.47 | 1.36 | 3.28 |
| `Duplex_Electrical_20121207.ifc` | 7,335 | 0.17 | 0.04 | 0.18 | 0.38 |
| `Duplex_Plumbing_20121113.ifc` | 43,714 | 1.55 | 0.28 | 0.97 | 2.79 |

A second `make ingest-samples` writes four `skipped` runs (5 to 8) and nothing else.
`tests/samples/test_duplex_ingest.py` checks that every table holds exactly one row
per record the transform produced, for each file.

### Quantities after loading

`v_quantity` has two sources to choose from only for the space areas: the GSA quantity
set and the Revit parameter ([ADR 3](adr/0003-quantity-sources-and-precedence.md)).
After `make ingest-samples`:

```sql
SELECT q.name, count(DISTINCT q.object_id) AS objects,
       max(abs(a.value - b.value)) AS largest_difference
FROM ifc.quantity AS q
JOIN ifc.quantity AS a ON a.object_id = q.object_id AND a.name = q.name AND a.source = 'ifc_qto'
JOIN ifc.quantity AS b ON b.object_id = q.object_id AND b.name = q.name AND b.source = 'revit_pset'
GROUP BY q.name;
--  name   | objects |   largest_difference
-- area_m2 |      65 | 1.0658141036401503e-14

SELECT source, count(*) FROM ifc.v_quantity GROUP BY source;
-- ifc_qto 65, revit_pset 1182; no geometry rows exist
```

### Issues after loading

```sql
SELECT m.discipline, i.code, count(*) AS issues
FROM ifc.ingest_issue AS i JOIN ifc.model AS m USING (model_id)
GROUP BY m.discipline, i.code ORDER BY m.discipline, i.code;
```

```
 discipline |                code                 | issues
------------+-------------------------------------+--------
 ELE        | conflicting_property                |      2
 ELE        | incomplete_classification_reference |     85
 ELE        | omniclass_unparsed                  |      1
 MEP        | conflicting_property                |      2
 MEP        | quantity_not_a_measure              |     44
 PLU        | conflicting_property                |    225
 PLU        | incomplete_classification_reference |    263
 PLU        | omniclass_unparsed                  |      1
 PLU        | quantity_not_a_measure              |     61
```

These are the counts in [revit-ifc-quirks.md](revit-ifc-quirks.md), now stored, and
`ifc-sql dq` turns each into a finding ([data-quality.md](data-quality.md)). Each
row names its object and says what happened, for example
`PSet_Revit_Dimensions.Length (IfcReal) is not a measure in m; no length_m` for the MEP
fitting `M_Transition - Generic:Transition - Generic:Transition - Generic:550098`.

## The read-only role

`ifc_reader` is the role the MCP server connects as, for every tool and at startup
([ADR 6](adr/0006-mcp-server.md)). Migration 0003 creates it, and 0009 adjusts it, with
these layers:

1. **Privileges, the actual boundary:** `USAGE` on schema `ifc` and `SELECT` on its
   tables and views. `ALTER DEFAULT PRIVILEGES` extends the grant to tables and views
   that later migrations create. Migration 0009 adds `SELECT` on
   `public.alembic_version`, so that `ifc-sql serve` can check the revision without the
   owner's password, and revokes `TEMPORARY` on the database from `PUBLIC`: PostgreSQL
   grants it to every role, and `ifc_reader` could otherwise create and fill temporary
   tables in a read-write transaction. Beyond that, the role has only what PostgreSQL
   gives every role: it can read the system catalogues and execute most built-in
   functions, which is why `run_sql` restricts both. It can read no other table, use no
   sequence and call no file function. `REVOKE CREATE ON SCHEMA public FROM PUBLIC`
   repeats the PostgreSQL 15+ default for databases initialised by older versions.
2. **Session defaults, guard rails on top:** per database (`ALTER ROLE … IN DATABASE`),
   `default_transaction_read_only = on`, `statement_timeout = 5s`,
   `idle_in_transaction_session_timeout = 30s`, and `search_path = ifc`, so an LLM can
   write `FROM v_element`. A client can override these with `SET`, which is why the
   tests check the privilege boundary inside an explicitly read-write transaction.
3. **Role attributes:** `LOGIN`, `CONNECTION LIMIT 20`, and none of `SUPERUSER`,
   `CREATEDB`, `CREATEROLE`, `REPLICATION` or `BYPASSRLS`. If a role of that name already
   exists with any of those, or is a member of another role, the migration refuses to
   continue rather than calling it read-only.

The MCP server adds its own layers on top
([ADR 6](adr/0006-mcp-server.md#2-one-role-the-server-connects-only-as-ifc_reader)):

4. **A session per call, one transaction each:** no connection pool, so nothing a call
   leaves in its session reaches the next; every transaction begins
   `REPEATABLE READ READ ONLY` from the client, sets `statement_timeout` (5 s),
   `lock_timeout` (1 s), `search_path` and `jit` with `SET LOCAL`, and is rolled back.
5. **For `run_sql`**, a sqlglot validator that refuses anything but one read-only query
   on `ifc` with allowed functions, and a cursor (`DECLARE … CURSOR`) that PostgreSQL
   accepts only for a single SELECT or VALUES.

`tests/integration/test_privileges.py` checks the role, and
`tests/integration/test_run_sql.py` each layer on its own.

**Password.** The password comes from `IFC_SQL_READER_PASSWORD`. Its dev-only default
is `ifc-reader-dev-only`, the same kind of public default as the development database
URL. The migration hashes it on the client into the SCRAM verifier PostgreSQL stores,
the same way `psql`'s `\password` works. The plaintext never reaches the server, so it
cannot appear in server logs even with `log_statement = 'all'`. Clients build their URL
with `reader_database_url()`, which keeps host, port and database from
`IFC_SQL_DATABASE_URL`. To rotate the password, run `ALTER ROLE ifc_reader PASSWORD …`
(or `\password ifc_reader`) as the owner and update the variable. Migrations set it
only when 0003 runs.

**Roles are cluster-wide; databases are not.** The development database and the
throwaway test databases share one server, and therefore one `ifc_reader`. So:

- **Upgrade** creates the role only if it is missing. It catches `duplicate_object`
  instead of checking first, which also covers two databases being migrated at the same
  moment. It then reapplies the login settings, password and this database's grants.
  Running it in a second database is harmless.
- **Downgrade** revokes everything this database granted, resets this database's role
  settings and keeps the role. Another database on the cluster may still rely on it,
  and PostgreSQL would refuse to drop a role that holds privileges elsewhere. On a
  cluster with no such database, `DROP ROLE ifc_reader` afterwards removes it;
  `make reset-db` removes it with everything else.

## Migrations

- **One transaction.** All pending migrations run in a single transaction (PostgreSQL
  DDL is transactional), so a failed upgrade changes nothing.
- **Frozen once released.** A migration never imports `schema.py`. A view file, once a
  migration uses it, is never edited: a changed view becomes `v_element.v2.sql` plus a
  new migration that drops `v1` and creates `v2`, and its downgrade restores `v1`. An old
  migration therefore always produces the same objects.
- **Configured in code.** The database URL always comes from `IFC_SQL_DATABASE_URL`;
  there is no `alembic.ini` with credentials in it. `ifc-sql migrate` builds the
  Alembic configuration in code, so an installed wheel can migrate a database.
  `[tool.alembic]` in `pyproject.toml` lets the plain CLI work for development
  (`uv run alembic history`).
- **`search_path` is pinned to `public`** on migration connections. The default path
  starts with `"$user"`, and the development role is called `ifc`, like the schema.
  Without the pin, Alembic's version table could land inside `ifc`, and an autogenerate
  comparison would skip `ifc` as "the default schema".
- **Offline (`--sql`) mode is refused,** because migration 0003 needs a live connection
  to hash the password.

**Writing a new migration.** Change `schema.py`, then run
`uv run alembic revision --autogenerate --rev-id 0010 -m "…"` against a migrated
database. Review the result, then run `make check`.

These tests keep the migrations honest:

| Test | Guards against |
|---|---|
| `test_schema_py_matches_migrated_database` | `schema.py` and the migrations drifting apart. It covers columns, types, nullability, defaults, comments, keys, foreign-key options and indexes. |
| `test_database_has_exactly_the_constraints_in_schema_py` | A missing or extra CHECK constraint, which autogenerate does not compare. |
| `test_every_revision_downgrades_and_upgrades_again` | A broken downgrade at any single step. |
| `test_upgrade_downgrade_upgrade_round_trip` | Leftovers after a full downgrade, and a failed re-upgrade with the role already present. |
| `test_every_table_view_and_column_has_a_comment` | Undocumented additions (the `ifc://schema` resource depends on comments). |
| `test_every_foreign_key_is_backed_by_an_index` | Unindexed foreign keys, which make joins and cascading deletes scan whole tables. |
| `test_view_files_are_versioned_and_each_is_used_by_a_migration` | Unversioned or orphaned view files. |

The integration tests never use the development database's contents. Each session
creates `<db>_test` and a few other throwaway databases on the same server and migrates
them from scratch. `make up && make test` therefore works on a fresh clone, and a test
cannot depend on data a developer has loaded.
