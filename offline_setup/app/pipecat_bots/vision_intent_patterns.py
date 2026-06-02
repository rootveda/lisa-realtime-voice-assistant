"""Load vision intent regexes from packaged JSON (override with VISION_INTENTS_FILE).

Falls back to built-in defaults if the file is missing or a pattern key is absent.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

_DEFAULT_JSON = Path(__file__).resolve().parent / "vision_policy" / "vision_intents.json"

# Built-in fallbacks (keep in sync with vision_intents.json; used if JSON is unreadable).
_DEFAULT_PRIMARY = (
    r"(what\s+do\s+you\s+see|what\s+.*\s+see|can\s+you\s+see|do\s+you\s+see|"
    r"look\s+at|look\s+around|show\s+me|point\s+to\s+the\s+camera|"
    r"describe\s+(the\s+)?(room|scene|environment|surroundings|view|picture)|"
    r"what(?:'s|\s+is)\s+(that|this|there|on\s+(the\s+)?(screen|desk|table))|"
    r"\b(see|seeing|camera|webcam|picture|image|objects?|environment|around\s+you)\b|"
    r"what\s+(?:are\s+we\s+doing|we\s+are\s+doing)\??|"
    r"what\s+is\s+(?:going\s+on|happening)\??(?:\s+here)?|"
    r"check\s+the\s+camera|can\s+you\s+check\s+(?:the\s+)?(?:camera|feed|view)|"
    r"(?:can\s+you|could\s+you)\s+check\b[\s\S]{0,160}?\bdoing\b)"
)
_DEFAULT_DETAIL = (
    r"(^|\b)(eyes?\s*(open|closed|shut)?|eye\s+contact|blink(s|ing)?|gaze|squint|wink|"
    r"\b(open|closed)\s+or\s+(not|open|closed)\b|open\s+or\s+not|closed\s+or\s+open|"
    r"^now\??$|^again\??$|^try\s+again\.?$|"
    r"what\s+about\s+now|how\s+about\s+now|right\s+now|at\s+this\s+moment|"
    r"my\s+(face|expression)|am\s+i\s+(smiling|frowning|blinking)|"
    r"can\s+you\s+tell.*\b(eyes|face)\b|"
    r"\b(face|mouth|nose|beard|hair|glasses|hat)\b.*\b(visible|see|look)\b)"
)
_DEFAULT_REFRESH = (
    r"(check\s+(that|it)\s+again|can\s+you\s+check(?:\s+that|\s+it)?\s+again|look\s+again|look\s+once\s+more|"
    r"take\s+another\s+look|another\s+look|one\s+more\s+look|"
    r"verify\s+again|double[-\s]?check|re[-\s]?check|"
    r"refresh\s+(the\s+)?(view|camera|feed|picture)|"
    r"^what\s+about\s+now\??$|^and\s+now\??$)"
)
_DEFAULT_COLOR_PROBE = (
    r"(\bany\b.*\b(green|red|blue|yellow|purple|orange|pink|white|black|brown|teal|cyan|grey|gray)\b|"
    r"\b(green|red|blue|yellow|purple|orange|pink|white|black|brown|teal|cyan|grey|gray)\s+colou?r\b|"
    r"\b(is\s+there|are\s+there|is\s+it|are\s+they)\b.*\b(green|red|blue|yellow|purple|orange|pink|white|black)\b|"
    r"\b(do\s+you\s+see|can\s+you\s+see|can\s+i\s+check)\b.*\b(green|red|blue|yellow|colou?r)\b|"
    r"\bcheck\s+whether\b.*\b(green|red|blue|colou?r)\b|"
    r"\bwhat\s+colou?rs?\b|\bwhich\s+colou?r\b|\bspot\s+any\b.*\b(colou?r|green|red|blue)\b)"
)

_FLAGS = re.IGNORECASE


@dataclass(frozen=True)
class VisionIntentPatterns:
    primary: re.Pattern[str]
    detail_followup: re.Pattern[str]
    refresh: re.Pattern[str]
    color_probe: re.Pattern[str]


def _intent_json_path() -> Path:
    raw = (os.getenv("VISION_INTENTS_FILE") or "").strip()
    return Path(raw) if raw else _DEFAULT_JSON


def _load_spec(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"[vision_intent_patterns] cannot read {path}: {e}; using built-in regex defaults")
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[vision_intent_patterns] invalid JSON in {path}: {e}; using built-in regex defaults")
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k in ("primary_regex", "detail_followup_regex", "refresh_regex", "color_probe_regex"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def _compile_or_fallback(spec: dict[str, str], key: str, fallback: str) -> re.Pattern[str]:
    src = spec.get(key) or fallback
    try:
        return re.compile(src, _FLAGS)
    except re.error as e:
        logger.error(f"[vision_intent_patterns] invalid regex for {key!r}: {e}; using built-in fallback")
        return re.compile(fallback, _FLAGS)


_cache_key: tuple[str, int | float] | None = None
_cache_patterns: VisionIntentPatterns | None = None


def get_vision_intent_patterns() -> VisionIntentPatterns:
    """Return compiled patterns; reload when the intents JSON file changes on disk."""
    global _cache_key, _cache_patterns
    path = _intent_json_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = (str(path.resolve()), mtime)
    if _cache_patterns is not None and _cache_key == key:
        return _cache_patterns
    spec = _load_spec(path)
    pats = VisionIntentPatterns(
        primary=_compile_or_fallback(spec, "primary_regex", _DEFAULT_PRIMARY),
        detail_followup=_compile_or_fallback(spec, "detail_followup_regex", _DEFAULT_DETAIL),
        refresh=_compile_or_fallback(spec, "refresh_regex", _DEFAULT_REFRESH),
        color_probe=_compile_or_fallback(spec, "color_probe_regex", _DEFAULT_COLOR_PROBE),
    )
    _cache_key = key
    _cache_patterns = pats
    logger.debug(f"[vision_intent_patterns] loaded intents from {path} (mtime={mtime})")
    return pats


def reload_vision_intent_patterns_for_tests() -> None:
    """Clear intent pattern cache (tests only)."""
    global _cache_key, _cache_patterns
    _cache_key = None
    _cache_patterns = None
