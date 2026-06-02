"""WebSocket handler for JPEG camera frames (/ws/mobile-voice-vision)."""

from __future__ import annotations

import asyncio
import json
import struct
import time
from typing import Any

from loguru import logger

from pipecat_bots.vision_client_prefs import clear_vision_client_prefs
from pipecat_bots.vision_session_store import get_vision_store

# Binary layout: 0x01 + u32 frame_id BE + u32 payload_len BE + JPEG bytes
_MAGIC_JPEG = 0x01

_MAX_SERVER_FPS = 2.0


async def run_mobile_voice_vision_ws(websocket: Any) -> None:
    """Handle vision WebSocket: query param session_id required; see docs/video_voice_build_plan.md §4."""
    await websocket.accept()
    try:
        session_id = (websocket.query_params.get("session_id") or "").strip()
    except Exception:
        session_id = ""
    if not session_id:
        await websocket.close(code=4400)
        return

    store = get_vision_store()

    max_fps = _MAX_SERVER_FPS
    min_interval = 1.0 / max_fps
    last_accepted = 0.0

    try:
        msg = await websocket.receive()
        if msg.get("type") != "websocket.receive":
            await websocket.close(code=4400)
            return
        raw = msg.get("text")
        if not raw:
            await websocket.close(code=4400)
            return
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await websocket.close(code=4400)
            return
        if obj.get("type") != "client-ready":
            await websocket.close(code=4401)
            return
        mf = obj.get("max_fps")
        if isinstance(mf, (int, float)) and mf > 0:
            max_fps = min(float(mf), _MAX_SERVER_FPS)
            min_interval = 1.0 / max_fps

        logger.info(
            f"[ws-mobile-voice-vision] session={session_id[:8]}… max_fps={max_fps} client-ready ok"
        )
        try:
            from pipecat_bots.face_recog.config import facerecog_enabled as _fe

            if _fe():
                fv = obj.get("face_voice")
                if fv is None:
                    fv = obj.get("face_voice_session")
                if isinstance(fv, bool) and fv:
                    from pipecat_bots.face_recog.session_state import set_voice_face_session

                    _st = set_voice_face_session(session_id, True)
                    _st.focus_locked = True
        except Exception:
            pass

        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("type") != "websocket.receive":
                continue
            data = msg.get("bytes")
            if data is None:
                continue
            if len(data) < 9:
                logger.warning("[ws-mobile-voice-vision] short binary frame")
                continue
            magic = data[0]
            if magic != _MAGIC_JPEG:
                logger.debug(f"[ws-mobile-voice-vision] skip magic={magic}")
                continue
            frame_id, payload_len = struct.unpack_from(">II", data, 1)
            if payload_len > 12 * 1024 * 1024 or 9 + payload_len > len(data):
                logger.warning("[ws-mobile-voice-vision] bad payload_len")
                continue
            jpeg = bytes(data[9 : 9 + payload_len])
            if not jpeg.startswith(b"\xff\xd8"):
                logger.warning("[ws-mobile-voice-vision] not JPEG")
                continue

            now = time.monotonic()
            if now - last_accepted < min_interval:
                continue
            last_accepted = now

            await store.set_frame(session_id, jpeg, frame_id)
            logger.debug(f"[ws-mobile-voice-vision] frame_id={frame_id} len={len(jpeg)}")
            try:
                from pipecat_bots.face_recog.config import facerecog_enabled

                if facerecog_enabled():
                    from pipecat_bots.face_recog.face_pipeline import on_jpeg

                    # Main UI voice/video stream is read-only for registry writes.
                    asyncio.create_task(on_jpeg(session_id, jpeg, allow_registry_write=False))
            except Exception as e:
                logger.warning(f"[ws-mobile-voice-vision] face hook: {e}")
    except Exception as e:
        logger.exception(f"[ws-mobile-voice-vision] error: {e}")
    finally:
        await store.clear_session(session_id)
        clear_vision_client_prefs(session_id)
        try:
            from pipecat_bots.face_recog.session_state import clear_state as _clear_face_session

            _clear_face_session(session_id)
        except Exception:
            pass
        logger.debug(f"[ws-mobile-voice-vision] cleared frames for session={session_id[:8]}…")
        try:
            await websocket.close()
        except Exception:
            pass
