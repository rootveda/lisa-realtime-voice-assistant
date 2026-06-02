#!/usr/bin/env bash
# Lisa RAG regression: generate >=50 MiB per format, ingest, deterministic FTS tests,
# then optional soak sleep so wall time is at least 30 minutes (single pytest pass — no duplicate runs).
set -euo pipefail

START_TS=$(date +%s)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd "$APP_ROOT/../.." && pwd)"

export WORKSPACE_ROOT
export LOCAL_RAG_DATA_DIR="${LOCAL_RAG_DATA_DIR:-$WORKSPACE_ROOT/offline_setup/rag_data}"
export LOCAL_RAG_PDF_MAX_PAGES="${LOCAL_RAG_PDF_MAX_PAGES:-0}"
export LOCAL_RAG_PPTX_MAX_SLIDES="${LOCAL_RAG_PPTX_MAX_SLIDES:-0}"
export LOCAL_RAG_XLSX_MAX_SHEETS="${LOCAL_RAG_XLSX_MAX_SHEETS:-0}"

TARGET_MIB="${TARGET_MIB:-52}"
MIN_WALL_SEC="${MIN_WALL_SEC:-1800}"

DOCS_OUT="${DOCS_OUT:-$WORKSPACE_ROOT/offline_setup/docs/RAG_ENTERPRISE_REGRESSION.md}"
LOG_FRAGMENT="${LOG_FRAGMENT:-$WORKSPACE_ROOT/offline_setup/docs/RAG_ENTERPRISE_REGRESSION_LAST_RUN.txt}"

cd "$APP_ROOT"
python3 -m pip install -q -r requirements.txt pytest

echo "[rag-lisa] Generating corpus (>=${TARGET_MIB} MiB per file)..."
python3 "$SCRIPT_DIR/generate_corpus.py" --target-mib "$TARGET_MIB"

echo "[rag-lisa] Ingesting documents into FTS..."
python3 -c "from pipecat_bots.local_rag import init_schema, ingest_documents_dir; init_schema(); print(ingest_documents_dir())"

export RAG_ENTERPRISE_REGRESSION=1
echo "[rag-lisa] Running pytest (180 retrieval checks + manifest assertions)..."
set +e
python3 -m pytest tests/test_rag_enterprise_regression.py -v --tb=short | tee "$LOG_FRAGMENT"
PY=$?
set -e

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))
REMAINING=$((MIN_WALL_SEC - ELAPSED))
if (( REMAINING > 0 )); then
  echo "[rag-lisa] Soak sleep ${REMAINING}s to reach minimum wall time ${MIN_WALL_SEC}s..."
  sleep "$REMAINING"
fi
TOTAL=$(( $(date +%s) - START_TS ))

{
  echo ""
  echo "---"
  echo "## Append-only run log"
  echo "- Timestamp (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- WORKSPACE_ROOT: $WORKSPACE_ROOT"
  echo "- TARGET_MIB: $TARGET_MIB"
  echo "- Pytest exit code: $PY"
  echo "- Elapsed before soak (s): $ELAPSED"
  echo "- Total wall time (s): $TOTAL"
} >> "$DOCS_OUT"

exit "$PY"
