"""REST: enrollment, runtime toggle, session focus, turns."""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import File, Form, HTTPException, Request, UploadFile
from starlette.responses import FileResponse

from pipecat_bots.face_recog import person_registry
from pipecat_bots.face_recog.face_thumbnails import thumb_path_if_exists
from pipecat_bots.face_recog.config import (
    facerecog_enabled,
    facerecog_env_enabled,
    facerecog_runtime_enabled,
    get_pending_quality_settings,
    set_pending_quality_settings,
    set_facerecog_runtime,
)
from pipecat_bots.face_recog.session_state import (
    get_state,
    session_public_dict,
    set_focus,
    set_voice_face_session,
)
from pipecat_bots.face_recog.face_pipeline import on_jpeg

_LEGACY_HASH_UNKNOWN = re.compile(r"^unknown_[0-9a-f]{12}$")


def _require_facerecog() -> None:
    if not facerecog_enabled():
        raise HTTPException(status_code=404, detail="face recognition disabled")


def _validate_image_upload(raw: bytes) -> None:
    if not raw:
        raise HTTPException(status_code=400, detail="empty image")
    # Lightweight format gate without optional deps (Python 3.13 removed imghdr).
    sig = bytes(raw[:16])
    is_img = (
        sig.startswith(b"\xff\xd8\xff")  # jpeg
        or sig.startswith(b"\x89PNG\r\n\x1a\n")  # png
        or sig.startswith(b"GIF87a")
        or sig.startswith(b"GIF89a")
        or sig.startswith(b"BM")  # bmp
        or sig.startswith(b"II*\x00")  # tiff little
        or sig.startswith(b"MM\x00*")  # tiff big
        or (len(sig) >= 12 and sig[:4] == b"RIFF" and sig[8:12] == b"WEBP")
    )
    if not is_img:
        raise HTTPException(status_code=400, detail="invalid image upload (decode/signature failed)")


def _annotate_person_row(row: dict[str, Any]) -> dict[str, Any]:
    pid = str(row.get("person_id") or "")
    dn = str(row.get("display_name") or "").strip()
    dn_low = dn.lower()
    kind = "enrolled"
    if pid.startswith("stub_vs_"):
        kind = "recognized_stub" if dn.lower() not in ("", "unknown") else "stub_pending"
    elif pid.startswith("unknown_"):
        kind = "unknown_bucket" if dn_low in ("", "unknown") else "enrolled"
    elif _LEGACY_HASH_UNKNOWN.match(pid):
        kind = "legacy_unknown_hash"
    elif dn_low in ("", "unknown"):
        kind = "detected_pending"
    out = dict(row)
    out["kind"] = kind
    out["has_thumbnail"] = thumb_path_if_exists(pid) is not None
    return out


async def get_face_runtime_status() -> dict[str, Any]:
    """Always available — UI can enable facerecog without shell env."""
    return {
        "env_enabled": facerecog_env_enabled(),
        "runtime_enabled": facerecog_runtime_enabled(),
        "effective_enabled": facerecog_enabled(),
    }


