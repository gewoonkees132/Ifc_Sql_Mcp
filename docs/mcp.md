# The MCP server

`ifc-sql serve` serves the loaded models to an LLM over the
[Model Context Protocol](https://modelcontextprotocol.io): eleven read-only tools, two
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
  [schedule](#schedule),
  [data_quality_report](#data_quality_report), [compare_models](#compare_models),
  [describe_schema](#describe_schema),
  [run_sql](#run_sql)
- [Resources](#resources) and [the prompt](#the-model_briefing-prompt)
- [Errors](#errors), [limits](#limits), [logging](#logging) and [tests](#tests)

## Running it

`ifc-sql serve` reads the same environment variables as the other commands
([`.env.example`](../.env.example)):

| Variable | Used for |
|---|---|
| `IFC_SQL_DATABASE_URL` | Host, port and database. Its user and password are **not** used: the server connects as `ifc_reader`, so `postgresql+psycopg://localhost:5432/ifc` is enough. |
| `IFC_SQL_READER_PASSWORD` | The password of `ifc_reader`, set by `ifc-sql migrate` (dev default `ifc-reader-dev-only`). |
| `IFC_SQL_DATA_DIR` | Instead of `IFC_SQL_DATABASE_URL`: the folder of the embedded PostgreSQL ([ADR 13](adr/0013-embedded-postgresql.md)). The server starts it, or attaches to the one running there, and still connects as `ifc_reader`. |
| `IFC_SQL_LOG_LEVEL` | `INFO` by default; `DEBUG` also logs the text of every `run_sql` query ([Logging](#logging)). |
| `IFC_SQL_VIEWER_FEED` | `1` (or `true`, `yes`, `on`) publishes every tool call to `ifc-sql view`'s page: one PostgreSQL notification per call, from a session of its own, which every `view` on the same database receives and lights up ([docs/viewer.md](viewer.md#the-mcp-servers-calls-the-feed)). Off by default; any login to the database can read the feed ([SECURITY.md](../SECURITY.md#the-viewers-feed-ifc_sql_viewer_feed)). With it, `run_sql`'s description tells the LLM how to make rows show in the viewer. |

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
| At an older revision, after a pull (here 0019, on the embedded database on Windows) | 1 | ``the database at postgresql+psycopg://ifc_reader:***@127.0.0.1:50920/ifc is at revision 0019, but serve needs revision 0020: run `ifc-sql migrate` first`` |

The revision the server needs is the newest the code has, so after pulling a new
version, run `ifc-sql migrate` before the client starts the server again. To go back to
a version before migration 0020, downgrade first, while the newer code is still checked
out: with `IFC_SQL_DATABASE_URL` naming the database, `uv run alembic downgrade 0019`
drops schema `ifc_viewer` and every scene in it, and leaves the models in schema `ifc` as
they are. (The plain `alembic` command reads only `IFC_SQL_DATABASE_URL`; it does not
start the embedded database.)

stdout carries nothing but JSON-RPC messages; every log line goes to stderr.
`make serve` runs the same command on your terminal (it waits for JSON-RPC on stdin).
Register the `uv` command below with a client, not `make`.

`make smoke` (`scripts/mcp_smoke.py`) starts the server over stdio with the SDK's
client, as an MCP client would, and checks every model. On the four Duplex files:

```
connected to ifc-sql-mcp 0.1.0 over stdio (protocol 2026-07-28): 11 tools, 1 resource, 1 resource template, 1 prompt
list_models: 4 models
  model 1 ARC Duplex_A_20110907.ifc: elements 268, spaces 21; dq run 1: 0 error, 7,651 warning, 551 info
  model 2 MEP Duplex_MEP_20110907.ifc: elements 926, spaces 42; dq run 2: 0 error, 30,035 warning, 1,961 info
  model 3 ELE Duplex_Electrical_20121207.ifc: elements 103, spaces 1; dq run 3: 0 error, 90 warning, 3,291 info
  model 4 PLU Duplex_Plumbing_20121113.ifc: elements 498, spaces 1; dq run 4: 0 error, 1,472 warning, 13,223 info
run_sql: SELECT discipline, count(*) AS elements FROM v_element GROUP BY 1 ORDER BY 1
  ['ARC', 268]
  ['ELE', 103]
  ['MEP', 926]
  ['PLU', 498]
ifc://schema: 26,541 characters
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
characters and `get_element` on a wall 16,353, and `run_sql` stops at 30,000 characters
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

| Parameter | Type | |
|---|---|---|
| `include_replaced` | boolean, default false | also list the models a later version replaces |

The loaded models, oldest first: id, file, discipline, IFC schema, authoring application,
project, the length unit the file declared, counts, and the status of its latest data
quality check. At most 200 models. `project_group` says which models are one project
(never a Revit default such as `0001`), and `replaces_model_id` and
`replaced_by_model_id` link the versions of a model. A model a later version replaces is
left out unless `include_replaced`, and `replaced_left_out` counts those
([data-model.md](data-model.md#projects-and-versions), audit finding A12).

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
  "project_group": "Duplex Apartment", "replaces_model_id": null, "replaced_by_model_id": null,
  "length_unit": "m",
  "ingested_at": "2026-11-17T19:51:54.616708Z",
  "storeys": 3, "spaces": 42, "elements": 926,
  "property_values": 63683, "quantities": 643, "materials": 0,
  "data_quality": {"status": "checked", "dq_run_id": 2,
                   "finished_at": "2026-11-17T19:52:09.538699Z",
                   "errors": 0, "warnings": 30035, "info": 1961}
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
// get_element {"object_id": 23}  — 16,353 characters in all
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
| `include_openings` | boolean | default false |

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
a group have the metric at all. `coverage` says the same for the whole takeoff
(`"0 of 6,095 elements have area_m2"`). An unmeasured total is unknown, not zero: when no
element has the metric, `total` is null and `note` starts with a warning that names the
likely cause. A model without any quantity was exported without them, and the warning
says which Revit export option to tick
([audit finding A8](audit/2026-11-modern-revit-exports.md#a8-unknown-totals-are-reported-as-0)).
When only some elements have it, `note` says the total covers those. A wall's area is one face. `material` gives material
totals, which are not element totals; the result says so in `basis` (`element` or
`material`) and `note`.

Openings, voids and projections (`IfcFeatureElement`, `v_element.is_feature`) are left out
of every grouping unless `include_openings` is true, and `openings_left_out` says how many
were. Revit names an opening after the wall or door it cuts, so counting it would count
that wall twice: 2,330 "walls" of a type with 1,165 on the audit's Revit 2022 tower
([audit finding A3](audit/2026-11-modern-revit-exports.md#a3-grouping-by-revit-type-counts-openings-as-walls-and-doors)).
Revit 2011 also copies a roof's `Dimensions` onto each of its openings, so on the
architectural model below the roof's 132.9 m² used to be counted five times.

On the architectural model:

```jsonc
// quantity_takeoff {"model_id": 1, "group_by": "ifc_class", "metric": "area_m2"}
{
  "model_id": 1, "group_by": "ifc_class", "metric": "area_m2", "unit": "m2",
  "basis": "element",
  "note": "Only 90 of 218 elements have area_m2: the total covers those, … Each element is counted once, …",
  "include_openings": false, "openings_left_out": 50,
  "groups": [
    {"group": "IfcWall", "group_id": null, "elements": 57, "elements_with_value": 56,
     "value": 587.6854224000203},
    {"group": "IfcSlab", "group_id": null, "elements": 21, "elements_with_value": 20,
     "value": 540.919223340545},
    {"group": "IfcCovering", "group_id": null, "elements": 13, "elements_with_value": 13,
     "value": 243.4588511799992},
    …  // 13 groups
  ],
  "total": 1504.9895329005642,
  "coverage": {"elements": 218, "elements_with_value": 90,
               "summary": "90 of 218 elements have area_m2"},
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
    {"group": "Insulation / Thermal Barriers - Rigid insulation", "group_id": 4,
     "elements": 13, "elements_with_value": null, "value": 404.9254519800101},
    …  // 19 groups
  ],
  "total": 4797.491258321183
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
| `limit` | 1–150, default 150 | page size |
| `offset` | integer ≥ 0, default 0 | `next_offset` of the previous page |

One page of the spaces, each with number, name, storey, area, perimeter, volume, height,
its OmniClass Table 13 code and title, and its Revit `category`, lowest storey first.
`space_count`, `total_area_m2`, `duplicate_spaces`, `total_area_without_duplicates_m2`,
`revit_area_spaces`, `revit_area_total_m2`, `spaces_without_area` and `note` cover
**every** space of the model, whichever page is asked for, so the first page gives the totals of a schedule of any size. A space is
about 225 characters, so a page of 150 is about 34,000; the 500 spaces returned at once
before were about 112,000, past the budget below (audit finding A11). `next_offset` is
null on the last page.
`total_area_m2` is null, not 0, when no space has an area, and `note` then warns of it.
A Revit model that exports both its Rooms and its MEP Spaces writes each room twice,
often under another number or name. `duplicate_of` marks the repeat: a space whose plan,
measured at ingest, lies at least 90 % inside larger spaces on its storey (a space
without a plan: the same number, name, storey and floor area). The total without
duplicates counts each room once: 4,721.4 m² against 8,090.7 m² on the audit's file C
(findings A7 and R1). Revit's area plans are spaces too, of `category` `Areas`: they are
listed, counted in `revit_area_spaces`, and left out of both totals. On the audit's file
E they are 25 spaces of 22,905.9 m², beside 7,438.8 m² of rooms.

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
  "space_count": 21, "offset": 0, "limit": 150, "next_offset": null,
  "total_area_m2": 422.0465663999985, "duplicate_spaces": 0,
  "total_area_without_duplicates_m2": 422.0465663999985,
  "revit_area_spaces": 0, "revit_area_total_m2": null, "spaces_without_area": 0, "note": null
}
```

### schedule

| Parameter | Type | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |
| `ifc_class` | text | required: exact (`IfcWallStandardCase`) or normalised (`IfcDoor`), any case |
| `group_by` | 1–3 of `storey`, `revit_type`, `ifc_class` | required, in order |
| `fields` | 1–8 texts | required: quantities (`width_m`) or properties (`Pset_DoorCommon.FireRating`) |
| `storey_name` | text | only this storey |

The elements of one class, grouped the way Revit schedules them, with chosen fields. A
field is a canonical quantity (`length_m`, `area_m2`, `volume_m3`, `perimeter_m`,
`height_m`, `width_m`) or a property as `PsetName.PropName`, matched without regard to
case. A door's attributes count as properties (`IfcDoor.OverallWidth`,
`IfcDoorStyle.OperationType`). Each group gives its element count and, per field:

- how many elements have a value, and how many distinct values there are;
- the 10 most common values with their counts. Numbers are told apart to 6 significant
  digits, because Revit writes the same 0.87 m width as 0.870000000000002 on one door and
  0.870000000000012 on the next;
- the unit, and the exact minimum, maximum and sum of the numbers.

Groups come by storey elevation when grouped by storey, then the largest first. A field
no element has comes back empty, and `note` names the closest properties the elements
do have. A schedule stops at 200 groups or 30,000 characters of them, always with the
first group; `truncated`, `group_count` and `note` say so. `elements` always counts every
group.

It is one call where a schedule from `find_elements` and `get_element` meant paging every
element. On the audit's tower (file B), the door schedule of level `XX_AR_L01` (159 doors
in 6 types, with width, height and fire rating) is 4,181 characters, and that of all
1,458 doors 3,759. Paging those doors with their properties was about 490,000.

```jsonc
// schedule {"model_id": 1, "ifc_class": "IfcDoor", "group_by": ["revit_type"],
//           "fields": ["width_m", "Pset_DoorCommon.FireRating"]}
{
  "model_id": 1, "ifc_class": "IfcDoor", "storey_name": null, "group_by": ["revit_type"],
  "fields": ["width_m", "Pset_DoorCommon.FireRating"], "elements": 14,
  "groups": [
    {"group": ["M_Single-Flush:0864 x 2032mm"], "elements": 6,
     "fields": {
       "width_m": {"elements_with_value": 6, "distinct_values": 1,
                   "values": [{"value": 0.864, "elements": 6}], "unit": "m",
                   "min": 0.8639999999999985, "max": 0.8639999999999999, "sum": 5.183999999999997},
       "Pset_DoorCommon.FireRating": {"elements_with_value": 6, "distinct_values": 1,
                   "values": [{"value": "Fire Rating", "elements": 6}], "unit": null,
                   "min": null, "max": null, "sum": null}}},
    …  // 4 types, 1,815 characters in all
  ],
  "group_count": 4, "truncated": false, "note": null
}
```

The Duplex's doors carry Revit 2011's placeholder `'Fire Rating'`, their property's own
name: the schedule shows it as written, and `value_is_property_name` reports it
([data-quality.md](data-quality.md)).

### data_quality_report

| Parameter | Type | |
|---|---|---|
| `model_id` | integer ≥ 1 | required |
| `severity` | `error`, `warning`, `info` | only rules of that severity |
| `rule_id` | text | only that rule |
| `scope` | `model_content`, `loader_coverage` | only rules of that scope |
| `samples_per_rule` | 0–20, default 3 | findings shown per rule |

The model's latest successful check (`ifc-sql dq`): the run and when it finished, the
findings per severity (always for the whole run), the same split by scope
(`findings_by_scope`: the model's own gaps, `model_content`, apart from what this tool
does not read, `loader_coverage`; [data-quality.md](data-quality.md#scope-the-model-or-the-loader)),
and per rule with findings, `model_content` rules first: severity, dimension, scope, the
rule's description, the number of findings and the first findings by
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
  "findings": {"error": 0, "warning": 30035, "info": 1961},
  "findings_by_scope": {"model_content": {"error": 0, "warning": 30035, "info": 1961},
                        "loader_coverage": {"error": 0, "warning": 0, "info": 0}},
  "rules": [
    {"rule_id": "value_is_property_name", "severity": "warning", "dimension": "validity",
     "scope": "model_content",
     "description": "A text property holds its own name, e.g. BarCode = 'BarCode'. …",
     "findings": 29967,
     "samples": [{"object_id": 296, "global_id": "0$LNoCntTAthq5oMj33Z8K",
                  "ifc_class": "IfcFlowSegment", "object_name": "Pipe Types:Waste:582956",
                  "message": "PSet_Revit_Other.AssetIdentifier = 'AssetIdentifier', the parameter's own name: a placeholder, not a value"}]},
    {"rule_id": "quantity_not_a_measure", …, "findings": 44, …},
    {"rule_id": "duplicate_space", …, "findings": 21, …},
    {"rule_id": "conflicting_property", …, "findings": 2, …},
    {"rule_id": "placeholder_project_info", …, "findings": 1, …},
    {"rule_id": "value_is_empty", "severity": "info", …, "findings": 1961, …}
  ],
  "rules_without_findings": ["conflicting_classification", "conflicting_quantity", …]  // 40 rules
}
```

The counts on all four files are the table in
[data-quality.md](data-quality.md#findings-on-the-four-duplex-files);
`tests/samples/test_duplex_mcp.py` checks that the report agrees with it.

### compare_models

| Parameter | Type | |
|---|---|---|
| `new_model_id` | integer ≥ 1 | required: the newer export |
| `old_model_id` | integer ≥ 1 | the older export; by default the one `new_model_id` replaces |
| `samples_per_change` | 0–10, default 3 | examples per class and per property |

What changed between two exports of a model. Objects are matched by GlobalId, which
Revit keeps from one export to the next. Objects only in the new model are added and
objects only in the old one removed, per class. On the objects in both, property values
are added, removed or changed per property set and name. A value is changed when its
text, number, boolean or unit differs. Numbers are compared exactly: a re-export of the
same Revit model writes the same digits. Each class and property comes with its first
examples by GlobalId, most changes first. A `note` says when the two models share no
GlobalId, so they are not versions of one model.

On the audit's files, D is A with fire ratings added (audit finding A15). Loaded as A's
replacement (`ifc-sql ingest … --replaces 1`), the comparison takes 0.02 s:

```jsonc
// compare_models {"new_model_id": 4, "samples_per_change": 1}
{
  "old_model_id": 1, "new_model_id": 4,
  "objects_in_both": 496, "objects_added": 4, "objects_removed": 4,
  "added_by_class": [{"ifc_class": "IfcOpeningElement", "objects": 4,
    "samples": [{"global_id": "0XCXgEAd58BP76Z_dV0rf5", "ifc_class": "IfcOpeningElement",
                 "name": "Basic Wall:SIP 202mm Wall - conc clad:198694"}]}],
  "removed_by_class": [{"ifc_class": "IfcOpeningElement", "objects": 4, "samples": […]}],
  "property_changes": [
    {"pset_name": "Pset_WallCommon", "prop_name": "FireRating",
     "added": 42, "removed": 0, "changed": 0,
     "samples": [{"global_id": "0NWseyvsH7_gBW225aGtuD", "object_name": "Basic Wall:CL_W1:493612",
                  "old": null, "new": "M2", "old_unit": null, "new_unit": null}]},
    {"pset_name": "Pset_CurtainWallCommon", "prop_name": "FireRating",
     "added": 9, "removed": 0, "changed": 0, "samples": […]}
  ],
  "truncated": false, "note": null
}
```

Revit regenerated the four openings with new GlobalIds, so they show as removed and
added. The `data_quality_report` of the two versions differs only in
`missing_fire_rating`: 107 elements in A, 56 in D.

### describe_schema

| Parameter | Type | |
|---|---|---|
| `relation` | text, 1–200 characters | one table or view; leave out for all |

The tables and views `run_sql` can read, from the database's own catalogue, so the
description always matches the database. Without `relation`: each view and table with
its comment and column names, views first (21 relations, 8,295 characters on the Duplex
database, a third of `ifc://schema`). With `relation` (`v_element`, `ifc.property`, any
case): that one in full, each column's type, whether it can be null and its comment, and
a table's keys.

This is the same catalogue as the [`ifc://schema`](#resources) resource, as a tool,
because in Claude Desktop the user attaches resources and the model cannot fetch them.
In the audit's six sessions, 20 of 72 calls failed on guessed names, `v_property` and
`information_schema` among them (audit finding A10,
[ADR 9](adr/0009-schema-discovery-tool.md)).

```jsonc
// describe_schema {"relation": "property"}
{
  "relations": [
    {"name": "property", "kind": "table",
     "description": "Property set values, one row per (object, property set, property): an entity-attribute-value table with typed value columns (ADR 2). …",
     "column_names": ["object_id", "pset_name", "prop_name", "value_type", "value_text", "value_num", "value_bool", "unit", "is_type_property", "is_implausible"],
     "columns": [{"name": "object_id", "type": "bigint", "not_null": true, "description": "…"}, …],
     "keys": ["PRIMARY KEY (object_id, pset_name, prop_name)",
              "FOREIGN KEY (object_id) REFERENCES object(object_id) ON DELETE CASCADE"]}
  ],
  "note": "run_sql reads this relation as property, without the ifc. prefix."
}
```

An unknown name gets the closest names and the full list:

```
schema ifc has no table or view named 'v_space'. Closest: v_space_schedule, v_takeoff. The tables and views of schema ifc are: v_dq_finding, v_element, v_model_summary, … spatial_element. Call describe_schema for their columns; property values are in property(object_id, pset_name, prop_name, value_text, value_num, value_bool, unit).
```

### run_sql

| Parameter | Type | |
|---|---|---|
| `sql` | text, 1–20,000 characters | one PostgreSQL query |
| `max_rows` | 1–500, default 100 | |

One read-only query on schema `ifc`, for the questions the other tools do not answer.
Unqualified names resolve to `ifc` (`FROM v_element`); [describe_schema](#describe_schema)
gives the tables and columns, and the description names `property` and its columns,
which nearly every question beyond counts needs. It returns the columns (name
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
| `SELECT * FROM pg_stat_activity` | `query refused: pg_stat_activity is a PostgreSQL system catalogue; only schema ifc can be read: call describe_schema for its tables, views and columns` |
| `SELECT table_name FROM information_schema.tables` | `query refused: only schema ifc can be read, not information_schema.tables; call describe_schema for its tables, views and columns` |

Also refused: more than one statement, SELECT … INTO, FOR UPDATE and the other locking
clauses, SET, RESET, SHOW, EXPLAIN, COPY, DO, CALL, transaction control, other schemas
and schema-qualified function calls ([ADR 6](adr/0006-mcp-server.md#5-run_sql-four-layers-each-tested-on-its-own-against-postgresql)
has the rules and the reasons). A query the database rejects comes back with
PostgreSQL's message and hint. When a relation or column does not exist, the commonest
failure, the error also names what does: the closest names, and every relation of the
schema, or the columns of the relations the query reads (of the one its qualifier
names, when it has one). The query runs inside a savepoint, so the transaction can still
read the catalogue after the error.

```
the database refused the query: column "storey_nme" does not exist Hint: Perhaps you meant to reference the column "v_element.storey_name". (SQLSTATE 42703). Closest: storey_name, storey_id, type_name. Columns of v_element: object_id, model_id, discipline, … width_m, materials.
the database refused the query: relation "v_property" does not exist (SQLSTATE 42P01). Closest: property. The tables and views of schema ifc are: v_dq_finding, v_element, … spatial_element. Call describe_schema for their columns; property values are in property(object_id, pset_name, prop_name, value_text, value_num, value_bool, unit).
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
| `ifc://schema` | `text/markdown` | Every table and view of `ifc` with columns, types, comments, keys and foreign keys, generated from `pg_catalog` on every read (26,541 characters). Views first. |
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
| Unknown relation or column in `run_sql` | the same, followed by the closest names and the relations or columns that exist ([run_sql](#run_sql)) |
| Unknown relation in `describe_schema` | `schema ifc has no table or view named 'v_space'. Closest: v_space_schedule, v_takeoff. The tables and views of schema ifc are: …` |
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
| Pages and lists | 100 elements or 150 spaces per page; 200 models, storeys or groups; a schedule's groups up to 30,000 characters; 500 properties and 100 findings per element; 20 samples per rule | Each list says when it was cut (`truncated`). |
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
level=info logger=ifc_sql_mcp.server.app msg="tool call" tool=run_sql sql_sha256=58d0e5bcb0e3a0aa sql_chars=32 outcome=database_error seconds=0.039 error="the database refused the query: column \"storey_nme\" does not exist Hint: Perhaps you meant to reference the column \"v_element.storey_name\". (SQLSTATE 42703). Closest: storey_name, storey_id, type_name. Columns of v_element: object_id, model_id, discipline, global_id, ifc_class, ifc_class_base, is_fe… [cut: 578 characters, the first 300 shown]"
level=info logger=ifc_sql_mcp.server.app msg="tool call" tool=describe_schema relation=None outcome=ok seconds=0.022 rows=21
level=info logger=ifc_sql_mcp.server.app msg="resource read" uri=ifc://schema outcome=ok seconds=0.022 chars=26541
```

`outcome` is one of `ok`, `invalid`, `refused`, `timeout`, `database_error`,
`unavailable` and `error`. For `run_sql` the line carries a fingerprint of the query
and its length; the text itself is logged only with `IFC_SQL_LOG_LEVEL=DEBUG`, because
it can be long and carries whatever the conversation put into it (ADR 6, decision 10).
An unexpected error adds a line with the traceback. The startup check adds two lines
from Alembic (`Context impl PostgresqlImpl.`), as ingest and dq do.

With `IFC_SQL_VIEWER_FEED=1` the startup line ends in `viewer_feed=on`, and a call whose
notification fails adds `msg="viewer feed: call not published"` with the tool, the call's
number and the database's error, never the payload; the call's result is unchanged. The
notification itself carries `run_sql`'s text, clipped to fit: whoever listens on the
database reads it, and PostgreSQL keeps it in its notification queue (`pg_notify/` in the
data folder) until every listener has read it (ADR 14 amends ADR 6, decision 10).

## Tests

| Test | Covers |
|---|---|
| `tests/unit/server/test_sql_guard.py` | Every kind of query the validator accepts and refuses, with the reason. |
| `tests/unit/server/test_values.py`, `test_tool_results.py` | JSON conversion of each kind of value; cutting of long text, arrays and JSON. |
| `tests/unit/server/test_unreachable_database.py` | Tools and resources on a database that does not answer; a refused query needs none. |
| `tests/integration/test_mcp_protocol.py` | What a client sees, in both protocol eras: tools with input and output schemas and annotations, resources, the prompt, the schema resource against the catalogue. |
| `tests/integration/test_mcp_tools.py` | Each tool's exact output on the synthetic fixture, pagination and limits, unknown ids, invalid `group_by` and `metric`, a checked and a never-checked model. |
| `tests/integration/test_compare_models.py` | `compare_models` on two versions built row by row: objects added and removed per class, values added, removed and changed (text, number, unit), the replaced model as default, unrelated models, limits and refused comparisons. |
| `tests/integration/test_schedule.py` | `schedule` by type and by storey, numbers that differ only in float noise, a field no element has, a schedule cut by groups and by size, refused arguments; `space_schedule`'s pages and their totals. |
| `tests/integration/test_schema_discovery.py` | `describe_schema` in brief and in full; the names added to an unknown relation's or column's error; the 20 failed calls of the audit's sessions, replayed, each now answered with the name to use. |
| `tests/integration/test_run_sql.py` | Every `run_sql` layer on its own: validator, executor, transaction (timeouts, `set_config`, a session per call), privileges; results, cuts and logging; the pinned list of functions sqlglot types. |
| `tests/integration/test_mcp_failures.py` | A bug in a tool or resource: a generic message, the traceback in the log. |
| `tests/integration/test_cli_serve.py`, `tests/unit/test_cli.py` | `ifc-sql serve` on an unreachable, unmigrated and too old database, and with a wrong password. |
| `tests/integration/test_serve_stdio.py` | `ifc-sql serve` as a subprocess, through the SDK's stdio client: nothing but protocol on stdout, with the viewer's feed off and on. |
| `tests/integration/test_feed_publish.py` | The viewer's feed: no notification and no session with it off; one per call with it on, failed calls with their outcome, `run_sql`'s ids, a notification that fails changing nothing; the worst-case event delivered as `ifc_reader`. |
| `tests/integration/test_mcp_smoke_script.py` | `make smoke`. |
| `tests/samples/test_duplex_mcp.py` | The tools on the four Duplex files, with the figures on this page (`make test-samples`). |
