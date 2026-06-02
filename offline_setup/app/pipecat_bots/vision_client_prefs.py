"""Per-session preference: attach camera context on every turn vs regex-only (voice + text chat)."""

from __future__ import annotations

import os

# session_id -> explicit override from client (None = fall back to VISION_AUGMENT_SESSION_LEVEL env)
_augment_every_turn: dict[str, bool | None] = {}


def vision_augment_session_level_enabled() -> bool:
    """Server default when the client does not send ``vision_augment_every_turn``.

    ``VISION_AUGMENT_SESSION_LEVEL=1`` → behave like “every turn” unless client overrides.
    Default **0** (regex-only) keeps latency low when the UI checkbox is off / absent.
    """
    v = (os.getenv("VISION_AUGMENT_SESSION_LEVEL") or "0").strip().lower()
    return v not in {"0", "false", "no", "off"}


def set_vision_augment_every_turn(session_id: str, value: bool | None) -> None:
    """Record client checkbox / JSON body. ``None`` clears override (use env default)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    if value is None:
        _augment_every_turn.pop(sid, None)
    else:
        _augment_every_turn[sid] = bool(value)


def clear_vision_client_prefs(session_id: str) -> None:
    """Drop prefs when vision WebSocket closes / session cleared."""
    sid = (session_id or "").strip()
    if not sid:
        return
    _augment_every_turn.pop(sid, None)


def effective_augment_every_turn(session_id: str) -> bool:
    """True = merge [Camera context] on every user turn; False = only when ``vision_intents`` regex matches."""
    sid = (session_id or "").strip()
    if not sid:
        return vision_augment_session_level_enabled()
    if sid in _augment_every_turn and _augment_every_turn[sid] is not None:
        return bool(_augment_every_turn[sid])
    return vision_augment_session_level_enabled()
