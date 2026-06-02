"""Raw PCM WebSocket serializer - bypasses WebRTC/ICE entirely.

Protocol:
- Client -> Server: JSON {"type": "client-ready"} first, then binary PCM (16-bit, 16kHz, mono)
- Optional mid-session: UTF-8 JSON {"type": "vision-prefs", "vision_session_id", "vision_augment_every_turn"}
  (same binary frame style as client-ready) updates every-turn vs regex-only without reconnect.
- Server -> Client: binary PCM (16-bit, 16kHz, mono) for audio; JSON for control (bot-ready, etc.)
"""

import json

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType


def _apply_vision_prefs_message(msg: dict) -> None:
    from pipecat_bots.vision_client_prefs import set_vision_augment_every_turn

    vsid = msg.get("vision_session_id")
    if not isinstance(vsid, str) or not vsid.strip():
        return
    v = msg.get("vision_augment_every_turn")
    if not isinstance(v, bool):
        return
    sid = vsid.strip()
    set_vision_augment_every_turn(sid, v)
    logger.info(f"[raw_pcm] vision-prefs sid={sid[:8]}… vision_augment_every_turn={v}")


class RawPCMFrameSerializer(FrameSerializer):
    """Serializer for raw PCM over WebSocket - no WebRTC/ICE needed."""

    SAMPLE_RATE = 16000

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.BINARY

    async def setup(self, frame):
        pass

    async def deserialize(self, data: str | bytes) -> Frame | None:
        def _parse_json_control(msg: dict) -> Frame | None:
            t = msg.get("type")
            if t == "client-ready":
                return InputTransportMessageFrame(message=msg)
            if t == "vision-prefs":
                _apply_vision_prefs_message(msg)
                return None
            if t is not None:
                logger.warning(f"[raw_pcm] unknown JSON control type={t!r} (dropped)")
            return None

        if isinstance(data, bytes):
            if len(data) == 0:
                return None
            if not data.startswith(b"{"):
                return InputAudioRawFrame(audio=data, sample_rate=self.SAMPLE_RATE, num_channels=1)
            try:
                msg = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return InputAudioRawFrame(audio=data, sample_rate=self.SAMPLE_RATE, num_channels=1)
            return _parse_json_control(msg) or None

        if isinstance(data, str):
            s = data.strip()
            if not s.startswith("{"):
                return None
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                return None
            return _parse_json_control(msg) or None
        return None

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message).encode("utf-8") if frame.message else None
        return None
