"""Shared logic: merge latest session JPEG + Ollama caption into user text (voice or HTTP chat)."""

from __future__ import annotations

from loguru import logger

from pipecat_bots.vision_client_prefs import (
    effective_augment_every_turn,
    vision_augment_session_level_enabled,
)


async def augment_user_text_with_vision(
    text: str,
    session_id: str | None,
    vision_enable: bool,
    *,
    utterance_source: str = "text",
) -> str:
    """If vision is enabled and session id is set, optionally append camera observation context.

    Per-session: checkbox / ``vision_augment_every_turn`` → every turn vs regex-only (see ``vision_client_prefs``).
    """
    if not vision_enable:
        return text
    sid = (session_id or "").strip()
    if not sid:
        return text
    utterance = (text or "").strip()
    if not utterance:
        return text

    from pipecat_bots.vision_intent import vision_augment_gate, vision_new_frame_intent
    from pipecat_bots.vision_policy_texts import format_camera_context_block, load_vision_policy_snippet
    from pipecat_bots.vision_session_store import get_vision_store

    if not effective_augment_every_turn(sid) and not vision_augment_gate(utterance):
        return text

    store = get_vision_store()
    src = "voice" if utterance_source == "voice" else "text"
    want_new_frame = vision_new_frame_intent(utterance)
    outcome = await store.wait_caption_for_augment(
        sid,
        utterance_source=src,
        require_fresh_frame=want_new_frame,
        recheck=want_new_frame,
    )
    caption = outcome.caption

    if caption in ("[Camera: no recent frame]", "[Camera: caption unavailable]"):
        suffix = load_vision_policy_snippet("unavailable")
        kind = "no_frame" if caption == "[Camera: no recent frame]" else "unavailable"
    else:
        suffix = load_vision_policy_snippet("substantive")
        kind = "substantive"
    preview = (caption[:120] + "…") if len(caption) > 120 else caption
    preview = preview.replace("\n", " ")
    logger.info(f"[vision_augment] sid={sid[:8]}… kind={kind} caption_preview={preview!r}")

    new_frame_note = ""
    if want_new_frame and kind == "substantive":
        new_frame_note = load_vision_policy_snippet("refresh_note")
    frame_for_block = outcome.frame_id if kind == "substantive" else None
    camera_block = format_camera_context_block(caption, frame_id=frame_for_block)
    return f"{utterance}\n\n{camera_block.rstrip()}{suffix}{new_frame_note}"


async def augment_chat_messages_for_vision(
    messages: list,
    vision_session_id: str | None,
    vision_enable: bool,
) -> list:
    """Return a deep-updated copy of OpenAI-style messages with last user string augmented."""
    import copy

    if not vision_enable:
        return messages
    sid = (vision_session_id or "").strip()
    if not sid:
        return messages

    out = copy.deepcopy(messages)
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        m["content"] = await augment_user_text_with_vision(content, sid, True, utterance_source="text")
        break
    return out
