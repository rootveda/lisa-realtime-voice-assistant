"""
Python parity with static/session_memory/face_session_phase.js + face_phase_harness.html.

If JS computeFaceSessionPhase changes, update this module and the harness together.
"""

from __future__ import annotations

import pytest


def safe_trim(value: object) -> str:
    return (value if isinstance(value, str) else "").strip()


def compute_face_session_phase(d: dict) -> dict:
    """Mirror face_session_phase.js computeFaceSessionPhase."""
    obs = d.get("observed_person_ids") or []
    names = d.get("observed_with_names") or []
    obs_now = d.get("observed_now_person_ids")
    names_now = d.get("observed_now_with_names") or []
    has_strict = isinstance(obs_now, list) and len(obs_now) > 0
    multi_all_strict = (
        has_strict
        and len(obs_now) >= 2
        and len(names_now) == len(obs_now)
        and all(n and n.get("enrolled") for n in names_now)
    )
    enrolled_merged = len([n for n in names if n and n.get("enrolled")])
    multi = (
        d.get("focus_mode") == "multiface"
        or multi_all_strict
        or (
            not has_strict
            and len(obs) >= 2
            and enrolled_merged >= 2
        )
    )
    phase = "unknown"
    primary_name = ""
    if multi:
        phase = "multi"
    elif (
        has_strict
        and len(obs_now) == 1
        and names_now
        and names_now[0]
        and names_now[0].get("enrolled")
    ):
        phase = "single"
        primary_name = safe_trim(names_now[0].get("display_name")) or "Guest"
    elif d.get("focus_mode") == "person" and safe_trim(d.get("focus_person_id")):
        fp = safe_trim(d.get("focus_person_id"))
        row = next((n for n in names if n.get("person_id") == fp), None) or next(
            (n for n in names_now if n.get("person_id") == fp), None
        )
        if row and row.get("enrolled"):
            phase = "single"
            primary_name = safe_trim(row.get("display_name")) or "Guest"
    if phase == "unknown" and enrolled_merged == 1:
        en = next((n for n in names if n and n.get("enrolled")), None)
        if en:
            phase = "single"
            primary_name = safe_trim(en.get("display_name")) or "Guest"
    return {"phase": phase, "multi": bool(multi)}


@pytest.mark.regression
@pytest.mark.face
def test_parity_vectors_match_harness():
    alice = "person_alice_unit"
    unk = "unknown_unitbeef001"
    bob = "person_bob_unit"

    grace_merge = {
        "observed_person_ids": [alice, unk],
        "observed_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
            {"person_id": unk, "display_name": "Unknown", "enrolled": False},
        ],
        "observed_now_person_ids": [alice],
        "observed_now_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
        ],
        "focus_mode": "person",
        "focus_person_id": alice,
        "voice_face_active": True,
    }
    r1 = compute_face_session_phase(grace_merge)
    assert r1["phase"] == "single" and not r1["multi"]

    two_enrolled = {
        "observed_person_ids": [alice, bob],
        "observed_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
            {"person_id": bob, "display_name": "Bob", "enrolled": True},
        ],
        "observed_now_person_ids": [alice, bob],
        "observed_now_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
            {"person_id": bob, "display_name": "Bob", "enrolled": True},
        ],
        "focus_mode": "auto",
        "focus_person_id": None,
        "voice_face_active": True,
    }
    r2 = compute_face_session_phase(two_enrolled)
    assert r2["phase"] == "multi" and r2["multi"]

    legacy_two = {
        "observed_person_ids": [alice, bob],
        "observed_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
            {"person_id": bob, "display_name": "Bob", "enrolled": True},
        ],
        "focus_mode": "auto",
        "focus_person_id": None,
        "voice_face_active": True,
    }
    r3 = compute_face_session_phase(legacy_two)
    assert r3["phase"] == "multi"

    strict_mixed = {
        "observed_person_ids": [alice, unk],
        "observed_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
            {"person_id": unk, "display_name": "Unknown", "enrolled": False},
        ],
        "observed_now_person_ids": [alice, unk],
        "observed_now_with_names": [
            {"person_id": alice, "display_name": "Alice", "enrolled": True},
            {"person_id": unk, "display_name": "Unknown", "enrolled": False},
        ],
        "focus_mode": "auto",
        "focus_person_id": None,
        "voice_face_active": True,
    }
    r4 = compute_face_session_phase(strict_mixed)
    assert r4["phase"] != "multi"
