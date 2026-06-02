"""text_chat_truncate: drop oldest turns under char budget."""

from __future__ import annotations

import pytest

from pipecat_bots.text_chat_truncate import truncate_messages_for_upstream


@pytest.mark.regression
def test_drops_oldest_chat_after_system_blocks():
    sys1 = {"role": "system", "content": "S" * 100}
    u1 = {"role": "user", "content": "old user"}
    a1 = {"role": "assistant", "content": "old asst"}
    u2 = {"role": "user", "content": "KEEP_ME_LAST"}
    msgs = [sys1, u1, a1, u2]
    out, meta = truncate_messages_for_upstream(msgs, max_chars=110)
    assert out[-1].get("content") == "KEEP_ME_LAST"
    assert meta["dropped_turns"] >= 1


@pytest.mark.regression
def test_truncates_oversized_final_user():
    big = "X" * 100_000
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": big}]
    out, meta = truncate_messages_for_upstream(msgs, max_chars=8000)
    assert meta["truncated_tail"] is True
    assert len(out[-1]["content"]) < len(big)
