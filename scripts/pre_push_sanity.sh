#!/usr/bin/env bash
# Pre-push sanity checks — fail if secrets, internal paths, or gitignored artifacts would ship.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository (run from repo root after git init)" >&2
  exit 1
fi

FAIL=0

echo "=== Pre-push sanity checks ==="

# 1) Internal / machine-specific path references in tracked files
if git grep -E '/home/beast|/beast/|backup_11_may|backup_1_june|Documents/cursor|nvidia_voice_backup' \
  -- ':!*.lock' ':!scripts/pre_push_sanity.sh' 2>/dev/null; then
  echo "FAIL: internal path references found in tracked files (see above)" >&2
  FAIL=1
else
  echo "OK   no internal path references"
fi

# 1b) Personal names / private presets that must not ship
if git grep -iE 'abhay|kiki|kumar singh|childminder|childminder_kiki|instruction2\.md|lite\.md|you are david|darpan' -- ':!scripts/pre_push_sanity.sh' 2>/dev/null; then
  echo "FAIL: personal/private preset content found in tracked files (see above)" >&2
  FAIL=1
else
  echo "OK   no personal preset names in tree"
fi

# 2) Real admin tokens (hex secrets, not REPLACE_WITH placeholders)
if git grep -E 'LISA_ADMIN_TOKEN=[0-9a-f]{20,}' 2>/dev/null; then
  echo "FAIL: LISA_ADMIN_TOKEN secret found in tracked files" >&2
  FAIL=1
else
  echo "OK   no LISA_ADMIN_TOKEN secrets in tree"
fi

# 3) Forbidden paths that must stay gitignored (.gitkeep skeleton files are OK)
BAD=$(git ls-files 2>/dev/null | grep -E 'rag_data/(attachments|documents|dedup)/|\.sqlite$|lisa_admin_token\.env$|(^|/)\.venv/|(^|/)\.cache/|offline_setup/models/|offline_setup/hf_cache/|offline_setup/bundle/|offline_setup/xtts_tts_data/|offline_setup/e2e_result\.txt' | grep -v '/\.gitkeep$' || true)
if [ -n "${BAD}" ]; then
  echo "FAIL: gitignored runtime/large artifacts are tracked:" >&2
  echo "${BAD}" >&2
  FAIL=1
else
  echo "OK   no forbidden runtime/large paths tracked"
fi

# 4) Individual tracked files larger than 10 MiB
LARGE=""
while IFS= read -r f; do
  [ -z "${f}" ] && continue
  if [ -f "${f}" ]; then
    sz=$(wc -c < "${f}" | tr -d ' ')
    if [ "${sz}" -gt 10485760 ]; then
      LARGE="${LARGE}${f} (${sz} bytes)\n"
    fi
  fi
done < <(git ls-files 2>/dev/null || true)
if [ -n "${LARGE}" ]; then
  echo -e "FAIL: tracked files exceed 10 MiB:\n${LARGE}" >&2
  FAIL=1
else
  echo "OK   no tracked file > 10 MiB"
fi

# 5) Summary stats
COUNT=$(git ls-files 2>/dev/null | wc -l | tr -d ' ')
echo "     tracked files: ${COUNT}"

if [ "${FAIL}" -ne 0 ]; then
  echo "=== Pre-push sanity: FAILED ===" >&2
  exit 1
fi

echo "=== Pre-push sanity: PASSED ==="
