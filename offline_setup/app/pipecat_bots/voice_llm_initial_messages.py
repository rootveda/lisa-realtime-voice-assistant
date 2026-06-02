"""Voice session LLM message seed (system prompt + vision/face addons).

Separated from bot_vllm so integration tests can run without importing Pipecat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from pipecat_bots.repo_root import resolve_repo_root


def load_default_system_prompt() -> str:
    """Load default assistant instructions: repo docs, then bundled app presets, then legacy file."""
    docs = resolve_repo_root() / "docs"
    bundled = Path(__file__).resolve().parent.parent / "presets" / "instructions"
    for instructions_path in (
        docs / "instructions" / "instruction1.md",
        bundled / "instruction1.md",
    ):
        try:
            text = instructions_path.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception as e:
            logger.warning(f"Could not read {instructions_path}: {e}")
    return (
        "You are a helpful voice assistant. Keep responses concise, clear, and conversational. "
        "Use plain text sentences suitable for speech synthesis."
    )


def build_voice_llm_initial_messages(body: dict | None) -> list[dict[str, Any]]:
    """Build LLM messages for a voice session: default or client_ready_messages, plus vision/face addons.

    Side-effect free (does not update vision_client_prefs). Used by bot_vllm and integration tests.
    """
    body = body if isinstance(body, dict) else {}
    vision_session_id: str | None = None
    vision_enable = True
    face_recognition = False
    vs = body.get("vision_session_id")
    if isinstance(vs, str) and vs.strip():
        vision_session_id = vs.strip()
    if "vision_enable" in body and isinstance(body["vision_enable"], bool):
        vision_enable = body["vision_enable"]
    elif "vision_mode" in body and isinstance(body["vision_mode"], bool):
        vision_enable = body["vision_mode"]
    fr = body.get("face_recognition")
    if isinstance(fr, bool):
        face_recognition = fr

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": load_default_system_prompt(),
        },
    ]

    client_messages = body.get("client_ready_messages")
    if isinstance(client_messages, list) and client_messages:
        sanitized_client_messages: list[dict[str, Any]] = []
        for msg in client_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role in {"system", "user", "assistant"} and isinstance(content, str) and content.strip():
                sanitized_client_messages.append({"role": role, "content": content.strip()})
        if sanitized_client_messages:
            messages = sanitized_client_messages

    from pipecat_bots.face_recog.config import facerecog_enabled as _face_env_on

    face_session_active = bool(
        _face_env_on() and face_recognition and vision_session_id and vision_enable
    )

    if vision_session_id and vision_enable:
        from pipecat_bots.vision_policy_texts import vision_system_addon_for_llm

        _vision_addon = "\n\n" + vision_system_addon_for_llm().strip()
        added = False
        for m in messages:
            if m.get("role") == "system" and isinstance(m.get("content"), str):
                m["content"] = (m["content"] or "") + _vision_addon
                added = True
                break
        if not added:
            messages.insert(0, {"role": "system", "content": load_default_system_prompt() + _vision_addon})

    if face_session_active:
        from pipecat_bots.face_recog.context_injector import face_system_addon_for_llm

        _face_addon = face_system_addon_for_llm().strip()
        if _face_addon:
            _fa = "\n\n" + _face_addon
            added_face = False
            for m in messages:
                if m.get("role") == "system" and isinstance(m.get("content"), str):
                    m["content"] = (m["content"] or "") + _fa
                    added_face = True
                    break
            if not added_face:
                messages.insert(0, {"role": "system", "content": load_default_system_prompt() + _fa})

    return messages
