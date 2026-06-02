#!/usr/bin/env python3
"""Backend E2E check: LLM, ASR, TTS health and latency.

Usage:
  python tests/check_backend_e2e.py
  LLM_URL=http://localhost:8000 TTS_URL=http://localhost:8001 ASR_URL=http://localhost:8080 python tests/check_backend_e2e.py
"""
import os
import sys
import time

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

LLM_URL = os.getenv("NVIDIA_LLAMA_CPP_URL", os.getenv("LLM_URL", "http://localhost:8000")).rstrip("/")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "xtts").lower()
_default_tts = "http://localhost:8002" if TTS_PROVIDER == "xtts" else "http://localhost:8001"
TTS_URL = os.getenv("XTTS_TTS_URL", os.getenv("NVIDIA_TTS_URL", os.getenv("TTS_URL", _default_tts))).rstrip("/")
ASR_URL = os.getenv("NVIDIA_ASR_URL", "http://localhost:8080").rstrip("/").replace("ws://", "http://").replace("wss://", "https://")
BOT_URL = os.getenv("BOT_URL", "http://localhost:7860").rstrip("/")


def check_llm():
    """Health + one completion latency."""
    print("LLM (llama.cpp)", LLM_URL)
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{LLM_URL}/health", timeout=5)
        t1 = time.perf_counter()
        if r.status_code != 200:
            print("  health FAIL:", r.status_code)
            return False
        print("  health OK  (%.0fms)" % ((t1 - t0) * 1000))
    except Exception as e:
        print("  health FAIL:", e)
        return False

    # One short completion for latency
    payload = {
        "prompt": "<|im_start|>user\nSay the number four.<|im_end|>\n<|im_start|>assistant\n",
        "n_predict": 10,
        "stream": False,
        "temperature": 0,
    }
    try:
        t0 = time.perf_counter()
        r = requests.post(f"{LLM_URL}/completion", json=payload, timeout=30)
        t1 = time.perf_counter()
        if r.status_code != 200:
            print("  completion FAIL:", r.status_code)
            return False
        data = r.json()
        text = data.get("content", "").strip()
        print("  completion OK (%.0fms) -> %r" % ((t1 - t0) * 1000, text[:60]))
        return True
    except Exception as e:
        print("  completion FAIL:", e)
        return False


def check_tts():
    """Health + optional short synthesis (XTTS or Magpie)."""
    label = "TTS (XTTS)" if TTS_PROVIDER == "xtts" else "TTS (Magpie)"
    print(label, TTS_URL)
    if TTS_PROVIDER == "xtts":
        # XTTS: /studio_speakers as health check
        try:
            t0 = time.perf_counter()
            r = requests.get(f"{TTS_URL}/studio_speakers", timeout=10)
            t1 = time.perf_counter()
            if r.status_code != 200:
                print("  studio_speakers FAIL:", r.status_code)
                return False
            data = r.json()
            print("  studio_speakers OK (%.0fms) %d speakers" % ((t1 - t0) * 1000, len(data)))
            if not data:
                return True
            # Optional: short synthesize with first speaker
            first_name = next(iter(data))
            sp = data[first_name]
            t0 = time.perf_counter()
            r2 = requests.post(
                f"{TTS_URL}/tts",
                json={
                    "text": "Hello.",
                    "language": "en",
                    "speaker_embedding": sp["speaker_embedding"],
                    "gpt_cond_latent": sp["gpt_cond_latent"],
                },
                timeout=30,
            )
            t1 = time.perf_counter()
            if r2.status_code != 200:
                print("  synthesize skip:", r2.status_code)
                return True
            import base64
            out_len = len(base64.b64decode(r2.content))
            print("  synthesize OK (%.0fms) %d bytes" % ((t1 - t0) * 1000, out_len))
            return True
        except Exception as e:
            print("  FAIL:", e)
            return False
    # Magpie: /health + /v1/audio/speech
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{TTS_URL}/health", timeout=5)
        t1 = time.perf_counter()
        if r.status_code != 200:
            print("  health FAIL:", r.status_code)
            return False
        print("  health OK  (%.0fms)" % ((t1 - t0) * 1000))
    except Exception as e:
        print("  health FAIL:", e)
        return False
    try:
        t0 = time.perf_counter()
        r = requests.post(
            f"{TTS_URL}/v1/audio/speech",
            json={"input": "Hello.", "voice": "aria", "model": "magpie"},
            timeout=15,
        )
        t1 = time.perf_counter()
        if r.status_code != 200:
            print("  synthesize FAIL:", r.status_code, "(endpoint may differ)")
            return True
        print("  synthesize OK (%.0fms) %d bytes" % ((t1 - t0) * 1000, len(r.content)))
        return True
    except Exception as e:
        print("  synthesize skip:", e)
        return True


def check_asr():
    """Health only (ASR is WebSocket; many expose /health)."""
    print("ASR", ASR_URL)
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{ASR_URL}/health", timeout=5)
        t1 = time.perf_counter()
        if r.status_code != 200:
            print("  health FAIL:", r.status_code)
            return False
        print("  health OK  (%.0fms)" % ((t1 - t0) * 1000))
        return True
    except Exception as e:
        print("  health FAIL:", e)
        return False


def check_bot():
    """Bot HTTP server up (client page)."""
    print("Bot", BOT_URL)
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{BOT_URL}/client", timeout=5)
        t1 = time.perf_counter()
        if r.status_code != 200:
            print("  GET /client FAIL:", r.status_code)
            return False
        print("  GET /client OK (%.0fms)" % ((t1 - t0) * 1000))
        return True
    except Exception as e:
        print("  FAIL:", e)
        return False


def main():
    print("=" * 60)
    print("Backend E2E check (LLM, ASR, TTS, Bot)")
    print("=" * 60)
    ok = True
    ok &= check_llm()
    print()
    ok &= check_tts()
    print()
    ok &= check_asr()
    print()
    ok &= check_bot()
    print("=" * 60)
    if ok:
        print("All backend checks passed.")
    else:
        print("Some checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
