#!/usr/bin/env python3
"""Smoke tests for optional face registry API + vision JPEG hook.

Requires a running stack (default http://127.0.0.1:7861).

Enables face recognition via POST /api/face/runtime (persisted toggle), then exercises
GET persons, POST person, vision WebSocket + JPEG. Previous behaviour: set client
FACERECOG_ENABLED=1 to run checks — optional now.

Run:
  cd offline_setup/app && uv run python scripts/e2e_face_recog_voice_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import sys
import uuid

import httpx
import websockets


def _tiny_jpeg() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r"
        b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9"
    )


def _frame_packet(jpeg: bytes, frame_id: int = 1) -> bytes:
    return b"\x01" + struct.pack(">II", frame_id, len(jpeg)) + jpeg


async def run_all(base: str) -> int:
    fails = 0
    sid = str(uuid.uuid4())
    jpeg = _tiny_jpeg()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        # Prefer runtime toggle so E2E works without shell FACERECOG_ENABLED on the bot.
        try:
            rr = await client.post(
                f"{base}/api/face/runtime",
                json={"enabled": True},
            )
            if rr.status_code != 200:
                print(
                    f"WARN: POST /api/face/runtime -> {rr.status_code} {rr.text[:200]}",
                    file=sys.stderr,
                )
                if rr.status_code == 404:
                    print(
                        "HINT: Face API routes missing — restart the bot so "
                        "`pipecat_offline_patch` loads (offline_setup/start_current_stack.sh).",
                        file=sys.stderr,
                    )
            else:
                print("OK   POST /api/face/runtime {enabled: true}")
        except Exception as e:
            print(f"WARN: runtime enable: {e}", file=sys.stderr)

        try:
            r = await client.get(f"{base}/api/face/persons")
            if r.status_code != 200:
                print(f"FAIL: GET /api/face/persons -> {r.status_code}", file=sys.stderr)
                fails += 1
            else:
                data = r.json()
                assert "persons" in data
                print("OK   GET /api/face/persons")
        except Exception as e:
            print(f"FAIL: persons list: {e}", file=sys.stderr)
            fails += 1

        try:
            r = await client.post(
                f"{base}/api/face/person",
                json={"person_id": "e2e_test_person", "display_name": "E2E Tester", "notes": "e2e"},
            )
            if r.status_code != 200:
                print(f"FAIL: POST /api/face/person -> {r.status_code} {r.text}", file=sys.stderr)
                fails += 1
            else:
                print("OK   POST /api/face/person")
        except Exception as e:
            print(f"FAIL: enroll: {e}", file=sys.stderr)
            fails += 1

        ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{ws_base}/ws/mobile-voice-vision?session_id={sid}"
        try:
            async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
                await ws.send(json.dumps({"type": "client-ready", "max_fps": 2}))
                await ws.send(_frame_packet(jpeg, 1))
            print("OK   vision WebSocket + one JPEG (face hook runs server-side if enabled)")
        except Exception as e:
            print(f"FAIL: vision ws: {e}", file=sys.stderr)
            fails += 1

        if os.getenv("FACE_E2E_DISABLE_RUNTIME_AFTER", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            try:
                cr = await client.post(f"{base}/api/face/runtime", json={"enabled": False})
                if cr.status_code == 200:
                    print("OK   POST /api/face/runtime {enabled: false} (cleanup)")
            except Exception:
                pass

    return fails


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.getenv("E2E_BASE_URL", "http://127.0.0.1:7861"))
    args = p.parse_args()
    base = args.base_url.rstrip("/")
    rc = asyncio.run(run_all(base))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
