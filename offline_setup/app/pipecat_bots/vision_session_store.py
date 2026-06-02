"""In-memory latest JPEG per vision session (process-local)."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class VisionCaptionOutcome:
    """Result of resolving a frame to a caption for LLM augmentation."""

    caption: str
    frame_id: int | None = None


@dataclass
class _FrameRecord:
    jpeg: bytes
    frame_id: int
    received_at: float


class VisionSessionStore:
    """Stores the most recent JPEG per session_id with TTL."""

    def __init__(self, ttl_sec: float | None = None) -> None:
        # Default 5s: fresh-enough frames without dropping valid JPEGs on voice/STT delay or slight stalls (override VISION_FRAME_TTL_SEC).
        raw = os.getenv("VISION_FRAME_TTL_SEC", str(ttl_sec if ttl_sec is not None else 5.0))
        self._ttl = max(0.25, min(120.0, float(raw)))
        self._data: dict[str, _FrameRecord] = {}
        # Reuse Ollama caption when the same frame_id is still current (session-level “every turn” mode).
        self._caption_cache: dict[str, tuple[int, str]] = {}
        self._lock = asyncio.Lock()
        # Drop session entries whose last frame is older than ``_session_ttl`` seconds.
        # Larger than ``_ttl`` so a brief stall doesn't lose the cached caption, but small
        # enough that disconnected sessions are reclaimed quickly.
        session_raw = os.getenv("VISION_SESSION_TTL_SEC", str(max(60.0, self._ttl * 12)))
        self._session_ttl = max(self._ttl * 2, min(3600.0, float(session_raw)))
        self._last_evict = 0.0

    def _evict_stale_nolock(self, now: float) -> int:
        """Drop dict entries that have not received a frame within ``_session_ttl``."""
        if not self._data:
            return 0
        cutoff = now - self._session_ttl
        stale = [sid for sid, rec in self._data.items() if rec.received_at < cutoff]
        for sid in stale:
            self._data.pop(sid, None)
            self._caption_cache.pop(sid, None)
        if stale:
            logger.debug("[vision_session_store] evicted {} stale session(s)", len(stale))
        return len(stale)

    async def set_frame(self, session_id: str, jpeg: bytes, frame_id: int) -> None:
        if not session_id:
            return
        async with self._lock:
            now = time.monotonic()
            # Sweep at most every ~30s to keep the work amortized.
            if now - self._last_evict > 30.0:
                self._evict_stale_nolock(now)
                self._last_evict = now
            self._data[session_id] = _FrameRecord(
                jpeg=jpeg,
                frame_id=frame_id,
                received_at=now,
            )

    async def clear_session(self, session_id: str) -> None:
        """Drop buffered frames for this session (e.g. vision WebSocket closed / camera off)."""
        if not session_id:
            return
        async with self._lock:
            self._data.pop(session_id, None)
            self._caption_cache.pop(session_id, None)

    async def get_latest(self, session_id: str) -> bytes | None:
        if not session_id:
            return None
        async with self._lock:
            rec = self._data.get(session_id)
            if rec is None:
                return None
            if time.monotonic() - rec.received_at > self._ttl:
                return None
            return rec.jpeg

    async def get_latest_record(self, session_id: str) -> _FrameRecord | None:
        """Latest non-expired frame with monotonic receive time (for settle polling)."""
        if not session_id:
            return None
        async with self._lock:
            rec = self._data.get(session_id)
            if rec is None:
                return None
            if time.monotonic() - rec.received_at > self._ttl:
                return None
            return rec

    async def wait_caption_for_augment(
        self,
        session_id: str,
        timeout: float | None = None,
        *,
        utterance_source: str = "text",
        require_fresh_frame: bool = False,
        recheck: bool = False,
    ) -> VisionCaptionOutcome:
        """Resolve latest JPEG → caption with settle polling (voice) and retries on thin output.

        Voice STT usually finalizes just *before* the next browser JPEG tick (~500ms). Without a
        short settle window we often caption a frame from mid-utterance; text chat feels fresher
        because the user hits Send slightly later.

        When ``require_fresh_frame`` is True (user asked to look again), wait until the store sees a
        **new** ``frame_id`` from the client so we do not re-caption the identical JPEG.
        """
        if not session_id:
            return VisionCaptionOutcome("[Camera: no recent frame]", None)
        cap_timeout = float(timeout if timeout is not None else os.getenv("VISION_CAPTION_WAIT_SEC", "14"))
        from pipecat_bots.vision_caption import caption_jpeg, is_substantive_caption

        baseline = await self.get_latest_record(session_id)
        if require_fresh_frame and baseline is not None:
            fid0 = baseline.frame_id
            fresh_deadline = time.monotonic() + float(os.getenv("VISION_FRESH_FRAME_WAIT_SEC", "1.25"))
            got_new = False
            while time.monotonic() < fresh_deadline:
                rec = await self.get_latest_record(session_id)
                if rec is not None and rec.frame_id != fid0:
                    got_new = True
                    logger.info(
                        f"[vision_session_store] fresh frame sid={session_id[:8]}… "
                        f"frame_id {fid0} → {rec.frame_id}"
                    )
                    break
                await asyncio.sleep(0.05)
            if not got_new:
                logger.warning(
                    f"[vision_session_store] no newer frame_id within wait (sid={session_id[:8]}…); "
                    "captioning latest JPEG anyway"
                )

        if utterance_source == "voice":
            settle = float(os.getenv("VISION_VOICE_CAPTION_SETTLE_SEC", "0.42"))
        else:
            settle = float(os.getenv("VISION_TEXT_CAPTION_SETTLE_SEC", "0.12"))
        settle = max(0.0, min(settle, 1.5))

        t_settle_end = time.monotonic() + settle
        best_jpeg: bytes | None = None
        best_frame_id: int | None = None
        best_at = -1.0
        while time.monotonic() < t_settle_end:
            rec = await self.get_latest_record(session_id)
            if rec is not None and rec.received_at > best_at:
                best_at = rec.received_at
                best_jpeg = rec.jpeg
                best_frame_id = rec.frame_id
            await asyncio.sleep(0.04)

        deadline = time.monotonic() + cap_timeout
        jpeg = best_jpeg or await self.get_latest(session_id)
        if not jpeg:
            return VisionCaptionOutcome("[Camera: no recent frame]", None)

        caption_frame_id = best_frame_id
        if caption_frame_id is None:
            rec0 = await self.get_latest_record(session_id)
            if rec0 is not None:
                caption_frame_id = rec0.frame_id

        async with self._lock:
            cached = self._caption_cache.get(session_id)
        if (
            cached is not None
            and caption_frame_id is not None
            and cached[0] == caption_frame_id
            and not require_fresh_frame
            and not recheck
            and cached[1] not in ("[Camera: no recent frame]", "[Camera: caption unavailable]")
        ):
            logger.debug(
                f"[vision_session_store] caption cache hit sid={session_id[:8]}… frame_id={caption_frame_id}"
            )
            return VisionCaptionOutcome(cached[1], caption_frame_id)

        last_cap = ""
        for attempt in range(3):
            if time.monotonic() > deadline:
                break
            try:
                cap = await caption_jpeg(jpeg, recheck=recheck)
                last_cap = cap
                if is_substantive_caption(cap):
                    async with self._lock:
                        self._caption_cache[session_id] = (caption_frame_id, cap)
                    return VisionCaptionOutcome(cap, caption_frame_id)
                logger.warning(f"[vision_session_store] thin caption (attempt {attempt + 1}), retrying")
            except Exception as e:
                logger.warning(f"[vision_session_store] caption attempt {attempt + 1} failed: {e}")
                last_cap = ""
            await asyncio.sleep(0.35)
            fresh = await self.get_latest(session_id)
            if fresh:
                jpeg = fresh
                rec_f = await self.get_latest_record(session_id)
                if rec_f is not None:
                    caption_frame_id = rec_f.frame_id

        if last_cap and is_substantive_caption(last_cap):
            async with self._lock:
                self._caption_cache[session_id] = (caption_frame_id, last_cap)
            return VisionCaptionOutcome(last_cap, caption_frame_id)
        return VisionCaptionOutcome("[Camera: caption unavailable]", caption_frame_id)


_store: VisionSessionStore | None = None


def get_vision_store() -> VisionSessionStore:
    global _store
    if _store is None:
        _store = VisionSessionStore()
    return _store
