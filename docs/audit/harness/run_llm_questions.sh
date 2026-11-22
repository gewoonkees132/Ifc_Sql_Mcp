#!/bin/bash
# Ask each architect question in a fresh headless Claude Code session that can use nothing
# but the ifc-sql MCP server (no shell, no file access), and keep the transcripts.
#
#   docs/audit/harness/run_llm_questions.sh            # all questions
#   ONLY="q3 q4" docs/audit/harness/run_llm_questions.sh
#
# Needs: the `claude` CLI, logged in; the models loaded as in the audit (model ids 5-7);
# `uv sync` done, so .venv/bin/ifc-sql exists. Transcripts go to $OUT (default ./out).
# Questions are read on fd 3 and claude's stdin is /dev/null, so no prompt leaks into another.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
OUT=${OUT:-$HERE/out}
mkdir -p "$OUT"
cat > "$OUT/mcp.json" <<EOF
{"mcpServers": {"ifc-sql": {"command": "$REPO/.venv/bin/ifc-sql", "args": ["serve"],
  "env": {"IFC_SQL_DATABASE_URL": "${IFC_SQL_DATABASE_URL:-postgresql+psycopg://localhost:5432/ifc}",
          "IFC_SQL_READER_PASSWORD": "${IFC_SQL_READER_PASSWORD:-ifc-reader-dev-only}"}}}}
EOF
cd "$OUT" || exit 1
while IFS=$'\t' read -r -u 3 id question; do
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then continue; fi
  start=$(date +%s)
  timeout 900 claude -p "$question" --tools "" --strict-mcp-config --mcp-config "$OUT/mcp.json" \
    --allowedTools "mcp__ifc-sql" --max-turns 30 --max-budget-usd 3 \
    --output-format stream-json --verbose < /dev/null > "$OUT/$id.jsonl" 2> "$OUT/$id.stderr"
  echo "$id exit=$? seconds=$(( $(date +%s) - start ))"
done 3< "$HERE/llm_questions.tsv"