async def post_face_runtime(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    en = body.get("enabled")
    if not isinstance(en, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")
    set_facerecog_runtime(en)
    return {"ok": True, **(await get_face_runtime_status())}


async def get_face_pending_quality() -> dict[str, Any]:
    return {"ok": True, **get_pending_quality_settings()}


async def post_face_pending_quality(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    try:
        c = float(body.get("pending_min_conf"))
        a = float(body.get("pending_min_bbox_area"))
    except Exception:
        raise HTTPException(status_code=400, detail="pending_min_conf and pending_min_bbox_area must be numbers")
    out = set_pending_quality_settings(
        pending_min_conf_val=c,
        pending_min_bbox_area_val=a,
    )
    return {"ok": True, **out}


async def post_voice_face_bind(request: Request) -> dict[str, Any]:
    """Declare Voice+Video+Face (or plain video) for a vision_session_id."""
    _require_facerecog()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    sid = str(body.get("vision_session_id") or "").strip()
    fv = body.get("face_voice")
    if not sid:
        raise HTTPException(status_code=400, detail="vision_session_id required")
    if not isinstance(fv, bool):
        raise HTTPException(status_code=400, detail="face_voice must be boolean")
    st = set_voice_face_session(sid, fv)
    if fv:
        st.focus_locked = True
    return {"ok": True, **session_public_dict(st)}


async def post_face_person(request: Request) -> dict[str, Any]:
    _require_facerecog()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    pid = str(body.get("person_id") or "").strip()
    name = str(body.get("display_name") or "").strip()
    if not pid or not name:
        raise HTTPException(status_code=400, detail="person_id and display_name required")
    notes = body.get("notes")
    emb = body.get("embedding")
    b = None
    if isinstance(emb, (bytes, bytearray)):
        b = bytes(emb)
    person_registry.upsert_person(pid, name, embedding=b, notes=str(notes).strip() if notes else None)

    vsid = str(body.get("vision_session_id") or "").strip()
    switch_focus = body.get("switch_focus", True)
    if isinstance(switch_focus, str):
        switch_focus = switch_focus.lower() in ("1", "true", "yes")
    if vsid and switch_focus:
        set_voice_face_session(vsid, True)
        set_focus(vsid, "person", pid, force=True)
        st = get_state(vsid)
        if st:
            st.focus_locked = True

    return {"ok": True, "person_id": pid}


async def get_face_persons(include_legacy_unknown: bool = False) -> dict[str, Any]:
    _require_facerecog()
    rows = person_registry.list_persons()
    raw_count = len(rows)
    filtered = rows
    if not include_legacy_unknown:
        filtered = [r for r in rows if not _LEGACY_HASH_UNKNOWN.match(str(r.get("person_id") or ""))]
    annotated = [_annotate_person_row(dict(r)) for r in filtered]
    return {
        "persons": annotated,
        "recognized": [
            r for r in annotated if r.get("kind") in ("enrolled", "recognized_stub")
        ],
        "stub_pending": [
            r for r in annotated if r.get("kind") in ("stub_pending", "unknown_bucket", "detected_pending")
        ],
        "legacy_unknown_filtered_count": raw_count - len(filtered),
        "total_raw_count": raw_count,
    }


async def delete_face_person(person_id: str) -> dict[str, Any]:
    _require_facerecog()
    pid = (person_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="missing person_id")
    person_registry.delete_person(pid)
    return {"ok": True, "person_id": pid}


async def get_face_person_thumbnail(person_id: str) -> FileResponse:
    _require_facerecog()
    pid = (person_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="missing person_id")
    path = thumb_path_if_exists(pid)
    if not path:
        raise HTTPException(
            status_code=404,
            detail="No face snapshot yet — use Voice+Video+Face with the camera so frames reach the server.",
        )
    return FileResponse(path, media_type="image/jpeg", filename="face.jpg")


async def get_face_person_turns(person_id: str, limit: int = 40) -> dict[str, Any]:
    _require_facerecog()
    pid = (person_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="missing person_id")
    lim = max(1, min(limit, 500))
    turns = person_registry.recent_turns_for_person(pid, limit=lim)
    return {"person_id": pid, "turns": turns}


async def get_face_session(vision_session_id: str) -> dict[str, Any]:
    _require_facerecog()
    sid = (vision_session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="missing vision_session_id")
    st = get_state(sid)
    if not st:
        return {
            "vision_session_id": sid,
            "active": False,
            "observed_person_ids": [],
            "observed_with_names": [],
            "observed_now_person_ids": [],
            "observed_now_with_names": [],
            "primary_person_id": None,
            "focus_mode": "auto",
            "focus_person_id": None,
            "voice_face_active": False,
            "focus_locked": False,
        }
    return session_public_dict(st)


async def post_face_focus(request: Request) -> dict[str, Any]:
    _require_facerecog()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    sid = str(body.get("vision_session_id") or "").strip()
    mode = str(body.get("mode") or "auto").strip().lower()
    pid = body.get("person_id")
    person_id = str(pid).strip() if pid else None
    force = bool(body.get("force"))
    if not sid:
        raise HTTPException(status_code=400, detail="vision_session_id required")
    pre = get_state(sid)
    if pre and pre.focus_locked and pre.voice_face_active and not force:
        raise HTTPException(
            status_code=423,
            detail="Focus is locked by Voice+Video+Face auto-policy — pass force=true to override.",
        )
    if mode not in ("auto", "multiface", "person"):
        raise HTTPException(status_code=400, detail="mode must be auto|multiface|person")
    if mode == "person" and not person_id:
        raise HTTPException(status_code=400, detail="person_id required when mode=person")
    st = set_focus(sid, mode, person_id if mode == "person" else None, force=force)
    if st:
        return {"ok": True, **session_public_dict(st)}
    return {"ok": True, "vision_session_id": sid, "focus_mode": mode}


async def post_face_capture(
    vision_session_id: str = Form(...),
    image: UploadFile = File(...),
) -> dict[str, Any]:
    """Single-frame capture API for face-manager manual enrollment flow."""
    _require_facerecog()
    sid = (vision_session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="vision_session_id required")
    if not image:
        raise HTTPException(status_code=400, detail="image file required")
    try:
        jpeg = await image.read()
    except Exception:
        raise HTTPException(status_code=400, detail="could not read image")
    _validate_image_upload(jpeg)
    try:
        capture_res = await on_jpeg(
            sid,
            jpeg,
            raise_on_error=True,
            allow_registry_write=True,
            allow_stub_fallback=False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"face processing failed: {e}")
    st = get_state(sid)
    observed = list(st.observed_person_ids) if st else []
    persons = await get_face_persons(include_legacy_unknown=True)
    pending = persons.get("stub_pending") or []
    recognized = persons.get("recognized") or []
    observed_set = set(observed)
    pending_ids = [str(x.get("person_id") or "") for x in pending if str(x.get("person_id") or "") in observed_set]
    recognized_ids = [str(x.get("person_id") or "") for x in recognized if str(x.get("person_id") or "") in observed_set]
    return {
        "ok": True,
        "vision_session_id": sid,
        "detected_count": len(observed),
        "detected_person_ids": observed,
        "inserted_count": int(capture_res.get("inserted_count") or 0),
        "inserted_person_ids": list(capture_res.get("inserted_person_ids") or []),
        "skipped_low_quality_count": int(capture_res.get("skipped_low_quality_count") or 0),
        "skipped_low_quality_person_ids": list(capture_res.get("skipped_low_quality_person_ids") or []),
        "pending_detected_person_ids": pending_ids,
        "recognized_detected_person_ids": recognized_ids,
        "pending_count": len(pending),
    }


async def post_face_extract_upload(
    images: list[UploadFile] = File(...),
    vision_session_id: str | None = Form(None),
) -> dict[str, Any]:
    """Extract faces from uploaded picture(s) and add unknown identities to pending."""
    _require_facerecog()
    sid = str(vision_session_id or "").strip() or f"upload_{uuid.uuid4().hex[:12]}"
    if not images:
        raise HTTPException(status_code=400, detail="at least one image is required")

    processed = 0
    detected_total = 0
    inserted_total = 0
    inserted_ids: set[str] = set()
    skipped_low_quality_total = 0
    skipped_low_quality_ids: set[str] = set()
    failed: list[dict[str, str]] = []

    for img in images:
        name = getattr(img, "filename", None) or "upload.jpg"
        try:
            raw = await img.read()
        except Exception:
            failed.append({"name": name, "error": "could not read image"})
            continue
        if not raw:
            failed.append({"name": name, "error": "empty image"})
            continue
        try:
            _validate_image_upload(raw)
        except HTTPException as e:
            failed.append({"name": name, "error": str(e.detail)})
            continue
        try:
            capture_res = await on_jpeg(
                sid,
                raw,
                raise_on_error=True,
                allow_registry_write=True,
                allow_stub_fallback=False,
            )
        except Exception as e:
            failed.append({"name": name, "error": f"face processing failed: {e}"})
            continue
        processed += 1
        detected_total += int(capture_res.get("detected_count") or 0)
        inserted_total += int(capture_res.get("inserted_count") or 0)
        skipped_low_quality_total += int(capture_res.get("skipped_low_quality_count") or 0)
        for pid in list(capture_res.get("inserted_person_ids") or []):
            s = str(pid or "").strip()
            if s:
                inserted_ids.add(s)
        for pid in list(capture_res.get("skipped_low_quality_person_ids") or []):
            s = str(pid or "").strip()
            if s:
                skipped_low_quality_ids.add(s)

    persons = await get_face_persons(include_legacy_unknown=True)
    pending_ids = [str(x.get("person_id") or "") for x in (persons.get("stub_pending") or []) if x.get("person_id")]
    return {
        "ok": True,
        "vision_session_id": sid,
        "files_total": len(images),
        "files_processed": processed,
        "files_failed": len(failed),
        "failed": failed[:100],
        "detected_count": detected_total,
        "inserted_count": inserted_total,
        "inserted_person_ids": sorted(inserted_ids),
        "skipped_low_quality_count": skipped_low_quality_total,
        "skipped_low_quality_person_ids": sorted(skipped_low_quality_ids),
        "pending_count": len(pending_ids),
    }
