"""Face detection stub; optional InsightFace / ONNX behind env (lazy later)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Iterable

from pipecat_bots.face_recog.config import facerecog_stub_simple
from pipecat_bots.face_recog import person_registry
from pipecat_bots.face_recog.face_thumbnails import thumb_path_if_exists


@dataclass
class FaceDetection:
    person_id_or_unknown: str
    bbox: tuple[float, float, float, float]
    confidence: float
    expression: str | None = None


def _stub_person_id_for_session(vision_session_id: str) -> str:
    """One stable id per vision_session_id (avoids flooding DB with per-frame unknown_*)."""
    sid = (vision_session_id or "").strip()
    if not sid:
        h = hashlib.sha256(b"nosession").hexdigest()[:12]
        return f"stub_vs_{h}"
    h = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:12]
    return f"stub_vs_{h}"


def _aux_person_id_for_session(vision_session_id: str, idx: int) -> str:
    base = (vision_session_id or "").strip() or "nosession"
    h = hashlib.sha256(f"{base}:{idx}".encode("utf-8")).hexdigest()[:12]
    return f"stub_vs_{h}"


def _unknown_bucket_person_id(vision_session_id: str, idx: int = 0) -> str:
    base = (vision_session_id or "").strip() or "nosession"
    h = hashlib.sha256(f"{base}:unknown:{idx}".encode("utf-8")).hexdigest()[:12]
    return f"unknown_{h}"


def analyze_jpeg_stub(jpeg_bytes: bytes, vision_session_id: str = "") -> list[FaceDetection]:
    """Synthetic faces: default one stable identity per session; optional second face for multiface tests."""
    pid = _stub_person_id_for_session(vision_session_id)
    out = [
        FaceDetection(
            person_id_or_unknown=pid,
            # Stub face region tuned to typical selfie framing in this stack.
            bbox=(0.30, 0.50, 0.56, 0.86),
            confidence=0.55,
            expression=None,
        )
    ]
    if os.getenv("FACERECOG_STUB_MULTI_FACE", "0").strip().lower() in ("1", "true", "yes"):
        h2 = hashlib.sha256((vision_session_id + "_b").encode()).hexdigest()[:12]
        out.append(
            FaceDetection(
                person_id_or_unknown=f"stub_vs_{h2}",
                bbox=(0.58, 0.50, 0.84, 0.86),
                confidence=0.52,
                expression=None,
            )
        )
    return out


def _detect_faces_opencv(jpeg_bytes: bytes) -> Iterable[tuple[int, int, int, int]]:
    """Yield (x, y, w, h) face rectangles from OpenCV Haar cascade."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return []
    if not jpeg_bytes:
        return []
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if im is None:
        return []
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    clf = cv2.CascadeClassifier(cascade_path)
    if clf.empty():
        return []
    faces = clf.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(36, 36),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    if faces is None or len(faces) == 0:
        return []
    return sorted(faces, key=lambda r: int(r[2] * r[3]), reverse=True)


def _opencv_to_norm_bbox(x: int, y: int, w: int, h: int, fw: int, fh: int) -> tuple[float, float, float, float]:
    x0 = max(0.0, min(1.0, x / fw))
    y0 = max(0.0, min(1.0, y / fh))
    x1 = max(0.0, min(1.0, (x + w) / fw))
    y1 = max(0.0, min(1.0, (y + h) / fh))
    return (x0, y0, x1, y1)


def _person_id_from_face_crop(im_bgr, x: int, y: int, w: int, h: int) -> str:
    """Content-derived id so the same face can map across sessions."""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    ih, iw = im_bgr.shape[:2]
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(iw, int(x + w))
    y1 = min(ih, int(y + h))
    if x1 <= x0 + 4 or y1 <= y0 + 4:
        return _stub_person_id_for_session("")
    roi = im_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return _stub_person_id_for_session("")
    # Normalize face crop to make ID robust to scale/lighting shifts.
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    norm = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    norm = cv2.GaussianBlur(norm, (3, 3), 0)
    norm = cv2.equalizeHist(norm)
    arr = np.ascontiguousarray(norm)
    hsh = hashlib.sha256(arr.tobytes()).hexdigest()[:12]
    return f"face_{hsh}"


def _face_ahash_bits(gray_img) -> int:
    import cv2  # type: ignore
    tiny = cv2.resize(gray_img, (16, 16), interpolation=cv2.INTER_AREA)
    mean = float(tiny.mean())
    bits = 0
    flat = tiny.flatten()
    for i, px in enumerate(flat):
        if float(px) >= mean:
            bits |= (1 << i)
    return bits


