#!/usr/bin/env bash
# Build a short MP4 walkthrough (Lisa stack components + flow) using ImageMagick + ffmpeg.
# Requires: convert (ImageMagick), ffmpeg.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/media"
FRAMES="${OUT_DIR}/_frames_arch"
OUT_MP4="${OUT_DIR}/architecture_flow.mp4"

mkdir -p "${FRAMES}"
rm -f "${FRAMES}"/frame_*.png

W=1280
H=720
BG="#0a0e14"
PANEL="#121a24"
EDGE="#30363d"
TEXT="#e6edf3"
MUTED="#8b9cb3"
ACCENT="#58a6ff"
FLOW="#3fb950"

# 8 steps: highlight moves through the pipeline
for i in $(seq 0 7); do
  F="${FRAMES}/frame_$(printf '%03d' "$i").png"
  # Base
  convert -size "${W}x${H}" "xc:${BG}" \
    -fill "${PANEL}" -stroke "${EDGE}" -strokewidth 2 \
    -draw "roundrectangle 40,80 280,180 12,12" \
    -draw "roundrectangle 320,80 560,180 12,12" \
    -draw "roundrectangle 600,80 880,180 12,12" \
    -draw "roundrectangle 920,80 1180,180 12,12" \
    -draw "roundrectangle 40,260 320,380 12,12" \
    -draw "roundrectangle 360,260 640,380 12,12" \
    -draw "roundrectangle 680,260 1180,380 12,12" \
    -draw "roundrectangle 200,420 1040,500 12,12" \
    -fill "${TEXT}" -font DejaVu-Sans -pointsize 22 \
    -annotate +120+135 'Browser / HTTPS :7860' \
    -annotate +400+135 'TLS → bot :7861' \
    -annotate +680+135 'bot_vllm Pipecat' \
    -annotate +980+135 'WS /ws/mobile-voice' \
    -annotate +120+330 'ASR Nemotron :8080' \
    -annotate +440+330 'Dialogue LLM :8000 / :11434' \
    -annotate +780+330 'XTTS :80' \
    -annotate +480+470 'Vision: Ollama caption + vision_session_store → augment LLM' \
    -fill "${MUTED}" -pointsize 16 \
    -annotate +40+40 'Lisa offline stack — architecture flow (generated)' \
    "${F}"

  case "$i" in
    0) HX=40; HY=80; HW=240; HH=100 ;;
    1) HX=320; HY=80; HW=240; HH=100 ;;
    2) HX=600; HY=80; HW=280; HH=100 ;;
    3) HX=920; HY=80; HW=260; HH=100 ;;
    4) HX=40; HY=260; HW=280; HH=120 ;;
    5) HX=360; HY=260; HW=280; HH=120 ;;
    6) HX=680; HY=260; HW=500; HH=120 ;;
    7) HX=200; HY=420; HW=840; HH=80 ;;
  esac

  convert "${F}" \
    -fill "none" -stroke "${ACCENT}" -strokewidth 4 \
    -draw "roundrectangle $((HX+2)),$((HY+2)),$((HX+HW-2)),$((HY+HH-2)) 10,10" \
    -stroke "${FLOW}" -strokewidth 3 \
    -draw "circle $((HX+HW/2)),$((HY-25)) $((HX+HW/2+8)),$((HY-25))" \
    "${F}"
done

# Duplicate frames for readable pacing (~12 s at 8 fps)
for i in $(seq 0 7); do
  src="${FRAMES}/frame_$(printf '%03d' "$i").png"
  for r in 0 1 2 3 4 5 6 7 8 9 10 11; do
    n=$((i * 12 + r))
    cp "${src}" "${FRAMES}/seq_$(printf '%04d' "$n").png"
  done
done

ffmpeg -y -hide_banner -loglevel warning \
  -framerate 8 \
  -i "${FRAMES}/seq_%04d.png" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  "${OUT_MP4}"

rm -rf "${FRAMES}"
echo "Wrote ${OUT_MP4}"
