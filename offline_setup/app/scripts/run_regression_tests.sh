#!/usr/bin/env bash
# Run all fast automated regression checks (no GPU, no live mic).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${APP_DIR}"
export PYTHONPATH=.

PY="${PYTHON:-python3}"
if [ -f "${APP_DIR}/../../.venv/bin/python" ]; then
  PY="${APP_DIR}/../../.venv/bin/python"
fi

echo "==> pytest (${PY})"
"${PY}" -m pytest tests/ -v --tb=short "$@"

E2E_DIR="${APP_DIR}/tests/e2e"
if [ -f "${E2E_DIR}/package.json" ] && command -v npm >/dev/null 2>&1; then
  if [ -d "${E2E_DIR}/node_modules/@playwright" ]; then
    echo "==> Playwright (face phase harness)"
    (cd "${E2E_DIR}" && npm test)
  else
    echo "==> Playwright skipped (run: cd tests/e2e && npm install && npx playwright install chromium)"
  fi
else
  echo "==> Playwright skipped (no npm or no package.json)"
fi

echo "==> Done."
