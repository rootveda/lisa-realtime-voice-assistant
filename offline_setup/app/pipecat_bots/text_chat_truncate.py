"""Trim OpenAI-style ``messages`` before upstream /chat/completions to avoid Ollama exceed_context_size_error."""

from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger


def _msg_chars(m: dict[str, Any]) -> int:
    c = m.get("content")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return len(json.dumps(c))
    return len(json.dumps(m))


def default_max_prompt_tokens() -> int:
    """Target max prompt tokens (Ollama counts tokens; ``n_ctx`` includes prompt + completion).

    Uses ``TEXT_CHAT_MAX_PROMPT_TOKENS`` when set; otherwise derives from ``LLM_CONTEXT_SIZE`` /
    ``LOCAL_LLAMA_CTX_SIZE`` (via ``effective_num_ctx``), leaving headroom for completion.
    """
    raw = os.getenv("TEXT_CHAT_MAX_PROMPT_TOKENS", "").strip()
    if raw:
        try:
            return max(512, min(int(raw), 500_000))
        except ValueError:
            pass
    try:
        from pipecat_bots.llm_context_size import effective_num_ctx

        ctx = effective_num_ctx()
        return max(512, min(ctx - 8192, int(ctx * 0.88)))
    except Exception:
        pass
    return 40960


def default_max_prompt_chars() -> int:
    """Hard cap on total message characters (secondary to token estimate).

    If unset, derived from ``default_max_prompt_tokens()`` × 3 (≈ chars per token for Latin text).
    Override with TEXT_CHAT_MAX_PROMPT_CHARS.
    """
    raw = os.getenv("TEXT_CHAT_MAX_PROMPT_CHARS", "").strip()
    if raw:
        try:
            return max(4096, min(int(raw), 2_000_000))
        except ValueError:
            pass
    return max(4096, default_max_prompt_tokens() * 3)


def _approx_prompt_tokens(message_list: list[Any]) -> int:
    """Conservative token estimate (real BPE often ≥ chars/3 for mixed text)."""
    tc = sum(_msg_chars(m) for m in message_list if isinstance(m, dict))
    # Use max of two heuristics so we trim before Ollama rejects (often ~chars/2 for code/dense text).
    return max((tc + 2) // 3, (tc + 1) // 2)


def truncate_messages_for_upstream(
    messages: list[Any],
    *,
    max_chars: int | None = None,
    max_prompt_tokens: int | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Drop oldest messages after leading ``system`` block until under char + estimated token budgets."""
    meta: dict[str, Any] = {
        "dropped_turns": 0,
        "truncated_tail": False,
        "max_chars": max_chars or default_max_prompt_chars(),
        "max_prompt_tokens": max_prompt_tokens or default_max_prompt_tokens(),
    }
    max_c = meta["max_chars"]
    max_tok = meta["max_prompt_tokens"]
    if not isinstance(messages, list) or not messages:
        return messages, meta

    msgs: list[Any] = [dict(m) if isinstance(m, dict) else m for m in messages]

    def total_chars() -> int:
        return sum(_msg_chars(m) for m in msgs if isinstance(m, dict))

    def over_budget() -> bool:
        return total_chars() > max_c or _approx_prompt_tokens(msgs) > max_tok

    sys_count = 0
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "system":
            sys_count += 1
        else:
            break

    while over_budget() and len(msgs) > sys_count + 1:
        msgs.pop(sys_count)
        meta["dropped_turns"] += 1

    truncate_marker = (
        "\n\n[…truncated: prompt exceeded context budget — reduce attachments or history lines, "
        "lower Text chat max prompt tokens/chars in LLM generation, set TEXT_CHAT_MAX_PROMPT_TOKENS, "
        "or increase model num_ctx]"
    )

    # Iteratively shrink the tail (last non-system) message until under budget.
    if over_budget() and msgs:
        for _ in range(8):
            if not over_budget():
                break
            last = msgs[-1]
            if not isinstance(last, dict) or not isinstance(last.get("content"), str):
                break
            c = last["content"]
            if len(c) <= 4000:
                break
            over_c = total_chars() - max_c + 800
            over_t = max(0, _approx_prompt_tokens(msgs) - max_tok) * 3
            # ``cut`` is the number of characters we want to remove from c.
            # Make sure each iteration strictly reduces len(c) by at least 25%.
            cut_candidate = max(over_c, over_t, 4000)
            min_cut = max(int(len(c) * 0.25), 4000)
            cut = max(cut_candidate, min_cut)
            keep = max(4000, len(c) - int(cut))
            if keep >= len(c):
                break
            last["content"] = c[:keep] + truncate_marker
            meta["truncated_tail"] = True

    # Last resort: trim large system prompts.
    if over_budget():
        for sm in msgs[:sys_count]:
            if isinstance(sm, dict) and isinstance(sm.get("content"), str):
                c = sm["content"]
                if len(c) > 4000:
                    sm["content"] = c[:4000] + "\n[…system block truncated for context limit]"
                    meta["truncated_tail"] = True
                    if not over_budget():
                        break

    if meta["dropped_turns"] or meta["truncated_tail"]:
        logger.warning(
            "[text-chat] context trim: dropped=%s truncated_tail=%s chars≈%s tok≈%s max_char=%s max_tok=%s",
            meta["dropped_turns"],
            meta["truncated_tail"],
            total_chars(),
            _approx_prompt_tokens(msgs),
            max_c,
            max_tok,
        )

    return msgs, meta
