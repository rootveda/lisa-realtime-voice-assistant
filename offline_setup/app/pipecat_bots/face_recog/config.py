"""Face recognition feature flags — default off; optional UI/runtime toggle."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pipecat_bots.repo_root import resolve_repo_root


def facerecog_env_enabled() -> bool:
    return os.getenv("FACERECOG_ENABLED", "0").strip().lower() in ("1", "true", "yes")


_runtime_override: bool | None = None
_pending_min_conf_override: float | None = None
_pending_min_bbox_area_override: float | None = None


def _runtime_json_path() -> Path:
    return Path(face_data_dir()) / "facerecog_runtime.json"


def _clamp01(v: float, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return default


def _load_runtime_blob() -> dict:
    p = _runtime_json_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_runtime_blob(blob: dict) -> None:
    p = _runtime_json_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(blob, indent=2), encoding="utf-8")


def facerecog_runtime_enabled() -> bool:
    """Persisted toggle (set from Assistant Voice+Video+Face or Face manager)."""
    global _runtime_override
    if _runtime_override is None:
        _runtime_override = bool(_load_runtime_blob().get("enabled"))
    return bool(_runtime_override)


def set_facerecog_runtime(enabled: bool) -> None:
    """Persist runtime enable so vision hook + voice facerecog work without shell env."""
    global _runtime_override
    _runtime_override = bool(enabled)
    blob = _load_runtime_blob()
    blob["enabled"] = _runtime_override
    _save_runtime_blob(blob)


def pending_min_conf() -> float:
    """Confidence threshold for adding unknown detections to pending registry."""
    global _pending_min_conf_override
    if _pending_min_conf_override is not None:
        return _pending_min_conf_override
    raw_env = os.getenv("FACERECOG_PENDING_MIN_CONF", "").strip()
    if raw_env:
        try:
            return _clamp01(float(raw_env), 0.62)
        except ValueError:
            pass
    blob = _load_runtime_blob()
    if "pending_min_conf" in blob:
        return _clamp01(blob.get("pending_min_conf"), 0.62)
    return 0.62


def pending_min_bbox_area() -> float:
    """Normalized bbox area threshold for adding unknown detections to pending registry."""
    global _pending_min_bbox_area_override
    if _pending_min_bbox_area_override is not None:
        return _pending_min_bbox_area_override
    raw_env = os.getenv("FACERECOG_PENDING_MIN_BBOX_AREA", "").strip()
    if raw_env:
        try:
            return _clamp01(float(raw_env), 0.035)
        except ValueError:
            pass
    blob = _load_runtime_blob()
    if "pending_min_bbox_area" in blob:
        return _clamp01(blob.get("pending_min_bbox_area"), 0.035)
    return 0.035


def get_pending_quality_settings() -> dict[str, float]:
    return {
        "pending_min_conf": pending_min_conf(),
        "pending_min_bbox_area": pending_min_bbox_area(),
    }


def set_pending_quality_settings(*, pending_min_conf_val: float, pending_min_bbox_area_val: float) -> dict[str, float]:
    global _pending_min_conf_override, _pending_min_bbox_area_override
    _pending_min_conf_override = _clamp01(pending_min_conf_val, 0.62)
    _pending_min_bbox_area_override = _clamp01(pending_min_bbox_area_val, 0.035)
    blob = _load_runtime_blob()
    blob["pending_min_conf"] = _pending_min_conf_override
    blob["pending_min_bbox_area"] = _pending_min_bbox_area_override
    _save_runtime_blob(blob)
    return get_pending_quality_settings()


def facerecog_enabled() -> bool:
    """True if env OR persisted runtime says on."""
    return facerecog_env_enabled() or facerecog_runtime_enabled()


def facerecog_stub_simple() -> bool:
    return os.getenv("FACERECOG_STUB_SIMPLE", "1").strip().lower() in ("1", "true", "yes")


def face_emotion_enabled() -> bool:
    return os.getenv("FACE_EMOTION_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def face_data_dir() -> str:
    raw = os.getenv("FACERECOG_DATA_DIR", "").strip()
    if raw:
        return raw
    base = resolve_repo_root() / "offline_setup" / "app" / "rag_data"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def registry_db_path() -> str:
    return os.path.join(face_data_dir(), "face_registry.sqlite")
