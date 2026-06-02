"""Tests for GET /api/face/session/{id} payload: merged vs observed_now_* fields."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.face]

from pipecat_bots.face_recog import person_registry as pr
from pipecat_bots.face_recog import session_state as ss
from pipecat_bots.face_recog.api import get_face_session
from pipecat_bots.face_recog.config import set_facerecog_runtime
from pipecat_bots.face_recog.session_state import (
    clear_state,
    session_public_dict,
    update_observations,
)


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("FACERECOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FACERECOG_ENABLED", "1")
    pr._conn = None
    ss._states.clear()
    set_facerecog_runtime(True)
    yield
    ss._states.clear()
    pr._conn = None


def _seed_persons():
    pr.upsert_person("person_alice_api", "Alice", embedding=None)
    pr.upsert_person("person_bob_api", "Bob", embedding=None)
    pr.upsert_person("unknown_bucket_api", "Unknown", embedding=None)


def test_session_public_dict_strict_vs_grace_merge(isolated_registry):
    """After two-frame sequence, merged list can hold grace ID while strict frame is single."""
    _seed_persons()
    sid = "vs-grace-1"
    alice, unk = "person_alice_api", "unknown_bucket_api"
    update_observations(sid, [alice, unk], alice)
    update_observations(sid, [alice], alice)
    st = ss.get_state(sid)
    assert st is not None
    assert st.observed_now_person_ids == [alice]
    assert alice in st.observed_person_ids
    assert unk in st.observed_person_ids
    pub = session_public_dict(st)
    assert pub["observed_now_person_ids"] == [alice]
    assert unk in pub["observed_person_ids"]
    assert len(pub["observed_now_with_names"]) == 1
    assert pub["observed_now_with_names"][0]["person_id"] == alice
    assert pub["observed_now_with_names"][0]["enrolled"] is True


def test_session_public_dict_two_enrolled_strict(isolated_registry):
    _seed_persons()
    sid = "vs-two"
    alice, bob = "person_alice_api", "person_bob_api"
    update_observations(sid, [alice, bob], alice)
    pub = session_public_dict(ss.get_state(sid))
    assert len(pub["observed_now_person_ids"]) == 2
    assert all(x["enrolled"] for x in pub["observed_now_with_names"])


@pytest.mark.asyncio
async def test_get_face_session_matches_public_dict(isolated_registry):
    _seed_persons()
    sid = "vs-api"
    alice, unk = "person_alice_api", "unknown_bucket_api"
    update_observations(sid, [alice, unk], alice)
    update_observations(sid, [alice], alice)
    out = await get_face_session(sid)
    assert out["observed_now_person_ids"] == ["person_alice_api"]
    assert "unknown_bucket_api" in out["observed_person_ids"]
    assert len(out["observed_now_with_names"]) == 1


def test_session_public_dict_includes_focus_person_when_not_observed(isolated_registry):
    """Pinned focus stays resolvable when a head turn drops the id from merged observations."""
    _seed_persons()
    sid = "vs-focus-pin"
    alice = "person_alice_api"
    update_observations(sid, [], None)
    st = ss.get_state(sid)
    assert st is not None
    st.focus_mode = "person"
    st.focus_person_id = alice
    st.voice_face_active = True
    pub = session_public_dict(st)
    rows = [x for x in pub["observed_with_names"] if x["person_id"] == alice]
    assert len(rows) == 1
    assert rows[0]["enrolled"] is True
    assert "Alice" in rows[0]["display_name"]


def test_get_face_session_inactive_returns_empty_observed_now(isolated_registry):
    async def run():
        return await get_face_session("nonexistent-session-xyz")

    out = asyncio.run(run())
    assert out.get("active") is False
    assert out.get("observed_now_person_ids") == []
    assert out.get("observed_now_with_names") == []
