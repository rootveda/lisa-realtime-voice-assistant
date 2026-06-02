#!/usr/bin/env python3
"""
Test XTTS server separately (no bot). Verifies the server responds and produces audio.

Usage:
  python tests/test_xtts_standalone.py
  XTTS_TTS_URL=http://localhost:8002 python tests/test_xtts_standalone.py
  python tests/test_xtts_standalone.py --stream   # use /tts_stream and save to WAV
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    sys.exit(1)

DEFAULT_URL = os.getenv("XTTS_TTS_URL", "http://localhost:8002").rstrip("/")
OUTPUT_DIR = Path(__file__).parent / "fixtures"
OUTPUT_WAV = OUTPUT_DIR / "test_xtts_output.wav"


def test_health(url: str) -> bool:
    """Check server is up (optional /health)."""
    try:
        r = requests.get(f"{url}/languages", timeout=5)
        return r.status_code == 200
    except Exception as e:
        print(f"Server unreachable: {e}")
        return False


def get_studio_speakers(url: str):
    """GET /studio_speakers -> dict name -> { speaker_embedding, gpt_cond_latent }."""
    r = requests.get(f"{url}/studio_speakers", timeout=10)
    r.raise_for_status()
    return r.json()


def tts_non_streaming(url: str, text: str, speaker_name: str, language: str = "en") -> bytes:
    """POST /tts, returns raw WAV bytes."""
    speakers = get_studio_speakers(url)
    if speaker_name not in speakers:
        names = list(speakers.keys())[:5]
        raise ValueError(f"Speaker '{speaker_name}' not in studio_speakers. Available (sample): {names}")
    payload = {
        "text": text,
        "language": language,
        "speaker_embedding": speakers[speaker_name]["speaker_embedding"],
        "gpt_cond_latent": speakers[speaker_name]["gpt_cond_latent"],
    }
    r = requests.post(f"{url}/tts", json=payload, timeout=60)
    r.raise_for_status()
    return base64.b64decode(r.content)


def tts_streaming(url: str, text: str, speaker_name: str, language: str = "en", stream_chunk_size: int = 20) -> bytes:
    """POST /tts_stream, returns raw audio bytes (streamed)."""
    speakers = get_studio_speakers(url)
    if speaker_name not in speakers:
        names = list(speakers.keys())[:5]
        raise ValueError(f"Speaker '{speaker_name}' not in studio_speakers. Available (sample): {names}")
    payload = {
        "text": text,
        "language": language,
        "stream_chunk_size": stream_chunk_size,
        "speaker_embedding": speakers[speaker_name]["speaker_embedding"],
        "gpt_cond_latent": speakers[speaker_name]["gpt_cond_latent"],
    }
    r = requests.post(f"{url}/tts_stream", json=payload, stream=True, timeout=60)
    r.raise_for_status()
    return b"".join(r.iter_content(chunk_size=4096))


def main():
    p = argparse.ArgumentParser(description="Test XTTS server standalone")
    p.add_argument("--url", default=DEFAULT_URL, help="XTTS server base URL")
    p.add_argument("--text", default="Hello. This is a test of the XTTS server.", help="Text to synthesize")
    p.add_argument("--speaker", default="Claribel Dervla", help="Studio speaker name")
    p.add_argument("--stream", action="store_true", help="Use /tts_stream instead of /tts")
    p.add_argument("--out", default=str(OUTPUT_WAV), help="Output WAV path")
    args = p.parse_args()

    url = args.url.rstrip("/")
    print(f"XTTS URL: {url}")
    print(f"Speaker:  {args.speaker}")
    print(f"Text:     {args.text[:50]}...")
    print()

    if not test_health(url):
        print("FAIL: Server not reachable. Is the XTTS container running? (e.g. ./scripts/start_xtts.sh)")
        sys.exit(1)
    print("OK: Server reachable")

    try:
        speakers = get_studio_speakers(url)
        print(f"OK: studio_speakers: {len(speakers)} voices")
        if args.speaker not in speakers:
            print(f"Available speakers (first 10): {list(speakers.keys())[:10]}")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    try:
        if args.stream:
            print("Calling POST /tts_stream ...")
            raw = tts_streaming(url, args.text, args.speaker)
        else:
            print("Calling POST /tts ...")
            raw = tts_non_streaming(url, args.text, args.speaker)
        print(f"OK: Received {len(raw)} bytes")
    except requests.exceptions.HTTPError as e:
        print(f"FAIL: HTTP {e.response.status_code}")
        body = getattr(e.response, "text", "") or e.response.content[:500]
        print(body)
        if e.response.status_code == 500 and "CUDA" in str(body):
            print("\nOn RTX 5090 (Blackwell) the pre-built GPU image fails. Use CPU or build Blackwell image:")
            print("  XTTS_CPU=1 ./scripts/start_xtts.sh")
            print("  ./scripts/build_xtts_gpu.sh  # then restart XTTS")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    print(f"Wrote: {out_path}")
    print("TTS test passed. Play with: aplay tests/fixtures/test_xtts_output.wav  (or open in an audio player)")


if __name__ == "__main__":
    main()
