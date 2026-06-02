#!/usr/bin/env python3
"""End-to-end test: LLM (llama.cpp) -> response text -> XTTS -> play WAV.

Verifies:
1. LLM returns sensible text (printed for accuracy check)
2. XTTS produces audio from that text
3. Plays WAV on speaker (aplay) so you can confirm sound.

Usage:
  python tests/test_e2e_llm_tts.py
  LLM_URL=http://localhost:8000 XTTS_URL=http://localhost:8002 python tests/test_e2e_llm_tts.py
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

LLM_URL = os.getenv("NVIDIA_LLAMA_CPP_URL", os.getenv("LLM_URL", "http://localhost:8000")).rstrip("/")
XTTS_URL = os.getenv("XTTS_TTS_URL", "http://localhost:8002").rstrip("/")
SPEAKER = "Claribel Dervla"
OUT_WAV = "/tmp/e2e_llm_tts_output.wav"

# Same system prompt as bot (short, spoken-style)
SYSTEM = (
    "You are a helpful AI assistant. Keep responses concise and conversational. "
    "Use simple sentences. Avoid special characters. Spell out numbers."
)


def build_prompt(user_text: str) -> str:
    """ChatML format matching llama_cpp_buffered_llm (no thinking)."""
    parts = [
        f"<|im_start|>system\n{SYSTEM}<|im_end|>",
        f"<|im_start|>user\n{user_text}<|im_end|>",
        "<|im_start|>assistant\n<think></think>\n",
    ]
    return "\n".join(parts)


def get_llm_response(prompt: str, max_tokens: int = 80) -> str:
    """POST /completion with stream=False if supported, else parse stream."""
    url = f"{LLM_URL}/completion"
    payload = {
        "prompt": prompt,
        "n_predict": max_tokens,
        "stream": False,
        "temperature": 0.7,
        "stop": ["<|im_end|>", "\n\n"],
    }
    try:
        r = requests.post(url, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        content = data.get("content", "")
        return content.strip()
    except Exception as e:
        print(f"LLM error: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(e.response.text[:500], file=sys.stderr)
        return ""


def get_xtts_audio(text: str) -> bytes:
    """POST /tts with studio speaker, return raw WAV bytes."""
    r = requests.get(f"{XTTS_URL}/studio_speakers", timeout=10)
    r.raise_for_status()
    speakers = r.json()
    if SPEAKER not in speakers:
        raise ValueError(f"Speaker {SPEAKER} not in studio_speakers")
    payload = {
        "text": text,
        "language": "en",
        "speaker_embedding": speakers[SPEAKER]["speaker_embedding"],
        "gpt_cond_latent": speakers[SPEAKER]["gpt_cond_latent"],
    }
    r = requests.post(f"{XTTS_URL}/tts", json=payload, timeout=60)
    r.raise_for_status()
    return base64.b64decode(r.content)


def play_wav(path: str) -> bool:
    """Play WAV with aplay or paplay. Return True if played."""
    for cmd in [["aplay", path], ["paplay", path]]:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def main():
    user_input = "What is two plus two? Reply in one short sentence."
    print("=" * 60)
    print("E2E test: LLM -> text -> XTTS -> play")
    print("=" * 60)
    print(f"LLM:  {LLM_URL}")
    print(f"XTTS: {XTTS_URL}")
    print(f"User: {user_input}")
    print()

    # 1. LLM
    prompt = build_prompt(user_input)
    print("Calling LLM...")
    response_text = get_llm_response(prompt)
    if not response_text:
        print("FAIL: No LLM response.")
        sys.exit(1)
    print("LLM response (text):")
    print(" ", repr(response_text))
    print()

    # 2. XTTS
    print("Calling XTTS...")
    try:
        wav_bytes = get_xtts_audio(response_text)
    except Exception as e:
        print(f"FAIL: XTTS error: {e}")
        sys.exit(1)
    Path(OUT_WAV).write_bytes(wav_bytes)
    print(f"Wrote {len(wav_bytes)} bytes -> {OUT_WAV}")
    print()

    # 3. Play
    print("Playing WAV on speaker...")
    if play_wav(OUT_WAV):
        print("Playback finished.")
    else:
        print("Could not play (aplay/paplay not available). Open manually:")
        print(f"  aplay {OUT_WAV}")
    print()
    print("E2E test done. Check: (1) LLM text makes sense (2) You heard the reply.")


if __name__ == "__main__":
    main()
