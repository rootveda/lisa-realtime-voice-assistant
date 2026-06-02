"""Detect spoken phrases that should trigger vision captioning.

Patterns are loaded from ``vision_policy/vision_intents.json`` (or ``VISION_INTENTS_FILE``).
"""

from __future__ import annotations

from pipecat_bots.vision_intent_patterns import get_vision_intent_patterns


def vision_intent(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    p = get_vision_intent_patterns()
    if p.primary.search(t):
        return True
    return bool(p.detail_followup.search(t))


def vision_refresh_intent(text: str) -> bool:
    """True when the user explicitly asks to re-examine the camera (wait for a newer frame + re-caption)."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(get_vision_intent_patterns().refresh.search(t))


def vision_color_probe_intent(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(get_vision_intent_patterns().color_probe.search(t))


def vision_new_frame_intent(text: str) -> bool:
    """Wait for a newer JPEG + recheck caption (refresh phrasing or color/detail probe)."""
    return vision_refresh_intent(text) or vision_color_probe_intent(text)


def vision_augment_gate(text: str) -> bool:
    """Whether to attach camera observation context for this user text."""
    return vision_intent(text) or vision_refresh_intent(text) or vision_color_probe_intent(text)
