#!/usr/bin/env python3
"""Minimal CSM-1B TTS server for the voice agent.

Sesame CSM (Conversational Speech Model): https://huggingface.co/sesame/csm-1b
Requires: transformers >= 4.52.1, torch, soundfile. Model is gated (accept terms on HF).

Stability: Synthesis runs in a dedicated thread (no event-loop blocking). Only one TTS
at a time (semaphore) to avoid GPU OOM. CUDA cache is cleared every N requests
(CSM_EMPTY_CACHE_INTERVAL=5) to reduce latency when GPU is full.

Usage:
  python scripts/csm_tts_server.py
  CSM_HOST=0.0.0.0 CSM_PORT=8004 python scripts/csm_tts_server.py

API:
  POST /tts  Body: {"text": "Hello world.", "speaker_id": 0}  -> WAV bytes (24kHz)
  GET /health  -> 200 when model loaded

Generation:
  Single voice: CSM_USE_REFERENCE_VOICE=1 (default) uses one fixed seed phrase as prior audio so every chunk matches that voice.
  When reference is used, do_sample/depth_decoder_do_sample are forced False. Set CSM_USE_REFERENCE_VOICE=0 to disable.
  CSM_REFERENCE_SEED_TEXT (default "Hello. This is my voice.") can be lengthened for stronger voice lock.
"""

import asyncio
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

# Only one TTS at a time to avoid GPU OOM and event-loop blocking
_synthesize_semaphore = asyncio.Semaphore(1)
# Dedicated thread for synthesis (avoids default executor contention; max_workers=1 = one synthesis at a time)
_synthesis_executor = None
# Call empty_cache every N requests to reduce latency (every request was causing slow next alloc on full GPU)
_empty_cache_interval = int(os.getenv("CSM_EMPTY_CACHE_INTERVAL", "5"))
_request_count = 0

# Optional: allow running without uvicorn for quick test
try:
    import uvicorn
except ImportError:
    uvicorn = None

HOST = os.getenv("CSM_HOST", "0.0.0.0")
PORT = int(os.getenv("CSM_PORT", "8004"))
MODEL_ID = os.getenv("CSM_MODEL_ID", "sesame/csm-1b")
# Force GPU: set CSM_DEVICE=cuda to require CUDA (fail if unavailable); unset = auto (cuda if available)
CSM_DEVICE_ENV = os.getenv("CSM_DEVICE", "").strip().lower()
# Single voice (deterministic): do_sample=False for consistent narration per chunk. Set CSM_DO_SAMPLE=1 for conversational variation.
CSM_DO_SAMPLE = os.getenv("CSM_DO_SAMPLE", "0").strip().lower() in ("1", "true", "yes")
# Single voice (reference audio): use one fixed seed phrase as prior audio so every chunk matches that voice. Set CSM_USE_REFERENCE_VOICE=0 to disable.
CSM_USE_REFERENCE_VOICE = os.getenv("CSM_USE_REFERENCE_VOICE", "1").strip().lower() in ("1", "true", "yes")
# Seed phrase for reference voice (short, neutral). Must be same speaker_id as requests.
CSM_REFERENCE_SEED_TEXT = os.getenv("CSM_REFERENCE_SEED_TEXT", "Hello. This is my voice.")
CSM_REFERENCE_SPEAKER_ID = int(os.getenv("CSM_REFERENCE_SPEAKER_ID", "0"))

# Lazy load model to avoid import at module level (transformers/torch heavy)
_model = None
_processor = None
_device = None
# Cached reference audio (numpy array, 24kHz) for single-voice consistency across chunks
_reference_audio_array = None
_reference_sample_rate = 24000


def _load_model():
    global _model, _processor, _device
    if _model is not None:
        return
    try:
        import torch
        from transformers import CsmForConditionalGeneration, CsmProcessor
    except ImportError as e:
        raise RuntimeError(
            "CSM TTS requires transformers>=4.52.1 and torch. "
            "Install with: pip install transformers torch"
        ) from e
    if CSM_DEVICE_ENV == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CSM_DEVICE=cuda but CUDA is not available. "
                "Check NVIDIA driver and PyTorch CUDA build."
            )
        _device = "cuda"
    else:
        _device = "cuda" if torch.cuda.is_available() else "cpu"
    _processor = CsmProcessor.from_pretrained(MODEL_ID)
    _model = CsmForConditionalGeneration.from_pretrained(MODEL_ID)
    _model = _model.to(_device)
    _model.eval()
    print(f"CSM TTS: loaded {MODEL_ID} on {_device}")


