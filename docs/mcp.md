# The MCP server

`ifc-sql serve` serves the loaded models to an LLM over the
[Model Context Protocol](https://modelcontextprotocol.io): eight read-only tools, two
resources and a prompt. An MCP client (Claude Desktop, Claude Code, …) starts it and
talks to it over stdin and stdout. It connects to PostgreSQL only as the read-only role
`ifc_reader`. The design, and the alternatives that were rejected, are in
[ADR 6](adr/0006-mcp-server.md); the threat model is in [SECURITY.md](../SECURITY.md).

```bash
make dq-samples     # load and check the four Duplex files (once)
make smoke          # start the server as a client would and call a few tools
uv run ifc-sql serve
```

## Contents

- [Running it](#running-it)
- [Registering it with a client](#registering-it-with-a-client)
- [Tools](#tools): [list_models](#list_models), [list_storeys](#list_storeys),
  [find_elements](#find_elements), [get_element](#get_element),
  [quantity_takeoff](#quantity_takeoff), [space_schedule](#space_schedule),
  [data_quality_report](#data_quality_report), [run_sql](#run_sql)
- [Resources](#resources) and [the prompt](#the-model_briefing-prompt)
- [Errors](#errors), [limits](#limits), [logging](#logging) and [tests](#tests)

## Running it

`ifc-sql serve` reads the same environment variables as the other commands
([`.env.example`](../.env.example)):

| Variable | Used for |
|---|---|
| `IFC_SQL_DATABASE_URL` | Host, port and database. Its user and password are **not** used: the server connects as `ifc_reader`, so `postgresql+psycopg://localhost:5432/ifc` is enough. |
| `IFC_SQL_READER_PASSWORD` | The password of `ifc_reader`, set by `ifc-sql migrate` (dev default `ifc-reader-dev-only`). |
| `IFC_SQL_LOG_LEVEL` | `INFO` by default; `DEBUG` also logs the text of every `run_sql` query ([Logging](#logging)). |

Before it reads the first message, the server checks the database, and refuses to start
if it cannot use it (ADR 6, decision 3). These are the outcomes, from runs in this
container:

| Situation | Exit | stderr |
|---|---|---|
| Ready | 0 when the client closes stdin | `msg=serving database=postgresql+psycopg://ifc_reader:***@localhost:5432/ifc transport=stdio` |
| Database unreachable | 1 | `cannot reach the database at postgresql+psycopg://ifc_reader:***@127.0.0.1:1/ifc: connection failed: connection to server at "127.0.0.1", port 1 failed: Connection refused …` |
| Wrong reader password | 1 | `cannot reach the database at postgresql+psycopg://ifc_reader:***@localhost:5432/ifc: connection failed: … FATAL:  password authentication failed for user "ifc_reader"` |
| Not migrated | 1 | ``the database at postgresql+psycopg://ifc_reader:***@localhost:5432/ifc_empty is not migrated, but serve needs revision 0009: run `ifc-sql migrate` first`` |
| Migrated before 0009 | 1 | ``the database at postgresql+psycopg://ifc_reader:***@localhost:5432/ifc_0008 is at a revision before 0009, whose version table ifc_reader may not read, but serve needs revision 0009: run `ifc-sql migrate` first`` |

stdout carries nothing but JSON-RPC messages; every log line goes to stderr.
`make serve` runs the same command on your terminal (it waits for JSON-RPC on stdin).
Register the `uv` command below with a client, not `make`.

`make smoke` (`scripts/mcp_smoke.py`) starts the server over stdio with the SDK's
client, as an MCP client would, and checks every model. On the four Duplex files:

```
connected to ifc-sql-mcp 0.1.0 over stdio (protocol 2026-07-28): 8 tools, 1 resource, 1 resource template, 1 prompt
list_models: 4 models
  model 1 ARC Duplex_A_20110907.ifc: elements 268, spaces 21; dq run 1: 0 error, 7,650 warning, 473 info
  model 2 MEP Duplex_MEP_20110907.ifc: elements 926, spaces 42; dq run 2: 0 error, 30,013 warning, 1,961 info
  model 3 ELE Duplex_Electrical_20121207.ifc: elements 103, spaces 1; dq run 3: 0 error, 89 warning, 3,291 info
  model 4 PLU Duplex_Plumbing_20121113.ifc: elements 498, spaces 1; dq run 4: 0 error, 1,471 warning, 13,223 info
run_sql: SELECT discipline, count(*) AS elements FROM v_element GROUP BY 1 ORDER BY 1
  ['ARC', 268]
  ['ELE', 103]
  ['MEP', 926]
  ['PLU', 498]
ifc://schema: 23,307 characters
```

## Registering it with a client

Both clients start the server themselves. Give them absolute paths: the client's `PATH`
may not contain `uv`, and the server must run from the repository (`uv run --directory`).
Replace `/path/to/uv` with the output of `which uv`, and `/path/to/Ifc_Sql_Mcp` with
your checkout. The reader password is the only secret the server needs.

### Claude Code

Following [Claude Code's MCP documentation](https://code.claude.com/docs/en/mcp)
(`claude mcp add [options] <name> -- <command> [args...]`; at least one other option must
come between `--env` and the server name):

```bash
claude mcp add --env IFC_SQL_DATABASE_URL=postgresql+psycopg://localhost:5432/ifc \
  --env IFC_SQL_READER_PASSWORD=ifc-reader-dev-only \
  --transport stdio ifc-sql -- /path/to/uv run --directory /path/to/Ifc_Sql_Mcp ifc-sql serve
claude mcp list        # shows ✔ Connected, or ✘ Failed to connect
```

`--scope local` (the default) keeps the server to you in the current project, `user` to
you in every project, and `project` writes it to `.mcp.json` in the project, to be
shared through version control. A shared `.mcp.json` can take the password from the
environment, since Claude Code expands `${VAR}` there:

```json
{
  "mcpServers": {
    "ifc-sql": {
      "command": "/path/to/uv",
      "args": ["run", "--directory", "/path/to/Ifc_Sql_Mcp", "ifc-sql", "serve"],
      "env": {
        "IFC_SQL_DATABASE_URL": "postgresql+psycopg://localhost:5432/ifc",
        "IFC_SQL_READER_PASSWORD": "${IFC_SQL_READER_PASSWORD}"
      }
    }
  }
}
```

Claude Code warns when a tool's output passes 10,000 tokens, and saves a text result of
more than 50,000 characters to a file instead of showing it. The results here are built
to stay below that: on the Duplex files, a full page of 100 MEP elements is 29,753
characters and `get_element` on a wall 14,902, and `run_sql` stops at 30,000 characters
of rows.

### Claude Desktop

Following [Connect to local MCP servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers):
open the Claude menu, Settings…, Developer, Edit Config. That opens
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or
`%APPDATA%\Claude\claude_desktop_config.json` on Windows. Add:

```json
{
  "mcpServers": {
    "ifc-sql": {
      "command": "/path/to/uv",
      "args": ["run", "--directory", "/path/to/Ifc_Sql_Mcp", "ifc-sql", "serve"],
      "env": {
        "IFC_SQL_DATABASE_URL": "postgresql+psycopg://localhost:5432/ifc",
        "IFC_SQL_READER_PASSWORD": "ifc-reader-dev-only"
      }
    }
  }
}
```

Then quit Claude Desktop completely and start it again. The server's stderr, with the
startup error if there is one and a line per tool call, is written to
`mcp-server-ifc-sql.log` in `~/Library/Logs/Claude` (macOS) or `%APPDATA%\Claude\logs`
(Windows).

## Tools

Every tool is annotated read-only, idempotent and closed-world. A result is a JSON
object, sent as structured content (validated against the tool's output schema) and as
the same JSON, compact, in a text block. Lengths, areas and volumes are in m, m2 and m3,
whatever unit the file declared, and are returned as stored (Revit's
`2.508000000000034` stays as it is). Text from an IFC file is returned as data only and
cut at 2,000 characters ([Limits](#limits)).

The examples are calls on the four Duplex files after `make dq-samples`, pretty-printed
here and trimmed where marked `…`.

### list_models

No parameters. Every loaded model, oldest first: id, file, discipline, IFC schema,
authoring application, project, the length unit the file declared, counts, and the
status of its latest data quality check. At most 200 models.

```jsonc
// list_models {}  — models[1] of 4
{
  "model_id": 2,
  "file_name": "Duplex_MEP_20110907.ifc",
  "discipline": "MEP",
  "ifc_schema": "IFC2X3",
  "mvd": "CoordinationView",
  "authoring_app": "Autodesk Revit MEP 2011",
  "project_name": "0001",
  "project_long_name": "Duplex Apartment",
  "length_unit": "m",
  "ingested_at": "2026-11-17T19:51:54.616708Z",
  "storeys": 3, "spaces": 42, "elements": 926,
  "property_values": 63683, "quantities": 643, "materials": 0,
  "data_quality": {"status": "checked", "dq_run_id": 2,
                   "finished_at": "2026-11-17T19:52:09.538699Z",
                   "errors": 0, "warnings": 30013, "info": 1961}
}
```

A model never checked has `"status": "never_checked"` and null counts, never zeros.

### list_storeys

| Parameter | Type | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |

The model's storeys, lowest first, with elevation in metres (millimetre precision) and
how many elements and spaces each holds, plus `elements_without_storey`. At most 200.

```jsonc
// list_storeys {"model_id": 1}
{
  "model_id": 1,
  "storeys": [
    {"storey_id": 141, "global_id": "1xS3BCk291UvhgP2dvNsgp", "name": "T/FDN",
     "long_name": null, "elevation_m": -1.25, "elements": 14, "spaces": 0},
    {"storey_id": 115, "global_id": "1xS3BCk291UvhgP2dvNMKI", "name": "Level 1",
     "long_name": null, "elevation_m": 0.0, "elements": 117, "spaces": 10},
    {"storey_id": 116, …, "name": "Level 2", "elevation_m": 3.1, "elements": 125, "spaces": 10},
    {"storey_id": 142, …, "name": "Roof", "elevation_m": 6.0, "elements": 12, "spaces": 1}
  ],
  "elements_without_storey": 0,
  "truncated": false
}
```

### find_elements

| Parameter | Type | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |
| `ifc_class` | text, 1–200 characters | exact entity (`IfcWallStandardCase`) or normalised (`IfcWall`, which also finds `IfcWallStandardCase`) |
| `storey_name` | text | a name from `list_storeys` |
| `revit_family`, `revit_type` | text | parsed from the Revit name `Family:Type:ElementId` |
| `name_contains` | text | part of the IFC name |
| `limit` | 1–100, default 25 | page size |
| `offset` | ≥ 0, default 0 | `next_offset` of the previous page |

The elements that match every filter given, in `object_id` order, one page at a time.
Matching ignores case; `name_contains` matches the characters as written (`%` and `_`
are not wildcards). Every value reaches SQL as a bind parameter.

```jsonc
// find_elements {"model_id": 1, "ifc_class": "IfcWall", "storey_name": "Level 1", "limit": 3}
{
  "model_id": 1, "total": 21, "offset": 0, "limit": 3, "next_offset": 3,
  "elements": [
    {"object_id": 23, "global_id": "0iEHWY1$XA8eQeeULq4jDb", "ifc_class": "IfcWallStandardCase",
     "name": "Basic Wall:Interior - Furring (152 mm Stud):190774",
     "revit_family": "Basic Wall", "revit_type": "Interior - Furring (152 mm Stud)",
     "storey_name": "Level 1", "space_number": null,
     "length_m": 2.508000000000034, "area_m2": 6.414525000000523, "volume_m3": 0.97500780000008},
    …
  ]
}
```

### get_element

| Parameter | Type | |
|---|---|---|
| `object_id` | integer ≥ 1 | the object's id |
| `model_id` and `global_id` | integer ≥ 1, 22 characters | instead of `object_id` |

Everything stored about one object, an element or a spatial element (a storey or a
space): identity, location (storey, room; for a spatial element its parent), the
quantities with their source, every property set (at most 500 values), classifications,
materials in layer order, and the object's findings in the latest check (at most 100).

```jsonc
// get_element {"object_id": 23}  — 14,902 characters in all
{
  "object_id": 23, "model_id": 1, "discipline": "ARC",
  "global_id": "0iEHWY1$XA8eQeeULq4jDb", "object_kind": "element",
  "ifc_class": "IfcWallStandardCase", "ifc_class_base": "IfcWall",
  "name": "Basic Wall:Interior - Furring (152 mm Stud):190774",
  "revit_family": "Basic Wall", "revit_type": "Interior - Furring (152 mm Stud)",
  "revit_element_id": 190774, "tag": "190774",
  "storey_id": 115, "storey_name": "Level 1", "storey_elevation_m": 0.0,
  "space_id": null, …,
  "quantities": [
    {"name": "area_m2", "value": 6.414525000000523, "source": "revit_pset",
     "source_ref": "PSet_Revit_Dimensions.Area"},
    …
  ],
  "property_sets": [
    {"name": "PSet_Revit_Constraints", "properties": [
      {"name": "Base Constraint", "value": "Level 1", "value_type": "IfcLabel",
       "unit": null, "from_type": false},
      {"name": "Top Offset", "value": -0.3050000000000001, "value_type": "IfcLengthMeasure",
       "unit": "m", "from_type": false},
      …]},
    …  // 10 sets, 63 values
  ],
  "properties_truncated": false,
  "classifications": [],
  "materials": [
    {"layer_index": 1, "material": "Metal - Stud Layer", "thickness_m": 0.136},
    {"layer_index": 2, "material": "Plasterboard", "thickness_m": 0.016}
  ],
  "data_quality": {"status": "checked", "dq_run_id": 1, "truncated": false, "findings": [
    {"rule_id": "value_is_property_name", "severity": "warning", "dimension": "validity",
     "message": "PSet_Revit_Other.AssetIdentifier = 'AssetIdentifier', the parameter's own name: a placeholder, not a value"},
    …  // 34 findings
  ]}
}
```

### quantity_takeoff

| Parameter | Values | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |
| `group_by` | `storey`, `ifc_class`, `revit_type`, `material` | required |
| `metric` | `count`, `length_m`, `area_m2`, `volume_m3` | required |

One metric totalled per group. Each group has `group` (the name; null for no storey,
no Revit type or no material), `group_id` (storey or material id), `elements`,
`elements_with_value` and `value`. At most 200 groups, the largest first (storeys
lowest first). What a combination means depends on the grouping:

| `group_by` | Source | `count` | `length_m`, `area_m2`, `volume_m3` |
|---|---|---|---|
| `storey` | `v_element` | elements on each storey | the elements' own quantities, each element once |
| `ifc_class` | `v_element` | elements of each normalised class | the same |
| `revit_type` | `v_element` | elements of each `Family:Type` | the same |
| `material` | `v_takeoff` | elements using each material; an element with two materials counts under both | the material's share: a layered material's area is the element's area times its number of layers, and its volume that area times the layers' thickness |

The first three are element totals, and `elements_with_value` says how many elements of
a group have the metric at all. A wall's area is one face. `material` gives material
totals, which are not element totals; the result says so in `basis` (`element` or
`material`) and `note`. On the architectural model:

```jsonc
// quantity_takeoff {"model_id": 1, "group_by": "ifc_class", "metric": "area_m2"}
{
  "model_id": 1, "group_by": "ifc_class", "metric": "area_m2", "unit": "m2",
  "basis": "element",
  "note": "Each element is counted once, with its own quantity from v_element. …",
  "groups": [
    {"group": "IfcWall", "group_id": null, "elements": 57, "elements_with_value": 56,
     "value": 587.6854224000201},
    {"group": "IfcSlab", "group_id": null, "elements": 21, "elements_with_value": 20,
     "value": 540.919223340545},
    {"group": "IfcOpeningElement", "group_id": null, "elements": 50, "elements_with_value": 4,
     "value": 531.7041439199988},
    …  // 14 groups
  ],
  "total": 2036.6936768205628,
  "truncated": false
}

// quantity_takeoff {"model_id": 1, "group_by": "material", "metric": "area_m2"}
{
  …, "basis": "material",
  "note": "From v_takeoff, which splits elements by material: … These totals are therefore not element totals …",
  "groups": [
    {"group": "Plasterboard", "group_id": 12, "elements": 63, "elements_with_value": null,
     "value": 931.9215685800265},
    {"group": "Metal - Stud Layer", "group_id": 10, "elements": 63, "elements_with_value": null,
     "value": 816.1357185800211},
    {"group": null, "group_id": null, "elements": 169, "elements_with_value": null,
     "value": 531.7041439199988},
    …  // 19 groups
  ],
  "total": 5329.195402241181
}
```

The same model: 2,037 m² by element, 5,329 m² by material. Plasterboard alone, on both
faces of the stud walls, is more than all the walls' area. `v_takeoff` does not count
the elements that lack a value, so `elements_with_value` is null by material. On MEP,
`group_by: ifc_class, metric: length_m` shows the 358 `IfcFlowFitting`s with no length
(`elements_with_value: 0`), next to 419 of 427 `IfcFlowSegment`s, 338.88 m.

### space_schedule

| Parameter | Type | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |

Every space with number, name, storey, area, perimeter, volume, height and its OmniClass
Table 13 code and title, lowest storey first, with `space_count`, `total_area_m2` and
`spaces_without_area`. At most 500 spaces.

```jsonc
// space_schedule {"model_id": 1}
{
  "model_id": 1,
  "spaces": [
    {"object_id": 8, "global_id": "0BTBFw6f90Nfh9rP1dlXrr", "number": "A101", "name": "Foyer",
     "storey_name": "Level 1", "area_m2": 17.93623674999977, "perimeter_m": 24.21599999999996,
     "volume_m3": 40.24125370199942, "height_m": 2.6,
     "omniclass_code": "13-51 24 11", "omniclass_title": "General Residential Space"},
    …  // 21 spaces
  ],
  "space_count": 21, "total_area_m2": 422.0465663999985, "spaces_without_area": 0,
  "truncated": false
}
```

### data_quality_report

| Parameter | Type | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |
| `severity` | `error`, `warning`, `info` | only rules of that severity |
| `rule_id` | text | only that rule |
| `samples_per_rule` | 0–20, default 3 | findings shown per rule |

The model's latest successful check (`ifc-sql dq`): the run and when it finished, the
findings per severity (always for the whole run), and per rule with findings: severity,
dimension, the rule's description, the number of findings and the first findings by
object id. `rules_without_findings` lists the catalogue's rules that found nothing
(including any rule added after the run). A model never checked has `status:
never_checked` and nothing else, which is not the same as no findings. Severities are
defined in [data-quality.md](data-quality.md#severity-and-dimension). The full list of
findings is the view `v_dq_finding`, through `run_sql`.

```jsonc
// data_quality_report {"model_id": 2, "samples_per_rule": 1}
{
  "model_id": 2, "status": "checked", "dq_run_id": 2,
  "finished_at": "2026-11-17T19:52:09.538699Z",
  "findings": {"error": 0, "warning": 30013, "info": 1961},
  "rules": [
    {"rule_id": "value_is_property_name", "severity": "warning", "dimension": "validity",
     "description": "A text property holds its own name, e.g. BarCode = 'BarCode'. …",
     "findings": 29967,
     "samples": [{"object_id": 296, "global_id": "0$LNoCntTAthq5oMj33Z8K",
                  "ifc_class": "IfcFlowSegment", "object_name": "Pipe Types:Waste:582956",
                  "message": "PSet_Revit_Other.AssetIdentifier = 'AssetIdentifier', the parameter's own name: a placeholder, not a value"}]},
    {"rule_id": "quantity_not_a_measure", …, "findings": 44, …},
    {"rule_id": "conflicting_property", …, "findings": 2, …},
    {"rule_id": "value_is_empty", "severity": "info", …, "findings": 1961, …}
  ],
  "rules_without_findings": ["conflicting_classification", "conflicting_quantity", …]  // 27 rules
}
```

The counts on all four files are the table in
[data-quality.md](data-quality.md#findings-on-the-four-duplex-files);
`tests/samples/test_duplex_mcp.py` checks that the report agrees with it.

### run_sql

| Parameter | Type | |
|---|---|---|
| `sql` | text, 1–20,000 characters | one PostgreSQL query |
| `max_rows` | 1–500, default 100 | |

One read-only query on schema `ifc`, for the questions the other tools do not answer.
Unqualified names resolve to `ifc` (`FROM v_element`); read
[`ifc://schema`](#resources) for the tables and columns. It returns the columns (name
and PostgreSQL type) and the rows as arrays, at most `max_rows` and at most 30,000
characters of rows; `truncated`, `truncated_by` and `note` say when there were more.

Which walls of the architectural model are external, by type:

```sql
SELECT e.revit_type, count(*) AS walls, round(sum(e.area_m2)::numeric, 2) AS area_m2
FROM v_element AS e
JOIN property AS p ON p.object_id = e.object_id
WHERE e.model_id = 1 AND e.ifc_class_base = 'IfcWall'
  AND p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'IsExternal' AND p.value_bool
GROUP BY e.revit_type
ORDER BY area_m2 DESC
```

```json
{"columns":[{"name":"revit_type","type":"text"},{"name":"walls","type":"int8"},{"name":"area_m2","type":"numeric"}],"rows":[["Exterior - Brick on Block",12,272.0],["Foundation - Concrete (417mm)",4,64.41],["Party Wall - CMU Residential Unit Dimising Wall",4,61.89],["Foundation - Concrete (435mm)",3,12.48]],"row_count":4,"truncated":false,"truncated_by":null,"values_cut":0,"note":null}
```

More rows than `max_rows` (`{"sql": "SELECT name FROM v_element WHERE model_id = 2
ORDER BY object_id", "max_rows": 3}`):

```json
{"columns":[{"name":"name","type":"text"}],"rows":[["Pipe Types:Waste:582956"],["Pipe Types:Hot Water:583239"],["Pipe Types:Hot Water:583174"]],"row_count":3,"truncated":true,"truncated_by":"max_rows","values_cut":0,"note":"The query returned more than 3 rows. Aggregate (count, sum, GROUP BY), raise max_rows (at most 500), or page with ORDER BY … LIMIT … OFFSET."}
```

**What it accepts.** One SELECT (also `WITH … SELECT`, `TABLE`, `UNION`, `INTERSECT`,
`EXCEPT`, `VALUES`) that reads only schema `ifc`, with the standard SQL functions sqlglot
recognises (count, sum, avg, round, lower, coalesce, date_trunc, string_agg, row_number,
…) and these PostgreSQL ones: `age`, `array_dims`, `array_fill`, `array_lower`,
`array_positions`, `array_replace`, `array_upper`, `cardinality`, `clock_timestamp`,
`every`, `gcd`, `generate_subscripts`, `isfinite`, `json_array_elements`,
`json_array_length`, `json_build_array`, `json_build_object`, `json_each`, `json_typeof`,
`jsonb_agg`, `jsonb_array_elements`, `jsonb_array_elements_text`, `jsonb_array_length`,
`jsonb_build_array`, `jsonb_build_object`, `jsonb_each`, `jsonb_each_text`,
`jsonb_extract_path`, `jsonb_extract_path_text`, `jsonb_object_keys`,
`jsonb_path_query`, `jsonb_pretty`, `jsonb_strip_nulls`, `jsonb_typeof`, `lcm`,
`make_date`, `num_nonnulls`, `num_nulls`, `octet_length`, `quote_ident`,
`quote_literal`, `quote_nullable`, `regexp_match`, `regexp_matches`,
`regexp_split_to_array`, `regexp_split_to_table`, `scale`, `statement_timestamp`,
`string_to_table`, `timeofday`, `to_json`, `to_jsonb`, `transaction_timestamp`,
`trim_scale`, `unistr`.

**What it refuses**, before the query reaches the database:

| Query | Reply |
|---|---|
| `DELETE FROM ifc.property` | `query refused: only a query that reads data is allowed (SELECT, WITH … SELECT, TABLE, UNION, INTERSECT, EXCEPT or VALUES); this is a DELETE statement` |
| `WITH d AS (DELETE FROM ifc.property RETURNING *) SELECT count(*) FROM d` | `query refused: the query contains DELETE: statements that change data are not allowed, also not inside WITH` |
| `SELECT pg_sleep(10)` | `query refused: function pg_sleep() is not allowed. Allowed are the standard SQL functions (…) and these PostgreSQL functions: age, array_dims, …` |
| `SELECT * FROM pg_stat_activity` | `query refused: pg_stat_activity is a PostgreSQL system catalogue; only schema ifc can be read, and the ifc://schema resource lists its tables and views` |

Also refused: more than one statement, SELECT … INTO, FOR UPDATE and the other locking
clauses, SET, RESET, SHOW, EXPLAIN, COPY, DO, CALL, transaction control, other schemas
and schema-qualified function calls ([ADR 6](adr/0006-mcp-server.md#5-run_sql-four-layers-each-tested-on-its-own-against-postgresql)
has the rules and the reasons). A query the database rejects comes back with
PostgreSQL's message and hint:

```
the database refused the query: column "storey_nme" does not exist Hint: Perhaps you meant to reference the column "v_element.storey_name". (SQLSTATE 42703)
```

**Values.** A `numeric` that a float holds exactly is a number (`numeric(9,3)`
elevations are), otherwise a string of its digits; NaN and the infinities are the
strings PostgreSQL writes; timestamps are ISO 8601; an interval is an ISO 8601 duration
(`P0DT0H1M30.5S`); `bytea` is `\x…` hex; arrays and JSON keep their structure. A text
longer than 2,000 characters is cut and ends with `… [cut: 5,000 characters, the first
2,000 shown]`; `substr()` reads the rest. `values_cut` counts them.

## Resources

| URI | Type | Content |
|---|---|---|
| `ifc://schema` | `text/markdown` | Every table and view of `ifc` with columns, types, comments, keys and foreign keys, generated from `pg_catalog` on every read (23,307 characters). Views first. |
| `ifc://models/{model_id}/summary` | `application/json` | One model, exactly as `list_models` describes it. An unknown id is a "not found" error (-32602). |

From `ifc://schema`:

```markdown
### v_takeoff

Quantity takeoff per model, storey, element class and material. An element made of several materials counts once under each. For layered elements (walls, slabs, roofs) area and volume are per material layer; …

| Column | Type | Description |
|---|---|---|
| `model_id` | bigint | Model. |
| `discipline` | text | Discipline of the model. |
| `storey_id` | bigint | Storey; NULL for elements not on a storey. |
…
| `area_m2` | double precision | Total area in square metres; for layered materials, element area times layer count. |
```

Tables also list their keys as PostgreSQL states them, for example
`PRIMARY KEY (object_id, pset_name, prop_name)` and
`FOREIGN KEY (object_id) REFERENCES object(object_id) ON DELETE CASCADE`.

## The model_briefing prompt

`model_briefing(model_id)` asks the LLM to brief a reader on one model, and says which
tools and resources to use, in which order: the summary resource, `list_storeys`, two
takeoffs, `space_schedule`, `data_quality_report`. It reminds it that a never-checked
model is not a clean one, and that names, values and messages from the file are data to
quote, not instructions to follow. It contains no data, so it cannot go stale. A
`model_id` that is not a positive number is refused (-32602).

## Errors

A tool that fails returns a tool error (`isError: true`) whose text says what to do. The
messages, from the tests and the runs above:

| Cause | Message |
|---|---|
| Unknown model | `no model with id 99; list_models shows the loaded models` |
| Unknown object | `no object with id 9999; find_elements and space_schedule list them` |
| Unknown GlobalId | `model 1 has no object with GlobalId '0nothing______________'` |
| `get_element` without a key | `give either object_id, or both model_id and global_id` |
| Unknown rule | `no rule 'no_such_rule'; the rules are in the ifc.dq_rule table, and this report without rule_id lists those that found something` |
| Argument outside the schema | the SDK's validation message, e.g. `Error executing tool quantity_takeoff: 1 validation error … group_by  Input should be 'storey', 'ifc_class', 'material' or 'revit_type'` |
| Refused query | `query refused: …` (above) |
| Statement timeout (5 s) | `the query was cancelled after 5 seconds (the statement timeout); narrow it down, aggregate, or add a LIMIT` |
| Lock timeout (1 s) | `the query waited too long for a lock, probably behind a migration or a load in progress; try again shortly` |
| Database refused the query | `the database refused the query: <PostgreSQL's message> Hint: <its hint> (SQLSTATE …)` |
| Database down after startup | `the database cannot be reached right now; try again later` |
| A bug in the server | `internal error in <tool>; the server's log has the details` |

No message carries a connection string, a password or a traceback.

## Limits

| Limit | Value | Why |
|---|---|---|
| Statement timeout | 5 s, `SET LOCAL` per call | The role's default too; a runaway query frees its session. |
| Lock timeout | 1 s | A query behind a migration gives up instead of waiting. |
| Text from an IFC file | 2,000 characters, then cut with a note | The longest real value in the Duplex files has 904. |
| `run_sql` rows | 100 by default, at most 500 | Prefer aggregates. |
| `run_sql` result | 30,000 characters of rows | Under Claude Code's 50,000-character cut-off. |
| `run_sql` query | 20,000 characters | |
| Pages and lists | 100 elements per page; 200 models, storeys or groups; 500 spaces; 500 properties and 100 findings per element; 20 samples per rule | Each list says when it was cut (`truncated`). |
| Sessions | One per call, as `ifc_reader` (`CONNECTION LIMIT 20`) | Nothing carries from one call to the next. |

Each call took tens of milliseconds on the Duplex files (`list_storeys` on MEP 0.022 s,
`get_element` 0.037 s, `find_elements` 0.030 s). The slowest is `data_quality_report`
on MEP, 0.213 s, which counts and samples 31,974 findings.

## Logging

Every call writes one `key=value` line to stderr. From the session above, without the
timestamp:

```
level=info logger=ifc_sql_mcp.server.app msg="tool call" tool=run_sql sql_sha256=58e9a8a8860a5b29 sql_chars=336 outcome=ok seconds=0.034 rows=4
level=info logger=ifc_sql_mcp.server.app msg="tool call" tool=run_sql sql_sha256=f0d94bba0817dd7d sql_chars=24 outcome=refused seconds=0.0 error="query refused: only a query that reads data is allowed (SELECT, WITH … SELECT, TABLE, UNION, INTERSECT, EXCEPT or VALUES); this is a DELETE statement"
level=info logger=ifc_sql_mcp.server.app msg="tool call" tool=run_sql sql_sha256=58d0e5bcb0e3a0aa sql_chars=32 outcome=database_error seconds=0.014 error="the database refused the query: column \"storey_nme\" does not exist Hint: Perhaps you meant to reference the column \"v_element.storey_name\". (SQLSTATE 42703)"
level=info logger=ifc_sql_mcp.server.app msg="resource read" uri=ifc://schema outcome=ok seconds=0.028 chars=23307
```

`outcome` is one of `ok`, `invalid`, `refused`, `timeout`, `database_error`,
`unavailable` and `error`. For `run_sql` the line carries a fingerprint of the query
and its length; the text itself is logged only with `IFC_SQL_LOG_LEVEL=DEBUG`, because
it can be long and carries whatever the conversation put into it (ADR 6, decision 10).
An unexpected error adds a line with the traceback. The startup check adds two lines
from Alembic (`Context impl PostgresqlImpl.`), as ingest and dq do.

## Tests

| Test | Covers |
|---|---|
| `tests/unit/server/test_sql_guard.py` | Every kind of query the validator accepts and refuses, with the reason. |
| `tests/unit/server/test_values.py`, `test_tool_results.py` | JSON conversion of each kind of value; cutting of long text, arrays and JSON. |
| `tests/unit/server/test_unreachable_database.py` | Tools and resources on a database that does not answer; a refused query needs none. |
| `tests/integration/test_mcp_protocol.py` | What a client sees, in both protocol eras: tools with input and output schemas and annotations, resources, the prompt, the schema resource against the catalogue. |
| `tests/integration/test_mcp_tools.py` | Each tool's exact output on the synthetic fixture, pagination and limits, unknown ids, invalid `group_by` and `metric`, a checked and a never-checked model. |
| `tests/integration/test_run_sql.py` | Every `run_sql` layer on its own: validator, executor, transaction (timeouts, `set_config`, a session per call), privileges; results, cuts and logging; the pinned list of functions sqlglot types. |
| `tests/integration/test_mcp_failures.py` | A bug in a tool or resource: a generic message, the traceback in the log. |
| `tests/integration/test_cli_serve.py`, `tests/unit/test_cli.py` | `ifc-sql serve` on an unreachable, unmigrated and too old database, and with a wrong password. |
| `tests/integration/test_serve_stdio.py` | `ifc-sql serve` as a subprocess, through the SDK's stdio client: nothing but protocol on stdout. |
| `tests/integration/test_mcp_smoke_script.py` | `make smoke`. |
| `tests/samples/test_duplex_mcp.py` | The tools on the four Duplex files, with the figures on this page (`make test-samples`). |
