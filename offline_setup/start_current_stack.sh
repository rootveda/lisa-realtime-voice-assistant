#!/usr/bin/env bash
# Canonical startup for current Lisa runtime:
# - ASR only (nemotron container, port 8080)
# - XTTS (port 80, voice: Andrew Chipper)
# - Ollama Gemma4 via OpenAI endpoint
# - bot_vllm on 7861
# - HTTPS proxy on 7860

set -euo pipefail

# Best-effort: release GPU memory held by Ollama runners (they survive docker stop / stack stop).
# Disable with STACK_UNLOAD_OLLAMA_AT_START=0 if you need models kept hot.
unload_ollama_gpu_runners() {
  local pause_sec="${1:-4}"
  command -v ollama >/dev/null 2>&1 || return 0
  echo "Unloading Ollama GPU runners (ollama stop)..."
  for _m in gemma4:26b-a4b-it-q4_K_M gemma4:31b gemma4:e2b-it-q4_K_M gemma4:e4b; do
    ollama stop "${_m}" >/dev/null 2>&1 || true
  done
  if ollama ps >/dev/null 2>&1; then
    while IFS= read -r _n; do
      [ -n "${_n}" ] && ollama stop "${_n}" >/dev/null 2>&1 || true
    done < <(ollama ps 2>/dev/null | awk 'NR>1 {print $1}')
  fi
  sleep "${pause_sec}"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${SCRIPT_DIR}"
APP_DIR="${BUNDLE_DIR}/app"
REPO_ROOT="$(cd "${BUNDLE_DIR}/.." && pwd)"
HF_CACHE="${BUNDLE_DIR}/hf_cache"

# `ollama` CLI (ps/stop) uses OLLAMA_HOST. Align with VISION_OLLAMA_BASE defaults so a stale shell
# OLLAMA_HOST (e.g. old WireGuard bind) does not break unload_ollama_gpu_runners. If the configured
# root is down but loopback answers, prefer loopback (matches zzz-ollama-bind / local ollama serve).
_vision_base_for_cli="${VISION_OLLAMA_BASE:-http://127.0.0.1:11434/v1}"
OLLAMA_API_ROOT="${_vision_base_for_cli%/v1}"
OLLAMA_API_ROOT="${OLLAMA_API_ROOT%/}"
export OLLAMA_HOST="${OLLAMA_HOST:-${OLLAMA_API_ROOT}}"
if ! curl -sf --connect-timeout 2 "${OLLAMA_API_ROOT}/api/tags" >/dev/null 2>&1; then
  if curl -sf --connect-timeout 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    export OLLAMA_HOST="http://127.0.0.1:11434"
    echo "NOTE: Ollama reachable at http://127.0.0.1:11434 — using that for ollama CLI (unload/stop)." >&2
  fi
fi

# Default to loopback bind for safety; opt in to LAN by setting LISA_BIND_PUBLIC=1
# (or BOT_HOST=0.0.0.0 explicitly).
if [ -z "${BOT_HOST:-}" ]; then
  if [ "${LISA_BIND_PUBLIC:-0}" = "1" ]; then
    BOT_HOST="0.0.0.0"
  else
    BOT_HOST="127.0.0.1"
  fi
fi
BOT_PORT="${BOT_PORT:-7860}"
HTTP_BOT_PORT="${HTTP_BOT_PORT:-7861}"
ENABLE_HTTPS="${ENABLE_HTTPS:-1}"
# Auth gate for mutating /api/* (see pipecat_offline_patch.py header).
LISA_BIND_LOOPBACK_ONLY="${LISA_BIND_LOOPBACK_ONLY:-1}"
LISA_ADMIN_TOKEN="${LISA_ADMIN_TOKEN:-}"
# Opt-in: Assistant Console "Run context stress" calls POST /api/context-stress/run (see pipecat_offline_patch).
ENABLE_CONTEXT_STRESS_RUN_API="${ENABLE_CONTEXT_STRESS_RUN_API:-}"
CONTEXT_STRESS_RUN_TOKEN="${CONTEXT_STRESS_RUN_TOKEN:-}"
CONTEXT_STRESS_BASE_URL="${CONTEXT_STRESS_BASE_URL:-}"
# Phase 2 (env propagation): make sure bot subprocess sees attachment cap so
# /api/chat/attachments/limits returns the operator-configured value.
CHAT_ATTACH_MAX_BYTES="${CHAT_ATTACH_MAX_BYTES:-}"

HTTPS_CERT_DIR="${BUNDLE_DIR}/certs"
HTTPS_CERT_FILE="${HTTPS_CERT_DIR}/localhost.crt"
HTTPS_KEY_FILE="${HTTPS_CERT_DIR}/localhost.key"

export HF_HUB_OFFLINE=1
export HF_CACHE_ROOT_OVERRIDE="${HF_CACHE}"
export WORKSPACE_ROOT="${REPO_ROOT}"
export REPO_ROOT="${REPO_ROOT}"

export XTTS_TTS_DATA_DIR="${BUNDLE_DIR}/xtts_tts_data"
export XTTS_HF_CACHE_DIR="${BUNDLE_DIR}/xtts_hf_cache"
export XTTS_OFFLINE=1
mkdir -p "${XTTS_TTS_DATA_DIR}" "${XTTS_HF_CACHE_DIR}"

echo "============================================"
echo "Lisa Current Stack (Gemma4 + XTTS + ASR)"
echo "============================================"

# 1) ASR-only nemotron
cd "${APP_DIR}"
./scripts/nemotron.sh stop 2>/dev/null || true
./scripts/nemotron.sh start --no-llm --no-tts

echo "Waiting for ASR..."
for i in $(seq 1 90); do
  if curl -sf --connect-timeout 2 "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
    echo "  ASR ready."
    break
  fi
  if [ "$i" = "90" ]; then
    echo "ERROR: ASR failed health check on :8080"
    exit 1
  fi
  sleep 2
done

# 1b) Ollama keeps VRAM after prior sessions; unload before XTTS so TTS/llama have headroom.
if [ "${STACK_UNLOAD_OLLAMA_AT_START:-1}" = "1" ]; then
  _ollama_probe="${OLLAMA_HOST:-http://127.0.0.1:11434}"
  [[ "${_ollama_probe}" == http://* || "${_ollama_probe}" == https://* ]] || _ollama_probe="http://${_ollama_probe}"
  _ollama_probe="${_ollama_probe%/}"
  if curl -sf --connect-timeout 2 "${_ollama_probe}/api/tags" >/dev/null 2>&1; then
    unload_ollama_gpu_runners 4
  else
    echo "WARN: Ollama not reachable at ${_ollama_probe}/api/tags — skipping ollama GPU unload." >&2
    echo "      Vision captions need ollama serve on loopback (or set VISION_OLLAMA_BASE to your API root)." >&2
    echo "      Fix bind: sudo bash ${SCRIPT_DIR}/scripts/ollama_listen_localhost.sh   (see docs/stack-start-stop.md)" >&2
  fi
fi

# 2) XTTS (force recreate to recover from possible stale CUDA assert state)
docker rm -f xtts-tts >/dev/null 2>&1 || true
bash "${REPO_ROOT}/scripts/start_xtts.sh"

echo "Waiting for XTTS..."
for i in $(seq 1 120); do
  if curl -sf --connect-timeout 2 "http://127.0.0.1:80/studio_speakers" >/dev/null 2>&1; then
    echo "  XTTS ready."
    break
  fi
  if [ "$i" = "120" ]; then
    echo "ERROR: XTTS failed health check on :80"
    exit 1
  fi
  sleep 2
done
# Vision + LLM + XTTS on one GPU often exhaust VRAM (XTTS returns HTTP 500 / CUDA OOM in docker logs).
# Mitigations: ollama stop after caption, lower LOCAL_LLAMA_N_GPU_LAYERS, XTTS_GPU_DEVICE=1, or XTTS_CPU=1.

# 3) Bot pinned to Ollama + XTTS
pkill -f 'pipecat_bots/bot_vllm.py' 2>/dev/null || true
pkill -f 'pipecat_bots/bot_interleaved_streaming.py' 2>/dev/null || true
docker rm -f lisa-llama-primary >/dev/null 2>&1 || true

# 3a) Primary local llama-server (Gemma4), with Ollama fallback
USE_LOCAL_LLAMA_PRIMARY="${USE_LOCAL_LLAMA_PRIMARY:-1}"
LOCAL_LLAMA_IMAGE="${LOCAL_LLAMA_IMAGE:-lisa-llama-gemma4:latest}"
LOCAL_LLAMA_MODEL_FILE="${LOCAL_LLAMA_MODEL_FILE:-${BUNDLE_DIR}/models/gemma-4-26B-A4B-it-Q4_K_M.gguf}"
LOCAL_LLAMA_MODEL_NAME="${LOCAL_LLAMA_MODEL_NAME:-gemma4-26b-a4b-it-q4_K_M-local}"
# Keep headroom for XTTS + ASR + Ollama vision on the same GPU.
LOCAL_LLAMA_N_GPU_LAYERS="${LOCAL_LLAMA_N_GPU_LAYERS:-28}"
# KV cache grows with context; very large ctx needs VRAM — default raised so 26B + vision prompts fit.
LOCAL_LLAMA_CTX_SIZE="${LOCAL_LLAMA_CTX_SIZE:-49152}"
LOCAL_LLAMA_HEALTH_TIMEOUT="${LOCAL_LLAMA_HEALTH_TIMEOUT:-300}"
PRIMARY_LLM_READY=0

if [ "${USE_LOCAL_LLAMA_PRIMARY}" = "1" ] && [ -f "${LOCAL_LLAMA_MODEL_FILE}" ]; then
  LOCAL_LLAMA_AVAILABLE=1
  if ! docker image inspect "${LOCAL_LLAMA_IMAGE}" >/dev/null 2>&1; then
    echo "Building local Gemma4 llama runtime image (one-time)..."
    if ! docker build -f "${REPO_ROOT}/Dockerfile.llama-gemma4-runtime" -t "${LOCAL_LLAMA_IMAGE}" "${REPO_ROOT}"; then
      echo "WARNING: local llama image build failed; using Ollama fallback."
      LOCAL_LLAMA_AVAILABLE=0
    fi
  fi

  if [ "${LOCAL_LLAMA_AVAILABLE}" = "1" ]; then
    # Ollama was already unloaded before XTTS; no second stop here (saves startup time).
    echo "Starting local llama primary on :8000 (ctx-size=${LOCAL_LLAMA_CTX_SIZE})..."
    if docker run -d \
      --name lisa-llama-primary \
      --gpus all \
      --network host \
      -v "${LOCAL_LLAMA_MODEL_FILE}:/models/model.gguf:ro" \
      "${LOCAL_LLAMA_IMAGE}" \
        -m /models/model.gguf \
        --host 127.0.0.1 \
        --port 8000 \
        --n-gpu-layers "${LOCAL_LLAMA_N_GPU_LAYERS}" \
        --ctx-size "${LOCAL_LLAMA_CTX_SIZE}" \
        --parallel 1 \
        --flash-attn on \
        --no-mmap \
        --no-warmup \
        --jinja \
        --reasoning off \
        --reasoning-budget 0 \
        --reasoning-format none >/dev/null; then
      for i in $(seq 1 "${LOCAL_LLAMA_HEALTH_TIMEOUT}"); do
        if curl -sf "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
          PRIMARY_LLM_READY=1
          break
        fi
        sleep 1
      done
      if [ "${PRIMARY_LLM_READY}" != "1" ]; then
        echo "WARNING: local llama did not become healthy within ${LOCAL_LLAMA_HEALTH_TIMEOUT}s (check VRAM / logs)."
        if docker inspect lisa-llama-primary >/dev/null 2>&1; then
          echo "---- docker logs lisa-llama-primary (tail) ----"
          docker logs lisa-llama-primary 2>&1 | tail -n 50 || true
        fi
      fi
    else
      echo "WARNING: local llama container failed to start; using Ollama fallback."
    fi
  fi
fi

export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
if [ "${PRIMARY_LLM_READY}" = "1" ]; then
  export NVIDIA_LLM_URL="${NVIDIA_LLM_URL:-http://127.0.0.1:8000/v1}"
  # Do not use NVIDIA_LLM_MODEL here (often set to an Ollama tag); local OpenAI id is fixed.
  export NVIDIA_LLM_MODEL="${NVIDIA_LLM_LOCAL_OPENAI_MODEL:-${LOCAL_LLAMA_MODEL_NAME}}"
  echo "LLM primary: local llama-server (${NVIDIA_LLM_URL}) model=${NVIDIA_LLM_MODEL}"
  printf "mode=primary\nprovider=local-llama\nurl=%s\nmodel=%s\n" "${NVIDIA_LLM_URL}" "${NVIDIA_LLM_MODEL}" > /tmp/stack_mode.txt
else
  export NVIDIA_LLM_URL="${NVIDIA_LLM_URL:-http://127.0.0.1:11434/v1}"
  # Smaller Gemma4 by default (~7 GiB) so XTTS + vision fit a single consumer GPU.
  export NVIDIA_LLM_MODEL="${NVIDIA_LLM_MODEL:-gemma4:e2b-it-q4_K_M}"
  echo "LLM fallback: Ollama (${NVIDIA_LLM_URL})"
  printf "mode=fallback\nprovider=ollama\nurl=%s\nmodel=%s\n" "${NVIDIA_LLM_URL}" "${NVIDIA_LLM_MODEL}" > /tmp/stack_mode.txt
fi
export NVIDIA_LLM_API_KEY="${NVIDIA_LLM_API_KEY:-not-needed}"

# Shared context for llama-server ``--ctx-size``, Ollama ``num_ctx`` (text + vision caption), and proxy trim budgets.
export LLM_CONTEXT_SIZE="${LLM_CONTEXT_SIZE:-${LOCAL_LLAMA_CTX_SIZE}}"
if [ -z "${TEXT_CHAT_MAX_PROMPT_TOKENS:-}" ]; then
  export TEXT_CHAT_MAX_PROMPT_TOKENS=$(( LLM_CONTEXT_SIZE * 88 / 100 - 8192 ))
fi
if [ -z "${TEXT_CHAT_MAX_PROMPT_CHARS:-}" ]; then
  export TEXT_CHAT_MAX_PROMPT_CHARS=$(( TEXT_CHAT_MAX_PROMPT_TOKENS * 4 ))
fi
export VOICE_COMPLETION_MAX_TOKENS_DEFAULT="${VOICE_COMPLETION_MAX_TOKENS_DEFAULT:-8192}"
export VISION_OLLAMA_NUM_CTX="${VISION_OLLAMA_NUM_CTX:-${LLM_CONTEXT_SIZE}}"
export STT_PROVIDER="${STT_PROVIDER:-nemotron-websocket}"
export NVIDIA_ASR_MODEL="${NVIDIA_ASR_MODEL:-nvidia/nemotron-speech-streaming-en-0.6b}"
export TTS_PROVIDER="xtts"
export XTTS_TTS_URL="${XTTS_TTS_URL:-http://127.0.0.1:80}"
export XTTS_VOICE_ID="${XTTS_VOICE_ID:-Andrew Chipper}"
export PIPECAT_USE_STUN="${PIPECAT_USE_STUN:-0}"
export PIPECAT_USE_TURN="${PIPECAT_USE_TURN:-0}"

# Vision captions use Ollama’s OpenAI-compatible API (separate from dialogue LLM when using local llama).
export VISION_OLLAMA_BASE="${VISION_OLLAMA_BASE:-http://127.0.0.1:11434/v1}"
# Image captions via Ollama (vision-capable Gemma4; smaller default to leave VRAM for XTTS).
export VISION_OLLAMA_MODEL="${VISION_OLLAMA_MODEL:-gemma4:e2b-it-q4_K_M}"
export VISION_OLLAMA_TIMEOUT="${VISION_OLLAMA_TIMEOUT:-45}"
# Native /api/chat only for vision captions (one VLM hop). Set to 1 to try OpenAI compat first (legacy / fallback).
export VISION_CAPTION_OPENAI_COMPAT="${VISION_CAPTION_OPENAI_COMPAT:-0}"

BOT_LISTEN_PORT="${HTTP_BOT_PORT}"
if [ "${ENABLE_HTTPS}" != "1" ]; then
  BOT_LISTEN_PORT="${BOT_PORT}"
fi

export LOCAL_TOOLS_ENABLE="${LOCAL_TOOLS_ENABLE:-1}"
export TOOL_HTTP_ALLOW_HOSTS="${TOOL_HTTP_ALLOW_HOSTS:-127.0.0.1,localhost,::1}"
export TOOL_HTTP_ALLOW_SCHEMES="${TOOL_HTTP_ALLOW_SCHEMES:-http,https}"

# Optional face recognition: set FACERECOG_* in the shell before this script, or enable at runtime
# (Assistant Voice+Video+Face → POST /api/face/runtime; persisted under app/rag_data/facerecog_runtime.json).

LISA_BOT_PID_FILE="${LISA_BOT_PID_FILE:-/tmp/lisa_bot.pid}"
LISA_TLS_PROXY_PID_FILE="${LISA_TLS_PROXY_PID_FILE:-/tmp/lisa_tls_proxy.pid}"
LISA_LOG_KEEP_BYTES="${LISA_LOG_KEEP_BYTES:-5242880}"  # 5 MiB

# Best-effort log rotation: when the previous run left a large log, rename to .prev
# so we have one archived copy without the file growing unboundedly across restarts.
_rotate_log_if_big() {
  local log_path="$1"
  if [ -f "${log_path}" ]; then
    local sz
    sz="$(stat -c '%s' "${log_path}" 2>/dev/null || echo 0)"
    if [ "${sz}" -gt "${LISA_LOG_KEEP_BYTES}" ]; then
      mv -f "${log_path}" "${log_path}.prev" 2>/dev/null || true
      echo "Rotated ${log_path} (${sz} bytes) -> ${log_path}.prev"
    fi
  fi
}
_rotate_log_if_big /tmp/bot.log
_rotate_log_if_big /tmp/https_proxy.log

nohup env \
  NVIDIA_LLM_URL="${NVIDIA_LLM_URL}" \
  NVIDIA_LLM_MODEL="${NVIDIA_LLM_MODEL}" \
  NVIDIA_LLM_API_KEY="${NVIDIA_LLM_API_KEY}" \
  LLM_CONTEXT_SIZE="${LLM_CONTEXT_SIZE}" \
  TEXT_CHAT_MAX_PROMPT_TOKENS="${TEXT_CHAT_MAX_PROMPT_TOKENS}" \
  TEXT_CHAT_MAX_PROMPT_CHARS="${TEXT_CHAT_MAX_PROMPT_CHARS}" \
  VOICE_COMPLETION_MAX_TOKENS_DEFAULT="${VOICE_COMPLETION_MAX_TOKENS_DEFAULT}" \
  VISION_OLLAMA_NUM_CTX="${VISION_OLLAMA_NUM_CTX}" \
  STT_PROVIDER="${STT_PROVIDER}" \
  NVIDIA_ASR_MODEL="${NVIDIA_ASR_MODEL}" \
  TTS_PROVIDER="${TTS_PROVIDER}" \
  XTTS_TTS_URL="${XTTS_TTS_URL}" \
  XTTS_VOICE_ID="${XTTS_VOICE_ID}" \
  LOCAL_LLAMA_CTX_SIZE="${LOCAL_LLAMA_CTX_SIZE:-}" \
  PIPECAT_USE_STUN="${PIPECAT_USE_STUN}" \
  PIPECAT_USE_TURN="${PIPECAT_USE_TURN}" \
  VISION_OLLAMA_BASE="${VISION_OLLAMA_BASE}" \
  VISION_OLLAMA_MODEL="${VISION_OLLAMA_MODEL}" \
  VISION_OLLAMA_TIMEOUT="${VISION_OLLAMA_TIMEOUT}" \
  VISION_CAPTION_OPENAI_COMPAT="${VISION_CAPTION_OPENAI_COMPAT}" \
  LOCAL_TOOLS_ENABLE="${LOCAL_TOOLS_ENABLE}" \
  TOOL_HTTP_ALLOW_HOSTS="${TOOL_HTTP_ALLOW_HOSTS}" \
  TOOL_HTTP_ALLOW_SCHEMES="${TOOL_HTTP_ALLOW_SCHEMES}" \
  TOOL_HTTP_ALLOW_PRIVATE_LAN="${TOOL_HTTP_ALLOW_PRIVATE_LAN:-}" \
  TOOL_HTTP_ALLOW_CIDRS="${TOOL_HTTP_ALLOW_CIDRS:-}" \
  FACERECOG_ENABLED="${FACERECOG_ENABLED:-}" \
  FACERECOG_STUB_SIMPLE="${FACERECOG_STUB_SIMPLE:-}" \
  FACERECOG_DATA_DIR="${FACERECOG_DATA_DIR:-}" \
  FACERECOG_INSTRUCTIONS_PATH="${FACERECOG_INSTRUCTIONS_PATH:-}" \
  ENABLE_CONTEXT_STRESS_RUN_API="${ENABLE_CONTEXT_STRESS_RUN_API:-}" \
  CONTEXT_STRESS_RUN_TOKEN="${CONTEXT_STRESS_RUN_TOKEN:-}" \
  CONTEXT_STRESS_BASE_URL="${CONTEXT_STRESS_BASE_URL:-}" \
  LISA_BIND_LOOPBACK_ONLY="${LISA_BIND_LOOPBACK_ONLY}" \
  LISA_ADMIN_TOKEN="${LISA_ADMIN_TOKEN}" \
  CHAT_ATTACH_MAX_BYTES="${CHAT_ATTACH_MAX_BYTES}" \
  HTTP_BOT_PORT="${HTTP_BOT_PORT}" \
  BOT_PORT="${BOT_PORT}" \
  BOT_LISTEN_PORT="${BOT_LISTEN_PORT}" \
  uv run python "${APP_DIR}/pipecat_bots/bot_vllm.py" \
    --host "${BOT_HOST}" --port "${BOT_LISTEN_PORT}" \
  > /tmp/bot.log 2>&1 &
LISA_BOT_PID="$!"
echo "${LISA_BOT_PID}" > "${LISA_BOT_PID_FILE}"
echo "Bot PID ${LISA_BOT_PID} (pidfile ${LISA_BOT_PID_FILE}, log /tmp/bot.log)"

# 4) HTTPS proxy
if [ "${ENABLE_HTTPS}" = "1" ]; then
  mkdir -p "${HTTPS_CERT_DIR}"
  if [ ! -f "${HTTPS_CERT_FILE}" ] || [ ! -f "${HTTPS_KEY_FILE}" ]; then
    if ! openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
        -keyout "${HTTPS_KEY_FILE}" \
        -out "${HTTPS_CERT_FILE}" \
        -subj "/CN=localhost" >/tmp/lisa_openssl.log 2>&1; then
      echo "ERROR: openssl failed to create self-signed cert. See /tmp/lisa_openssl.log"
      exit 1
    fi
  fi
  if [ ! -r "${HTTPS_CERT_FILE}" ] || [ ! -r "${HTTPS_KEY_FILE}" ]; then
    echo "ERROR: TLS cert/key not readable: ${HTTPS_CERT_FILE} / ${HTTPS_KEY_FILE}"
    exit 1
  fi
  if [ -f "${LISA_TLS_PROXY_PID_FILE}" ]; then
    _old_proxy_pid="$(cat "${LISA_TLS_PROXY_PID_FILE}" 2>/dev/null || true)"
    if [ -n "${_old_proxy_pid}" ] && kill -0 "${_old_proxy_pid}" 2>/dev/null; then
      kill "${_old_proxy_pid}" 2>/dev/null || true
    fi
  fi
  pkill -f tls_tcp_proxy.py 2>/dev/null || true
  nohup uv run python "${APP_DIR}/scripts/tls_tcp_proxy.py" \
    --listen-host "${BOT_HOST}" \
    --listen-port "${BOT_PORT}" \
    --target-host "127.0.0.1" \
    --target-port "${BOT_LISTEN_PORT}" \
    --certfile "${HTTPS_CERT_FILE}" \
    --keyfile "${HTTPS_KEY_FILE}" > /tmp/https_proxy.log 2>&1 &
  LISA_TLS_PROXY_PID="$!"
  echo "${LISA_TLS_PROXY_PID}" > "${LISA_TLS_PROXY_PID_FILE}"
  echo "TLS proxy PID ${LISA_TLS_PROXY_PID} (pidfile ${LISA_TLS_PROXY_PID_FILE}, log /tmp/https_proxy.log)"
fi

# 5) Wait for the bot HTTP listener to come up before declaring "Ready:".
# Without this, the script returns and the user sees Ready before the FastAPI
# app is actually accepting connections (~5-15s on cold boot).
echo "Waiting for bot HTTP listener on 127.0.0.1:${BOT_LISTEN_PORT}..."
LISA_BOT_READY=0
for i in $(seq 1 60); do
  if ! kill -0 "${LISA_BOT_PID}" 2>/dev/null; then
    echo "ERROR: bot process ${LISA_BOT_PID} exited before becoming ready. See /tmp/bot.log:"
    tail -n 20 /tmp/bot.log 2>/dev/null | sed 's/^/  /'
    exit 1
  fi
  if curl -sf --connect-timeout 2 "http://127.0.0.1:${BOT_LISTEN_PORT}/api/runtime-status" >/dev/null 2>&1; then
    LISA_BOT_READY=1
    echo "  Bot HTTP ready (${i}s)."
    break
  fi
  sleep 1
done
if [ "${LISA_BOT_READY}" != "1" ]; then
  echo "WARN: bot HTTP listener did not respond within 60s; continuing (check /tmp/bot.log)."
fi

echo ""
echo "Ready:"
if [ "${BOT_HOST}" = "127.0.0.1" ] || [ "${BOT_HOST}" = "::1" ]; then
  echo "  Bind:              ${BOT_HOST} (loopback-only; LAN/remote disabled — set LISA_BIND_PUBLIC=1 to expose)"
else
  if [ -n "${LISA_ADMIN_TOKEN}" ]; then
    _admin_mode="LISA_ADMIN_TOKEN set (Authorization: Bearer required for mutating /api/*)"
  elif [ "${LISA_BIND_LOOPBACK_ONLY}" = "1" ]; then
    _admin_mode="loopback-only gate (peer must be 127.0.0.1; reverse proxies will appear loopback — set LISA_ADMIN_TOKEN!)"
  else
    _admin_mode="FAIL-CLOSED (mutating /api/* will return 503; set LISA_ADMIN_TOKEN)"
  fi
  echo "  Bind:              ${BOT_HOST} (PUBLIC) — admin gate: ${_admin_mode}"
fi
echo "  Assistant Console: https://127.0.0.1:${BOT_PORT}/assistant-console"
echo "  HTTPS client:      https://127.0.0.1:${BOT_PORT}/client"
echo "  Mobile voice:      https://127.0.0.1:${BOT_PORT}/mobile-voice-test"
echo "  Mobile voice+vision: https://127.0.0.1:${BOT_PORT}/mobile-voice-vision-test"
echo "  API info:          https://127.0.0.1:${BOT_PORT}/api/mobile-voice"
echo "  Vision API info:   https://127.0.0.1:${BOT_PORT}/api/mobile-voice-vision"
echo "  Face registry UI:  https://127.0.0.1:${BOT_PORT}/face-manager"
echo "  Instructions UI:   https://127.0.0.1:${BOT_PORT}/instructions-manager"
echo "  Vision captions:   VISION_CAPTION_OPENAI_COMPAT=${VISION_CAPTION_OPENAI_COMPAT} (0=native /api/chat only; set 1 for OpenAI compat first)"
echo "  Vision policies:   ${APP_DIR}/pipecat_bots/vision_policy/  (optional: VISION_POLICY_DIR, VISION_INTENTS_FILE — see docs/stack-start-stop.md)"
echo "  Face recognition:  optional FACERECOG_* env or runtime toggle (rag_data/facerecog_runtime.json)"

if command -v python3 >/dev/null 2>&1 && [ -f "${SCRIPT_DIR}/scripts/stack_gpu_summary.py" ]; then
  export STACK_STATUS_BOT_HTTP="http://127.0.0.1:${HTTP_BOT_PORT}"
  export STACK_STATUS_BOT_HTTPS="https://127.0.0.1:${BOT_PORT}"
  echo ""
  python3 "${SCRIPT_DIR}/scripts/stack_gpu_summary.py" 2>/dev/null || true
fi
