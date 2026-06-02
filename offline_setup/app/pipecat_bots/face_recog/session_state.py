"""Per vision_session_id face observation state (process-local)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from pipecat_bots.face_recog import person_registry


@dataclass
class FaceSessionState:
    vision_session_id: str
    # Strict current-frame identities (no grace-memory merge).
    observed_now_person_ids: list[str] = field(default_factory=list)
    observed_person_ids: list[str] = field(default_factory=list)
    primary_person_id: str | None = None
    active_speaker_person_id: str | None = None  # reserved Phase 4
    stub_unknown_id: str | None = None
    focus_mode: str = "auto"
    focus_person_id: str | None = None
    # Voice+Video+Face: server-driven focus; locked until voice ends or force override.
    voice_face_active: bool = False
    focus_locked: bool = False
    # Stabilization memory to avoid rapid mode flapping on brief misses/pose changes.
    recent_seen_at: dict[str, float] = field(default_factory=dict)
    auto_single_hits: int = 0
    auto_multi_hits: int = 0
    auto_unknown_hits: int = 0
    auto_handover_hits: int = 0


_states: dict[str, FaceSessionState] = {}


def get_state(session_id: str) -> FaceSessionState | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    return _states.get(sid)


def get_or_create_state(session_id: str) -> FaceSessionState:
    sid = (session_id or "").strip()
    if sid not in _states:
        _states[sid] = FaceSessionState(vision_session_id=sid)
    return _states[sid]


def clear_state(session_id: str) -> None:
    sid = (session_id or "").strip()
    _states.pop(sid, None)


def set_voice_face_session(session_id: str, active: bool) -> FaceSessionState:
    st = get_or_create_state(session_id)
    st.voice_face_active = bool(active)
    if not active:
        st.focus_locked = False
    return st


def _reconcile_primary(st: FaceSessionState) -> None:
    if (
        st.focus_mode == "person"
        and st.focus_person_id
        and st.focus_person_id in st.observed_person_ids
    ):
        st.primary_person_id = st.focus_person_id


def update_observations(
    session_id: str,
    observed: list[str],
    primary: str | None,
) -> FaceSessionState:
    st = get_or_create_state(session_id)
    now = time.monotonic()
    # Keep recently seen IDs briefly so small pose/tilt dropouts don't instantly erase people.
    try:
        grace = float(os.getenv("FACERECOG_OBS_GRACE_SECS", "2.2") or "2.2")
    except ValueError:
        grace = 2.2
    grace = max(0.0, min(grace, 8.0))
    cur = [x for x in observed if (x or "").strip()]
    st.observed_now_person_ids = list(dict.fromkeys(cur))
    for pid in cur:
        st.recent_seen_at[pid] = now
    merged: list[str] = []
    for pid, ts in list(st.recent_seen_at.items()):
        if (now - ts) <= grace:
            merged.append(pid)
        else:
            st.recent_seen_at.pop(pid, None)
    # preserve detector order first, then grace-kept IDs
    out = list(dict.fromkeys(cur + merged))
    st.observed_person_ids = out
    st.primary_person_id = primary or (out[0] if out else None)
    _reconcile_primary(st)
    return st


def apply_auto_focus_from_detection(session_id: str) -> None:
    """Voice+Video+Face: single named face → person session; multi or unnamed mix → multiface."""
    st = get_state(session_id)
    if not st or not st.voice_face_active:
        return
    # For multiface switching, rely on strict current-frame observations only.
    obs_now = [x for x in st.observed_now_person_ids if x.strip()]
    obs = [x for x in st.observed_person_ids if x.strip()]
    # Hysteresis: require repeated evidence before switching modes.
    multi_now = len(obs_now) >= 2
    multi_all_enrolled_now = multi_now and all(person_registry.identity_is_enrolled(pid) for pid in obs_now)
    single_enrolled_now = (
        len(obs) == 1 and person_registry.identity_is_enrolled(obs[0])
    )
    current_focus = (st.focus_person_id or "").strip() if st.focus_mode == "person" else ""
    handover_now = bool(
        single_enrolled_now
        and current_focus
        and obs[0] != current_focus
    )
    unknown_now = not multi_now and not single_enrolled_now

    if multi_all_enrolled_now:
        st.auto_multi_hits += 1
        st.auto_single_hits = 0
        st.auto_unknown_hits = 0
        st.auto_handover_hits = 0
    elif handover_now:
        st.auto_handover_hits += 1
        st.auto_multi_hits = 0
        st.auto_single_hits = 0
        st.auto_unknown_hits = 0
    elif single_enrolled_now:
        st.auto_single_hits += 1
        st.auto_multi_hits = 0
        st.auto_unknown_hits = 0
        st.auto_handover_hits = 0
    else:
        st.auto_unknown_hits += 1
        st.auto_multi_hits = 0
        st.auto_single_hits = 0
        st.auto_handover_hits = 0

    # Two ticks balances responsiveness and stability.
    if st.auto_multi_hits >= 2:
        st.focus_mode = "multiface"
        st.focus_person_id = None
    elif st.auto_handover_hits >= 2 and single_enrolled_now:
        # Stable single-face handover to another enrolled person.
        st.focus_mode = "person"
        st.focus_person_id = obs[0]
    elif st.auto_single_hits >= 2:
        st.focus_mode = "person"
        st.focus_person_id = obs[0]
    elif st.auto_unknown_hits >= 2:
        # UX preference: once we are confidently in single-person mode, do not
        # demote to unknown on brief/medium misses. Switch away only when
        # multiface evidence appears (handled above) or explicit stop/reset.
        if st.focus_mode != "person":
            st.focus_mode = "auto"
            st.focus_person_id = None
    st.focus_locked = True
    _reconcile_primary(st)


def set_focus(
    session_id: str,
    mode: str,
    person_id: str | None = None,
    *,
    force: bool = False,
) -> FaceSessionState | None:
    """mode: auto | multiface | person"""
    sid = (session_id or "").strip()
    if not sid:
        return None
    st = get_or_create_state(session_id)
    if st.focus_locked and st.voice_face_active and not force:
        return st
    m = (mode or "auto").strip().lower()
    if m not in ("auto", "multiface", "person"):
        m = "auto"
    st.focus_mode = m
    pid = (person_id or "").strip() or None
    st.focus_person_id = pid if m == "person" else None
    _reconcile_primary(st)
    return st


def effective_primary(
    vision_session_id: str | None,
    client_face_profile: str | None = None,
) -> str | None:
    cf = (client_face_profile or "").strip()
    if cf:
        return cf
    sid = (vision_session_id or "").strip()
    if not sid:
        return None
    st = get_state(sid)
    if not st:
        return None
    if st.focus_mode == "person" and st.focus_person_id:
        return st.focus_person_id
    return st.primary_person_id or (st.observed_person_ids[0] if st.observed_person_ids else None)


def user_turn_target_person_ids(vision_session_id: str | None) -> list[str]:
    sid = (vision_session_id or "").strip()
    if not sid:
        return []
    st = get_state(sid)
    if not st:
        ep = effective_primary(sid, None)
        return [ep] if ep else []

    obs = list(st.observed_person_ids)
    if st.focus_mode == "multiface":
        return obs
    if len(obs) > 1:
        return obs
    ep = effective_primary(sid, None)
    if ep:
        return [ep]
    return obs[:1]


def session_public_dict(st: FaceSessionState) -> dict:
    names: list[dict[str, str | bool]] = []
    seen_ids: set[str] = set()
    for pid in st.observed_person_ids:
        row = person_registry.get_person(pid)
        dn = str((row or {}).get("display_name") or pid).strip()
        names.append(
            {
                "person_id": pid,
                "display_name": dn,
                "enrolled": person_registry.identity_is_enrolled(pid),
            }
        )
        seen_ids.add(pid)
    # While Voice+Video+Face keeps focus pinned (brief misses / head turn), detection may drop the
    # enrolled id from grace-merged observations — UI still needs a row to resolve focus_person_id.
    fpid = (st.focus_person_id or "").strip()
    if st.focus_mode == "person" and fpid and fpid not in seen_ids:
        row = person_registry.get_person(fpid)
        dn = str((row or {}).get("display_name") or fpid).strip()
        names.append(
            {
                "person_id": fpid,
                "display_name": dn,
                "enrolled": person_registry.identity_is_enrolled(fpid),
            }
        )
    now_names: list[dict[str, str | bool]] = []
    for pid in st.observed_now_person_ids:
        row = person_registry.get_person(pid)
        dn = str((row or {}).get("display_name") or pid).strip()
        now_names.append(
            {
                "person_id": pid,
                "display_name": dn,
                "enrolled": person_registry.identity_is_enrolled(pid),
            }
        )
    return {
        "vision_session_id": st.vision_session_id,
        "active": True,
        "observed_person_ids": list(st.observed_person_ids),
        "observed_with_names": names,
        "observed_now_person_ids": list(st.observed_now_person_ids),
        "observed_now_with_names": now_names,
        "primary_person_id": st.primary_person_id,
        "focus_mode": st.focus_mode,
        "focus_person_id": st.focus_person_id,
        "voice_face_active": st.voice_face_active,
        "focus_locked": st.focus_locked,
    }
