"""Per-person conversation turns (SQLite via person_registry)."""

from __future__ import annotations

from pipecat_bots.face_recog import person_registry


def append_turn(
    person_id: str,
    role: str,
    content: str,
    vision_session_id: str | None = None,
) -> None:
    person_registry.append_turn(person_id, role, content, vision_session_id)


def recent_turns(person_id: str, limit: int = 8):
    return person_registry.recent_turns_for_person(person_id, limit)


def broadcast_assistant_turn(
    person_ids: list[str],
    content: str,
    vision_session_id: str | None,
) -> None:
    """Duplicate assistant summary to each observed person (multi-face rule)."""
    for pid in person_ids:
        if pid.strip():
            append_turn(pid.strip(), "assistant", content, vision_session_id)
