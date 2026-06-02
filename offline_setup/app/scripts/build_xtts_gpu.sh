#!/usr/bin/env bash
# Build Coqui XTTS v2 GPU image using Nemotron's CUDA 13 / Blackwell stack.
# Use when the pre-built xtts-streaming-server:cuda121 fails on RTX 5090.
#
# Prereq: nemotron-unified:cuda13 must already be built.
#
# Usage:
#   ./scripts/build_xtts_gpu.sh
#   docker run -d --name xtts-tts --gpus all -e COQUI_TOS_AGREED=1 -p 8002:80 xtts-gpu:blackwell

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="${XTTS_GPU_IMAGE:-xtts-gpu:blackwell}"

echo "============================================"
echo "Build XTTS v2 GPU (Blackwell / CUDA 13)"
echo "============================================"
echo "  Base: nemotron-unified:cuda13"
echo "  Image: $IMAGE_NAME"
echo "============================================"

if ! docker image inspect nemotron-unified:cuda13 >/dev/null 2>&1; then
  echo "ERROR: Base image nemotron-unified:cuda13 not found."
  echo "  Build it first: docker build -f Dockerfile.unified -t nemotron-unified:cuda13 ."
  exit 1
fi

cd "$REPO_ROOT"
docker build -f Dockerfile.xtts-gpu -t "$IMAGE_NAME" .

echo ""
echo "Build complete. Run XTTS on GPU:"
echo "  docker run -d --name xtts-tts --gpus all -e COQUI_TOS_AGREED=1 -p 8002:80 $IMAGE_NAME"
echo ""
echo "Then: TTS_PROVIDER=xtts XTTS_TTS_URL=http://localhost:8002 uv run pipecat_bots/bot_interleaved_streaming.py"
