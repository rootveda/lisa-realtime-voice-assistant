#!/usr/bin/env bash
# Start Coqui XTTS v2 streaming server via Docker.
# XTTS listens on port 80 inside container (image may ignore PORT env). Use 80 for bot URL.
#
# Usage:
#   ./scripts/start_xtts.sh              # Try GPU image (cuda121; may fail on Blackwell)
#   ./scripts/start_xtts.sh               # If xtts-gpu:blackwell exists, use it (CUDA 13)
#   XTTS_CPU=1 ./scripts/start_xtts.sh    # Force CPU (slow but works on any host)
#   XTTS_GPU_DEVICE=1 ./scripts/start_xtts.sh   # Pin XTTS to GPU index 1 (frees GPU 0 for LLM/Ollama)
#
# Bot: TTS_PROVIDER=xtts XTTS_TTS_URL=http://localhost:8002

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTAINER_NAME="${XTTS_CONTAINER_NAME:-xtts-tts}"
# Pre-built image (CUDA 12.1; may fail on RTX 5090 / Blackwell). Prefer local Blackwell build.
if docker image inspect xtts-gpu:blackwell >/dev/null 2>&1; then
  IMAGE_NAME="${XTTS_IMAGE:-xtts-gpu:blackwell}"
  USING_GPU="xtts-gpu:blackwell (GPU, Blackwell)"
else
  IMAGE_NAME="${XTTS_IMAGE:-ghcr.io/coqui-ai/xtts-streaming-server:latest-cuda121}"
  USING_GPU="pre-built cuda121 (may fail on RTX 5090)"
fi
HOST_PORT="${XTTS_PORT:-80}"

# Force CPU
if [ -n "${XTTS_CPU:-}" ]; then
  IMAGE_NAME="ghcr.io/coqui-ai/xtts-streaming-server:latest-cpu"
  USING_GPU="CPU"
fi

echo "============================================"
echo "Coqui XTTS v2 Streaming Server"
echo "============================================"
echo "  Container: $CONTAINER_NAME"
echo "  Image:     $IMAGE_NAME ($USING_GPU)"
echo "  Port:      $HOST_PORT"
echo "============================================"

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "Container '$CONTAINER_NAME' is already running."
  echo "  Bot: TTS_PROVIDER=xtts XTTS_TTS_URL=http://localhost:${HOST_PORT}"
  exit 0
fi

# Pull if not local
if [[ "$IMAGE_NAME" == *"/"* ]] && ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "Pulling $IMAGE_NAME ..."
  docker pull "$IMAGE_NAME"
fi

# GPU unless CPU image (XTTS_GPU_DEVICE=0|1|... pins a single device to avoid sharing VRAM with LLM)
GPU_ARGS=""
if [ "$USING_GPU" != "CPU" ] && docker run --help 2>/dev/null | grep -q '\-\-gpus'; then
  if [ -n "${XTTS_GPU_DEVICE:-}" ]; then
    GPU_ARGS="--gpus device=${XTTS_GPU_DEVICE}"
    echo "  GPU:      device ${XTTS_GPU_DEVICE} (XTTS_GPU_DEVICE)"
  else
    GPU_ARGS="--gpus all"
  fi
fi

# Coqui TTS stores models in ~/.local/share/tts (not HF cache). Mount XTTS_TTS_DATA_DIR so model persists.
# Set XTTS_TTS_DATA_DIR to a host path; container will use it as /root/.local/share/tts.
# Set XTTS_OFFLINE=1 to use cache only (HF_HUB_OFFLINE=1); leave unset on first run so model can download.
VOLUME_ARGS=()
if [ -n "${XTTS_TTS_DATA_DIR:-}" ] && [ -d "$XTTS_TTS_DATA_DIR" ]; then
  VOLUME_ARGS+=(-v "${XTTS_TTS_DATA_DIR}:/root/.local/share/tts")
  echo "  TTS data: $XTTS_TTS_DATA_DIR (mounted; model persists here)"
fi
if [ -n "${XTTS_HF_CACHE_DIR:-}" ] && [ -d "$XTTS_HF_CACHE_DIR" ]; then
  VOLUME_ARGS+=(-v "${XTTS_HF_CACHE_DIR}:/root/.cache/huggingface")
  echo "  HF cache: $XTTS_HF_CACHE_DIR (mounted)"
fi
OFFLINE_ARGS=()
if [ "${XTTS_OFFLINE:-0}" = "1" ]; then
  OFFLINE_ARGS=(-e "HF_HUB_OFFLINE=1")
  echo "  Mode:   offline (HF_HUB_OFFLINE=1)"
fi

# Same binding as Nemotron: host network, service on host port.
docker run -d \
  --name "$CONTAINER_NAME" \
  $GPU_ARGS \
  --network=host \
  -e COQUI_TOS_AGREED=1 \
  -e PORT="${HOST_PORT}" \
  "${VOLUME_ARGS[@]}" \
  "${OFFLINE_ARGS[@]}" \
  "$IMAGE_NAME"

echo ""
echo "XTTS server starting on http://localhost:${HOST_PORT}"
echo "  Studio speakers: curl -s http://localhost:${HOST_PORT}/studio_speakers | head -c 200"
echo ""
echo "Run the voice bot with XTTS:"
echo "  export TTS_PROVIDER=xtts"
echo "  export XTTS_TTS_URL=http://localhost:${HOST_PORT}"
echo "  cd $REPO_ROOT/offline_setup/app && uv run pipecat_bots/bot_interleaved_streaming.py"
echo ""
echo "If you see CUDA 'no kernel image' errors on RTX 5090, build the Blackwell image:"
echo "  ./scripts/build_xtts_gpu.sh"
echo ""
echo "Stop: docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME"