def _ensure_reference_audio():
    """Generate and cache reference audio (seed phrase) so all chunks use the same voice."""
    global _reference_audio_array, _reference_sample_rate
    if _reference_audio_array is not None:
        return
    _load_model()
    import torch
    device = next(_model.parameters()).device
    prompt = f"[{CSM_REFERENCE_SPEAKER_ID}]{CSM_REFERENCE_SEED_TEXT.strip()}"
    inputs = _processor(prompt, add_special_tokens=True, return_tensors="pt")
    inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}
    gen_kwargs = {"output_audio": True, "do_sample": False, "depth_decoder_do_sample": False}
    with torch.no_grad():
        audio = _model.generate(**inputs, **gen_kwargs)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        _processor.save_audio(audio, path)
        try:
            import numpy as np
            import soundfile as sf
            arr, _reference_sample_rate = sf.read(path, dtype="float32")
            _reference_audio_array = np.ascontiguousarray(arr if arr.ndim == 1 else arr.mean(axis=1), dtype=np.float32)
        except ImportError:
            import numpy as np
            import wave
            with wave.open(path, "rb") as wf:
                nch = wf.getnchannels()
                nframes = wf.getnframes()
                _reference_sample_rate = wf.getframerate()
                raw = wf.readframes(nframes)
                _reference_audio_array = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if nch == 2:
                    _reference_audio_array = np.ascontiguousarray(_reference_audio_array.reshape(-1, 2).mean(axis=1), dtype=np.float32)
        print(f"CSM TTS: reference voice cached (seed '{CSM_REFERENCE_SEED_TEXT}', {len(_reference_audio_array)} samples @ {_reference_sample_rate}Hz)")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _synthesize_sync(text: str, speaker_id: int = 0) -> bytes:
    """Generate WAV bytes from text (runs in thread). Uses reference audio when enabled for single-voice consistency."""
    _load_model()
    if CSM_USE_REFERENCE_VOICE and _reference_audio_array is None:
        _ensure_reference_audio()
    import torch
    device = next(_model.parameters()).device
    use_reference = CSM_USE_REFERENCE_VOICE and _reference_audio_array is not None and speaker_id == CSM_REFERENCE_SPEAKER_ID
    # When using reference voice, always use deterministic generation so output matches the reference voice.
    gen_kwargs = {
        "output_audio": True,
        "do_sample": False if use_reference else CSM_DO_SAMPLE,
        "depth_decoder_do_sample": False if use_reference else CSM_DO_SAMPLE,
    }

    if use_reference:
        # Single voice: use conversation format with cached reference audio so this chunk matches the same voice
        conversation = [
            {
                "role": str(speaker_id),
                "content": [
                    {"type": "text", "text": CSM_REFERENCE_SEED_TEXT.strip()},
                    {"type": "audio", "path": _reference_audio_array},
                ],
            },
            {
                "role": str(speaker_id),
                "content": [{"type": "text", "text": text.strip()}],
            },
        ]
        inputs = _processor.apply_chat_template(
            conversation, tokenize=True, return_dict=True
        )
        inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}
    else:
        # No reference or different speaker: plain prompt
        prompt = f"[{speaker_id}]{text.strip()}"
        inputs = _processor(prompt, add_special_tokens=True, return_tensors="pt")
        inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}

    with torch.no_grad():
        audio = _model.generate(**inputs, **gen_kwargs)
    # save_audio typically wants a path; use temp file then read
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        _processor.save_audio(audio, path)
        with open(path, "rb") as f:
            out = f.read()
        return out
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        # Free GPU cache periodically (not every request) to avoid 45s+ latency on next alloc when GPU is full
        global _request_count
        _request_count += 1
        if device.type == "cuda" and _request_count % _empty_cache_interval == 0:
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load model on startup (optional; can lazy-load on first /tts)
    try:
        _load_model()
    except Exception as e:
        print(f"CSM pre-load skipped: {e}")
    yield
    # shutdown
    pass


app = FastAPI(title="CSM TTS", lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    speaker_id: int = 0


@app.get("/health")
async def health():
    try:
        _load_model()
        device = str(next(_model.parameters()).device)
        return {"status": "ok", "model": MODEL_ID, "device": device}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


def _get_synthesis_executor():
    global _synthesis_executor
    if _synthesis_executor is None:
        import concurrent.futures
        _synthesis_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="csm_tts")
    return _synthesis_executor


@app.post("/tts")
async def tts(request: TTSRequest):
    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    async with _synthesize_semaphore:
        try:
            import time
            t0 = time.perf_counter()
            loop = asyncio.get_running_loop()
            wav_bytes = await loop.run_in_executor(
                _get_synthesis_executor(), _synthesize_sync, request.text, request.speaker_id
            )
            elapsed = time.perf_counter() - t0
            print(f"CSM TTS: synthesized {len(request.text)} chars in {elapsed:.2f}s -> {len(wav_bytes)} bytes WAV")
            return Response(content=wav_bytes, media_type="audio/wav")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


def main():
    if uvicorn is None:
        raise RuntimeError("Install uvicorn to run the server: pip install uvicorn")
    print(f"CSM TTS server: http://{HOST}:{PORT}")
    print("  POST /tts  Body: {\"text\": \"Hello.\", \"speaker_id\": 0}")
    print("  GET /health")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
