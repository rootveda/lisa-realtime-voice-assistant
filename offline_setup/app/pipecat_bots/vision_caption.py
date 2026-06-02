"""Image → caption via Ollama OpenAI-compatible API (Tier A vision)."""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from loguru import logger

# Default when VISION_OLLAMA_MODEL is unset: Gemma vision tag commonly installed next to this stack’s local llama.
# Override with e.g. `ollama pull moondream` + VISION_OLLAMA_MODEL=moondream:latest for a smaller model.
DEFAULT_VISION_OLLAMA_MODEL = "gemma4:e2b-it-q4_K_M"

# Doc/examples often use these as placeholders; if copied into shell/.env they must not override the real default.
VISION_OLLAMA_MODEL_PLACEHOLDERS = frozenset({"...", "<tag>", "<model>", "<model:tag>"})


def effective_vision_ollama_model() -> str:
    """``VISION_OLLAMA_MODEL`` if set to a real tag, else ``DEFAULT_VISION_OLLAMA_MODEL``."""
    raw = (os.getenv("VISION_OLLAMA_MODEL") or "").strip()
    if not raw or raw in VISION_OLLAMA_MODEL_PLACEHOLDERS:
        return DEFAULT_VISION_OLLAMA_MODEL
    return raw


def vision_caption_try_openai_compat_first() -> bool:
    """When True, try ``/v1/chat/completions`` before native ``/api/chat``. When False, native only (lower latency)."""
    _oc = (os.getenv("VISION_CAPTION_OPENAI_COMPAT") or "1").strip().lower()
    return _oc not in {"0", "false", "no", "off", "native"}


# Vision models sometimes leak CoT / rubric / self-edit instructions into the reply; never inject as facts.
_BAD_CAPTION_RE = re.compile(
    r"review\s+against\s+(constraints|rules)|\*\*\s*review\s+against|internal\s+thinking|"
    r"<\|[^|]+\|>|thought\s*:|analysis\s*:|\bchain[-\s]?of[-\s]?thought\b|"
    r"\bself[-\s]?correction\b|\brefine\s+and\s+polish\b|\bensuring\s+flow\b|flow\s+and\s+density|"
    r"\bmeta[-\s]?rubric\b|\bediting\s+notes\b|\bcoverage\s+points\b|"
    r"description\s+flows\s+naturally|required\s+coverage|\bdrafting\b.*\brevision\b|"
    r"\bpolish\s*\(|\(\s*self[-\s]?correction|"
    r"\bdraft\s+the\s+description\b|\bsynthesize\s+into\s+(a\s+)?cohesive\s+paragraph\b|"
    r"\bfocusing\s+on\s+density\b|density\s+and\s+facts|initial\s+focus\s+on\s+the\s+interaction|"
    r"\*describe\s+the\s+people\b",
    re.IGNORECASE | re.MULTILINE,
)

# Rubric headings that may appear inline after real prose — strip everything from here onward (handled before _BAD_CAPTION_RE).
_INLINE_RUBRIC_START = re.compile(
    r"\b(?:Draft\s+the\s+Description|Synthesize\s+into\s+(?:a\s+)?Cohesive\s+Paragraph|"
    r"Refine\s+and\s+Polish)\s*[\(:]",
    re.IGNORECASE,
)


def _flatten_markdown_list_lines(text: str) -> str:
    """Join list items into one line (vision models often return '* item' fragments)."""
    parts: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[\*\-•]+\s*", "", s)
        s = re.sub(r"^\d+\.\s+", "", s)
        if s:
            parts.append(s)
    return " ".join(parts).strip()


def is_substantive_caption(text: str) -> bool:
    """True if caption is usable as scene context (not a fragment like '*   The')."""
    t = _flatten_markdown_list_lines((text or "").strip())
    if not t or len(t) < 14:
        return False
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'-]*", t)
    if len(words) < 4:
        return False
    # Reject fragments like "The" or "*   The room" cut mid-stream; allow short but dense lines.
    if len(words) < 5 and len(t) < 30:
        return False
    return True