def _hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _enrolled_thumb_hashes():
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    out: list[tuple[str, int, object]] = []
    rows = person_registry.list_persons(limit=1000)
    for r in rows:
        pid = str(r.get("person_id") or "").strip()
        if not pid or not person_registry.identity_is_enrolled(pid):
            continue
        p = thumb_path_if_exists(pid)
        if not p:
            continue
        try:
            raw = p.read_bytes()
            arr = np.frombuffer(raw, dtype=np.uint8)
            im = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if im is None or im.size == 0:
                continue
            norm = cv2.resize(im, (48, 48), interpolation=cv2.INTER_AREA)
            norm = cv2.equalizeHist(norm)
            out.append((pid, _face_ahash_bits(im), norm))
        except Exception:
            continue
    return out


def _best_enrolled_person_match(im_bgr, x: int, y: int, w: int, h: int) -> tuple[str | None, int, float]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    ih, iw = im_bgr.shape[:2]
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(iw, int(x + w))
    y1 = min(ih, int(y + h))
    if x1 <= x0 + 4 or y1 <= y0 + 4:
        return None, 10**9, 1e9
    roi = im_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None, 10**9, 1e9
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    probe_hash = _face_ahash_bits(gray)
    probe_norm = cv2.resize(gray, (48, 48), interpolation=cv2.INTER_AREA)
    probe_norm = cv2.equalizeHist(probe_norm)
    candidates = _enrolled_thumb_hashes()
    if not candidates:
        return None, 10**9, 1e9
    best_pid = None
    best_dist = 10**9
    best_mad = 1e9
    for pid, ref_hash, ref_norm in candidates:
        d = _hamming_distance(probe_hash, ref_hash)
        mad = float(np.mean(np.abs(probe_norm.astype("float32") - ref_norm.astype("float32"))))
        if d < best_dist:
            best_dist = d
            best_pid = pid
            best_mad = mad
        elif d == best_dist and mad < best_mad:
            best_pid = pid
            best_mad = mad
    return best_pid, int(best_dist), float(best_mad)


def analyze_jpeg_real(jpeg_bytes: bytes, vision_session_id: str = "") -> list[FaceDetection]:
    """Best-effort real face detection; stable IDs by session + index."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return []
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if im is None:
        return []
    fh, fw = im.shape[:2]
    if fw < 8 or fh < 8:
        return []
    rects = list(_detect_faces_opencv(jpeg_bytes))
    if not rects:
        return []
    max_faces = int(os.getenv("FACERECOG_MAX_FACES", "2") or "2")
    max_faces = max(1, min(8, max_faces))
    rects = rects[:max_faces]
    out: list[FaceDetection] = []
    used_enrolled_ids: set[str] = set()
    for i, (x, y, w, h) in enumerate(rects):
        # Prefer enrolled identity, but do not assign same enrolled person to multiple faces in one frame.
        match_pid, match_dist, match_mad = _best_enrolled_person_match(im, int(x), int(y), int(w), int(h))
        pid = None
        if (
            match_pid
            and match_pid not in used_enrolled_ids
            and (match_dist <= 56 or match_mad <= 20.0)
        ):
            pid = match_pid
            used_enrolled_ids.add(match_pid)
        if not pid:
            pid = _unknown_bucket_person_id(vision_session_id, i)
        if not pid:
            pid = _stub_person_id_for_session(vision_session_id) if i == 0 else _aux_person_id_for_session(vision_session_id, i)
        out.append(
            FaceDetection(
                person_id_or_unknown=pid,
                bbox=_opencv_to_norm_bbox(int(x), int(y), int(w), int(h), fw, fh),
                confidence=0.80,
                expression=None,
            )
        )
    return out


def analyze_jpeg(
    jpeg_bytes: bytes,
    vision_session_id: str = "",
    *,
    allow_stub_fallback: bool = True,
) -> list[FaceDetection]:
    # Explicit override: force synthetic boxes for deterministic tests.
    if allow_stub_fallback and os.getenv("FACERECOG_FORCE_STUB", "0").strip().lower() in ("1", "true", "yes"):
        return analyze_jpeg_stub(jpeg_bytes, vision_session_id)
    # Prefer real detection whenever available, even if legacy FACERECOG_STUB_SIMPLE is set.
    dets = analyze_jpeg_real(jpeg_bytes, vision_session_id)
    if dets:
        return dets
    # Legacy compatibility: if real detection unavailable, keep previous stub behavior.
    if not allow_stub_fallback:
        return []
    if facerecog_stub_simple():
        return analyze_jpeg_stub(jpeg_bytes, vision_session_id)
    return analyze_jpeg_stub(jpeg_bytes, vision_session_id)
