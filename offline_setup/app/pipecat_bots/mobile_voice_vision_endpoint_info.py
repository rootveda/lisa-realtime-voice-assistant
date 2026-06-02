"""Payload for GET /api/mobile-voice-vision — JPEG vision WebSocket + preview API."""

from __future__ import annotations

import os
from typing import Any, Optional

from pipecat_bots.vision_caption import effective_vision_ollama_model, vision_caption_try_openai_compat_first


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


def _computed_urls(request: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if request is None:
        return out
    try:
        host = request.headers.get("host") or "127.0.0.1"
        scheme = request.url.scheme
        if request.headers.get("x-forwarded-proto") == "https":
            scheme = "https"
        ws_scheme = "wss" if scheme == "https" else "ws"
        base = f"{scheme}://{host}".rstrip("/")
        out["request_base_url"] = base
        out["websocket_vision_example"] = f"{ws_scheme}://{host}/ws/mobile-voice-vision?session_id=<uuid>"
        out["browser_vision_test_example"] = f"{base}/mobile-voice-vision-test"
        out["api_vision_info_example"] = f"{base}/api/mobile-voice-vision"
        out["api_vision_preview_example"] = f"{base}/api/vision/preview"
    except Exception:
        pass
    return out


def build_mobile_voice_vision_payload(request: Optional[Any] = None) -> dict[str, Any]:
    model = effective_vision_ollama_model()
    ttl = float(_env("VISION_FRAME_TTL_SEC") or "5")
    max_fps = 2.0
    ollama_host = (_env("OLLAMA_HOST") or "127.0.0.1").strip()
    ollama_port = (_env("OLLAMA_PORT") or "11434").strip()
    ollama_http = f"http://{ollama_host}:{ollama_port}" if not ollama_host.startswith("http") else ollama_host

    v1_base = (_env("VISION_OLLAMA_BASE") or f"http://{ollama_host}:{ollama_port}/v1").strip().rstrip("/")

    return {
        "title": "Mobile voice + camera (JPEG over second WebSocket)",
        "version": 1,
        "description": (
            "Use the same /ws/mobile-voice flow for audio. Open /ws/mobile-voice-vision?session_id=<uuid> "
            "and stream JPEG frames (binary framing in docs/video_voice_build_plan.md). "
            "Send the same session id in the mobile-voice client-ready JSON as vision_session_id. "
            "On vision-intent utterances, the server runs Ollama vision and merges a [Camera context] block. "
            "Operators can tune intents and LLM-facing prompts via pipecat_bots/vision_policy/ "
            "(see docs/stack-start-stop.md and docs/e2e_vision_voice_tests.md)."
        ),
        "computed_from_request": _computed_urls(request),
        "environment": {
            "VISION_OLLAMA_BASE": v1_base,
            "VISION_OLLAMA_MODEL": model,
            "VISION_OLLAMA_TIMEOUT": _env("VISION_OLLAMA_TIMEOUT", "45"),
            "VISION_FRAME_TTL_SEC": str(ttl),
            "OLLAMA_HTTP": ollama_http,
            "VISION_POLICY_DIR": _env("VISION_POLICY_DIR"),
            "VISION_INTENTS_FILE": _env("VISION_INTENTS_FILE"),
            "VISION_CAPTION_TEMPERATURE": _env("VISION_CAPTION_TEMPERATURE"),
            "VISION_CAPTION_MAX_TOKENS": _env("VISION_CAPTION_MAX_TOKENS"),
            "VISION_CAPTION_OPENAI_COMPAT": _env("VISION_CAPTION_OPENAI_COMPAT"),
            "vision_caption_try_openai_compat_first": vision_caption_try_openai_compat_first(),
            "VISION_AUGMENT_SESSION_LEVEL": _env("VISION_AUGMENT_SESSION_LEVEL") or "0",
            "FACERECOG_ENABLED": _env("FACERECOG_ENABLED", "0"),
            "FACERECOG_STUB_SIMPLE": _env("FACERECOG_STUB_SIMPLE", "1"),
            "FACERECOG_DATA_DIR": _env("FACERECOG_DATA_DIR"),
        },
        "limits": {
            "max_server_fps": max_fps,
            "jpeg_framing": "0x01 u8 + frame_id u32 be + len u32 be + raw JPEG",
        },
        "paths": {
            "websocket_vision": "/ws/mobile-voice-vision",
            "mobile_voice_websocket": "/ws/mobile-voice",
            "browser_test_page": "/mobile-voice-vision-test",
            "vision_preview_post": "/api/vision/preview",
            "this_discovery_document": "/api/mobile-voice-vision",
        },
        "client_ready_fields": {
            "vision_session_id": "string UUID; must match query param on vision WebSocket",
            "vision_enable": "optional bool; default true",
            "vision_mode": "optional bool alias for vision_enable",
            "vision_augment_every_turn": "optional bool; true = attach [Camera context] on every user turn (slower). false/absent uses VISION_AUGMENT_SESSION_LEVEL env (default 0 = regex-only intent)",
            "face_voice": "optional bool; same as Assistant POST /api/face/voice-bind face_voice — locks Voice+Video+Face session policy on server",
            "face_recognition": "optional bool; send true with Voice+Video+Face UI — requires server FACERECOG_ENABLED=1 for face pipeline",
            "face_profile": "optional string person_id; pins primary person for context when set",
        },
        "mid_session_mobile_voice": {
            "type": "vision-prefs",
            "frame": "UTF-8 JSON in a binary WebSocket frame (same as client-ready), after handshake",
            "vision_session_id": "string; must match /ws/mobile-voice-vision session",
            "vision_augment_every_turn": "bool; updates server without reconnecting Voice+Video",
        },
    }
