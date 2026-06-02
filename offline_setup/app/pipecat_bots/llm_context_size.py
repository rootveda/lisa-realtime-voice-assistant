"""Shared LLM context window: llama-server ``--ctx-size`` and Ollama ``num_ctx``."""

from __future__ import annotations

import os
from typing import Any

_DEFAULT = 49152
_MAX = 262144


def effective_num_ctx() -> int:
    """Best-effort context length from stack env (capped)."""
    for key in (
        "LLM_CONTEXT_SIZE",
        "LOCAL_LLAMA_CTX_SIZE",
        "OLLAMA_NUM_CTX",
        "VISION_OLLAMA_NUM_CTX",
    ):
        raw = (os.getenv(key) or "").strip()
        if raw:
            try:
                return max(2048, min(int(raw), _MAX))
            except ValueError:
                pass
    return _DEFAULT


def is_probably_ollama_openai_base(url: str) -> bool:
    u = (url or "").lower().rstrip("/")
    return ":11434" in u or u.endswith("11434/v1")


def inject_ollama_options_num_ctx(payload: dict[str, Any] | None) -> None:
    """Set ``options.num_ctx`` for Ollama OpenAI ``/v1/chat/completions`` when URL looks like Ollama."""
    if not isinstance(payload, dict):
        return
    base = (os.environ.get("NVIDIA_LLM_URL") or "").strip().rstrip("/")
    if not is_probably_ollama_openai_base(base):
        return
    ctx = effective_num_ctx()
    opts = payload.get("options")
    if not isinstance(opts, dict):
        opts = {}
        payload["options"] = opts
    opts.setdefault("num_ctx", ctx)
