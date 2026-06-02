"""HTTP client for Coqui XTTS v2 streaming server.

Connects to a local XTTS server (Docker or scripts) for speech synthesis.
Uses /studio_speakers and POST /tts. Pushes ChunkedLLMContinueGenerationFrame
so the buffered LLM receives the continue signal (Pipecat's XTTSService does not).

Usage:
    tts = XTTSHTTPTTSService(server_url="http://localhost:80", voice_id="Andrew Chipper")
"""

import asyncio
import base64
import re
from typing import AsyncGenerator, Optional

import httpx
import numpy as np
from loguru import logger
from pydantic import BaseModel

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import TTSService

from frames import ChunkedLLMContinueGenerationFrame

# XTTS typically 24kHz
XTTS_SAMPLE_RATE = 24000
AUDIO_OUT_SAMPLE_RATE = 16000
CHUNK_MS = 10
CHUNK_BYTES_16K = int(AUDIO_OUT_SAMPLE_RATE * CHUNK_MS / 1000) * 2
WAV_HEADER_LENGTH = 44

# XTTS GPU path is not stable with concurrent inference requests on this setup.
# Queue all /tts calls across sessions to avoid CUDA device-side asserts.
_XTTS_GLOBAL_LOCK = asyncio.Lock()


class XTTSHTTPTTSService(TTSService):
    """HTTP client for Coqui XTTS v2. Pushes continue frame for buffered LLM."""

    class InputParams(BaseModel):
        voice_id: str = "Andrew Chipper"

    def __init__(
        self,
        *,
        server_url: str = "http://127.0.0.1:80",
        voice_id: str = "Andrew Chipper",
        language: str = "en",
        sample_rate: Optional[int] = None,
        params: Optional[InputParams] = None,
        **kwargs,
    ):
        out_rate = sample_rate or AUDIO_OUT_SAMPLE_RATE
        super().__init__(sample_rate=out_rate, aggregate_sentences=False, **kwargs)
        params = params or XTTSHTTPTTSService.InputParams()
        self._server_url = server_url.rstrip("/")
        self._voice_id = getattr(params, "voice_id", voice_id)
        self._language = language
        self._sample_rate = out_rate
        # Longer connect timeout for XTTS (model load); 60s read/write
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=30.0))
        self._speaker_embedding: Optional[list] = None
        self._gpt_cond_latent: Optional[list] = None
        self.set_model_name("xtts-v2")
        logger.info(f"XTTSHTTPTTS initialized: server={server_url}, voice={self._voice_id}")

    def can_generate_metrics(self) -> bool:
        return True

    async def _ensure_speaker(self):
        """Fetch studio_speakers and cache embedding for _voice_id. Retries on connect failure."""
        if self._speaker_embedding is not None:
            return
        last_err = None
        for attempt in range(4):  # 4 attempts: 0,1,2,3
            try:
                r = await self._client.get(f"{self._server_url}/studio_speakers")
                r.raise_for_status()
                data = r.json()
                if self._voice_id not in data:
                    names = list(data.keys())[:5]
                    raise ValueError(f"Voice '{self._voice_id}' not in studio_speakers. Available (sample): {names}")
                self._speaker_embedding = data[self._voice_id]["speaker_embedding"]
                self._gpt_cond_latent = data[self._voice_id]["gpt_cond_latent"]
                return
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_err = e
                if attempt < 3:
                    wait = 2.0 * (attempt + 1)
                    logger.warning(f"XTTS connection attempt {attempt + 1}/4 failed: {e}. Retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"XTTS studio_speakers: {e}")
                    raise
            except Exception as e:
                logger.error(f"XTTS studio_speakers: {e}")
                raise
        if last_err is not None:
            raise last_err

    # Regex patterns for stripping code blocks before TTS speaks
    _CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
    _INLINE_CODE_RE = re.compile(r"`[^`]+`")

    @staticmethod
    def _strip_code_for_speech(text: str) -> str:
        """Remove fenced code blocks and inline code so TTS doesn't read them."""
        text = XTTSHTTPTTSService._CODE_BLOCK_RE.sub("", text)
        text = XTTSHTTPTTSService._INLINE_CODE_RE.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        """Generate speech via XTTS POST /tts; push continue frame after audio."""
        await self.start_ttfb_metrics()
        yield TTSStartedFrame()

        if not text or not text.strip():
            yield TTSStoppedFrame()
            await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)
            return

        # Strip code blocks so TTS doesn't read them aloud
        text = self._strip_code_for_speech(text)
        if not text or not text.strip():
            yield TTSStoppedFrame()
            await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)
            return

        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u201C", '"').replace("\u201D", '"')
        text = text.replace("\u2014", "-").replace("\u2013", "-")

        logger.debug(f"XTTSHTTPTTS: Generating [{text[:50]}...]")

        try:
            async with _XTTS_GLOBAL_LOCK:
                await self._ensure_speaker()
                payload = {
                    "text": text,
                    "language": self._language,
                    "speaker_embedding": self._speaker_embedding,
                    "gpt_cond_latent": self._gpt_cond_latent,
                }
                resp = await self._client.post(f"{self._server_url}/tts", json=payload)
                if resp.status_code in (500, 502, 503, 504):
                    await asyncio.sleep(1.5)
                    resp = await self._client.post(f"{self._server_url}/tts", json=payload)

            if resp.status_code != 200:
                body = (resp.text or "")[:800]
                error_msg = f"XTTS TTS error: {resp.status_code} - {body[:300]}"
                low = body.lower()
                if any(
                    x in low
                    for x in (
                        "cuda",
                        "out of memory",
                        "cudaerrormemoryallocation",
                        "acceleratorerror",
                    )
                ):
                    error_msg += (
                        " | GPU VRAM exhausted (common when Ollama vision/LLM + XTTS share one GPU). "
                        "Try: ollama stop <model>, lower LOCAL_LLAMA_N_GPU_LAYERS / ctx, "
                        "docker restart xtts-tts, run XTTS on another GPU (XTTS_GPU_DEVICE), or XTTS_CPU=1."
                    )
                elif resp.status_code >= 500:
                    error_msg += (
                        " | If there is no audio, check: docker logs xtts-tts --tail 80 "
                        "(often CUDA OOM alongside a large LLM)."
                    )
                logger.error(error_msg)
                yield ErrorFrame(error=error_msg)
                yield TTSStoppedFrame()
                await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)
                return

            await self.stop_ttfb_metrics()

            # Server returns base64-encoded WAV
            raw = resp.content
            try:
                wav_bytes = base64.b64decode(raw)
            except Exception:
                wav_bytes = raw

            if len(wav_bytes) < 100:
                logger.warning(f"XTTS returned very little data ({len(wav_bytes)} bytes)")
                yield ErrorFrame(error=f"XTTS returned {len(wav_bytes)} bytes")
                yield TTSStoppedFrame()
                await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)
                return

            if wav_bytes[:4] == b"RIFF" and len(wav_bytes) > WAV_HEADER_LENGTH:
                audio_bytes_24k = wav_bytes[WAV_HEADER_LENGTH:]
            else:
                audio_bytes_24k = wav_bytes

            audio_int16 = np.frombuffer(audio_bytes_24k, dtype=np.int16)
            n_in = len(audio_int16)
            n_out = int(n_in * AUDIO_OUT_SAMPLE_RATE / XTTS_SAMPLE_RATE)
            if n_out < 1:
                n_out = 1
            x_out = np.linspace(0, n_in - 1, n_out, dtype=np.float64)
            resampled = np.interp(
                x_out, np.arange(n_in, dtype=np.float64), audio_int16.astype(np.float64)
            )
            audio_bytes = resampled.astype(np.int16).tobytes()

            num_chunks = 0
            for i in range(0, len(audio_bytes), CHUNK_BYTES_16K):
                chunk = audio_bytes[i : i + CHUNK_BYTES_16K]
                if not chunk:
                    continue
                num_chunks += 1
                yield TTSAudioRawFrame(chunk, self._sample_rate, 1)

            logger.info(f"XTTSHTTPTTS: got {len(wav_bytes)} bytes WAV -> {num_chunks} chunks 16kHz")

            await self.start_tts_usage_metrics(text)
            yield TTSStoppedFrame()
            await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)
            logger.debug("XTTSHTTPTTS: pushed continue signal upstream for LLM")

        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to XTTS at {self._server_url}: {e}"
            logger.error(error_msg)
            yield ErrorFrame(error=error_msg)
            yield TTSStoppedFrame()
            await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)
        except Exception as e:
            logger.error(f"XTTSHTTPTTS error: {e}")
            yield ErrorFrame(error=str(e))
            yield TTSStoppedFrame()
            await self.push_frame(ChunkedLLMContinueGenerationFrame(), FrameDirection.UPSTREAM)

    async def close(self):
        await self._client.aclose()
