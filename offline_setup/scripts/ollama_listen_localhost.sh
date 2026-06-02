#!/usr/bin/env bash
# Bind Ollama's HTTP API so local apps using defaults (127.0.0.1:11434) work,
# e.g. vision captions (VISION_OLLAMA_BASE / OLLAMA_HOST in the voice stack).
#
# Drop-in name is zzz-ollama-bind.conf so it sorts AFTER override.conf/other fragments
# that may force OLLAMA_HOST (e.g. WireGuard-only 10.x); those overrides were causing
# "bind: cannot assign requested address" when that IP is not on the host.
set -euo pipefail

DROPIN_DIR="/etc/systemd/system/ollama.service.d"
# Sorts last among typical names (override.conf, listen-localhost.conf, …).
DROPIN="${DROPIN_DIR}/zzz-ollama-bind.conf"
LEGACY_DROPIN="${DROPIN_DIR}/listen-localhost.conf"

if [[ "${1:-}" == "--all-interfaces" ]]; then
  HOST_BIND="0.0.0.0:11434"
else
  HOST_BIND="127.0.0.1:11434"
fi

if [[ -f /etc/systemd/system/ollama.service.d/override.conf ]] && grep -q 'OLLAMA_HOST=10\.' /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null; then
  echo "NOTE: override.conf sets OLLAMA_HOST on a private IP. ${DROPIN##*/} is installed so it wins;" >&2
  echo "      edit or remove override.conf if you want that file to control binding again." >&2
fi

sudo mkdir -p "${DROPIN_DIR}"
sudo tee "${DROPIN}" > /dev/null << EOF
# Managed by offline_setup/scripts/ollama_listen_localhost.sh (loads last; wins over override.conf)
[Service]
Environment="OLLAMA_HOST=${HOST_BIND}"
EOF

# Old script name sorted before override.conf and had no effect.
if [[ -f "${LEGACY_DROPIN}" ]]; then
  sudo rm -f "${LEGACY_DROPIN}"
fi

sudo systemctl daemon-reload
sudo systemctl restart ollama

echo "Ollama service updated: OLLAMA_HOST=${HOST_BIND} (via ${DROPIN##*/})"
echo "Check: curl -sS http://127.0.0.1:11434/api/tags | head -c 200"
echo "Revert: sudo rm ${DROPIN} && sudo systemctl daemon-reload && sudo systemctl restart ollama"
