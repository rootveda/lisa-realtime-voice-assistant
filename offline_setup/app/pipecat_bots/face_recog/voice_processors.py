"""Pipecat processors: inject face context on user STT; log turns for per-person memory."""

from __future__ import annotations

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipecat_bots.face_recog import conversation_store
from pipecat_bots.face_recog.config import facerecog_enabled
from pipecat_bots.face_recog.context_injector import build_face_context_prefix
from pipecat_bots.face_recog.instructions_loader import load_instructions
from pipecat_bots.face_recog.session_state import get_state, user_turn_target_person_ids


class FaceTranscriptionProcessor(FrameProcessor):
    """After vision/RAG chain slot: prepend face context to final user transcripts."""

    def __init__(
        self,
        vision_session_id: str | None,
        face_client_on: bool,
        active_speaker_override: str | None = None,
    ) -> None:
        super().__init__()
        self._sid = (vision_session_id or "").strip()
        self._face_client_on = bool(face_client_on and self._sid)
        self._speaker_override = (active_speaker_override or "").strip() or None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return
        if not facerecog_enabled() or not self._face_client_on:
            await self.push_frame(frame, direction)
            return

        prefix = build_face_context_prefix(self._sid, primary_override=self._speaker_override)
        text = frame.text or ""

        if text.strip():
            for pid in user_turn_target_person_ids(self._sid):
                if pid.strip():
                    conversation_store.append_turn(
                        pid.strip(),
                        "user",
                        text.strip(),
                        vision_session_id=self._sid,
                    )

        if prefix:
            merged = f"[Face context]\n{prefix}\n\n{text}".strip()
            enriched = TranscriptionFrame(
                merged,
                frame.user_id,
                frame.timestamp,
                getattr(frame, "language", None),
            )
            await self.push_frame(enriched, direction)
            return

        await self.push_frame(frame, direction)


class FaceAssistantTurnLogger(FrameProcessor):
    """Accumulate sanitized LLM tokens; on end frame, broadcast assistant text to observed persons."""

    def __init__(self, vision_session_id: str | None, face_client_on: bool) -> None:
        super().__init__()
        self._sid = (vision_session_id or "").strip()
        self._face_client_on = bool(face_client_on and self._sid)
        self._buf: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            facerecog_enabled()
            and self._face_client_on
            and isinstance(frame, LLMTextFrame)
        ):
            chunk = frame.text or ""
            if chunk:
                self._buf.append(chunk)

        if isinstance(frame, LLMFullResponseEndFrame):
            if (
                facerecog_enabled()
                and self._face_client_on
                and self._buf
            ):
                instr = load_instructions()
                if instr.get("broadcast_assistant_turns", True):
                    st = get_state(self._sid)
                    ids = list(st.observed_person_ids) if st else []
                    text = "".join(self._buf).strip()
                    if text and ids:
                        conversation_store.broadcast_assistant_turn(ids, text, self._sid)
            self._buf.clear()

        await self.push_frame(frame, direction)
