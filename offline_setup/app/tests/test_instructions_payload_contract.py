"""
Contract for POST/PUT /api/instructions body fields.

IMPORTANT: Keep aligned with pipecat_offline_patch.py instructions_create / instructions_update.
If handlers change, update this test.
"""

from __future__ import annotations

import pytest


def resolve_instruction_text(payload: dict) -> str:
    """Mirror server: prompt wins; else content."""
    p = str(payload.get("prompt") or "").strip()
    if not p:
        p = str(payload.get("content") or "").strip()
    return p


@pytest.mark.regression
@pytest.mark.instructions
def test_prompt_or_content_fallback():
    assert resolve_instruction_text({"prompt": " A "}) == "A"
    assert resolve_instruction_text({"content": " B "}) == "B"
    assert resolve_instruction_text({"prompt": "win", "content": "lose"}) == "win"
    assert resolve_instruction_text({"id": "x", "overwrite": True}) == ""
