#!/usr/bin/env bash
#
# Single entrypoint: start | stop | restart | status for the Lisa offline stack.
# "start" always runs a clean stop first so no duplicate bots/proxies/containers linger.
#
# Does NOT stop the Ollama daemon (system service). On start, start_current_stack.sh runs
# ollama stop on all loaded runners (STACK_UNLOAD_OLLAMA_AT_START, default 1) so GPU VRAM
# is free for XTTS + llama. Stop still only tears down stack containers/processes.
# Local llama docker "lisa-llama-primary" and Nemotron ASR, XTTS, bot_vllm, tls_tcp_proxy are stopped.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${SCRIPT_DIR}"
APP_DIR="${BUNDLE_DIR}/app"
START_SCRIPT="${BUNDLE_DIR}/start_current_stack.sh"

LISA_ADMIN_ENV="${BUNDLE_DIR}/lisa_admin_token.env"

lisa_apply_home_dev_env() {
  unset LISA_BIND_PUBLIC LISA_ADMIN_TOKEN 2>/dev/null || true
  export LISA_BIND_LOOPBACK_ONLY=1
}

lisa_apply_admin_env() {
  if [ ! -f "${LISA_ADMIN_ENV}" ]; then
    echo "ERROR: ${LISA_ADMIN_ENV} not found." >&2
    echo "  cp offline_setup/lisa_admin_token.env.example offline_setup/lisa_admin_token.env" >&2
    echo "  Edit LISA_ADMIN_TOKEN, then: ./offline_setup/lisa_stack.sh start --admin" >&2
    exit 1
  fi
  set -a
  # shellcheck source=/dev/null
  source "${LISA_ADMIN_ENV}"
  set +a
  if [ -z "${LISA_ADMIN_TOKEN:-}" ]; then
    echo "ERROR: LISA_ADMIN_TOKEN is empty in ${LISA_ADMIN_ENV}" >&2
    exit 1
  fi
  export LISA_BIND_LOOPBACK_ONLY="${LISA_BIND_LOOPBACK_ONLY:-0}"
  export LISA_BIND_PUBLIC="${LISA_BIND_PUBLIC:-1}"
}

lisa_configure_bind_mode() {
  if [ "${admin_flag}" = "1" ]; then
    echo "=== Bind mode: admin (LAN + LISA_ADMIN_TOKEN from lisa_admin_token.env) ==="
    lisa_apply_admin_env
  else
    echo "=== Bind mode: home dev (loopback-only; admin from 127.0.0.1 without token) ==="
    lisa_apply_home_dev_env
  fi
}

usage() {
  echo "Usage: $(basename "$0") {start|stop|restart|status} [--admin] [--network-proxy]"
  echo ""
  echo "  start    Clean stop, then run start_current_stack.sh (full stack)."
  echo "  stop     Bot, TLS proxy, XTTS, lisa-llama-primary, Nemotron ASR."
  echo "  restart  Same as start."
  echo "  status   stack_mode, processes, docker, HTTP probes, vision, GPU+models table."
  echo ""
  echo "  --admin           LAN/public bind + LISA_ADMIN_TOKEN (sources offline_setup/lisa_admin_token.env)."
  echo "                    Default start/restart is home dev: loopback-only, no token required locally."
  echo "  --network-proxy   After start/restart: install/reload nginx LAN reverse proxy"
  echo "                    (see docs/network-proxy.md). Requires nginx + sudo. Does not stop"
  echo "                    nginx on stop; only prints a reminder if passed with stop."
  echo ""
  echo "Env: LISA_STACK_ADMIN=1 is equivalent to --admin."
  echo "     LISA_NETWORK_PROXY=1 is equivalent to --network-proxy."
  echo "     See docs/stack-start-stop.md, docs/admin-token-and-security.md, docs/network-proxy.md"
  echo "Ollama: vision + startup unload expect ollama serve on 127.0.0.1:11434 by default. If DOWN in status,"
  echo "        sudo bash ${SCRIPT_DIR}/scripts/ollama_listen_localhost.sh"
}

# Walk upward from offline_setup to find checkout root containing Network/nginx/lisa-bot.conf.
find_network_repo_root() {
  local d="${SCRIPT_DIR}"
  while [ "${d}" != "/" ]; do
    if [ -f "${d}/Network/nginx/lisa-bot.conf" ]; then
      echo "${d}"
      return 0
    fi
    d="$(dirname "${d}")"
  done
  return 1
}

