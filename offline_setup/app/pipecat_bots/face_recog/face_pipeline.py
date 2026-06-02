"""Optional JPEG hook: detect faces, update session state, enroll stub row once."""

from __future__ import annotations

from loguru import logger

from pipecat_bots.face_recog import person_registry
from pipecat_bots.face_recog.config import (
    facerecog_enabled,
    pending_min_bbox_area,
    pending_min_conf,
)
from pipecat_bots.face_recog.face_analyzer import analyze_jpeg
from pipecat_bots.face_recog.face_thumbnails import update_from_jpeg_frame
from pipecat_bots.face_recog.session_state import apply_auto_focus_from_detection, update_observations


def _bbox_area(b: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = b
    w = max(0.0, float(x1) - float(x0))
    h = max(0.0, float(y1) - float(y0))
    return w * h


async def on_jpeg(
    session_id: str,
    jpeg_bytes: bytes,
    *,
    raise_on_error: bool = False,
    allow_registry_write: bool = False,
    allow_stub_fallback: bool = True,
) -> dict:
    if not facerecog_enabled():
        return {"ok": False, "error": "face recognition disabled", "detected_person_ids": [], "inserted_person_ids": []}
    sid = (session_id or "").strip()
    if not sid or not jpeg_bytes:
        return {"ok": False, "error": "missing session or image", "detected_person_ids": [], "inserted_person_ids": []}
    try:
        dets = analyze_jpeg(
            jpeg_bytes,
            vision_session_id=sid,
            allow_stub_fallback=allow_stub_fallback,
        )
        observed: list[str] = []
        inserted: list[str] = []
        skipped_low_quality: list[str] = []
        min_conf = pending_min_conf()
        min_area = pending_min_bbox_area()
        for d in dets:
            pid = d.person_id_or_unknown.strip()
            if not pid:
                continue
            is_enrolled = person_registry.identity_is_enrolled(pid)
            if allow_registry_write and not is_enrolled:
                if float(d.confidence) < min_conf or _bbox_area(d.bbox) < min_area:
                    skipped_low_quality.append(pid)
                    continue
            observed.append(pid)
            if allow_registry_write:
                update_from_jpeg_frame(pid, jpeg_bytes, d.bbox)
            row = person_registry.get_person(pid)
            if row is None and allow_registry_write:
                # Any newly detected identity starts as pending until operator enrolls/renames.
                person_registry.upsert_person(pid, display_name="Unknown", embedding=None)
                inserted.append(pid)
        # Avoid multiface flapping from duplicate detections of same identity in one frame.
        if observed:
            observed = list(dict.fromkeys(observed))
        primary = observed[0] if observed else None
        update_observations(sid, observed, primary)
        apply_auto_focus_from_detection(sid)
        return {
            "ok": True,
            "detected_person_ids": observed,
            "detected_count": len(observed),
            "inserted_person_ids": inserted,
            "inserted_count": len(inserted),
            "skipped_low_quality_person_ids": skipped_low_quality,
            "skipped_low_quality_count": len(skipped_low_quality),
            "pending_quality_min_conf": min_conf,
            "pending_quality_min_bbox_area": min_area,
        }
    except Exception as e:
        logger.warning(f"[face_recog] on_jpeg failed session={sid}: {e}")
        if raise_on_error:
            raise
        return {"ok": False, "error": str(e), "detected_person_ids": [], "inserted_person_ids": []}
