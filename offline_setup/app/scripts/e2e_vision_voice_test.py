#!/usr/bin/env python3
"""E2E checks for vision + text chat + (optional) voice stack endpoints.

Requires a running bot (default http://127.0.0.1:7861). Uses:
  GET  /api/runtime-status
  GET  /api/mobile-voice-vision
  POST /api/vision/preview
  WebSocket /ws/mobile-voice-vision?session_id=…  (JPEG framing from docs/video_voice_build_plan.md)
  POST /api/text-chat/completions  (with extension fields vision_session_id / vision_enable)

Does **not** send real microphone audio (no ASR transcript); voice+vision is validated manually
via /mobile-voice-vision-test or Assistant Console with “Live camera” enabled.

Run:
  cd offline_setup/app && uv run python scripts/e2e_vision_voice_test.py
  uv run python scripts/e2e_vision_voice_test.py --base-url https://127.0.0.1:7860 --insecure

Exit code 0 if all automated cases pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import subprocess
import sys
import uuid
import ssl
from typing import Any

import httpx
import websockets

# 48×36 JPEG (#445566), generated with Pillow — decodes without ImageMagick when ``convert`` is missing.
_TEST_JPEG_FALLBACK = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb00430006040506050406060506070706080a100a0a09090a140e0f0c1017141818171416161a1d251f1a1b231c1616202c20232627292a29191f2d302d283025282928ffdb0043010707070a080a130a0a13281a161a2828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828ffc00011080024003003012200021101031101ffc4001500010100000000000000000000000000000006ffc40014100100000000000000000000000000000000ffc4001501010100000000000000000000000000000004ffc40014110100000000000000000000000000000000ffda000c03010002110311003f0082016a40000000000000000000000007ffd9"
)


def _test_jpeg_bytes() -> bytes:
    """Prefer E2E_TEST_JPEG file, else ImageMagick ``convert``, else tiny inline JPEG."""
    path = (os.environ.get("E2E_TEST_JPEG") or "").strip()
    if path and os.path.isfile(path):
        return open(path, "rb").read()
    out = "/tmp/e2e_vision_probe.jpg"
    try:
        subprocess.run(
            ["convert", "-size", "64x48", "xc:#445566", out],
            check=True,
            timeout=8,
            capture_output=True,
        )
        return open(out, "rb").read()
    except Exception:
        return _TEST_JPEG_FALLBACK


def _frame_packet(jpeg: bytes, frame_id: int = 1) -> bytes:
    return b"\x01" + struct.pack(">II", frame_id, len(jpeg)) + jpeg


async def _send_vision_frames(ws_url: str, jpeg: bytes, n_frames: int = 2, ssl_ctx: ssl.SSLContext | None = None) -> None:
    connect_kw: dict[str, Any] = {"max_size": 16 * 1024 * 1024}
    if ssl_ctx is not None:
        connect_kw["ssl"] = ssl_ctx
    async with websockets.connect(ws_url, **connect_kw) as ws:
        await ws.send(json.dumps({"type": "client-ready", "max_fps": 2, "max_width": 480}))
        for i in range(n_frames):
            await ws.send(_frame_packet(jpeg, frame_id=i + 1))
            await asyncio.sleep(0.15)


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"OK   {msg}")


async def run_all(base_http: str, verify_tls: bool) -> int:
    base = base_http.rstrip("/")
    client_kw: dict[str, Any] = {"timeout": httpx.Timeout(120.0, connect=10.0)}
    ws_ssl: ssl.SSLContext | None = None
    if base.startswith("https"):
        client_kw["verify"] = verify_tls
        ws_ssl = ssl.create_default_context()
        if not verify_tls:
            ws_ssl.check_hostname = False
            ws_ssl.verify_mode = ssl.CERT_NONE

    fails = 0
    async with httpx.AsyncClient(**client_kw) as client:
        # 1) Runtime
        try:
            r = await client.get(f"{base}/api/runtime-status")
            r.raise_for_status()
            data = r.json()
            assert "models" in data
            _ok("GET /api/runtime-status")
        except Exception as e:
            _fail(f"runtime-status: {e}")
            fails += 1
            return fails

        model = (data.get("models") or {}).get("llm_model") or ""

        # 2) Vision discovery
        try:
            r = await client.get(f"{base}/api/mobile-voice-vision")
            r.raise_for_status()
            info = r.json()
            assert info.get("title")
            _ok("GET /api/mobile-voice-vision")
        except Exception as e:
            _fail(f"mobile-voice-vision: {e}")
            fails += 1

        # 3) Vision preview (Ollama must be up with a vision-capable model)
        jpeg = _test_jpeg_bytes()
        try:
            r = await client.post(
                f"{base}/api/vision/preview",
                files={"image": ("probe.jpg", jpeg, "image/jpeg")},
            )
            body = r.json()
            if not body.get("ok"):
                raise RuntimeError(body.get("error", body))
            cap = (body.get("caption") or "").strip()
            if len(cap) >= 2:
                _ok("POST /api/vision/preview (caption received)")
            else:
                print(
                    "WARN: /api/vision/preview returned ok but empty/short caption; "
                    "check VISION_OLLAMA_MODEL, Ollama vision model, or set E2E_TEST_JPEG",
                    file=sys.stderr,
                )
        except Exception as e:
            _fail(f"vision/preview: {e} (set VISION_OLLAMA_MODEL and ensure Ollama has a vision model)")
            fails += 1

        # 4) Text chat while “feed” is live: WS sends JPEG, then text with vision_session_id
        session_id = str(uuid.uuid4())
        host_part = base.split("://", 1)[-1]
        ws_scheme = "wss" if base.startswith("https") else "ws"
        ws_url = f"{ws_scheme}://{host_part}/ws/mobile-voice-vision?session_id={session_id}"

        try:
            await _send_vision_frames(ws_url, jpeg, n_frames=3, ssl_ctx=ws_ssl)
            _ok("WebSocket /ws/mobile-voice-vision (JPEG frames sent)")
        except Exception as e:
            _fail(f"vision websocket: {e}")
            fails += 1
            return fails

        if not model:
            _fail("no llm_model in runtime-status; cannot run text-chat tests")
            fails += 1
            return fails

        # 4b) Vision-grounded text question
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are concise. Ground answers in [Camera context] when present."},
                    {"role": "user", "content": "What do you see? Name colors or objects briefly."},
                ],
                "max_tokens": 128,
                "temperature": 0.3,
                "vision_session_id": session_id,
                "vision_enable": True,
            }
            r = await client.post(f"{base}/api/text-chat/completions", json=payload)
            r.raise_for_status()
            out = r.json()
            choice = (out.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = (msg.get("content") or msg.get("reasoning") or "").strip()
            if not text:
                raise RuntimeError("no assistant text")
            _ok("POST /api/text-chat/completions with vision_session_id (vision question)")
        except Exception as e:
            _fail(f"text-chat + vision: {e}")
            fails += 1

        # 4c) Text chat without vision intent (should not require caption path to succeed)
        try:
            session_id2 = str(uuid.uuid4())
            ws_url2 = f"{ws_scheme}://{host_part}/ws/mobile-voice-vision?session_id={session_id2}"
            await _send_vision_frames(ws_url2, jpeg, n_frames=1, ssl_ctx=ws_ssl)
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are concise."},
                    {"role": "user", "content": "Reply with exactly: pong"},
                ],
                "max_tokens": 32,
                "temperature": 0.0,
                "vision_session_id": session_id2,
                "vision_enable": True,
            }
            r = await client.post(f"{base}/api/text-chat/completions", json=payload)
            r.raise_for_status()
            _ok("POST /api/text-chat/completions (no vision intent; feed optional)")
        except Exception as e:
            _fail(f"text-chat generic: {e}")
            fails += 1

    return fails


def main() -> None:
    ap = argparse.ArgumentParser(description="E2E vision + text-chat tests against running bot")
    ap.add_argument(
        "--base-url",
        default="http://127.0.0.1:7861",
        help="Bot base URL (HTTP :7861 or HTTPS :7860)",
    )
    ap.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verify for https base URLs",
    )
    args = ap.parse_args()
    fails = asyncio.run(run_all(args.base_url, verify_tls=not args.insecure))
    if fails:
        print(f"\n{fails} case(s) failed.", file=sys.stderr)
        sys.exit(1)
    print("\nAll automated E2E checks passed.")
    print("Manual: open /mobile-voice-vision-test or Assistant Console with “Live camera” and ask by voice.")


if __name__ == "__main__":
    main()