def sanitize_caption_for_context(raw: str) -> str:
    """Return plain scene text, or empty string if the model output looks like meta/CoT junk."""
    t = (raw or "").strip()
    if not t:
        return ""
    # Strip trailing assignment-style rubrics ("Draft the Description…", "Synthesize into…") from same line or next line.
    m_inline = _INLINE_RUBRIC_START.search(t)
    if m_inline:
        head = t[: m_inline.start()].strip()
        if len(head) >= 40:
            t = head
            logger.info("[vision_caption] truncated caption at inline rubric heading")
        else:
            logger.warning("[vision_caption] rejected caption (rubric-led or rubric-only)")
            return ""
    # If the model pasted scene + rubric on a new line, keep only text before the first rubric line when obvious.
    cut = re.split(
        r"\n\s*(?:Refine\s+and\s+Polish|Self[-\s]?Correction|Draft\s+the\s+Description|"
        r"Synthesize\s+into|\*\*\s*Refine)",
        t,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(cut) > 1 and len(cut[0].strip()) >= 40:
        t = cut[0].strip()
        logger.info("[vision_caption] truncated caption at rubric tail (newline)")
    if _BAD_CAPTION_RE.search(t):
        logger.warning("[vision_caption] rejected caption (meta/CoT pattern detected)")
        return ""
    # Drop obvious markdown scaffolding at start
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"^\*\*([^*]+)\*\*\s*", r"\1 ", t)
    t = _flatten_markdown_list_lines(t)
    if not is_substantive_caption(t):
        logger.warning("[vision_caption] rejected caption (too thin or list junk)")
        return ""
    max_chars = int(os.getenv("VISION_CAPTION_MAX_CHARS", "3200"))
    max_chars = max(400, min(max_chars, 8000))
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(".", 1)[0] + "."
    return t.strip()


