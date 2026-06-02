#!/usr/bin/env python3
"""Assert voice LLM initial messages include face enrollment guidance when face mode is active.

No HTTP server, microphone, or Pipecat — uses pipecat_bots.voice_llm_initial_messages only.

Run from offline_setup/app:
  PYTHONPATH=. python scripts/test_voice_face_system_prompt.py
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    from pipecat_bots.voice_llm_initial_messages import build_voice_llm_initial_messages
    from pipecat_bots.face_recog.context_injector import face_system_addon_for_llm
    from pipecat_bots.face_recog.instructions_loader import reload_instructions

    os.environ["FACERECOG_ENABLED"] = "1"
    reload_instructions()
    needle_source = face_system_addon_for_llm()
    if not needle_source:
        print("FAIL: face_system_addon_for_llm() is empty — check face_recognition_instructions.yaml", file=sys.stderr)
        return 1
    # Stable anchors from assistant_enrollment_note (defaults + docs).
    if "/face-manager" not in needle_source and "Face manager" not in needle_source:
        print("FAIL: enrollment note missing expected UI hint", file=sys.stderr)
        return 1

    body_default = {
        "vision_session_id": "e2e-vision-sess",
        "vision_enable": True,
        "face_recognition": True,
    }
    msgs = build_voice_llm_initial_messages(body_default)
    sys_text = "\n".join(m.get("content", "") for m in msgs if m.get("role") == "system")
    if needle_source.strip() not in sys_text:
        print("FAIL: default system path missing face enrollment addon", file=sys.stderr)
        print(sys_text[:1200], file=sys.stderr)
        return 1

    body_custom = {
        **body_default,
        "client_ready_messages": [
            {"role": "system", "content": "Synthetic operator session prompt for integration test."},
        ],
    }
    msgs2 = build_voice_llm_initial_messages(body_custom)
    first_system = next((m["content"] for m in msgs2 if m.get("role") == "system"), "")
    if needle_source.strip() not in first_system:
        print("FAIL: client_ready_messages system message missing face enrollment addon", file=sys.stderr)
        print(first_system[:1200], file=sys.stderr)
        return 1
    if "Synthetic operator session prompt" not in first_system:
        print("FAIL: custom system prompt was dropped", file=sys.stderr)
        return 1

    off = build_voice_llm_initial_messages({"vision_session_id": "x", "face_recognition": False})
    off_sys = "\n".join(m.get("content", "") for m in off if m.get("role") == "system")
    if needle_source.strip() in off_sys:
        print("FAIL: face addon present when face_recognition is false", file=sys.stderr)
        return 1

    print("OK   build_voice_llm_initial_messages includes face enrollment note (default + client_ready)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
