"""Load face_recognition_instructions.yaml with safe defaults."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pipecat_bots.repo_root import resolve_repo_root

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


def _preset_paths() -> list[Path]:
    root = resolve_repo_root()
    env_p = (os.getenv("FACERECOG_INSTRUCTIONS_PATH") or "").strip()
    out = []
    if env_p:
        out.append(Path(env_p))
    out.append(root / "docs" / "face_recognition_instructions.yaml")
    out.append(Path(__file__).resolve().parent.parent.parent / "presets" / "face_recognition_instructions.yaml")
    return out


@lru_cache(maxsize=2)
def load_instructions() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "version": 1,
        "known_person_prefix": "[People context — known: {names}. Primary: {primary}.]",
        "unknown_person_prefix": "[People context — unknown visitor; be polite and generic.]",
        "multi_person_note": "Multiple people may be visible.",
        "broadcast_assistant_turns": True,
        "greeting_hint": "Greet {primary_name} naturally if appropriate.",
        "memory_snippet_lines": 8,
        "memory_label": "Earlier with this person:",
        "speaker_focus_note": "Audio speaker ID not active; using visual primary.",
        "enrollment_cta_line": (
            "Face identities can be saved locally on this deployment via the Face manager UI "
            "or POST /api/face/person; describe that when the user asks to enroll or save a face."
        ),
        "assistant_enrollment_note": (
            "This stack keeps face identities in a local SQLite registry. "
            "Enrollment is via /face-manager or POST /api/face/person — not via chat. "
            "Do not tell the user that faces or biometrics cannot be stored or that no registry exists; "
            "say you cannot enroll them yourself unless tools are enabled and point operators to Face manager."
        ),
    }
    for p in _preset_paths():
        if not p or not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            if yaml:
                data = yaml.safe_load(raw)
                if isinstance(data, dict):
                    merged = {**defaults, **data}
                    return merged
            break
        except Exception:
            continue
    return defaults


def reload_instructions() -> dict[str, Any]:
    load_instructions.cache_clear()
    return load_instructions()
