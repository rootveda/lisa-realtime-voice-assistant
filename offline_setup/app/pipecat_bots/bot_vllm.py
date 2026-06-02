#!/usr/bin/env python3
#
# Pipecat bot using OpenAI-compatible LLM API with XTTS output.
#
# Environment variables:
#   NVIDIA_ASR_URL        ASR WebSocket URL (default: ws://localhost:8080)
#   NVIDIA_LLM_URL        OpenAI-compatible API URL (default: http://localhost:8000/v1)
#   NVIDIA_LLM_MODEL      Model name/path (default: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16)
#   NVIDIA_LLM_API_KEY    API key (default: not-needed)
#   XTTS_TTS_URL          XTTS server URL (default: http://127.0.0.1:80)
#   XTTS_VOICE_ID         XTTS studio speaker id (default: Andrew Chipper)
#   VOICE_COMPLETION_MAX_TOKENS_DEFAULT  Default max new tokens per assistant turn (default: 2048)
#   VOICE_COMPLETION_MAX_TOKENS_FLOOR    Raise client max_tokens if below this when client-ready
#                                         omits llm_params.max_tokens_floor (default: 1024)
#   VOICE_LOCAL_RAG                       Prepend offline RAG snippets to each voice utterance (default: on).
#   VOICE_RAG_HIT_LIMIT                   Max snippets per utterance (default 6).
#   VOICE_RAG_CONTEXT_CHARS               Max chars of snippet block (default 6000).
#
# Usage:
#   uv run pipecat_bots/bot_vllm.py
#   uv run pipecat_bots/bot_vllm.py -t daily
#   uv run pipecat_bots/bot_vllm.py -t webrtc
#

import os
import re
import sys
from pathlib import Path

# Must run before any `pipecat_bots.*` import: stack scripts invoke this file by absolute path
# without setting cwd or PYTHONPATH to offline_setup/app.
_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from dotenv import load_dotenv
from loguru import logger