lisa_network_proxy_after_start() {
  local repo_root helper
  repo_root="$(find_network_repo_root || true)"
  if [ -z "${repo_root}" ]; then
    echo "WARN: could not find Network/nginx/lisa-bot.conf above ${SCRIPT_DIR}; skipping nginx setup." >&2
    return 1
  fi
  helper="${repo_root}/Network/scripts/lisa-network-proxy.sh"
  if [ ! -f "${helper}" ]; then
    echo "WARN: missing ${helper}; skipping nginx setup." >&2
    return 1
  fi
  chmod +x "${helper}" 2>/dev/null || true
  if bash "${helper}" install "${repo_root}"; then
    return 0
  fi
  echo "WARN: nginx reverse-proxy setup failed (need sudo?). Bot is still running." >&2
  echo "      Retry: sudo ${helper} install ${repo_root}" >&2
  bash "${helper}" print-urls "${repo_root}" || true
  return 1
}

_kill_pid_file() {
  local label="$1"
  local pidfile="$2"
  if [ -f "${pidfile}" ]; then
    local pid
    pid="$(cat "${pidfile}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      echo "  Stopping ${label} (pid ${pid} from ${pidfile})..."
      kill "${pid}" 2>/dev/null || true
      for _i in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 "${pid}" 2>/dev/null; then
          break
        fi
        sleep 0.3
      done
      if kill -0 "${pid}" 2>/dev/null; then
        echo "  ${label} did not exit on SIGTERM; sending SIGKILL."
        kill -9 "${pid}" 2>/dev/null || true
      fi
    fi
    rm -f "${pidfile}" 2>/dev/null || true
  fi
}

lisa_stack_stop() {
  echo "=== Lisa stack: stopping ==="

  _kill_pid_file "bot" "${LISA_BOT_PID_FILE:-/tmp/lisa_bot.pid}"
  _kill_pid_file "tls proxy" "${LISA_TLS_PROXY_PID_FILE:-/tmp/lisa_tls_proxy.pid}"

  echo "  Stopping any remaining bot processes (fallback)..."
  pkill -f 'pipecat_bots/bot_vllm.py' 2>/dev/null || true
  pkill -f 'pipecat_bots/bot_interleaved_streaming.py' 2>/dev/null || true

  echo "  Stopping any remaining TLS proxy (fallback)..."
  pkill -f 'tls_tcp_proxy.py' 2>/dev/null || true

  echo "  Stopping local llama container (if any)..."
  docker rm -f lisa-llama-primary >/dev/null 2>&1 || true

  echo "  Stopping XTTS container..."
  docker stop xtts-tts >/dev/null 2>&1 || true
  docker rm -f xtts-tts >/dev/null 2>&1 || true

  echo "  Stopping Nemotron ASR..."
  if [ -x "${APP_DIR}/scripts/nemotron.sh" ]; then
    (cd "${APP_DIR}" && ./scripts/nemotron.sh stop) || true
  else
    # Fallback: stop by common container name
    docker ps --filter "name=nemotron" -q 2>/dev/null | while read -r id; do
      [ -n "${id}" ] && docker stop "${id}" >/dev/null 2>&1 || true
    done
  fi

  sleep 1
  echo "=== Stop finished ==="
}

lisa_stack_start() {
  lisa_stack_stop
  if [ ! -f "${START_SCRIPT}" ]; then
    echo "ERROR: ${START_SCRIPT} not found." >&2
    exit 1
  fi
  echo "=== Lisa stack: starting (${START_SCRIPT}) ==="
  lisa_configure_bind_mode
  # shellcheck source=/dev/null
  bash "${START_SCRIPT}"
}

check_url() {
  local label="$1"
  local url="$2"
  local curl_opts=(-sf --connect-timeout 2)
  case "${url}" in
    https://*) curl_opts=(-skf --connect-timeout 2) ;;
  esac
  if curl "${curl_opts[@]}" "${url}" >/dev/null 2>&1; then
    echo "  OK   ${label}"
    echo "       ${url}"
  else
    echo "  DOWN ${label}"
    echo "       ${url}"
  fi
}

