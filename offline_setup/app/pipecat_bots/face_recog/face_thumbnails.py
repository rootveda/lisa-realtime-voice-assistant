"""Save last face crop per person_id for Face manager preview (optional Pillow)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from loguru import logger

from pipecat_bots.face_recog.config import face_data_dir

_WARNED_NO_PIL = False
_MAX_EDGE = 160
_JPEG_QUALITY = 82
_FACE_PAD = 0.04


def _thumb_file(person_id: str) -> Path:
    h = hashlib.sha256((person_id or "").encode("utf-8")).hexdigest()
    base = Path(face_data_dir()) / "face_thumbnails"
    return base / f"thumb_{h}.jpg"


def thumb_path_if_exists(person_id: str) -> Path | None:
    p = _thumb_file(person_id)
    return p if p.is_file() else None


def remove_for_person(person_id: str) -> None:
    p = _thumb_file((person_id or "").strip())
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def update_from_jpeg_frame(
    person_id: str,
    jpeg_bytes: bytes,
    bbox_norm: tuple[float, float, float, float],
) -> bool:
    """Crop face from full frame and save JPEG. bbox is x0,y0,x1,y1 in 0..1. Returns True if saved."""
    global _WARNED_NO_PIL
    pid = (person_id or "").strip()
    if not pid or not jpeg_bytes:
        return False
    try:
        from PIL import Image
    except Exception:
        if not _WARNED_NO_PIL:
            logger.info(
                "[face_thumbnails] Pillow not installed — face previews disabled. "
                "Install with: pip install Pillow"
            )
            _WARNED_NO_PIL = True
        return False

    x0n, y0n, x1n, y1n = bbox_norm
    try:
        im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    except Exception as e:
        logger.debug(f"[face_thumbnails] decode jpeg failed: {e}")
        return False
    w, h = im.size
    if w < 8 or h < 8:
        return False
    px0 = max(0, int(x0n * w))
    py0 = max(0, int(y0n * h))
    px1 = min(w, int(x1n * w))
    py1 = min(h, int(y1n * h))
    if px1 <= px0 + 2 or py1 <= py0 + 2:
        return False
    bw = px1 - px0
    bh = py1 - py0
    padx = max(1, int(bw * _FACE_PAD))
    pady = max(1, int(bh * _FACE_PAD))
    sx0 = max(0, px0 - padx)
    sy0 = max(0, py0 - pady)
    sx1 = min(w, px1 + padx)
    sy1 = min(h, py1 + pady)
    if sx1 <= sx0 + 4 or sy1 <= sy0 + 4:
        return False
    crop = im.crop((sx0, sy0, sx1, sy1))
    crop.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.Resampling.LANCZOS)
    out = _thumb_file(pid)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        crop.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return True
    except Exception as e:
        logger.warning(f"[face_thumbnails] save failed: {e}")
        return False
