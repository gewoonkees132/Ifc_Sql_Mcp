# Audit harness

The scripts behind [the audit of modern Revit exports](../2026-11-modern-revit-exports.md).
None of them is part of the product. They exist so the audit can be repeated after a fix,
and its figures used as assertions.

| Script | What it does |
|---|---|
| `ground_truth.py` | Reads an IFC file with IfcOpenShell only (never `ifc_sql_mcp`). Writes counts, storeys, door and window sizes, `Pset_*Common` flags, material kinds and quantity sets as JSON. With `--geometry` it also measures walls and slabs. |
| `doors_on_level.py` | The ground-truth door schedule of one storey. |
| `architect_workflow.py` | Starts `ifc-sql serve` over stdio, as Claude Desktop does. Calls every tool, 11 `run_sql` queries, the resources and the prompt, and logs time, size and result. |
| `run_llm_questions.sh` | Asks `llm_questions.tsv` one question at a time, each in a fresh headless Claude Code session that can use only the `ifc-sql` MCP server. |
| `parse_transcript.py` | Prints the tool calls, final answer and cost of each session. |
| `measure.py` | Wall time and peak memory of a command. |
| `space_truth.py` | The [re-check](../2026-11-recheck.md)'s room totals, from IfcOpenShell and shapely only: each space's footprint, Revit Areas apart, and each room once. |
| `quantity_sets.py` | The quantity sets each class of a file carries, and the coverings' totals. |

## The files

None is stored in this repository. Fetch them from the pinned commits, check the
checksums, and keep them in their own directory (`data/` is git-ignored):

```bash
mkdir -p data/audit /tmp/audit-src && cd /tmp/audit-src
git clone -q --filter=blob:none --no-checkout https://github.com/youshengCode/IfcSampleFiles
git clone -q --filter=blob:none --no-checkout https://github.com/ThatOpen/engine_web-ifc
cd - >/dev/null
S=/tmp/audit-src
git -C $S/IfcSampleFiles  cat-file -p 7e6927982d7e57b29409bb631ef93d6770ef0665:Ifc4_Revit_ARC.ifc > data/audit/Ifc4_Revit_ARC.ifc
git -C $S/engine_web-ifc  cat-file -p d1962b4178c0fbfa87368e0b415c71ab97c0220d:tests/ifcfiles/public/ISSUE_098_R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc > data/audit/R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc
git -C $S/IfcSampleFiles  cat-file -p 7e6927982d7e57b29409bb631ef93d6770ef0665:Ifc4_Revit_MEP.ifc > data/audit/Ifc4_Revit_MEP.ifc
git -C $S/IfcSampleFiles  cat-file -p 7e6927982d7e57b29409bb631ef93d6770ef0665:Ifc4_Revit_ARC_FireRatingAdded.ifc > data/audit/Ifc4_Revit_ARC_FireRatingAdded.ifc
git -C $S/engine_web-ifc  cat-file -p d1962b4178c0fbfa87368e0b415c71ab97c0220d:tests/ifcfiles/public/ISSUE_068_ARK_NUS_skolebygg.ifc > data/audit/ARK_NUS_skolebygg.ifc
sha256sum -c <<'EOF'
c4dd3612f126a0ca5a57f0e75d28f3e5da62751def85a452d849883c2892b518  data/audit/Ifc4_Revit_ARC.ifc
08ab44c7a0e68efe6c1722c54235b405c0d649195254d92f964eaa47438aa62e  data/audit/R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc
829c08715d56256e04d04fa7be40ec68928e0397631c3b846efd72d003f96495  data/audit/Ifc4_Revit_MEP.ifc
e015b8314d5bb64291a3ad9f7e3807beea5714469de28bb5d4c34aacd7bf4fbf  data/audit/Ifc4_Revit_ARC_FireRatingAdded.ifc
362f0f5e5ad35557014100a6c4de75ec166f6dbd9197933170dc1423f4af2a76  data/audit/ARK_NUS_skolebygg.ifc
EOF
```

Licences: engine_web-ifc is MPL-2.0. IfcSampleFiles states no licence ("A collection of IFC
sample files for software test use"). The files are used here for testing and are not
redistributed.

## Running it

On an empty, migrated database, load the Duplex samples first so the model ids match the
report: 1–4 Duplex, 5 A, 6 B, 7 C, 8 D, 9 E.

```bash
make dq-samples
H=docs/audit/harness
for f in Ifc4_Revit_ARC R8_F1_MAB_AR_M3_XX_XXX_MO_7000; do
  uv run python -I $H/measure.py uv run ifc-sql ingest data/audit/$f.ifc --discipline ARC
done
uv run ifc-sql ingest data/audit/Ifc4_Revit_MEP.ifc --discipline MEP
uv run ifc-sql ingest data/audit/Ifc4_Revit_ARC_FireRatingAdded.ifc --discipline ARC
uv run ifc-sql ingest data/audit/ARK_NUS_skolebygg.ifc --discipline ARC
uv run ifc-sql dq 5 6 7 8 9

# ground truth (B with geometry takes about 40 s on 4 cores)
.venv/bin/python -I $H/ground_truth.py data/audit/Ifc4_Revit_ARC.ifc out_A.json --geometry
.venv/bin/python -I $H/ground_truth.py data/audit/R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc out_B.json --geometry
.venv/bin/python -I $H/doors_on_level.py data/audit/R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc XX_AR_L01

# the MCP calls, as a client makes them
.venv/bin/python -I $H/architect_workflow.py .venv/bin/ifc-sql workflow.json 5 6 7

# six real Claude sessions (needs the claude CLI, logged in; about $1.50 in all)
$H/run_llm_questions.sh && .venv/bin/python -I $H/parse_transcript.py $H/out/q*.jsonl
```

The figures to compare against are in the report and in [`../evidence/`](../evidence/).