from pipecat_bots.voice_llm_initial_messages import build_voice_llm_initial_messages

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
# Use our custom SentenceAggregator that flushes on LLMFullResponseEndFrame
from sentence_aggregator import SentenceAggregator
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import (
    RTVIBotLLMTextMessage,
    RTVIBotTranscriptionMessage,
    RTVIConfig,
    RTVIObserver,
    RTVIObserverParams,
    RTVIProcessor,
    RTVITextMessageData,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.utils.string import match_endofsentence

# Import our custom local services
from nvidia_stt import NVidiaWebSocketSTTService
from xtts_http_tts import XTTSHTTPTTSService
from v2v_metrics import V2VMetricsProcessor

load_dotenv(override=True)

# Voice uses one completion per assistant turn; each turn is capped at max_tokens (Ollama/local llama).
# Keep defaults generous so banter + long answers fit; floor prevents tiny caps from old UI sessions.
VOICE_COMPLETION_MAX_TOKENS_DEFAULT = int(os.getenv("VOICE_COMPLETION_MAX_TOKENS_DEFAULT", "2048"))
VOICE_COMPLETION_MAX_TOKENS_FLOOR = int(os.getenv("VOICE_COMPLETION_MAX_TOKENS_FLOOR", "1024"))

# Configuration from environment
NVIDIA_ASR_URL = os.getenv("NVIDIA_ASR_URL", "ws://localhost:8080")
NVIDIA_LLM_URL = os.getenv("NVIDIA_LLM_URL", "http://localhost:8000/v1")
NVIDIA_LLM_MODEL = os.getenv(
    "NVIDIA_LLM_MODEL",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16"
)
NVIDIA_LLM_API_KEY = os.getenv("NVIDIA_LLM_API_KEY", "not-needed")
XTTS_TTS_URL = os.getenv("XTTS_TTS_URL", "http://127.0.0.1:80").rstrip("/")
XTTS_VOICE_ID = os.getenv("XTTS_VOICE_ID", "Andrew Chipper")

# VAD configuration - used by both VAD analyzer and V2V metrics
VAD_STOP_SECS = 0.2

# Transport configurations with VAD and SmartTurn analyzer
transport_params = {
    "daily": lambda: DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
}


class ChannelTagSanitizer(FrameProcessor):
    """Strip Gemma channel/thought markup before TTS."""

    _channel_tag_re = re.compile(r"<\|?/?channel\|?>", re.IGNORECASE)
    _thought_prefix_re = re.compile(r"^\s*thought\b[\s:,\-]*", re.IGNORECASE)
    _repeat_word_re = re.compile(r"\b([A-Za-z][A-Za-z']*)\b(?:\s+\1\b)+", re.IGNORECASE)
    _repeat_concat_word_re = re.compile(r"\b([A-Za-z][A-Za-z']{1,20})\1\b", re.IGNORECASE)
    _repeat_punct_re = re.compile(r"([,?.!])\1+")

    @classmethod
    def _clean_text(cls, text: str) -> str:
        cleaned = cls._channel_tag_re.sub("", text or "")
        cleaned = cls._thought_prefix_re.sub("", cleaned)
        cleaned = re.sub(r"''+", "'", cleaned)
        for _ in range(3):
            updated = cls._repeat_concat_word_re.sub(r"\1", cleaned)
            if updated == cleaned:
                break
            cleaned = updated
        cleaned = cls._repeat_word_re.sub(lambda m: m.group(1), cleaned)
        cleaned = cls._repeat_punct_re.sub(r"\1", cleaned)
        # Keep boundary spaces between streamed chunks; only collapse excessive whitespace.
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            # Pipecat OpenAI-compatible streaming sends **deltas** per chunk, not a growing
            # cumulative string. Overlap removal against the previous delta is incorrect and can
            # drop every chunk (silent voice / no TTS).
            cleaned = self._clean_text(frame.text or "")
            if not cleaned:
                return
            frame = LLMTextFrame(text=cleaned)

        await self.push_frame(frame, direction)


class SanitizingRTVIObserver(RTVIObserver):
    """Sanitize raw bot-llm-text events before sending to RTVI clients."""

    async def on_push_frame(self, data: FramePushed):
        """Clear sentence buffer between LLM turns."""
        if isinstance(data.frame, LLMFullResponseEndFrame):
            self._bot_transcription = ""
        await super().on_push_frame(data)

    async def _handle_llm_text_frame(self, frame: LLMTextFrame):
        cleaned = ChannelTagSanitizer._clean_text(frame.text or "")
        if not cleaned:
            return

        message = RTVIBotLLMTextMessage(data=RTVITextMessageData(text=cleaned))
        await self.send_rtvi_message(message)

        # Keep bot-transcription behavior aligned with sanitized text.
        self._bot_transcription += cleaned
        if match_endofsentence(self._bot_transcription) and len(self._bot_transcription) > 0:
            await self.send_rtvi_message(
                RTVIBotTranscriptionMessage(data=RTVITextMessageData(text=self._bot_transcription))
            )
            self._bot_transcription = ""


def _coerce_client_llm_params(raw: object) -> dict:
    """Pick safe numeric sampling params from mobile-voice client-ready JSON."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if "max_tokens" in raw:
        try:
            v = int(raw["max_tokens"])
            if 1 <= v <= 45000:
                out["max_tokens"] = v
        except (TypeError, ValueError):
            pass
    if "temperature" in raw:
        try:
            v = float(raw["temperature"])
            if 0.0 <= v <= 2.0:
                out["temperature"] = v
        except (TypeError, ValueError):
            pass
    if "top_p" in raw:
        try:
            v = float(raw["top_p"])
            if 0.0 < v <= 1.0:
                out["top_p"] = v
        except (TypeError, ValueError):
            pass
    if "max_tokens_floor" in raw:
        try:
            v = int(raw["max_tokens_floor"])
            if 0 <= v <= 45000:
                out["max_tokens_floor"] = v
        except (TypeError, ValueError):
            pass
    return out


def _apply_client_llm_params(llm: OpenAILLMService, params: dict) -> None:
    """Merge client-provided params into service settings and extra_body (vLLM/Ollama)."""
    if not params:
        return
    extra = llm._settings.get("extra")
    if not isinstance(extra, dict):
        extra = {}
    else:
        extra = dict(extra)
    eb = extra.get("extra_body")
    if not isinstance(eb, dict):
        eb = {}
    else:
        eb = dict(eb)
    if "max_tokens" in params:
        llm._settings["max_tokens"] = params["max_tokens"]
        eb["max_tokens"] = params["max_tokens"]
    if "temperature" in params:
        llm._settings["temperature"] = params["temperature"]
        eb["temperature"] = params["temperature"]
    if "top_p" in params:
        llm._settings["top_p"] = params["top_p"]
        eb["top_p"] = params["top_p"]
    extra["extra_body"] = eb
    llm._settings["extra"] = extra
    logger.info(f"Applied client llm_params: {params}")


def _ensure_voice_max_tokens_floor(llm: OpenAILLMService, floor: int) -> None:
    """Raise max_tokens if below floor (local Ollama can usually afford a larger completion budget)."""
    if floor < 1:
        return
    extra = llm._settings.get("extra")
    if not isinstance(extra, dict):
        extra = {}
    else:
        extra = dict(extra)
    eb = extra.get("extra_body")
    if not isinstance(eb, dict):
        eb = {}
    else:
        eb = dict(eb)
    cur = eb.get("max_tokens")
    if cur is None:
        cur = llm._settings.get("max_tokens")
    try:
        cur_i = int(cur) if cur is not None else floor
    except (TypeError, ValueError):
        cur_i = floor
    if cur_i < floor:
        eb["max_tokens"] = floor
        llm._settings["max_tokens"] = floor
        extra["extra_body"] = eb
        llm._settings["extra"] = extra
        logger.info(f"Voice max_tokens raised from {cur_i} to floor {floor}")


class VisionTranscriptionProcessor(FrameProcessor):
    """On final TranscriptionFrame, attach Ollama caption when vision intent matches."""

    def __init__(self, vision_session_id: str | None, vision_active: bool) -> None:
        super().__init__()
        sid = (vision_session_id or "").strip()
        self._session_id = sid
        self._vision_active = bool(vision_active and sid)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return
        if not self._vision_active:
            await self.push_frame(frame, direction)
            return

        from pipecat_bots.vision_augment import augment_user_text_with_vision

        text = frame.text or ""
        merged = await augment_user_text_with_vision(
            text, self._session_id, True, utterance_source="voice"
        )
        if merged == text:
            await self.push_frame(frame, direction)
            return

        enriched = TranscriptionFrame(
            merged,
            frame.user_id,
            frame.timestamp,
            getattr(frame, "language", None),
        )
        await self.push_frame(enriched, direction)


class RagTranscriptionProcessor(FrameProcessor):
    """After vision augmentation, prepend lexical RAG snippets to each STT transcript (offline FTS)."""

    def __init__(self, enabled: bool) -> None:
        super().__init__()
        self._enabled = bool(enabled)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return
        if not self._enabled:
            await self.push_frame(frame, direction)
            return

        from pipecat_bots.local_rag import augment_voice_transcript_with_rag

        text = frame.text or ""
        merged = augment_voice_transcript_with_rag(text)
        if merged == text:
            await self.push_frame(frame, direction)
            return

        enriched = TranscriptionFrame(
            merged,
            frame.user_id,
            frame.timestamp,
            getattr(frame, "language", None),
        )
        await self.push_frame(enriched, direction)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting vLLM bot")
    logger.info(f"  ASR URL: {NVIDIA_ASR_URL}")
    logger.info(f"  LLM URL: {NVIDIA_LLM_URL}")
    logger.info(f"  LLM Model: {NVIDIA_LLM_MODEL}")
    logger.info(f"  XTTS URL: {XTTS_TTS_URL}")
    logger.info(f"  Transport: {type(transport).__name__}")
    logger.info(f"  VAD stop_secs: {VAD_STOP_SECS}s")

    # NVIDIA Parakeet ASR via WebSocket
    stt = NVidiaWebSocketSTTService(
        url=NVIDIA_ASR_URL,
        sample_rate=16000,
    )

    tts = XTTSHTTPTTSService(
        server_url=XTTS_TTS_URL,
        voice_id=XTTS_VOICE_ID,
        language="en",
    )
    logger.info(f"Using XTTS TTS (Coqui HTTP): {XTTS_TTS_URL}, voice={XTTS_VOICE_ID}")

    # vLLM via OpenAI-compatible API
    llm = OpenAILLMService(
        api_key=NVIDIA_LLM_API_KEY,
        base_url=NVIDIA_LLM_URL,
        model=NVIDIA_LLM_MODEL,
        params=OpenAILLMService.InputParams(
            extra={
                # extra_body passes vLLM-specific params in the request body
                "extra_body": {
                    # llama.cpp template: disable reasoning slot where supported (local llama-server)
                    "chat_template_kwargs": {"enable_thinking": False},
                    "temperature": 0.4,
                    "max_tokens": VOICE_COMPLETION_MAX_TOKENS_DEFAULT,
                }
            }
        )
    )
    logger.info("Using vLLM via OpenAILLMService (thinking disabled)")

    body = getattr(runner_args, "body", None) or {}
    vision_session_id: str | None = None
    vision_enable = True
    face_recognition = False
    face_profile: str | None = None
    if isinstance(body, dict):
        vs = body.get("vision_session_id")
        if isinstance(vs, str) and vs.strip():
            vision_session_id = vs.strip()
        if "vision_enable" in body and isinstance(body["vision_enable"], bool):
            vision_enable = body["vision_enable"]
        elif "vision_mode" in body and isinstance(body["vision_mode"], bool):
            vision_enable = body["vision_mode"]
        fr = body.get("face_recognition")
        if isinstance(fr, bool):
            face_recognition = fr
        fp = body.get("face_profile")
        if isinstance(fp, str) and fp.strip():
            face_profile = fp.strip()

    if isinstance(body, dict) and vision_session_id and isinstance(body.get("vision_augment_every_turn"), bool):
        from pipecat_bots.vision_client_prefs import set_vision_augment_every_turn

        set_vision_augment_every_turn(vision_session_id, body["vision_augment_every_turn"])

    client_llm = _coerce_client_llm_params(body.get("llm_params") if isinstance(body, dict) else None)
    floor_from_client = client_llm.pop("max_tokens_floor", None)
    _apply_client_llm_params(llm, client_llm)
    effective_floor = (
        VOICE_COMPLETION_MAX_TOKENS_FLOOR
        if floor_from_client is None
        else int(floor_from_client)
    )
    _ensure_voice_max_tokens_floor(llm, effective_floor)

    # Voice-to-voice response time metrics
    v2v_metrics = V2VMetricsProcessor(vad_stop_secs=VAD_STOP_SECS)

    messages = build_voice_llm_initial_messages(body if isinstance(body, dict) else None)

    # Allow ws/mobile-voice client-ready messages to override prompt behavior per session.
    use_custom_session_prompt = False
    if isinstance(body, dict):
        client_messages = body.get("client_ready_messages")
        if isinstance(client_messages, list) and client_messages:
            for msg in client_messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content")
                if role in {"system", "user", "assistant"} and isinstance(content, str) and content.strip():
                    use_custom_session_prompt = True
                    break
            if use_custom_session_prompt:
                _preview = messages[0].get("content", "")[:200] if messages else ""
                logger.info(
                    "Applied client-ready message(s); "
                    f"first role={messages[0].get('role')!r} content_preview={_preview!r}..."
                )

    from pipecat_bots.face_recog.config import facerecog_enabled as _face_env_on

    face_session_active = bool(
        _face_env_on() and face_recognition and vision_session_id and vision_enable
    )

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(context)
    sentence_aggregator = SentenceAggregator()
    sanitizer = ChannelTagSanitizer()

    # RTVI processor for client communication
    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))
    async def _sanitize_rtvi_text(text: str, _aggregation_type: str) -> str:
        return ChannelTagSanitizer._clean_text(text)
    rtvi_params = RTVIObserverParams(
        bot_llm_enabled=True,
        bot_output_transforms=[("*", _sanitize_rtvi_text)],
    )

    vision_proc = VisionTranscriptionProcessor(vision_session_id, vision_enable)

    from pipecat_bots.face_recog.voice_processors import (
        FaceAssistantTurnLogger,
        FaceTranscriptionProcessor,
    )

    if face_session_active:
        logger.info("Face recognition augmentation enabled for this voice session")

    face_user_proc = FaceTranscriptionProcessor(
        vision_session_id,
        face_session_active,
        face_profile,
    )
    face_asst_proc = FaceAssistantTurnLogger(vision_session_id, face_session_active)

    voice_rag_on = os.getenv("VOICE_LOCAL_RAG", "1").strip().lower() not in ("0", "false", "no")
    rag_proc = RagTranscriptionProcessor(voice_rag_on)
    if voice_rag_on:
        logger.info("Voice local RAG augmentation enabled (VOICE_LOCAL_RAG)")

    pipeline = Pipeline(
        [
            transport.input(),
            rtvi,
            stt,
            vision_proc,
            face_user_proc,
            rag_proc,
            context_aggregator.user(),
            llm,
            sanitizer,
            face_asst_proc,
            sentence_aggregator,
            tts,
            v2v_metrics,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    # Do not use pipeline idle timeout for websocket voice: long STT or LLM work can
    # go many seconds without BotSpeakingFrame/UserSpeakingFrame, which would cancel
    # the task and leave the session silent until reconnect.
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[SanitizingRTVIObserver(rtvi, params=rtvi_params)],
        idle_timeout_secs=None,
    )

    @rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("RTVI client ready")
        await rtvi.set_bot_ready()
        # Running LLM immediately (before any user speech) adds an assistant turn and often
        # ignores the fresh system prompt. Skip auto-run when the session supplied client-ready
        # messages (Assistant Console / mobile-voice); first reply happens after STT user text.
        if not use_custom_session_prompt:
            await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat runner."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    # Enable offline route extensions (/mobile-voice-test, /api/mobile-voice, /ws/mobile-voice).
    # This matches bot_interleaved_streaming behavior for local/mobile testing endpoints.
    import pipecat_bots.pipecat_offline_patch  # noqa: F401

    from pipecat.runner.run import main

    main()