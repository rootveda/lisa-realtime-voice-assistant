"""Persist Assistant Console chat sessions on disk (mirrors browser session_store.js)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_SESSIONS = 64
_MAX_MESSAGES_PER_SESSION = 2000
_MAX_MESSAGE_CHARS = 120_000
_MAX_SESSION_NAME_CHARS = 128


def chat_sessions_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assistant_console_chat_sessions.v1.json"


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "activeId": "", "updatedAt": 0, "sessions": []}


def load_chat_sessions() -> dict[str, Any]:
    p = chat_sessions_path()
    if not p.is_file():
        return _empty_store()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        out, _ = coerce_chat_sessions_payload(data, partial=True)
        return out
    except Exception:
        return _empty_store()


def save_chat_sessions(payload: dict[str, Any]) -> None:
    p = chat_sessions_path()
    tmp = p.with_name(p.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    raw = text.encode("utf-8")
    if len(raw) > _MAX_FILE_BYTES:
        raise ValueError(f"chat sessions payload exceeds {_MAX_FILE_BYTES} bytes")
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(p)


def _norm_session(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    sid = str(raw.get("id") or "").strip()
    if not sid:
        return None
    name = str(raw.get("name") or "Untitled session").strip()[:_MAX_SESSION_NAME_CHARS] or "Untitled session"
    msgs_in = raw.get("messages") if isinstance(raw.get("messages"), list) else []
    messages: list[dict[str, Any]] = []
    for m in msgs_in[:_MAX_MESSAGES_PER_SESSION]:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip()
        if role not in ("user", "assistant", "system"):
            continue
        content = str(m.get("content") or "")[:_MAX_MESSAGE_CHARS]
        if not content and role != "system":
            continue
        out: dict[str, Any] = {"role": role, "content": content}
        if m.get("ts") is not None:
            try:
                out["ts"] = int(m.get("ts"))
            except (TypeError, ValueError):
                pass
        messages.append(out)
    settings_raw = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    rag = settings_raw.get("ragEnabled")
    settings = {"ragEnabled": rag is not False}
    try:
        created = int(raw.get("createdAt") or 0)
    except (TypeError, ValueError):
        created = 0
    try:
        updated = int(raw.get("updatedAt") or 0)
    except (TypeError, ValueError):
        updated = 0
    now_ms = int(time.time() * 1000)
    if created <= 0:
        created = now_ms
    if updated <= 0:
        updated = created
    return {
        "id": sid,
        "name": name,
        "createdAt": created,
        "updatedAt": updated,
        "messages": messages,
        "settings": settings,
    }


def coerce_chat_sessions_payload(body: object, *, partial: bool = False) -> tuple[dict[str, Any], str]:
    if not isinstance(body, dict):
        return _empty_store(), "expected JSON object"
    sessions_in = body.get("sessions")
    if sessions_in is None and partial:
        sessions_in = []
    if not isinstance(sessions_in, list):
        return _empty_store(), "sessions must be an array"
    if len(sessions_in) > _MAX_SESSIONS:
        return _empty_store(), f"too many sessions (max {_MAX_SESSIONS})"
    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in sessions_in:
        s = _norm_session(raw if isinstance(raw, dict) else {})
        if not s or s["id"] in seen:
            continue
        seen.add(s["id"])
        sessions.append(s)
    active_id = str(body.get("activeId") or "").strip()
    if active_id and not any(s["id"] == active_id for s in sessions):
        active_id = sessions[0]["id"] if sessions else ""
    elif not active_id and sessions:
        active_id = sessions[0]["id"]
    try:
        updated_at = int(body.get("updatedAt") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    if updated_at <= 0:
        updated_at = int(time.time() * 1000)
    return {
        "version": 1,
        "activeId": active_id,
        "updatedAt": updated_at,
        "sessions": sessions,
    }, ""