# Defaults aligned with start_current_stack.sh + vision_caption.DEFAULT_VISION_OLLAMA_MODEL
lisa_stack_vision_status() {
  local ollama_host="${OLLAMA_HOST:-127.0.0.1}"
  local ollama_port="${OLLAMA_PORT:-11434}"
  local v1_base="${VISION_OLLAMA_BASE:-}"
  if [ -z "${v1_base}" ]; then
    if [[ "${ollama_host}" == http* ]]; then
      ollama_host="${ollama_host%/}"
      v1_base="${ollama_host}/v1"
    else
      v1_base="http://${ollama_host}:${ollama_port}/v1"
    fi
  fi
  local vision_model="${VISION_OLLAMA_MODEL:-gemma4:e2b-it-q4_K_M}"
  local bot_port="${BOT_PORT:-7860}"
  local enable_https="${ENABLE_HTTPS:-1}"
  local vision_base_url
  if [ "${enable_https}" = "1" ]; then
    vision_base_url="https://127.0.0.1:${bot_port}"
  else
    vision_base_url="http://127.0.0.1:${HTTP_BOT_PORT:-7861}"
  fi

  echo ""
  echo "=== Vision (requirements cross-check) ==="
  echo "  Effective VISION_OLLAMA_BASE (caption): ${v1_base}"
  echo "  Effective VISION_OLLAMA_MODEL (caption): ${vision_model}"
  echo "  VISION_CAPTION_OPENAI_COMPAT (env): ${VISION_CAPTION_OPENAI_COMPAT:-unset} (empty/unset + manual bot → code default tries compat first; start_current_stack exports 0)"

  local ollama_root="${v1_base%/v1}"

  if curl -sf --connect-timeout 2 "${ollama_root}/api/tags" >/dev/null 2>&1; then
    echo "  OK   Ollama /api/tags"
    echo "       ${ollama_root}/api/tags"
  else
    echo "  DOWN Ollama /api/tags (captions will fail)"
    echo "       ${ollama_root}/api/tags"
    echo "       Fix: sudo bash ${BUNDLE_DIR}/scripts/ollama_listen_localhost.sh"
    echo "       (zzz-ollama-bind.conf wins over override.conf if OLLAMA_HOST was pinned to a missing IP)"
  fi

  if curl -sf --connect-timeout 2 "${v1_base}/models" >/dev/null 2>&1; then
    echo "  OK   Ollama OpenAI-compat /v1/models"
    echo "       ${v1_base}/models"
  else
    echo "  WARN Ollama OpenAI-compat /v1/models (caption code falls back to /api/chat)"
    echo "       ${v1_base}/models"
  fi

  local tags_json
  tags_json="$(curl -sf --connect-timeout 3 "${ollama_root}/api/tags" 2>/dev/null || true)"
  if [ -n "${tags_json}" ]; then
    if command -v python3 >/dev/null 2>&1; then
      if printf '%s' "${tags_json}" | python3 -c "
import json, sys
m = sys.argv[1]
d = json.load(sys.stdin)
names = [x.get('name','') for x in d.get('models', [])]
sys.exit(0 if m in names else 1)
" "${vision_model}" 2>/dev/null; then
        echo "  OK   Ollama has vision model tag: ${vision_model}"
      else
        echo "  WARN Vision model not in \`ollama list\`: ${vision_model}"
        echo "       Set VISION_OLLAMA_MODEL to a pulled vision-capable tag, or: ollama pull ${vision_model}"
      fi
    else
      echo "  (skip model-in-list check: python3 not found)"
    fi
  else
    echo "  (skip model-in-list check: no tags JSON)"
  fi

  local probe="/tmp/lisa_stack_vision_probe.jpg"
  if command -v uv >/dev/null 2>&1; then
    if (cd "${APP_DIR}" && PYTHONPATH="${APP_DIR}" uv run python -c "from scripts.e2e_vision_voice_test import _test_jpeg_bytes; open('${probe}','wb').write(_test_jpeg_bytes())" 2>/dev/null); then
      local pv_code pv_body
      pv_body="$(mktemp)"
      pv_code="$(curl -sk -o "${pv_body}" -w '%{http_code}' --connect-timeout 60 \
        -X POST -F "image=@${probe};type=image/jpeg" \
        "${vision_base_url}/api/vision/preview" 2>/dev/null || echo "000")"
      if [ "${pv_code}" = "200" ] && command -v python3 >/dev/null 2>&1; then
        if python3 -c "
import json,sys
p='${pv_body}'
d=json.load(open(p))
ok=d.get('ok') is True
cap=(d.get('caption') or '').strip()
sys.exit(0 if ok and len(cap)>1 else 1)
" 2>/dev/null; then
          echo "  OK   POST /api/vision/preview (caption returned)"
          echo "       ${vision_base_url}/api/vision/preview"
        else
          echo "  WARN POST /api/vision/preview HTTP ${pv_code} but body not ok/empty"
          echo "       ${vision_base_url}/api/vision/preview"
        fi
      else
        echo "  WARN POST /api/vision/preview HTTP ${pv_code}"
        echo "       ${vision_base_url}/api/vision/preview"
      fi
      rm -f "${pv_body}" 2>/dev/null || true
    else
      echo "  WARN Could not build probe JPEG (uv run python in app dir failed)"
    fi
  else
    echo "  WARN Skipping /api/vision/preview probe (uv not found)"
  fi

  echo ""
  echo "  Recent vision merges (server log):"
  if [ -f /tmp/bot.log ] && command -v grep >/dev/null 2>&1; then
    grep vision_augment /tmp/bot.log 2>/dev/null | tail -n 3 | sed 's/^/    /' || echo "    (no vision_augment lines yet)"
  else
    echo "    (no /tmp/bot.log or grep)"
  fi

  echo ""
  echo "  Browser: ${vision_base_url}/mobile-voice-vision-test"
  echo "           ${vision_base_url}/assistant-console  (Live camera + text or voice)"
  echo "  Logs:    grep vision_augment /tmp/bot.log | tail"
  echo "  Ollama:  ollama ps   (caption model loads on first vision question)"
}

