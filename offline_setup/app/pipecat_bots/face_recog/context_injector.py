"""Build optional prefix from instructions YAML + session state + recent turns."""

from __future__ import annotations

from pipecat_bots.face_recog import person_registry
from pipecat_bots.face_recog.instructions_loader import load_instructions
from pipecat_bots.face_recog.session_state import effective_primary, get_state


def _row_display_name(row: dict | None) -> str:
    if not row:
        return ""
    return str(row.get("display_name") or "").strip()


def _label_for_llm(person_id: str) -> str:
    """Human-readable label; never surface raw stub ids or literal Unknown as a person's name."""
    pid = (person_id or "").strip()
    if person_registry.identity_is_enrolled(pid):
        row = person_registry.get_person(pid)
        return _row_display_name(row) or pid
    if pid.startswith("stub_vs_"):
        return "Unregistered visitor (camera session stub)"
    if pid.startswith("unknown_"):
        return "Unregistered visitor (legacy id)"
    return f"Unregistered visitor ({pid[:24]})"


def _all_observed_are_unregistered(observed: list[str]) -> bool:
    return all(not person_registry.identity_is_enrolled(p) for p in observed)


def build_face_context_prefix(
    vision_session_id: str | None,
    primary_override: str | None = None,
) -> str | None:
    sid = (vision_session_id or "").strip()
    if not sid:
        return None
    st = get_state(sid)
    if not st or not st.observed_person_ids:
        return None
    instr = load_instructions()
    observed = list(st.observed_person_ids)
    obs_now = [x for x in st.observed_now_person_ids if (x or "").strip()]
    multi_enrolled_now = (
        len(obs_now) >= 2 and all(person_registry.identity_is_enrolled(p) for p in obs_now)
    )
    primary_id = (effective_primary(sid, primary_override) or "").strip()
    if not primary_id:
        return None

    primary_label = _label_for_llm(primary_id)
    names_joined = ", ".join(_label_for_llm(p) for p in observed)
    all_unreg = _all_observed_are_unregistered(observed)

    lines: list[str] = []
    if st.focus_mode == "multiface" or (multi_enrolled_now and st.focus_mode == "auto"):
        lines.append(
            "[Session mode: multi-face — shared dialogue context; user/assistant turns are recorded for each visible person_id.]"
        )
    if st.focus_mode == "person" and st.focus_person_id:
        lines.append(
            f"[Focus pinned to person_id={st.focus_person_id} ({_label_for_llm(st.focus_person_id)}).]"
        )

    if all_unreg:
        unk = str(instr.get("unknown_person_prefix") or "").strip()
        if unk:
            lines.append(unk)
        enroll = str(instr.get("enrollment_cta_line") or "").strip()
        if enroll and all_unreg:
            lines.append(enroll)
    else:
        tpl = str(instr.get("known_person_prefix") or "").strip()
        if tpl:
            lines.append(tpl.format(names=names_joined, primary=primary_label))

    if st.focus_mode == "multiface" or multi_enrolled_now:
        mp = str(instr.get("multi_person_note") or "").strip()
        if mp:
            lines.append(mp)

    spk = str(instr.get("speaker_focus_note") or "").strip()
    if spk:
        lines.append(spk)

    n = int(instr.get("memory_snippet_lines") or 8)
    recent = person_registry.recent_turns_for_person(primary_id, limit=max(1, n))
    if recent:
        label = str(instr.get("memory_label") or "Earlier with this person:").strip()
        if label:
            lines.append(label)
        for t in recent[-n:]:
            role = t.get("role", "")
            content = (t.get("content") or "").strip()
            if content:
                lines.append(f"- ({role}) {content}")

    gh = str(instr.get("greeting_hint") or "").strip()
    if gh and recent and person_registry.identity_is_enrolled(primary_id):
        try:
            lines.append(gh.format(primary_name=primary_label))
        except Exception:
            pass

    out = "\n".join(lines).strip()
    return out or None


def face_system_addon_for_llm() -> str:
    """Appended to voice session system prompt when face_recognition is on."""
    instr = load_instructions()
    return str(instr.get("assistant_enrollment_note") or "").strip()