def _ollama_v1_base() -> str:
    """Base URL with /v1 suffix, e.g. http://127.0.0.1:11434/v1."""
    explicit = (os.getenv("VISION_OLLAMA_BASE") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = (os.getenv("OLLAMA_HOST") or "127.0.0.1").strip().rstrip("/")
    port = (os.getenv("OLLAMA_PORT") or "11434").strip()
    if host.startswith("http://") or host.startswith("https://"):
        root = host.rstrip("/")
        return root if root.endswith("/v1") else f"{root}/v1"
    return f"http://{host}:{port}/v1"


def _ollama_root_no_v1() -> str:
    """Ollama server root without ``/v1`` (for ``/api/chat``)."""
    b = _ollama_v1_base().rstrip("/")
    return b.removesuffix("/v1") if b.endswith("/v1") else b


def _caption_from_cot_blob(blob: str) -> str | None:
    """Pull usable prose from reasoning/thinking tails (Gemma often puts the scene at the end)."""
    text = blob.strip()
    if not text:
        return None
    if len(text) > 500:
        parts = re.split(r"(?<=[.!?])\s+", text)
        if len(parts) > 1:
            text = parts[-1].strip()[:800]
        else:
            text = text[:2000]
    return sanitize_caption_for_context(text) or None


def _extract_assistant_text(message: dict[str, Any]) -> str | None:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        c = sanitize_caption_for_context(content.strip())
        if c:
            return c
    # OpenAI compat: `reasoning`. Ollama native Gemma4: `thinking` (when content is empty / think stream).
    for key in ("reasoning", "thinking"):
        blob = message.get(key)
        if isinstance(blob, str) and blob.strip():
            c = _caption_from_cot_blob(blob)
            if c:
                return c
    return None


def _parse_openai_compat_caption(data: dict[str, Any]) -> str | None:
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            m = c0.get("message")
            if isinstance(m, dict):
                out = _extract_assistant_text(m)
                if out:
                    return out
    msg = data.get("message") if isinstance(data, dict) else None
    if isinstance(msg, dict):
        out = _extract_assistant_text(msg)
        if out:
            return out
    return None


def _caption_instruction(*, recheck: bool) -> str:
    from pipecat_bots.vision_policy_texts import load_vision_policy_snippet

    base = load_vision_policy_snippet("caption_instruction_base").strip()
    if not base:
        base = (
            "Describe this image in dense factual plain sentences only. "
            "Cover people, clothing colors, setting, and lighting; no markdown or bullet lists."
        )
    if recheck:
        extra = load_vision_policy_snippet("caption_instruction_recheck_suffix").strip()
        if extra:
            return f"{base}\n{extra}"
    return base


async def caption_jpeg(jpeg: bytes, *, recheck: bool = False) -> str:
    """Return a detailed caption for JPEG bytes; raises on HTTP/network failure."""
    import httpx

    model = effective_vision_ollama_model()
    timeout_sec = float(os.getenv("VISION_OLLAMA_TIMEOUT", "45"))
    # Low values cause mid-sentence stops when the model spends tokens on labels ("Examine the image:").
    max_tokens = int(os.getenv("VISION_CAPTION_MAX_TOKENS", "512"))
    max_tokens = max(120, min(max_tokens, 1200))
    if recheck:
        max_tokens = min(max_tokens + int(os.getenv("VISION_CAPTION_RECHECK_EXTRA_TOKENS", "120")), 1200)

    url_openai = f"{_ollama_v1_base()}/chat/completions"
    url_native = f"{_ollama_root_no_v1()}/api/chat"

    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    instruct = _caption_instruction(recheck=recheck)
    from pipecat_bots.llm_context_size import effective_num_ctx

    _n_ctx = effective_num_ctx()
    body_openai: dict[str, Any] = {
        "model": model,
        "stream": False,
        "max_tokens": max_tokens,
        "options": {"num_ctx": _n_ctx},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": instruct,
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
    }
    temp_raw = (os.getenv("VISION_CAPTION_TEMPERATURE") or "").strip()
    if temp_raw:
        try:
            body_openai["temperature"] = max(0.0, min(2.0, float(temp_raw)))
        except ValueError:
            logger.warning(f"[vision_caption] invalid VISION_CAPTION_TEMPERATURE={temp_raw!r}; ignored")
    if (os.getenv("VISION_CHAT_THINK", "") or "").strip().lower() not in {"1", "true", "yes"}:
        body_openai["think"] = False

    body_native: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": instruct + " Use plain sentences only. Do not output refine/polish/rubric/self-correction text.",
                "images": [b64],
            }
        ],
    }
    if (os.getenv("VISION_CHAT_THINK", "") or "").strip().lower() not in {"1", "true", "yes"}:
        body_native["think"] = False

    # Native Ollama options (shared for /api/chat).
    options: dict[str, Any] = {"num_predict": max_tokens, "num_ctx": _n_ctx}
    if temp_raw:
        try:
            options["temperature"] = max(0.0, min(2.0, float(temp_raw)))
        except ValueError:
            pass

    # Two hops when try_openai_compat_first and compat returns 200 but unparsed — set VISION_CAPTION_OPENAI_COMPAT=0 for native only.
    use_openai_compat = vision_caption_try_openai_compat_first()

    timeout = httpx.Timeout(timeout_sec, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        data: dict[str, Any] | None = None
        openai_compat_error: str | None = None

        if use_openai_compat:
            bo = dict(body_openai)
            r = await client.post(url_openai, json=bo)
            if r.status_code == 400 and bo.get("think") is not None:
                bo.pop("think", None)
                r = await client.post(url_openai, json=bo)
            if r.is_success:
                try:
                    data = r.json()
                except json.JSONDecodeError:
                    openai_compat_error = f"OpenAI compat returned non-JSON: {(r.text or '')[:400]}"
                    logger.warning(f"[vision_caption] {openai_compat_error}")
            else:
                snippet = (r.text or "").strip()[:1200]
                openai_compat_error = f"OpenAI compat HTTP {r.status_code}: {snippet}"
                logger.warning(f"[vision_caption] {openai_compat_error}")
                if r.status_code in (404, 405, 501):
                    logger.warning(f"[vision_caption] compat unavailable; using native {url_native!r}")

            if data is not None:
                out = _parse_openai_compat_caption(data)
                if out:
                    return out
                logger.warning(f"[vision_caption] empty parse from OpenAI-compat keys={list(data.keys())}")
        else:
            logger.debug("[vision_caption] VISION_CAPTION_OPENAI_COMPAT disabled — native /api/chat only")

        # Native /api/chat — works when /v1/chat/completions rejects the body (common: 400 on think/vision schema).
        bn = dict(body_native)
        bn["options"] = dict(options)
        r2 = await client.post(url_native, json=bn)
        if r2.status_code == 400 and bn.get("think") is not None:
            bn.pop("think", None)
            r2 = await client.post(url_native, json=bn)
        if not r2.is_success:
            tail = (r2.text or "").strip()[:1200]
            msg = f"Ollama native HTTP {r2.status_code}: {tail}"
            if openai_compat_error:
                msg = f"{openai_compat_error} | then {msg}"
            raise RuntimeError(msg)
        raw_native = r2.json()
        if not isinstance(raw_native, dict):
            raise RuntimeError(f"Ollama /api/chat: expected object, got {type(raw_native)}")
        chat_msg = raw_native.get("message")
        if isinstance(chat_msg, dict):
            out = _extract_assistant_text(chat_msg)
            if out:
                return out
        logger.warning(f"[vision_caption] unexpected /api/chat keys: {list(raw_native.keys())}")
        raise RuntimeError(f"Could not parse Ollama /api/chat response: {json.dumps(raw_native, default=str)[:500]}")