lisa_stack_status() {
  echo "=== Stack mode file (last start) ==="
  if [ -f /tmp/stack_mode.txt ]; then
    cat /tmp/stack_mode.txt | sed 's/^/  /'
  else
    echo "  (missing /tmp/stack_mode.txt — stack may never have started)"
  fi

  echo ""
  echo "=== Processes (bot / TLS proxy) ==="
  if pgrep -af 'pipecat_bots/bot_vllm.py' >/dev/null 2>&1; then
    pgrep -af 'pipecat_bots/bot_vllm.py' | sed 's/^/  /'
  else
    echo "  (no bot_vllm)"
  fi
  if pgrep -af 'tls_tcp_proxy.py' >/dev/null 2>&1; then
    pgrep -af 'tls_tcp_proxy.py' | sed 's/^/  /'
  else
    echo "  (no tls_tcp_proxy)"
  fi

  echo ""
  echo "=== Docker (xtts / llama / nemotron) ==="
  if command -v docker >/dev/null 2>&1; then
    docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
      | awk 'NR==1 || /xtts-tts|lisa-llama-primary|nemotron/' || echo "  (none matched)"
  else
    echo "  (docker not in PATH)"
  fi

  echo ""
  echo "=== HTTP checks ==="
  check_url "ASR health" "http://127.0.0.1:8080/health"
  check_url "XTTS studio_speakers" "http://127.0.0.1:80/studio_speakers"
  check_url "Bot runtime-status" "http://127.0.0.1:7861/api/runtime-status"
  check_url "HTTPS runtime-status" "https://127.0.0.1:7860/api/runtime-status"
  check_url "HTTPS vision API info" "https://127.0.0.1:7860/api/mobile-voice-vision"
  check_url "HTTPS instructions manager" "https://127.0.0.1:7860/instructions-manager"

  if [ -f /etc/nginx/sites-enabled/lisa-bot.conf ] || [ -L /etc/nginx/sites-enabled/lisa-bot.conf ]; then
    echo ""
    echo "=== nginx LAN proxy (sites-enabled/lisa-bot.conf) ==="
    check_url "nginx → bot (LAN HTTPS :${LISA_NGINX_LAN_PORT:-8088})" "https://127.0.0.1:${LISA_NGINX_LAN_PORT:-8088}/api/runtime-status"
  fi

  lisa_stack_vision_status

  echo ""
  echo "=== GPU & models (summary) ==="
  if command -v python3 >/dev/null 2>&1 && [ -f "${BUNDLE_DIR}/scripts/stack_gpu_summary.py" ]; then
    export STACK_STATUS_BOT_HTTP="http://127.0.0.1:${HTTP_BOT_PORT:-7861}"
    export STACK_STATUS_BOT_HTTPS="https://127.0.0.1:${BOT_PORT:-7860}"
    python3 "${BUNDLE_DIR}/scripts/stack_gpu_summary.py" 2>/dev/null || echo "  (stack_gpu_summary.py failed)"
  else
    echo "  (need python3 and scripts/stack_gpu_summary.py)"
  fi
}

cmd="${1:-}"
if [ -z "${cmd}" ]; then
  usage >&2
  exit 1
fi
shift

admin_flag=0
network_proxy_flag=0
while [ $# -gt 0 ]; do
  case "$1" in
    --admin)
      admin_flag=1
      ;;
    --network-proxy)
      network_proxy_flag=1
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

if [ "${LISA_STACK_ADMIN:-0}" = "1" ]; then
  admin_flag=1
fi
if [ "${LISA_NETWORK_PROXY:-0}" = "1" ]; then
  network_proxy_flag=1
fi

case "${cmd}" in
  stop)
    lisa_stack_stop
    if [ "${network_proxy_flag}" = "1" ]; then
      echo ""
      echo "Note: system nginx was not stopped. The Lisa proxy site may still listen (default :8088)."
      echo "  Disable site: sudo rm /etc/nginx/sites-enabled/lisa-bot.conf && sudo nginx -t && sudo systemctl reload nginx"
      echo "  Stop nginx:   sudo systemctl stop nginx"
    fi
    ;;
  start)
    lisa_stack_start
    if [ "${network_proxy_flag}" = "1" ]; then
      lisa_network_proxy_after_start || true
    fi
    ;;
  restart)
    lisa_stack_start
    if [ "${network_proxy_flag}" = "1" ]; then
      lisa_network_proxy_after_start || true
    fi
    ;;
  status)
    lisa_stack_status
    ;;
  help|-h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
