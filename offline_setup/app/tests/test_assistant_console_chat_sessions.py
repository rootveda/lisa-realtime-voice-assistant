"""Unit tests for assistant_console_chat_sessions persistence."""

from __future__ import annotations

import pytest

from pipecat_bots import assistant_console_chat_sessions as mod


@pytest.fixture
def sessions_file(tmp_path, monkeypatch):
    path = tmp_path / "assistant_console_chat_sessions.v1.json"
    monkeypatch.setattr(mod, "chat_sessions_path", lambda: path)
    return path


@pytest.mark.regression
def test_empty_store_when_missing(sessions_file):
    assert mod.load_chat_sessions() == mod._empty_store()


@pytest.mark.regression
def test_coerce_and_save_round_trip(sessions_file):
    payload = {
        "version": 1,
        "activeId": "sess_a",
        "updatedAt": 1_700_000_000_000,
        "sessions": [
            {
                "id": "sess_a",
                "name": "Work",
                "createdAt": 1_700_000_000_000,
                "updatedAt": 1_700_000_000_100,
                "messages": [
                    {"role": "user", "content": "hello", "ts": 1_700_000_000_050},
                    {"role": "assistant", "content": "hi"},
                ],
                "settings": {"ragEnabled": True},
            }
        ],
    }
    coerced, err = mod.coerce_chat_sessions_payload(payload)
    assert err == ""
    assert coerced["activeId"] == "sess_a"
    assert len(coerced["sessions"]) == 1
    assert coerced["sessions"][0]["messages"][0]["content"] == "hello"

    mod.save_chat_sessions(coerced)
    loaded = mod.load_chat_sessions()
    assert loaded["activeId"] == "sess_a"
    assert loaded["sessions"][0]["name"] == "Work"


@pytest.mark.regression
def test_rejects_too_many_sessions(sessions_file):
    sessions = [{"id": f"s{i}", "name": f"S{i}", "messages": []} for i in range(mod._MAX_SESSIONS + 1)]
    _, err = mod.coerce_chat_sessions_payload({"sessions": sessions})
    assert "too many sessions" in err


@pytest.mark.regression
def test_save_rejects_oversized_file(sessions_file, monkeypatch):
    monkeypatch.setattr(mod, "_MAX_FILE_BYTES", 64)
    big = {
        "version": 1,
        "activeId": "x",
        "updatedAt": 1,
        "sessions": [
            {
                "id": "x",
                "name": "x",
                "messages": [{"role": "user", "content": "x" * 200}],
            }
        ],
    }
    coerced, _ = mod.coerce_chat_sessions_payload(big)
    with pytest.raises(ValueError, match="exceeds"):
        mod.save_chat_sessions(coerced)
