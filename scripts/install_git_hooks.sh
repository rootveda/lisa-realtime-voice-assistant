#!/usr/bin/env bash
# Install pre-commit + pre-push guards into .git/hooks (no git config changes).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository: ${ROOT}" >&2
  exit 1
fi

HOOKS_SRC="${ROOT}/.githooks"
GIT_DIR="$(git rev-parse --git-dir)"
HOOKS_DST="$(cd "${GIT_DIR}" && pwd)/hooks"
mkdir -p "${HOOKS_DST}"

for name in pre-commit pre-push; do
  src="${HOOKS_SRC}/${name}"
  dst="${HOOKS_DST}/${name}"
  if [ ! -f "${src}" ]; then
    echo "ERROR: missing ${src}" >&2
    exit 1
  fi
  cp "${src}" "${dst}"
  chmod 755 "${dst}"
  echo "Installed ${dst}"
done

echo ""
echo "Git will now block commits/pushes that include secrets, TLS keys, or runtime data."
echo "Re-run this script after cloning on a new machine."
echo "Emergency bypass only: LISA_SKIP_PUSH_GUARD=1 git push"
