# Data quality

`ifc-sql dq` checks loaded models against a catalogue of rules and records what it finds,
one run per model. Every issue the ingest reported becomes a finding, and five SQL rules
check what was stored: Revit's placeholder values, empty strings, elements without a
storey and spaces without an area. The design, and the alternatives that were rejected,
are in [ADR 5](adr/0005-data-quality-rules.md).

```bash
make dq-samples            # make ingest-samples, then check every loaded model
uv run ifc-sql dq          # check every loaded model
uv run ifc-sql dq 2 4      # check models 2 and 4
```

## Running it

`ifc-sql dq` takes model ids (`ifc.model.model_id`), or none to check every loaded model.
It then:

1. Refuses a database that is not at the latest migration.
2. Loads `src/ifc_sql_mcp/rules/dq_rules.yaml` and checks it
   ([below](#the-rule-catalogue)). An invalid catalogue stops the command before
   anything is written.
3. Refuses a model id that does not exist. Nothing is written either.
4. Synchronises the catalogue into `ifc.dq_rule`
   ([When the catalogue changes](#when-the-catalogue-changes)).
5. For each model, records a `running` run and commits it, then writes every rule's
   findings and marks the run `succeeded`, in one transaction. On an error that
   transaction rolls back, and the run is marked `failed` with the error. A failed run
   does not stop the next model.

Each run evaluates every rule and adds new rows. Earlier runs are kept, so quality can
be compared over time ([Reading the findings](#reading-the-findings)).

| Outcome | Run | Exit | Output |
|---|---|---|---|
| Checked | `succeeded` | 0 | `checked model 2 (MEP, Duplex_MEP_20110907.ifc) in dq run 2: 0 error, 30,013 warning, 1,961 info findings` |
| A rule failed | `failed` | 1 | `dq of model 1 failed (dq run 1): RuleError: rule fails_for_model_1 failed: DivisionByZero: division by zero`; the other models are still checked |
| No model loaded | none | 0 | `no models are loaded; nothing to check` |
| Unknown model id | none | 2 | Usage error: `Invalid value for MODEL_ID: no model with id 7` |
| Model id not a number | none | 2 | Usage error: `Invalid value for 'MODEL_ID': Input should be a valid integer, …` |
| Invalid catalogue | none | 1 | `invalid rule catalogue: dq_rules.yaml: rule 2 (value_is_not_applicable): severity must be one of error, warning, info; got 'notice'` |
| Database unreachable | none | 1 | `cannot reach the database at postgresql+psycopg://ifc:***@127.0.0.1:1/ifc: connection failed: …` (the password is masked) |
| Database not at the latest migration | none | 1 | ``the database at postgresql+psycopg://ifc:***@localhost:5432/ifc_empty is not migrated, but dq needs revision 0009: run `ifc-sql migrate` first`` |

The outputs come from runs of the command in this container, except the failed rule and
the invalid catalogue. Those two need an edited catalogue, so their wording is checked by
`tests/integration/test_cli_dq.py`, which builds one in a temporary directory.

The summaries go to stdout, and errors and logs to stderr. Logs are one line for the
sync, one per rule that found something, and one per model with the duration. These
are the ELE lines from `make dq-samples`, without the timestamp and logger fields:

```
msg="rules synchronised" rules=31 added=31 updated=0 retired=0 deleted=0
msg=checking dq_run_id=3 model_id=3
msg=findings dq_run_id=3 rule_id=value_is_property_name severity=warning count=1
msg=findings dq_run_id=3 rule_id=value_is_not_applicable severity=info count=3018
msg=findings dq_run_id=3 rule_id=value_is_empty severity=info count=273
msg=findings dq_run_id=3 rule_id=conflicting_property severity=warning count=2
msg=findings dq_run_id=3 rule_id=omniclass_unparsed severity=warning count=1
msg=findings dq_run_id=3 rule_id=incomplete_classification_reference severity=warning count=85
msg=checked dq_run_id=3 model_id=3 rules=31 findings=3380 seconds=0.233
```

## The rule catalogue

[`src/ifc_sql_mcp/rules/dq_rules.yaml`](../src/ifc_sql_mcp/rules/dq_rules.yaml) holds
the rules. It lives inside the package, so an installed wheel can run `ifc-sql dq`.
Every rule has a `rule_id`, a `description` (the text an LLM reads in `ifc.dq_rule`), a
`severity` and a `dimension`, and is evaluated in one of two ways:

- **`issue_code`**: the findings are the model's `ifc.ingest_issue` rows with that code,
  each with the issue's object and message. All 26 issue codes have exactly one rule,
  named after the code. `tests/unit/dq/test_catalogue.py` fails until a new `IssueCode`
  gets one.
- **`sql`**: a SELECT returning `object_id` (NULL for a finding about the whole model)
  and `message`, for the model whose id is `:model_id`. For example:

```yaml
  - rule_id: value_is_property_name
    description: >-
      A text property holds its own name, e.g. BarCode = 'BarCode'. Revit 2011 fills
      unset parameters this way. The value looks like data, so a query that groups or
      filters on the property treats the placeholder as a real value.
    severity: warning
    dimension: validity
    sql: |
      SELECT p.object_id,
             format('%s.%s = %L, the parameter''s own name: a placeholder, not a value',
                    p.pset_name, p.prop_name, p.value_text) AS message
      FROM ifc.property AS p
      JOIN ifc.object AS o ON o.object_id = p.object_id
      WHERE o.model_id = :model_id
        AND p.value_text = p.prop_name
```

The loader (`src/ifc_sql_mcp/dq/catalogue.py`) refuses, naming the rule and the
problem: YAML syntax errors; a key written twice in one mapping; unknown or missing
keys; values that are not text (an unquoted `no` is read as false); a `rule_id` that is
not snake_case or is used twice; a severity or dimension outside the CHECKs on
`dq_rule`; both or neither of `issue_code` and `sql`; an issue code that does not exist
or is mapped by two rules; and SQL that does not use `:model_id`, or that uses any other
parameter. Without `:model_id` a rule would report every model's objects. SQLAlchemy
reads `':word'` as a parameter even inside a string literal, so any other parameter is
refused too.

### Severity and dimension

| Severity | Meaning |
|---|---|
| `error` | Something in the file is not in the database: an object was skipped, or a value was dropped or left NULL, and nothing stored stands in for it. |
| `warning` | What is stored may mislead: a value was chosen between two, assumed, could not be used as intended, or is a placeholder that looks like real data. |
| `info` | Stored as written and plainly not a value (`'N/A'`, an empty string). It misleads no one who reads it, but it is a gap worth counting. |

| Dimension | Meaning |
|---|---|
| `completeness` | Something expected is missing. |
| `validity` | A value breaks a format, type or range, or a rule of IFC. |
| `consistency` | Two pieces of data disagree. |
| `uniqueness` | Something that must occur once occurs more than once. |

Each finding copies its rule's severity when it is found (`dq_result.severity`), so a
later change to the rule does not rewrite earlier runs.

### The rules

The five SQL rules check stored values:

| Rule | Severity | Dimension | Finds |
|---|---|---|---|
| `value_is_property_name` | warning | validity | A text value equal to its property's name (`BarCode = 'BarCode'`), Revit 2011's placeholder. |
| `value_is_not_applicable` | info | completeness | The value `'N/A'`, which Revit 2013 writes for unset parameters. |
| `value_is_empty` | info | completeness | An empty or whitespace-only text value. |
| `element_without_storey` | warning | completeness | An element whose `storey_id` is NULL: takeoffs by storey leave it out. |
| `space_without_area` | warning | completeness | A space with no `area_m2` from any source: schedules show no area. |

The 26 issue rules, one per `IssueCode` (the issue's meaning is in
[revit-ifc-quirks.md](revit-ifc-quirks.md) and `ingest/issues.py`):

| Severity | Rules |
|---|---|
| error | `invalid_project_global_id`, `unit_not_convertible` (validity); `invalid_global_id`, `invalid_elevation`, `non_finite_value`, `invalid_layer_thickness` (validity); `duplicate_global_id` (uniqueness); `unnamed_property`, `unsupported_property`, `unsupported_property_set`, `unsupported_classification`, `unsupported_material`, `unnamed_material` (completeness) |
| warning | `unit_assumed_si`, `incomplete_classification_reference` (completeness); `measure_not_converted`, `revit_name_unparsed`, `quantity_not_a_measure`, `negative_quantity`, `omniclass_unparsed` (validity); `revit_tag_mismatch`, `conflicting_property`, `conflicting_quantity`, `conflicting_classification` (consistency); `multiple_projects`, `multiple_material_associations` (uniqueness) |

A few of these calls deserve a sentence:

- **`incomplete_classification_reference` is a warning, not an error**, although the
  reference is dropped. A reference without a code classifies nothing, so no
  classification was lost. What is missing was already missing in the file.
- **`conflicting_property` is a warning**: one value is stored, and the message names
  the one that was dropped.
- **`measure_not_converted` is a warning**: the value is stored, but in the file's own
  unit and without one, so it cannot be compared with the same measure from another file.

## Findings on the four Duplex files

After `make dq-samples` on the development database (`make psql`):

```sql
SELECT f.rule_id, f.severity, f.dimension,
       count(*) FILTER (WHERE m.discipline = 'ARC') AS arc,
       count(*) FILTER (WHERE m.discipline = 'MEP') AS mep,
       count(*) FILTER (WHERE m.discipline = 'ELE') AS ele,
       count(*) FILTER (WHERE m.discipline = 'PLU') AS plu
FROM ifc.v_dq_finding AS f
JOIN ifc.model AS m USING (model_id)
GROUP BY f.rule_id, f.severity, f.dimension
ORDER BY f.severity, f.rule_id;
```

```
               rule_id               | severity |  dimension   | arc  |  mep  | ele  |  plu
-------------------------------------+----------+--------------+------+-------+------+-------
 value_is_empty                      | info     | completeness |  473 |  1961 |  273 |  1759
 value_is_not_applicable             | info     | completeness |    0 |     0 | 3018 | 11464
 conflicting_property                | warning  | consistency  |    0 |     2 |    2 |   225
 incomplete_classification_reference | warning  | completeness |    0 |     0 |   85 |   263
 omniclass_unparsed                  | warning  | validity     |    0 |     0 |    1 |     1
 quantity_not_a_measure              | warning  | validity     |    0 |    44 |    0 |    61
 value_is_property_name              | warning  | validity     | 7650 | 29967 |    1 |   921
(7 rows)
```

The other 24 rules find nothing in any of the four files. `tests/samples/test_duplex_dq.py`
pins every count (`make test-samples`), and checks them against two independent sources:

- **The issue rules equal the issue table** in
  [revit-ifc-quirks.md](revit-ifc-quirks.md#what-the-pipeline-promises): 44 and 61
  `quantity_not_a_measure`, 2, 2 and 225 `conflicting_property`, 1 and 1
  `omniclass_unparsed`, 85 and 263 `incomplete_classification_reference`.
- **The placeholder rules equal section 8** of the same page, which
  `scripts/quirks_report.py` counts in the transform's records before anything is
  loaded: 7,650, 29,967, 1 and 921 values equal to their name; 3,018 and 11,464 `'N/A'`;
  473, 1,961, 273 and 1,759 empty strings. No file has a whitespace-only value, so the
  empty-value rule's wider test changes no count.

What the numbers say:

- **No errors.** Nothing in the four files was skipped or dropped, and every element has
  a storey and every space an area.
- **Placeholders dominate.** In MEP, 38 parameters hold their own name on 969 of the 973
  objects; `AccessibilityPerformance`, `AssetIdentifier`, `BarCode`, `CodePerformance`
  and `Color` do so on all 926 elements. The 2013 files write `'N/A'` instead, an `info`
  finding.
- **The warnings that affect takeoffs** are the 105 fittings without a `length_m`
  (`quantity_not_a_measure`; all `IfcFlowFitting` of the family `M_Transition - Generic`)
  and the 348 elements whose UniFormat reference was empty
  (`incomplete_classification_reference`), one finding each.

Example findings, one per rule (from `v_dq_finding`):

| Rule | File | Object | Message |
|---|---|---|---|
| `value_is_property_name` | ARC | `IfcSpace` A103 | `PSet_Revit_Other.CeilingCovering = 'CeilingCovering', the parameter's own name: a placeholder, not a value` |
| `value_is_not_applicable` | ELE | `IfcFlowTerminal` `M_Microwave:760 x 400 x 450mm:…:615600` | `Other.AccessibilityPerformance = 'N/A': no value` |
| `value_is_empty` | ARC | `IfcSpace` A103 | `Pset_SpaceCommon.Reference = '': no value` |
| `conflicting_property` | MEP | `IfcFlowTerminal` `M_Refrigerator:850 x 760mm:…:575375` | `PSet_Revit_Type_Other.Material has several values; kept 'Metal - Paint Finish - Ivory, Matte', dropped 'Material'` |
| `quantity_not_a_measure` | MEP | `IfcFlowFitting` `M_Transition - Generic:…:578437` | `PSet_Revit_Dimensions.Length (IfcReal) is not a measure in m; no length_m` |
| `omniclass_unparsed` | ELE | `IfcSpace` R301 | `Identity Data.OmniClass Table 13 Category = 'Other General Facility Service Spaces' has no OmniClass Table 13 code` |
| `incomplete_classification_reference` | ELE | `IfcFlowTerminal` `M_Microwave:…:615600` | `classification reference '' in 'UniFormat' has no code` |

Each run's duration is `finished_at - started_at` in `dq_run`, from the same
`make dq-samples`:

| File | Findings | Run (s) |
|---|---:|---:|
| `Duplex_A_20110907.ifc` | 8,123 | 0.47 |
| `Duplex_MEP_20110907.ifc` | 31,974 | 1.52 |
| `Duplex_Electrical_20121207.ifc` | 3,380 | 0.23 |
| `Duplex_Plumbing_20121113.ifc` | 14,694 | 0.77 |

## Reading the findings

`v_model_summary` counts the findings of each model's latest successful run, and shows
NULL, not 0, for a model that was never checked. After the `make dq-samples` above:

```
 model_id | discipline | dq_run_id | errors | warnings | info
----------+------------+-----------+--------+----------+-------
        1 | ARC        |         1 |      0 |     7650 |   473
        2 | MEP        |         2 |      0 |    30013 |  1961
        3 | ELE        |         3 |      0 |       89 |  3291
        4 | PLU        |         4 |      0 |     1471 | 13223
```

(`SELECT model_id, discipline, dq_run_id, dq_error_count AS errors, dq_warning_count AS
warnings, dq_info_count AS info FROM ifc.v_model_summary ORDER BY model_id`.)

`v_dq_finding` (migration 0008) lists the findings of the same runs, one row each, with
what a reader needs: `model_id`, `dq_run_id`, `rule_id`, `severity`, the rule's
`dimension`, and the object's `object_id`, `global_id`, `ifc_class` and `object_name`,
plus the `message`. It picks the latest run exactly as `v_model_summary` does, so their
counts always agree (a test checks this on all four files). The rule's description is
in `ifc.dq_rule`.

Earlier runs stay in `dq_run` and `dq_result`. A second `make dq` wrote runs 5 to 8;
comparing MEP's two runs rule by rule:

```sql
SELECT res.rule_id,
       count(*) FILTER (WHERE run.dq_run_id = 2) AS run_2,
       count(*) FILTER (WHERE run.dq_run_id = 6) AS run_6
FROM ifc.dq_run AS run
JOIN ifc.dq_result AS res USING (dq_run_id)
WHERE run.model_id = 2
GROUP BY res.rule_id ORDER BY res.rule_id;
```

```
        rule_id         | run_2 | run_6
------------------------+-------+-------
 conflicting_property   |     2 |     2
 quantity_not_a_measure |    44 |    44
 value_is_empty         |  1961 |  1961
 value_is_property_name | 29967 | 29967
```

The same file gives the same findings. A new version of a model is a new model
([ADR 4](adr/0004-idempotent-ingest.md)), so comparing versions means comparing their
latest runs.

## When the catalogue changes

Every `ifc-sql dq` makes `ifc.dq_rule` match the catalogue before it evaluates anything
(`src/ifc_sql_mcp/dq/sync.py`):

| Change to the YAML | Effect on `dq_rule` |
|---|---|
| New rule | Inserted. |
| Description, severity or dimension edited | Updated in place. Findings of earlier runs keep the severity they were found with. |
| Rule removed, and findings refer to it | Kept, with `retired_at` set. Later runs do not evaluate it. `dq_result.rule_id` deliberately does not cascade, so each finding goes on naming its rule. |
| Rule removed, and nothing refers to it | Deleted. A retired rule is deleted once its last findings are gone, for example with their model. |
| Retired rule back in the file | `retired_at` is cleared and runs evaluate it again. |

A rule's query and issue code are not stored, so changing them changes nothing in
`dq_rule`. When a change alters what a rule means, give it a new `rule_id`: the old rule
is then retired and keeps its findings. A run does not record which rules it evaluated,
so a rule added later simply has no findings in earlier runs; comparisons across a
catalogue change have to allow for that (ADR 5).

## Issues and findings

An issue records what the transform did; a finding is an evaluation against a rule
([ADR 4](adr/0004-idempotent-ingest.md)). Each issue rule's findings are the model's
`ingest_issue` rows with its code, inserted by `INSERT … SELECT`
(`run.ISSUE_FINDINGS`), with the issue's object and message.

An object whose GlobalId is malformed is never loaded, so its `invalid_global_id`
finding has no `object_id`. `dq_result` has no GlobalId column, and none was added: the
value matches no object and would fail the GlobalId format check, so it could only be
shown, never joined. The message shows it:

```
IfcWall 'Basic Wall:Generic:1' has a malformed GlobalId; skipped (GlobalId 'not-a-global-id': no object with it was loaded)
```

(from `tests/integration/test_dq_rules.py`; none of the four files has a malformed
GlobalId). `ingest_issue.global_id` keeps the raw value.

## Tests

| Test | Covers |
|---|---|
| `tests/unit/dq/test_catalogue.py` | Every way a catalogue is refused, the shipped file, and every `IssueCode` having exactly one rule. |
| `tests/integration/test_dq_rules.py` | Each of the 31 rules on the synthetic fixture: how many findings, about which objects, with which message; issue findings equal the stored issues; a run reports only its own model; the edge cases of the SQL rules. |
| `tests/integration/test_dq_sync.py` | Every catalogue change in the table above, and two syncs at once. |
| `tests/integration/test_dq_run.py` | A run's bookkeeping, a re-run, a failed run that leaves nothing but the failed run, `v_model_summary` before and after, `v_dq_finding`, deleting a model. |
| `tests/integration/test_cli_dq.py` | Each outcome in the table at the top. |
| `tests/samples/test_duplex_dq.py` | Every rule's count on each of the four files (`make test-samples`). |

On the fixture the rules find 16 problems, all of them put there on purpose
(`tests/fixtures/make_revit_quirks.py`): 2 placeholders, 2 `'N/A'`, 3 empty strings, a
store room without an area, and the transform's 8 issues.
