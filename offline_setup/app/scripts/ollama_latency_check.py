#!/usr/bin/env python3
"""Measure Ollama round-trip latency (vision caption vs simple text chat).

Uses the same defaults as ``pipecat_bots.vision_caption`` (``VISION_OLLAMA_*``, ``OLLAMA_HOST``, …).

Run from the app directory so imports resolve::

    cd offline_setup/app
    uv run python scripts/ollama_latency_check.py
    uv run python scripts/ollama_latency_check.py --mode text --runs 5
    uv run python scripts/ollama_latency_check.py --image /path/to/test.jpg --runs 10 --warmup 2
    uv run python scripts/ollama_latency_check.py --camera --runs 3 --warmup 1

``--camera`` grabs a **fresh** JPEG from the default webcam each warmup/timed run (OpenCV). On **macOS** the script uses the **AVFoundation** backend; allow **Camera** access for your terminal (System Settings → Privacy & Security → Camera). On Linux, a V4L2 device (e.g. ``/dev/video0``) is used.

Exit code 0 on success; non-zero on bad arguments, missing camera file, or uncaught errors.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import statistics
import sys
import time
from pathlib import Path
from typing import Callable


def _tiny_jpeg_bytes() -> bytes:
    """Synthetic minimal JPEG (fallback when ImageMagick unavailable)."""
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r"
        b"\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c"
        b"(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11"
        b"\x03\x11\x00\x3f\x00\xaa\xff\xd9"
    )


def _probe_jpeg_bytes(path: str | None) -> bytes:
    if path:
        p = Path(path)
        if not p.is_file():
            print(f"error: --image not found: {p}", file=sys.stderr)
            sys.exit(2)
        return p.read_bytes()
    env_path = (os.environ.get("E2E_TEST_JPEG") or "").strip()
    if env_path and Path(env_path).is_file():
        return Path(env_path).read_bytes()
    out = "/tmp/ollama_latency_probe.jpg"
    try:
        subprocess.run(
            ["convert", "-size", "320x240", "xc:#445566", out],
            check=True,
            timeout=8,
            capture_output=True,
        )
        return Path(out).read_bytes()
    except Exception:
        return _tiny_jpeg_bytes()


def _open_video_capture(cv2, index: int):
    """Prefer AVFoundation on macOS — default OpenCV backend often fails on built-in FaceTime camera."""
    if sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
        cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(index)


def _camera_open_error_message(index: int) -> str:
    base = f"Cannot open camera index {index}."
    if sys.platform == "darwin":
        return (
            f"{base} On Mac: System Settings → Privacy & Security → Camera — enable the app running "
            f"this script (Terminal, iTerm, Cursor, VS Code, …). Try --camera-index 0 or 1. "
            "OpenCV uses the AVFoundation backend on macOS."
        )
    return f"{base} No device, permission denied, or busy. Try another --camera-index; check /dev/video* on Linux."


class _CameraJpegSource:
    """One frame per call — matches voice pipeline (new JPEG each turn)."""

    def __init__(
        self,
        index: int,
        *,
        width: int | None,
        height: int | None,
        jpeg_quality: int,
        discard_after_open: int,
    ) -> None:
        os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")
        try:
            import cv2
        except ImportError as e:
            raise RuntimeError(
                "OpenCV is required for --camera. Install with: pip install opencv-python"
            ) from e

        self._cv2 = cv2
        self.cap = _open_video_capture(cv2, index)
        if not self.cap.isOpened():
            raise RuntimeError(_camera_open_error_message(index))
        if width and width > 0:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        if height and height > 0:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        for _ in range(max(0, discard_after_open)):
            self.cap.read()
        # Log actual geometry once
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._geom = (w, h)
        self._encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, jpeg_quality))]

    @property
    def geometry(self) -> tuple[int, int]:
        return self._geom

    def next_jpeg(self) -> bytes:
        ret, frame = self.cap.read()
        if not ret or frame is None:
            raise RuntimeError("Camera read() failed (device unplugged or busy).")
        ok, buf = self._cv2.imencode(".jpg", frame, self._encode_params)
        if not ok or buf is None:
            raise RuntimeError("cv2.imencode(.jpg) failed")
        return buf.tobytes()

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None  # type: ignore[assignment]


def _vision_ollama_base() -> str:
    """Mirror ``vision_caption._ollama_v1_base()`` without importing the module."""
    explicit = (os.getenv("VISION_OLLAMA_BASE") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = (os.getenv("OLLAMA_HOST") or "127.0.0.1").strip().rstrip("/")
    port = (os.getenv("OLLAMA_PORT") or "11434").strip()
    if host.startswith("http://") or host.startswith("https://"):
        root = host.rstrip("/")
        return root if root.endswith("/v1") else f"{root}/v1"
    return f"http://{host}:{port}/v1"


async def _run_text_chat(model: str, timeout_sec: float) -> tuple[float, int]:
    """Single POST /v1/chat/completions (no image). Returns (elapsed_sec, http_status)."""
    import httpx

    url = f"{_vision_ollama_base().rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "stream": False,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    }
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=10.0)) as client:
        r = await client.post(url, json=body)
        elapsed = time.perf_counter() - t0
        return elapsed, r.status_code


async def _run_vision_caption(jpeg: bytes) -> tuple[float, str]:
    """Uses production ``caption_jpeg`` (same HTTP body and fallbacks as the bot)."""
    from pipecat_bots.vision_caption import caption_jpeg

    t0 = time.perf_counter()
    text = await caption_jpeg(jpeg, recheck=False)
    elapsed = time.perf_counter() - t0
    return elapsed, (text[:120] + "…") if len(text) > 120 else text


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000.0:.1f} ms"


async def main_async(args: argparse.Namespace) -> None:
    app_root = Path(__file__).resolve().parent.parent
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    from pipecat_bots.vision_caption import (
        DEFAULT_VISION_OLLAMA_MODEL,
        VISION_OLLAMA_MODEL_PLACEHOLDERS,
        effective_vision_ollama_model,
    )

    model = effective_vision_ollama_model()
    v_raw = (os.getenv("VISION_OLLAMA_MODEL") or "").strip()
    om = (os.getenv("OLLAMA_MODEL") or "").strip()
    # Old behavior: when VISION is unset or a doc placeholder, fall back to OLLAMA_MODEL for this CLI.
    if (
        model == DEFAULT_VISION_OLLAMA_MODEL
        and om
        and om not in VISION_OLLAMA_MODEL_PLACEHOLDERS
        and (not v_raw or v_raw in VISION_OLLAMA_MODEL_PLACEHOLDERS)
    ):
        model = om
    timeout_sec = float(os.getenv("VISION_OLLAMA_TIMEOUT", "45"))

    mode = args.mode
    if args.camera and mode == "text":
        print("error: --camera only applies to vision; use --mode vision or --mode both", file=sys.stderr)
        sys.exit(2)

    cam: _CameraJpegSource | None = None
    get_jpeg: Callable[[], bytes] | None = None
    if mode in ("vision", "both"):
        if args.camera:
            try:
                cam = _CameraJpegSource(
                    args.camera_index,
                    width=(args.camera_width if args.camera_width > 0 else None),
                    height=(args.camera_height if args.camera_height > 0 else None),
                    jpeg_quality=args.jpeg_quality,
                    discard_after_open=args.camera_discard,
                )
            except RuntimeError as e:
                print(f"error: {e}", file=sys.stderr)
                sys.exit(2)
            get_jpeg = cam.next_jpeg
        else:
            fixed = _probe_jpeg_bytes(args.image)
            get_jpeg = lambda: fixed

    print("Ollama latency check")
    print(f"  VISION_OLLAMA_BASE / effective v1 root: {_vision_ollama_base()}")
    print(f"  model (VISION_OLLAMA_MODEL / fallback): {model}")
    print(f"  timeout: {timeout_sec}s")
    if mode in ("vision", "both") and args.camera and cam is not None:
        w, h = cam.geometry
        print(f"  camera: index={args.camera_index}  frame≈{w}x{h}  jpeg_q={args.jpeg_quality}")
        print("  (fresh JPEG from webcam each warmup/timed run)")
    print()

    try:
        if mode in ("vision", "both") and get_jpeg is not None:
            # Warmup (loads model into VRAM)
            for i in range(args.warmup):
                print(f"vision warmup {i + 1}/{args.warmup} …")
                await _run_vision_caption(get_jpeg())
            times: list[float] = []
            for i in range(args.runs):
                dt, preview = await _run_vision_caption(get_jpeg())
                times.append(dt)
                print(f"  vision run {i + 1}/{args.runs}: {_fmt_ms(dt)}  preview={preview!r}")
            print()
            print(
                f"vision caption:  n={len(times)}  "
                f"min={_fmt_ms(min(times))}  max={_fmt_ms(max(times))}  "
                f"mean={_fmt_ms(statistics.mean(times))}"
                + (f"  stdev={_fmt_ms(statistics.stdev(times))}" if len(times) > 1 else "")
            )
            print()

    finally:
        if cam is not None:
            cam.close()

    if mode in ("text", "both"):
        for i in range(args.warmup):
            print(f"text warmup {i + 1}/{args.warmup} …")
            await _run_text_chat(model, timeout_sec)
        t_times: list[float] = []
        for i in range(args.runs):
            dt, code = await _run_text_chat(model, timeout_sec)
            t_times.append(dt)
            print(f"  text run {i + 1}/{args.runs}: {_fmt_ms(dt)}  HTTP {code}")
        print()
        print(
            f"text chat:       n={len(t_times)}  "
            f"min={_fmt_ms(min(t_times))}  max={_fmt_ms(max(t_times))}  "
            f"mean={_fmt_ms(statistics.mean(t_times))}"
            + (f"  stdev={_fmt_ms(statistics.stdev(t_times))}" if len(t_times) > 1 else "")
        )

    if mode == "both":
        print()
        print(
            "Note: vision uses image+instruction tokens; text uses a tiny prompt—"
            "compare means only as a rough GPU/text vs vision ratio."
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark Ollama latency (vision vs text).")
    p.add_argument(
        "--mode",
        choices=("vision", "text", "both"),
        default="vision",
        help="vision = image caption (same as bot); text = tiny chat; both = both",
    )
    p.add_argument("--runs", type=int, default=3, help="timed iterations after warmup")
    p.add_argument("--warmup", type=int, default=1, help="untimed runs first (model load)")
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--image",
        type=str,
        default=None,
        help="JPEG path (default without --camera: E2E_TEST_JPEG, ImageMagick, or tiny inline)",
    )
    src.add_argument(
        "--camera",
        action="store_true",
        help="Use live webcam (OpenCV): new JPEG per run; requires --mode vision or both",
    )
    p.add_argument("--camera-index", type=int, default=0, help="VideoCapture index (default 0)")
    p.add_argument("--camera-width", type=int, default=0, help="Optional capture width (0 = driver default)")
    p.add_argument("--camera-height", type=int, default=0, help="Optional capture height (0 = driver default)")
    p.add_argument(
        "--camera-discard",
        type=int,
        default=10,
        help="Frames to read after open before benchmarking (clears auto-exposure warmup)",
    )
    p.add_argument(
        "--jpeg-quality",
        type=int,
        default=80,
        help="OpenCV JPEG quality 1–100 (default 80; browser stack often ~74)",
    )
    args = p.parse_args()
    if args.runs < 1:
        p.error("--runs must be >= 1")
    if args.warmup < 0:
        p.error("--warmup must be >= 0")
    if args.jpeg_quality < 1 or args.jpeg_quality > 100:
        p.error("--jpeg-quality must be 1–100")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
