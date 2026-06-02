#!/usr/bin/env python3
"""WebRTC test client for multi-turn voice agent testing.

Connects to the bot via WebRTC, maintains a persistent connection,
and supports sending multiple audio turns with proper timing.

Usage:
    # Multi-turn test (primary use case)
    uv run scripts/run_20_turn_test.py

    # Single turn via this script
    uv run scripts/voice_agent_test_client.py --text "Hello, how are you?"
"""

import asyncio
import base64
import binascii
import json
import os
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import aiohttp
    from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
    from aiortc.contrib.media import MediaRecorder
    from av import AudioFrame
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install aiortc aiohttp numpy")
    exit(1)


@dataclass
class TurnMetrics:
    """Metrics collected for a single conversation turn."""
    turn_number: int
    utterance_text: str
    utterance_duration_ms: float
    audio_sent_time: float  # Timestamp when audio finished sending
    bot_started_speaking_time: Optional[float] = None
    bot_stopped_speaking_time: Optional[float] = None
    events: list = field(default_factory=list)

    @property
    def time_to_response_ms(self) -> Optional[float]:
        """Time from audio sent to bot starting to speak."""
        if self.bot_started_speaking_time and self.audio_sent_time:
            return (self.bot_started_speaking_time - self.audio_sent_time) * 1000
        return None

    @property
    def response_duration_ms(self) -> Optional[float]:
        """Duration of bot's response."""
        if self.bot_started_speaking_time and self.bot_stopped_speaking_time:
            return (self.bot_stopped_speaking_time - self.bot_started_speaking_time) * 1000
        return None


def load_audio_file(path: str, target_sample_rate: int = 16000) -> np.ndarray:
    """Load audio from WAV or raw PCM file and resample to target rate."""
    # Try to open as WAV first
    try:
        with wave.open(path, "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            raw_data = wf.readframes(wf.getnframes())
    except wave.Error:
        # Not a WAV file - assume raw PCM (Magpie format: 22kHz mono s16le)
        with open(path, "rb") as f:
            raw_data = f.read()
        nchannels = 1
        sampwidth = 2
        framerate = 22000

    # Convert to numpy array
    if sampwidth == 2:
        samples = np.frombuffer(raw_data, dtype=np.int16)
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    # Convert stereo to mono
    if nchannels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1).astype(np.int16)

    # Resample if needed
    if framerate != target_sample_rate:
        ratio = target_sample_rate / framerate
        new_length = int(len(samples) * ratio)
        indices = np.linspace(0, len(samples) - 1, new_length)
        samples = np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)

    return samples


class MultiTurnAudioTrack(MediaStreamTrack):
    """Audio track that supports sending multiple audio segments across conversation turns.

    Sends silence when idle, real audio when queued. Maintains realtime pacing.
    """

    kind = "audio"

    def __init__(self, sample_rate: int = 16000):
        super().__init__()
        self._sample_rate = sample_rate
        self._frame_duration = 0.02  # 20ms frames
        self._samples_per_frame = int(sample_rate * self._frame_duration)

        # Audio state
        self._current_audio: Optional[np.ndarray] = None
        self._audio_position = 0
        self._audio_complete = asyncio.Event()
        self._audio_complete.set()  # Initially complete (no audio pending)

        # Timing
        self._start_time: Optional[float] = None
        self._frame_count = 0

    def queue_audio(self, samples: np.ndarray):
        """Queue audio samples to be sent. Clears any previous audio."""
        self._current_audio = samples
        self._audio_position = 0
        self._audio_complete.clear()

    async def wait_for_completion(self) -> float:
        """Wait until queued audio has been fully sent. Returns completion timestamp."""
        await self._audio_complete.wait()
        return time.time()

    def is_sending(self) -> bool:
        """Check if currently sending non-silence audio."""
        return self._current_audio is not None and self._audio_position < len(self._current_audio)

    async def recv(self) -> AudioFrame:
        """Generate next audio frame at realtime rate."""
        # Initialize timing on first call
        if self._start_time is None:
            self._start_time = time.time()

        # Calculate expected time for this frame
        expected_time = self._start_time + self._frame_count * self._frame_duration
        now = time.time()
        if expected_time > now:
            await asyncio.sleep(expected_time - now)

        # Get samples for this frame
        if self._current_audio is not None and self._audio_position < len(self._current_audio):
            # Send real audio
            start = self._audio_position
            end = min(start + self._samples_per_frame, len(self._current_audio))
            samples = self._current_audio[start:end]

            # Pad if needed
            if len(samples) < self._samples_per_frame:
                samples = np.pad(samples, (0, self._samples_per_frame - len(samples)))

            self._audio_position = end

            # Check if audio is complete
            if self._audio_position >= len(self._current_audio):
                self._audio_complete.set()
        else:
            # Send silence
            samples = np.zeros(self._samples_per_frame, dtype=np.int16)

        # Create AudioFrame
        frame = AudioFrame(format="s16", layout="mono", samples=self._samples_per_frame)
        frame.sample_rate = self._sample_rate
        frame.pts = self._frame_count * self._samples_per_frame
        frame.planes[0].update(samples.tobytes())

        self._frame_count += 1
        return frame


