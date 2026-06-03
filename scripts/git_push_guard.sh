#!/usr/bin/env bash
# Block commit/push when secrets, TLS keys, or gitignored runtime data would ship.
# Used by .githooks/pre-commit and pre-push (install via scripts/install_git_hooks.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

MODE="${1:---all}"
if [ "${MODE}" != "--all" ] && [ "${MODE}" != "--staged" ]; then
  echo "Usage: $(basename "$0") [--all|--staged]" >&2
  exit 2
fi

if [ "${LISA_SKIP_PUSH_GUARD:-}" = "1" ]; then
  echo "WARN: LISA_SKIP_PUSH_GUARD=1 — push/commit guard skipped" >&2
  exit 0
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository" >&2
  exit 1
fi

FAIL=0
LABEL="pre-push"
[ "${MODE}" = "--staged" ] && LABEL="pre-commit"

echo "=== Git push guard (${LABEL}, ${MODE}) ==="

_staged_files() {
  git diff --cached --name-only --diff-filter=AM 2>/dev/null || true
}

_staged_grep() {
  local pattern="$1"
  local hits=""
  while IFS= read -r f; do
    [ -z "${f}" ] && continue
    case "${f}" in
      *.lock|scripts/git_push_guard.sh) continue ;;
    esac
    if git show ":${f}" 2>/dev/null | grep -qE "${pattern}"; then
      hits="${hits}${f}:$(git show ":${f}" 2>/dev/null | grep -E "${pattern}" | head -1)"$'\n'
    fi
  done < <(_staged_files)
  printf '%s' "${hits}"
}

# 1) Machine-specific paths
if [ "${MODE}" = "--staged" ]; then
  PATH_HITS=$(_staged_grep '/home/[a-zA-Z0-9._-]+/|backup_11_may|backup_1_june|Documents/cursor|nvidia_voice_backup')
else
  PATH_HITS=$(git grep -E '/home/[a-zA-Z0-9._-]+/|backup_11_may|backup_1_june|Documents/cursor|nvidia_voice_backup' \
    -- ':!*.lock' ':!scripts/git_push_guard.sh' 2>/dev/null || true)
fi
if [ -n "${PATH_HITS}" ]; then
  echo "FAIL: machine-specific path references (see above)" >&2
  echo "${PATH_HITS}" >&2
  FAIL=1
else
  echo "OK   no machine-specific path references"
fi

# 2) Personal / private preset strings
if [ "${MODE}" = "--staged" ]; then
  PERSONAL=$(_staged_grep 'abhay|kiki|kumar singh|childminder|childminder_kiki|instruction2\.md|lite\.md|you are david|darpan')
else
  PERSONAL=$(git grep -iE 'abhay|kiki|kumar singh|childminder|childminder_kiki|instruction2\.md|lite\.md|you are david|darpan' \
    -- ':!scripts/git_push_guard.sh' 2>/dev/null || true)
fi
if [ -n "${PERSONAL}" ]; then
  echo "FAIL: personal/private preset content" >&2
  echo "${PERSONAL}" >&2
  FAIL=1
else
  echo "OK   no personal preset names"
fi

# 3) Real admin tokens
if [ "${MODE}" = "--staged" ]; then
  TOKEN_HITS=$(_staged_grep 'LISA_ADMIN_TOKEN=[0-9a-f]{20,}')
else
  TOKEN_HITS=$(git grep -E 'LISA_ADMIN_TOKEN=[0-9a-f]{20,}' 2>/dev/null || true)
fi
if [ -n "${TOKEN_HITS}" ]; then
  echo "FAIL: LISA_ADMIN_TOKEN secret in changes" >&2
  echo "${TOKEN_HITS}" >&2
  FAIL=1
else
  echo "OK   no LISA_ADMIN_TOKEN hex secrets"
fi

# 4) PEM private keys in tree or staged blobs
if [ "${MODE}" = "--staged" ]; then
  while IFS= read -r f; do
    [ -z "${f}" ] && continue
    if git show ":${f}" 2>/dev/null | grep -qE 'BEGIN (RSA )?PRIVATE KEY'; then
      echo "FAIL: private key in staged file: ${f}" >&2
      FAIL=1
    fi
  done < <(git diff --cached --name-only --diff-filter=AM 2>/dev/null || true)
else
  if git grep -lE 'BEGIN (RSA )?PRIVATE KEY' 2>/dev/null; then
    echo "FAIL: PEM private key in tracked files (see above)" >&2
    FAIL=1
  fi
fi
if [ "${FAIL}" -eq 0 ]; then
  echo "OK   no PEM private keys"
fi

# 5) Forbidden paths (runtime, secrets, certs)
if [ "${MODE}" = "--staged" ]; then
  BAD=$(git diff --cached --name-only --diff-filter=AM 2>/dev/null | grep -E \
    'rag_data/(attachments|documents|dedup)/|\.sqlite$|lisa_admin_token\.env$|offline_setup/certs/.*\.(key|crt)$|(^|/)\.venv/|(^|/)\.cache/|offline_setup/models/|offline_setup/hf_cache/|offline_setup/bundle/|offline_setup/xtts_tts_data/|offline_setup/e2e_result\.txt|assistant_console_.*\.v1\.json' \
    | grep -v '/\.gitkeep$' || true)
else
  BAD=$(git ls-files 2>/dev/null | grep -E \
    'rag_data/(attachments|documents|dedup)/|\.sqlite$|lisa_admin_token\.env$|offline_setup/certs/.*\.(key|crt)$|(^|/)\.venv/|(^|/)\.cache/|offline_setup/models/|offline_setup/hf_cache/|offline_setup/bundle/|offline_setup/xtts_tts_data/|offline_setup/e2e_result\.txt|assistant_console_.*\.v1\.json' \
    | grep -v '/\.gitkeep$' || true)
fi
if [ -n "${BAD}" ]; then
  echo "FAIL: forbidden paths would ship:" >&2
  echo "${BAD}" >&2
  FAIL=1
else
  echo "OK   no forbidden runtime/secret paths"
fi

# 6) Large files (>10 MiB) in full-tree mode only
if [ "${MODE}" = "--all" ]; then
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
  COUNT=$(git ls-files 2>/dev/null | wc -l | tr -d ' ')
  echo "     tracked files: ${COUNT}"
fi

if [ "${FAIL}" -ne 0 ]; then
  echo "=== Git push guard: BLOCKED ===" >&2
  echo "Fix the issues above. Emergency bypass (not recommended): LISA_SKIP_PUSH_GUARD=1" >&2
  exit 1
fi

echo "=== Git push guard: OK ==="
