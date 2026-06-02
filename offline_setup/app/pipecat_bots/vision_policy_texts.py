"""Load vision prompts and policy snippets from files (editable without code changes).

Override a single file with env vars, or set ``VISION_POLICY_DIR`` to a directory containing the
packaged ``vision_policy/*.txt`` files (including ``caption_instruction_*.txt`` and
``camera_context_template.txt``). Intent regexes live in ``vision_intents.json``; see
``pipecat_bots.vision_intent_patterns`` and ``VISION_INTENTS_FILE``.
"""

from __future__ import annotations

import os
from pathlib import Path

_POLICY_NAMES = frozenset(
    {
        "substantive",
        "unavailable",
        "refresh_note",
        "system_addon",
        "caption_instruction_base",
        "caption_instruction_recheck_suffix",
        "camera_context_template",
    }
)

_ENV_FOR_NAME: dict[str, str] = {
    "substantive": "VISION_POLICY_SUBSTANTIVE_FILE",
    "unavailable": "VISION_POLICY_UNAVAILABLE_FILE",
    "refresh_note": "VISION_POLICY_REFRESH_NOTE_FILE",
    "system_addon": "VISION_SYSTEM_ADDON_FILE",
    "caption_instruction_base": "VISION_CAPTION_INSTRUCTION_BASE_FILE",
    "caption_instruction_recheck_suffix": "VISION_CAPTION_INSTRUCTION_RECHECK_FILE",
    "camera_context_template": "VISION_CAMERA_CONTEXT_TEMPLATE_FILE",
}

_DEFAULT_DIR = Path(__file__).resolve().parent / "vision_policy"


def _policy_dir() -> Path:
    raw = (os.getenv("VISION_POLICY_DIR") or "").strip()
    return Path(raw) if raw else _DEFAULT_DIR


def _path_for(name: str) -> Path:
    if name not in _POLICY_NAMES:
        raise ValueError(f"unknown vision policy snippet: {name!r}")
    env_key = _ENV_FOR_NAME[name]
    override = (os.getenv(env_key) or "").strip()
    return Path(override) if override else _policy_dir() / f"{name}.txt"


def load_vision_policy_snippet(name: str) -> str:
    path = _path_for(name)
    try:
        # rstrip only: keep leading newlines in packaged snippets (e.g. blank line before [Vision policy]).
        text = path.read_text(encoding="utf-8").rstrip()
    except OSError as e:
        raise RuntimeError(
            f"Vision policy file missing or unreadable: {path} ({e}). "
            f"Set {_ENV_FOR_NAME[name]} or VISION_POLICY_DIR, or restore packaged vision_policy/*.txt."
        ) from e
    if not text.strip():
        raise RuntimeError(f"Vision policy file is empty: {path}")
    return text


def vision_system_addon_for_llm() -> str:
    """Text appended to the session system prompt when vision is enabled (voice)."""
    return load_vision_policy_snippet("system_addon")


def format_camera_context_block(caption: str, *, frame_id: int | None) -> str:
    """Build the user-message camera block from ``camera_context_template.txt`` (placeholders: ``caption``, ``assistant_note``)."""
    template = load_vision_policy_snippet("camera_context_template")
    assistant_note = (
        f"(Assistant grounding: description matches camera frame #{frame_id}.)\n\n"
        if frame_id is not None
        else ""
    )
    return (
        template.replace("{assistant_note}", assistant_note)
        .replace("{caption}", caption)
        .rstrip()
        + "\n"
    )