class MultiTurnVoiceAgentClient:
    """WebRTC client for multi-turn voice agent testing.

    Maintains a persistent connection and supports sending multiple turns.
    """

    def __init__(
        self,
        server_url: str = "http://localhost:7860",
        tts_url: str = "http://localhost:8001",
        output_dir: Optional[str] = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.tts_url = tts_url.rstrip("/")
        self.output_dir = Path(output_dir) if output_dir else Path("/tmp/voice_agent_test")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.pc: Optional[RTCPeerConnection] = None
        self.audio_track: Optional[MultiTurnAudioTrack] = None
        self.data_channel = None

        # Connection state
        self._connection_ready = asyncio.Event()
        self._bot_stopped_speaking = asyncio.Event()

        # Event tracking
        self.all_events: list = []
        self._turn_events: list = []  # Events for current turn
        self._bot_stopped_count = 0
        self._current_turn = 0
        self.connection_start_time: Optional[float] = None

        # Turn timing
        self._bot_started_time: Optional[float] = None
        self._bot_stopped_time: Optional[float] = None
        self._bot_text_event = asyncio.Event()
        self._user_transcription_event = asyncio.Event()
        self._bot_llm_done_event = asyncio.Event()

    async def connect(self) -> bool:
        """Establish WebRTC connection to voice agent."""
        self.pc = RTCPeerConnection()
        self.connection_start_time = time.time()

        # Create audio track that starts with silence
        self.audio_track = MultiTurnAudioTrack()
        self.pc.addTrack(self.audio_track)

        # Create data channel for RTVI messages
        self.data_channel = self.pc.createDataChannel("rtvi-ai", ordered=True)

        @self.data_channel.on("open")
        def on_dc_open():
            print("Data channel opened, sending client-ready")
            ready_msg = json.dumps({
                "label": "rtvi-ai",
                "type": "client-ready",
                "id": "test-client-1",
                "data": {}
            })
            self.data_channel.send(ready_msg)

        @self.data_channel.on("message")
        def on_dc_message(message):
            self._handle_rtvi_message(message)

        # Handle incoming audio track from bot
        @self.pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                print("Receiving bot audio track")
                # Could add recording here if needed

        # Create and send offer
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        # Exchange SDP with server
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.server_url}/api/offer",
                    json={"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type},
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status != 200:
                        print(f"Failed to connect: {response.status}")
                        text = await response.text()
                        print(f"Response: {text[:200]}")
                        return False
                    answer = await response.json()
                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
                    )
                    print("WebRTC connection established")

                    # Wait for bot-ready
                    try:
                        await asyncio.wait_for(self._connection_ready.wait(), timeout=10.0)
                        print("Bot is ready")
                    except asyncio.TimeoutError:
                        print("Warning: Timeout waiting for bot-ready")

                    return True
            except aiohttp.ClientError as e:
                print(f"Connection error: {e}")
                return False

    def _handle_rtvi_message(self, message: str):
        """Handle incoming RTVI message."""
        try:
            event = json.loads(message)
            event_time = time.time()

            # Store event
            event_record = {"time": event_time, "event": event}
            self.all_events.append(event_record)
            self._turn_events.append(event_record)

            event_type = event.get("type", "unknown")

            if event_type == "bot-ready":
                self._connection_ready.set()
            elif event_type == "bot-llm-stopped":
                self._bot_llm_done_event.set()

            elif event_type == "bot-started-speaking":
                self._bot_started_time = event_time

            elif event_type == "bot-stopped-speaking":
                self._bot_stopped_count += 1
                self._bot_stopped_time = event_time
                self._bot_stopped_speaking.set()
            elif event_type in {"bot-tts-text", "bot-transcription", "bot-output", "bot-llm-text"}:
                # Any post-ASR assistant text is a strong response signal.
                self._bot_text_event.set()
            elif event_type == "user-transcription":
                self._user_transcription_event.set()

        except json.JSONDecodeError:
            pass

    async def wait_for_greeting(self, timeout: float = 60.0) -> bool:
        """Wait for bot's initial greeting to complete (LLM + TTS can take >30s with XTTS)."""
        print("Waiting for bot greeting to complete...")
        try:
            await asyncio.wait_for(self._bot_stopped_speaking.wait(), timeout=timeout)
            print("Bot greeting complete")
            self._bot_stopped_speaking.clear()
            # Let any trailing bot speech flush; prevents overlap with first turn.
            await asyncio.sleep(1.5)
            return True
        except asyncio.TimeoutError:
            print("Timeout waiting for greeting (continuing with turn anyway)")
            return False

    async def synthesize_audio(self, text: str) -> str:
        """Synthesize audio from text using either Magpie or XTTS HTTP APIs."""
        output_path = f"/tmp/test_audio_{int(time.time() * 1000)}.pcm"

        async with aiohttp.ClientSession() as session:
            # 1) Try legacy OpenAI-style endpoint first (Magpie path).
            async with session.post(
                f"{self.tts_url}/v1/audio/speech",
                json={"input": text, "voice": "aria", "model": "magpie"},
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status == 200:
                    audio_data = await response.read()
                elif response.status == 404:
                    # 2) Fallback to XTTS API: GET /studio_speakers, then POST /tts.
                    async with session.get(f"{self.tts_url}/studio_speakers") as speakers_resp:
                        if speakers_resp.status != 200:
                            raise RuntimeError(
                                f"TTS failed: /v1/audio/speech={response.status}, "
                                f"/studio_speakers={speakers_resp.status}"
                            )
                        speakers = await speakers_resp.json()
                    if not isinstance(speakers, dict) or not speakers:
                        raise RuntimeError("XTTS /studio_speakers returned no voices")
                    # Prefer stack default; otherwise pick first available speaker.
                    voice_name = "Andrew Chipper" if "Andrew Chipper" in speakers else next(iter(speakers.keys()))
                    voice = speakers.get(voice_name) or {}
                    payload = {
                        "text": text,
                        "language": "en",
                        "speaker_embedding": voice.get("speaker_embedding"),
                        "gpt_cond_latent": voice.get("gpt_cond_latent"),
                    }
                    async with session.post(
                        f"{self.tts_url}/tts",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as xtts_resp:
                        if xtts_resp.status != 200:
                            raise RuntimeError(
                                f"TTS failed: /v1/audio/speech={response.status}, /tts={xtts_resp.status}"
                            )
                        audio_data = await xtts_resp.read()
                        # XTTS /tts commonly returns base64-encoded WAV bytes.
                        try:
                            decoded = base64.b64decode(audio_data, validate=True)
                            if decoded[:4] == b"RIFF":
                                audio_data = decoded
                        except (binascii.Error, ValueError):
                            pass
                else:
                    raise RuntimeError(f"TTS failed: {response.status}")

        with open(output_path, "wb") as f:
            f.write(audio_data)

        return output_path

    async def send_turn(self, text: str, timeout: float = 90.0) -> TurnMetrics:
        """Send a conversation turn and wait for bot response.

        Args:
            text: The utterance text to synthesize and send
            timeout: Maximum time to wait for bot response

        Returns:
            TurnMetrics with timing and event data
        """
        self._current_turn += 1
        self._turn_events = []
        self._bot_stopped_speaking.clear()
        self._bot_text_event.clear()
        self._user_transcription_event.clear()
        self._bot_llm_done_event.clear()
        self._bot_started_time = None
        self._bot_stopped_time = None

        # Synthesize audio (with retry on transient failures)
        for attempt in range(3):
            try:
                audio_path = await self.synthesize_audio(text)
                break
            except RuntimeError as e:
                if attempt < 2:
                    print(f"  TTS failed, retrying... ({e})")
                    await asyncio.sleep(0.5)
                else:
                    raise

        samples = load_audio_file(audio_path)
        utterance_duration_ms = len(samples) / self.audio_track._sample_rate * 1000

        # Primary input path for this test client: send PCM via RTVI raw-audio on the
        # data channel. This is more deterministic in CI/headless environments than
        # relying on WebRTC upstream audio track timing.
        audio_sent_time = await self._send_raw_audio_via_datachannel(samples)

        # NOW clear timing state - ignore all false starts that occurred during audio.
        # The real response will be the first bot speaking cycle AFTER audio is done.
        self._turn_events = []
        self._bot_stopped_speaking.clear()
        self._bot_text_event.clear()
        self._user_transcription_event.clear()
        self._bot_llm_done_event.clear()
        self._bot_started_time = None
        self._bot_stopped_time = None

        # Wait for post-turn response. Primary: bot-stopped-speaking (full cycle).
        # Fallback: any assistant text event after our audio was sent.
        try:
            await asyncio.wait_for(self._bot_stopped_speaking.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"  No bot-stopped-speaking for turn {self._current_turn}; checking text fallback...")
            try:
                await asyncio.wait_for(self._bot_text_event.wait(), timeout=4.0)
                print(f"  Received assistant text fallback for turn {self._current_turn}")
            except asyncio.TimeoutError:
                # Helpful diagnostics to distinguish STT miss vs downstream response miss.
                user_seen = any(
                    (ev.get("event") or {}).get("type") == "user-transcription" for ev in self._turn_events
                )
                if not user_seen:
                    print(
                        f"  Timeout waiting for response on turn {self._current_turn} "
                        f"(no user-transcription seen); retrying via RTVI send-text fallback..."
                    )
                    await self._send_text_via_datachannel(text)
                    try:
                        await asyncio.wait_for(self._bot_llm_done_event.wait(), timeout=12.0)
                        if self._bot_text_event.is_set():
                            print(f"  send-text fallback produced assistant response on turn {self._current_turn}")
                        else:
                            print(
                                f"  send-text fallback completed LLM turn on turn {self._current_turn} "
                                f"(no bot text frame observed)"
                            )
                    except asyncio.TimeoutError:
                        print(f"  send-text fallback also timed out on turn {self._current_turn}")
                else:
                    print(f"  Timeout waiting for response on turn {self._current_turn} (user-transcription seen)")

        # Clean up temp file
        try:
            os.unlink(audio_path)
        except OSError:
            pass

        return TurnMetrics(
            turn_number=self._current_turn,
            utterance_text=text,
            utterance_duration_ms=utterance_duration_ms,
            audio_sent_time=audio_sent_time,
            bot_started_speaking_time=self._bot_started_time,
            bot_stopped_speaking_time=self._bot_stopped_time,
            events=list(self._turn_events),
        )

    async def _send_raw_audio_via_datachannel(self, samples: np.ndarray) -> float:
        """Stream int16 mono 16k audio through RTVI raw-audio messages."""
        if self.data_channel is None:
            raise RuntimeError("Data channel not available")
        if getattr(self.data_channel, "readyState", "") != "open":
            raise RuntimeError("Data channel is not open")

        chunk_samples = int(self.audio_track._sample_rate * 0.02)  # 20ms
        for i in range(0, len(samples), chunk_samples):
            chunk = samples[i : i + chunk_samples]
            if len(chunk) < chunk_samples:
                chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
            payload = {
                "label": "rtvi-ai",
                "type": "raw-audio",
                "id": f"raw-{self._current_turn}-{i}",
                "data": {
                    "sampleRate": int(self.audio_track._sample_rate),
                    "numChannels": 1,
                    "base64Audio": base64.b64encode(chunk.astype(np.int16).tobytes()).decode("ascii"),
                },
            }
            self.data_channel.send(json.dumps(payload))
            # Keep near-real-time pacing to satisfy VAD/turn detection.
            await asyncio.sleep(0.02)
        return time.time()

    async def _send_text_via_datachannel(self, text: str) -> None:
        """Fallback: inject a text turn directly via RTVI when ASR misses audio."""
        if self.data_channel is None or getattr(self.data_channel, "readyState", "") != "open":
            raise RuntimeError("Data channel is not open")
        self._bot_text_event.clear()
        payload = {
            "label": "rtvi-ai",
            "type": "send-text",
            "id": f"send-text-{self._current_turn}-{int(time.time() * 1000)}",
            "data": {
                "content": text,
                "options": {"run_immediately": True, "audio_response": True},
            },
        }
        self.data_channel.send(json.dumps(payload))

    async def close(self):
        """Close the WebRTC connection."""
        if self.pc:
            await self.pc.close()
            print("Connection closed")

        # Save all events
        events_file = self.output_dir / "all_events.json"
        with open(events_file, "w") as f:
            json.dump(self.all_events, f, indent=2, default=str)


async def synthesize_audio(text: str, tts_url: str = "http://localhost:8001") -> str:
    """Synthesize audio from text using Magpie TTS (standalone function for compatibility)."""
    output_path = f"/tmp/test_audio_{int(time.time() * 1000)}.pcm"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{tts_url}/v1/audio/speech",
            json={"input": text, "voice": "aria", "model": "magpie"},
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"TTS failed: {response.status}")
            audio_data = await response.read()

    with open(output_path, "wb") as f:
        f.write(audio_data)

    return output_path


async def main():
    """Simple single-turn test for standalone usage."""
    import argparse

    parser = argparse.ArgumentParser(description="Voice agent test client")
    parser.add_argument("--text", required=True, help="Text to synthesize and send")
    parser.add_argument("--server-url", default="http://localhost:7860", help="Bot server URL")
    parser.add_argument("--tts-url", default="http://localhost:8001", help="TTS server URL")
    parser.add_argument("--output-dir", default="/tmp/voice_agent_test", help="Output directory")
    parser.add_argument("--timeout", type=float, default=90.0, help="Response timeout (seconds)")
    parser.add_argument("--greeting-timeout", type=float, default=60.0, help="Timeout for initial greeting (default 60s for XTTS)")
    parser.add_argument("--no-wait-greeting", action="store_true", help="Skip waiting for greeting; send turn immediately after bot-ready")
    args = parser.parse_args()

    client = MultiTurnVoiceAgentClient(
        server_url=args.server_url,
        tts_url=args.tts_url,
        output_dir=args.output_dir,
    )

    try:
        if not await client.connect():
            return

        if not args.no_wait_greeting:
            await client.wait_for_greeting(timeout=args.greeting_timeout)
        else:
            print("Skipping greeting wait (--no-wait-greeting)")
            await asyncio.sleep(1)  # brief pause after bot-ready

        metrics = await client.send_turn(args.text, timeout=args.timeout)

        print(f"\nResults:")
        print(f"  Utterance: {metrics.utterance_text[:50]}...")
        print(f"  Utterance duration: {metrics.utterance_duration_ms:.0f}ms")
        if metrics.time_to_response_ms:
            print(f"  Time to response: {metrics.time_to_response_ms:.0f}ms")
        if metrics.response_duration_ms:
            print(f"  Response duration: {metrics.response_duration_ms:.0f}ms")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
