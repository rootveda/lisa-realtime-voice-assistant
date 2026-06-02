#!/usr/bin/env bash
# Internet-only RAG Lisa regression: download+assemble >=50 MiB per type, ingest, one pytest run,
# then optional minimum wall time (default 30 minutes) without repeating the test suite.
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

MIN_MIB="${MIN_MIB:-52}"
MIN_WALL_SEC="${MIN_WALL_SEC:-1800}"

cd "$APP_ROOT"
python3 -m pip install -q -r requirements.txt pytest

ASM_ARGS=(--min-mib "$MIN_MIB")
if [[ "${RUN_INTERNET_SMOKE:-}" == "1" ]]; then
  ASM_ARGS+=(--smoke)
fi

echo "[internet-lisa] Assembling corpus from HTTPS sources (no locally generated prose)..."
python3 "$SCRIPT_DIR/assemble_internet_corpus.py" "${ASM_ARGS[@]}"

export RAG_INTERNET_ENTERPRISE=1
echo "[internet-lisa] Pytest (9 types × 20 cases + manifest assertions; single pass)..."
python3 -m pytest tests/test_rag_internet_enterprise_regression.py -v --tb=short \
  | tee "${WORKSPACE_ROOT}/offline_setup/docs/RAG_INTERNET_ENTERPRISE_LAST_RUN.txt"

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))
REMAINING=$((MIN_WALL_SEC - ELAPSED))
if (( REMAINING > 0 )); then
  echo "[internet-lisa] Soak sleep ${REMAINING}s to reach MIN_WALL_SEC=${MIN_WALL_SEC}s..."
  sleep "$REMAINING"
fi
echo "[internet-lisa] Done. Wall clock ~ $(( $(date +%s) - START_TS ))s."
