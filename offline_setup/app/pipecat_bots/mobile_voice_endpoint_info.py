"""Payload for GET /api/mobile-voice — discovery URL, pipeline models, limits, LLM verbosity.

Imported by pipecat_offline_patch when serving /api/mobile-voice (and /api/mobile-voice/info).
Environment variables mirror defaults in bot_interleaved_streaming.py unless documented otherwise.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen
from typing import Any, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


def _pipecat_version() -> str:
    try:
        import pipecat

        return getattr(pipecat, "__version__", "unknown")
    except Exception:
        return "unknown"


def _optional_hf_model_hint() -> Optional[str]:
    """Best-effort path/label from env (set by ops scripts if desired)."""
    for key in (
        "LLAMA_MODEL",
        "MOBILE_INFO_LLM_GGUF_PATH",
        "OFFLINE_LLM_GGUF_PATH",
        "LLAMA_MODEL_PATH",
    ):
        p = _env(key)
        if p:
            return p
    return None


def _derive_llm_display_name(llama_model_path: Optional[str]) -> str:
    if not llama_model_path:
        return (
            "llama.cpp model (path not exposed via environment; set LLAMA_MODEL for explicit label)"
        )
    filename = os.path.basename(llama_model_path)
    lower_name = filename.lower()
    if "gemma-4-31b" in lower_name or "gemma4" in lower_name:
        return f"Google Gemma 4 31B (GGUF via llama.cpp, file: {filename})"
    if "nemotron-3-nano-30b" in lower_name:
        return f"NVIDIA Nemotron-3-Nano-30B-A3B (GGUF via llama.cpp, file: {filename})"
    if "qwen" in lower_name:
        return f"Qwen-family GGUF via llama.cpp (file: {filename})"
    return f"GGUF via llama.cpp (file: {filename})"


def _llama_model_alias_from_props(llama_url: str) -> Optional[str]:
    try:
        with urlopen(f"{llama_url.rstrip('/')}/props", timeout=1.5) as resp:
            if resp.status != 200:
                return None
            payload = resp.read().decode("utf-8", errors="ignore")
        data = __import__("json").loads(payload)
        alias = data.get("model_alias")
        if isinstance(alias, str) and alias:
            return alias
    except (URLError, TimeoutError, ValueError):
        return None
    except Exception:
        return None
    return None


def _computed_urls(request: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if request is None:
        return out
    try:
        host = request.headers.get("host") or "127.0.0.1"
        scheme = request.url.scheme
        if request.headers.get("x-forwarded-proto") == "https":
            scheme = "https"
        ws_scheme = "wss" if scheme == "https" else "ws"
        base = f"{scheme}://{host}".rstrip("/")
        out["request_base_url"] = base
        out["websocket_url_example"] = f"{ws_scheme}://{host}/ws/mobile-voice"
        out["browser_test_example"] = f"{base}/mobile-voice-test"
        out["api_info_example"] = f"{base}/api/mobile-voice"
        out["playground_example"] = f"{base}/client/"
        out["diagnose_example"] = f"{base}/diagnose"
    except Exception:
        pass
    return out


def build_mobile_voice_payload(request: Optional[Any] = None) -> dict[str, Any]:
    """Full JSON document for GET /api/mobile-voice."""

    # Defaults aligned with bot_interleaved_streaming.load_dotenv + constants
    asr_url = _env("NVIDIA_ASR_URL", "ws://127.0.0.1:8080")
    llama_url = _env("NVIDIA_LLAMA_CPP_URL", "http://127.0.0.1:8000")
    magpie_url = _env("NVIDIA_TTS_URL", "http://127.0.0.1:8001")
    tts_provider = (_env("TTS_PROVIDER", "xtts") or "xtts").lower()
    kokoro_url = _env("KOKORO_TTS_URL", "http://127.0.0.1:8003")
    kokoro_voice = _env("KOKORO_VOICE", "af_bella")
    xtts_url = (_env("XTTS_TTS_URL", "http://127.0.0.1:80") or "").rstrip("/")
    xtts_voice = _env("XTTS_VOICE_ID", "Andrew Chipper")
    csm_url = (_env("CSM_TTS_URL", "http://127.0.0.1:8004") or "").rstrip("/")
    csm_speaker = _env("CSM_SPEAKER_ID", "0")

    vad_stop = float(_env("VAD_STOP_SECS") or "0.2")

    gguf_hint = _optional_hf_model_hint()
    llm_alias = _llama_model_alias_from_props(llama_url)
    if _env("MOBILE_INFO_LLM_DISPLAY_NAME"):
        llm_display = _env("MOBILE_INFO_LLM_DISPLAY_NAME")
    elif llm_alias:
        llm_display = f"GGUF via llama.cpp (active alias: {llm_alias})"
    else:
        llm_display = _derive_llm_display_name(gguf_hint)

    llm_verbose = (
        "The Pipecat pipeline uses LlamaCppBufferedLLMService against the llama.cpp HTTP API "
        f"at `{llama_url}`. "
        "Generations are chunked at sentence boundaries (SentenceBuffer): the first burst uses a "
        "small token cap for low time-to-first-byte; later segments allow more tokens per chunk. "
        "The service runs in single-slot mode for stable KV-cache reuse across turns (no mid-stream cancel races). "
        "Typical decoding uses temperature 0.0 and repeat_penalty 1.0 (greedy, Nemotron-friendly). "
        "Context sizing is negotiated with the server on startup (defaults: max_context_tokens 16384, reserve 2048). "
        "User text reaches the LLM after ASR transcription on mobile voice turns; for /ws/mobile-voice the client "
        "supplies initial chat history in the JSON handshake only—subsequent user input is speech→text via STT."
    )

    stt_block: dict[str, Any] = {
        "role": "Speech-to-text (user audio → transcript)",
        "implementation_class": "NVidiaWebSocketSTTService",
        "backend_description": (
            "NVIDIA Parakeet streaming ASR exposed as a WebSocket from the Nemotron unified container "
            "(same stack as offline nemotron start). Sends interim and final transcripts as JSON frames."
        ),
        "websocket_url_env": "NVIDIA_ASR_URL",
        "websocket_url_resolved": asr_url,
        "audio_expected_from_client_hz": 16000,
        "audio_format": "16-bit signed little-endian mono PCM",
        "notes": (
            "Server expects reset messages between utterances where applicable; "
            "the Pipecat transport feeds continuous mic frames after VAD/turn detection."
        ),
    }

    tts_block: dict[str, Any] = {
        "tts_provider_env": "TTS_PROVIDER",
        "tts_provider_resolved": tts_provider,
        "notes": "Magpie uses WebSocket streaming; Kokoro/XTTS/CSM use HTTP from the bot process.",
    }

    if tts_provider == "xtts":
        tts_block["implementation_class"] = "XTTSHTTPTTSService"
        tts_block["backend_description"] = (
            "Coqui XTTS v2 streaming HTTP server (Docker image xtts-gpu or equivalent). "
            "Fetches speaker embedding from /studio_speakers; synthesis via POST /tts."
        )
        tts_block["model_name_hint"] = "xtts-v2"
        tts_block["server_url_env"] = "XTTS_TTS_URL"
        tts_block["server_url_resolved"] = xtts_url
        tts_block["voice_env"] = "XTTS_VOICE_ID"
        tts_block["voice_resolved"] = xtts_voice
        tts_block["internal_sample_rate_hz"] = 24000
        tts_block["transport_output_sample_rate_hz"] = 16000
    elif tts_provider == "kokoro":
        tts_block["implementation_class"] = "KokoroHTTPTTSService"
        tts_block["backend_description"] = "Kokoro-FastAPI HTTP TTS."
        tts_block["server_url_env"] = "KOKORO_TTS_URL"
        tts_block["server_url_resolved"] = kokoro_url
        tts_block["voice_env"] = "KOKORO_VOICE"
        tts_block["voice_resolved"] = kokoro_voice
    elif tts_provider == "csm":
        tts_block["implementation_class"] = "CsmHTTPTTSService"
        tts_block["backend_description"] = "Sesame CSM-1B HTTP TTS."
        tts_block["server_url_env"] = "CSM_TTS_URL"
        tts_block["server_url_resolved"] = csm_url
        tts_block["speaker_id_env"] = "CSM_SPEAKER_ID"
        tts_block["speaker_id_resolved"] = csm_speaker
    else:
        tts_block["implementation_class"] = "MagpieWebSocketTTSService"
        tts_block["backend_description"] = (
            "NVIDIA Magpie WebSocket TTS (unified Nemotron stack when TTS enabled there). "
            "Offline bundle often uses XTTS instead—check tts_provider_resolved."
        )
        tts_block["server_url_env"] = "NVIDIA_TTS_URL"
        tts_block["server_url_resolved"] = magpie_url
        tts_block["voice_default"] = "aria"

    llm_block: dict[str, Any] = {
        "role": "Large language model (transcript + history → assistant text)",
        "implementation_class": "LlamaCppBufferedLLMService",
        "api_style": "OpenAI-compatible HTTP completions against llama.cpp server",
        "base_url_env": "NVIDIA_LLAMA_CPP_URL",
        "base_url_resolved": llama_url,
        "display_model_name": llm_display,
        "weights_file_hint": gguf_hint,
        "verbose": llm_verbose,
        "generation_defaults": {
            "first_segment_max_tokens": 24,
            "first_segment_hard_max_tokens": 24,
            "segment_max_tokens": 32,
            "segment_hard_max_tokens": 96,
            "temperature": 0.0,
            "repeat_penalty": 1.0,
            "slot_id": 0,
            "max_context_tokens": 16384,
            "context_reserve_tokens": 2048,
        },
    }

    audio_pipeline = {
        "vad": {
            "engine": "SileroVADAnalyzer",
            "stop_secs": vad_stop,
            "env_override": "VAD_STOP_SECS",
        },
        "turn_detection": {
            "engine": "LocalSmartTurnAnalyzerV3",
            "params_family": "SmartTurnParams",
        },
        "metrics": {
            "v2v_latency": "V2VMetricsProcessor",
        },
    }

    mobile_endpoint: dict[str, Any] = {
        "title": "Mobile raw-PCM voice (client-managed LLM history)",
        "description": (
            "Connect with WebSocket /ws/mobile-voice. First frame is JSON client-ready with messages[]; "
            "then stream 16 kHz mono PCM int16 LE. Responses are synthesized speech (and RTVI control frames). "
            "Use GET /api/mobile-voice for machine-readable discovery and model metadata."
        ),
        "computed_from_request": _computed_urls(request),
        "paths": {
            "websocket": "/ws/mobile-voice",
            "browser_mic_test_page": "/mobile-voice-test",
            "connection_diagnostic": "/diagnose",
            "full_playground": "/client/",
            "alternate_websocket_voice": "/ws/voice",
            "this_discovery_document": "/api/mobile-voice",
        },
        "websocket_patterns": {
            "plaintext_example": "ws://<HOST>:<PORT>/ws/mobile-voice",
            "tls_example": "wss://<HOST>:<PORT>/ws/mobile-voice",
            "note": "Use wss when the bot sits behind HTTPS (e.g. TLS terminator on 7860 → bot 7861).",
        },
        "protocol": {
            "pcm_input": "raw_pcm_16k_mono_int16_le",
            "pcm_output_sample_rate_hz": 16000,
            "handshake": {
                "first_message": {
                    "type": "client-ready",
                    "messages": [
                        {"role": "system", "content": "(Your app supplies full prompt and history here.)"}
                    ],
                },
                "encoding": "Send the handshake as one UTF-8 JSON object in a WebSocket binary or text frame.",
            },
            "after_handshake": "Binary frames of 16-bit LE mono PCM at 16000 Hz. Optional control: UTF-8 JSON in a binary frame, e.g. "
            '{"type":"vision-prefs","vision_session_id":"<uuid>","vision_augment_every_turn":true} '
            "to change every-turn camera context vs regex-only without reconnecting (see /api/mobile-voice-vision mid_session_mobile_voice).",
            "server_managed_prompts": False,
            "first_llm_turn": "Runs after user speech is transcribed (skip_initial_llm_run on mobile path waits for speech).",
            "related_session": "/ws/voice uses server-managed prompts; /ws/mobile-voice is client-managed.",
        },
        "limits": {
            "max_handshake_messages": 128,
            "max_message_content_chars": 32000,
            "allowed_roles": ["system", "user", "assistant"],
        },
        "models_and_services": {
            "llm": llm_block,
            "stt": stt_block,
            "tts": tts_block,
        },
        "pipeline_order": [
            "transport.input (WebSocket PCM in)",
            "RTVIProcessor",
            "STT (Parakeet WS → transcript frames)",
            "LLM context aggregator (user)",
            "LlamaCppBufferedLLMService",
            "TTS (XTTS/Magpie/Kokoro/CSM)",
            "V2VMetricsProcessor",
            "transport.output (PCM out)",
            "LLM context aggregator (assistant)",
        ],
        "audio_processing": audio_pipeline,
        "runtime": {
            "python": sys.version.split()[0],
            "pipecat_version": _pipecat_version(),
            "recording_env": "ENABLE_RECORDING",
            "recording_enabled": (_env("ENABLE_RECORDING", "false") or "").lower() == "true",
            "posix_timestamp_ms": int(time.time() * 1000),
            "utc_iso": datetime.now(timezone.utc).isoformat(),
        },
        "hints_for_ui": [
            "Show models_and_services.llm.display_model_name and STT/TTS backend for a settings or about screen.",
            "Use computed_from_request.websocket_url_example when present so LAN/WSS URLs match the page origin.",
            "Surface limits.max_handshake_messages when the user imports long transcripts.",
            "Display pipeline_order in an advanced troubleshooting panel.",
        ],
    }

    payload: dict[str, Any] = {
        "schema_version": 2,
        "mobile_endpoint": mobile_endpoint,
        # Legacy flat keys — keep minimal clients working
        "websocket_url_pattern": "ws://<HOST>:<PORT>/ws/mobile-voice",
        "websocket_path": "/ws/mobile-voice",
        "http_get_note": "Discovery document; open /ws/mobile-voice with a WebSocket client.",
        "protocol": "raw_pcm_16k_mono",
        "handshake": mobile_endpoint["protocol"]["handshake"],
        "after_handshake": mobile_endpoint["protocol"]["after_handshake"],
        "server_prompts": "None. Only your supplied messages are used.",
        "first_llm_turn": mobile_endpoint["protocol"]["first_llm_turn"],
        "related": mobile_endpoint["protocol"]["related_session"],
    }

    return payload
