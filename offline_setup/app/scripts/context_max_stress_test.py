#!/usr/bin/env python3
"""Push ``/api/text-chat/completions`` prompts until context / OOM / HTTP errors; print live estimates.

Uses the same proxy as the Assistant Console. Passes large ``text_chat_max_prompt_tokens`` /
``text_chat_max_prompt_chars`` so the server does not trim the padding before upstream.

**Live context**
  Each successful response prints ``usage.prompt_tokens`` / ``usage.total_tokens`` when the
  backend returns OpenAI-style ``usage`` (local llama / Ollama often do).

**UI mirror**
  While running, POSTs compact state to ``/api/context-stress/live`` so the Assistant Console
  footer shows a **LIVE** context-stress line. Disable with ``--no-publish``.

**Terminal**
  On a TTY, updates one in-place line (GPU draw / util / VRAM + elapsed) during each HTTP request.

Examples (from checkout: ``lisa/nvidia_voice/offline_setup/app`` — not repo root):

  cd offline_setup/app && uv run python scripts/context_max_stress_test.py
  uv run python scripts/context_max_stress_test.py --base-url https://127.0.0.1:7860 --insecure
  uv run python scripts/context_max_stress_test.py --base-url http://127.0.0.1:7861 --live-gpu --max-steps 25
  uv run python scripts/context_max_stress_test.py --no-publish
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any


def _msg_chars(m: dict[str, Any]) -> int:
    c = m.get("content")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return len(json.dumps(c))
    return len(json.dumps(m))


def approx_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """Match ``text_chat_truncate._approx_prompt_tokens`` (conservative)."""
    tc = sum(_msg_chars(m) for m in messages if isinstance(m, dict))
    return max((tc + 2) // 3, (tc + 1) // 2)


def _urlopen(req: urllib.request.Request, *, timeout: float, insecure: bool):
    if req.full_url.lower().startswith("https"):
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url: str, *, timeout: float, insecure: bool) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with _urlopen(req, timeout=timeout, insecure=insecure) as r:
        return json.loads(r.read().decode())


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    insecure: bool,
) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(req, timeout=timeout, insecure=insecure) as r:
            raw = r.read().decode()
            ct = r.headers.get("Content-Type", "")
            if "json" in ct.lower():
                return r.status, json.loads(raw)
            return r.status, raw
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def nvidia_smi_brief() -> str:
    try:
        import subprocess

        p = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return (p.stdout or "").strip().replace("\n", " | ")
    except Exception as e:
        return f"(smi err: {e})"


def nvidia_smi_stress_line(gpu_index: int = 0) -> str:
    """Short live GPU snippet: power W, util %, memory MiB for the requested device."""
    try:
        import subprocess

        p = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu,power.draw",
                "--format=csv,noheader,nounits",
                f"--id={gpu_index}",
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if p.returncode != 0 or not (p.stdout or "").strip():
            return "GPU —"
        line = (p.stdout or "").strip().split("\n")[0]
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3:
            mem, util, pwr = parts[0], parts[1], parts[2]
            return f"{pwr}W · {util}% · {mem}MiB"
        return line
    except Exception as e:
        return f"GPU err: {e}"


def _tty_live_line(live: dict[str, Any]) -> str:
    chunks: list[str] = [
        f"{live.get('step')}/{live.get('max_steps')}",
        str(live.get("phase") or ""),
    ]
    ap = live.get("approx_prompt_tokens")
    if isinstance(ap, (int, float)):
        chunks.append(f"~{int(ap):,} tok")
    ctx = live.get("ctx_limit")
    pt = live.get("prompt_tokens")
    if isinstance(ctx, int) and isinstance(pt, int):
        chunks.append(f"{pt:,}/{ctx:,} ctx")
    elif isinstance(ctx, int):
        chunks.append(f"ctx≤{ctx:,}")
    ph = str(live.get("phase") or "")
    if ph.startswith("POST /") and live.get("elapsed_s") is not None:
        try:
            chunks.append(f"{float(live['elapsed_s']):.1f}s")
        except (TypeError, ValueError):
            pass
    gl = live.get("gpu_line")
    if gl:
        chunks.append(str(gl))
    return " | ".join(c for c in chunks if c)


_publish_warn_state = {"failures": 0, "last_warn_step": -1}


def publish_stress_state(
    base: str,
    *,
    insecure: bool,
    enabled: bool,
    payload: dict[str, Any],
    timeout: float = 3.0,
) -> None:
    if not enabled:
        return
    url = f"{base.rstrip('/')}/api/context-stress/live"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(req, timeout=timeout, insecure=insecure):
            pass
        _publish_warn_state["failures"] = 0
    except Exception as exc:  # noqa: BLE001 (we explicitly want to log + continue)
        _publish_warn_state["failures"] += 1
        step = int(payload.get("step", 0) or 0)
        # Warn once per step, plus every 5th consecutive failure.
        if (
            _publish_warn_state["last_warn_step"] != step
            or _publish_warn_state["failures"] % 5 == 1
        ):
            _publish_warn_state["last_warn_step"] = step
            print(
                f"  [publish_stress_state] mirror POST failed (n={_publish_warn_state['failures']}): {exc!r}",
                file=sys.stderr,
                flush=True,
            )


def _gpu_watch(stop: threading.Event, interval: float) -> None:
    while not stop.wait(timeout=interval):
        ts = time.strftime("%H:%M:%S")
        print(f"[live-gpu {ts}] {nvidia_smi_brief()}", flush=True)


def _failure_detail(code: int, resp: dict[str, Any] | str) -> str:
    if isinstance(resp, dict):
        if "error" in resp:
            return json.dumps(resp["error"])[:900]
        return json.dumps(resp)[:900]
    return str(resp)[:900]


def is_context_or_oom(msg: str) -> bool:
    m = msg.lower()
    keys = (
        "exceed_context",
        "context size",
        "n_ctx",
        "context length",
        "kv cache",
        "out of memory",
        "oom",
        "cuda",
        "resource exhausted",
        "allocation",
    )
    return any(k in m for k in keys)


def run_stress(
    base: str,
    *,
    insecure: bool,
    max_tokens: int,
    max_steps: int,
    growth: float,
    initial_chars: int,
    request_timeout: float,
    live_gpu: bool,
    gpu_interval: float,
    publish: bool,
    pulse_interval: float,
    gpu_index: int = 0,
) -> int:
    base = base.rstrip("/")
    status_url = f"{base}/api/runtime-status"
    chat_url = f"{base}/api/text-chat/completions"

    print(f"GET {status_url}", flush=True)
    st = _get_json(status_url, timeout=30.0, insecure=insecure)
    models = st.get("models") or {}
    model = str(models.get("llm_model") or "").strip()
    ctx_lim = models.get("llm_context_tokens")
    prov = str(models.get("llm_provider") or "")
    if not model:
        print("ERROR: runtime-status has no llm_model", file=sys.stderr)
        return 2
    print(f"  provider={prov!r} model={model!r} llm_context_tokens={ctx_lim!r}", flush=True)
    if publish:
        print("  UI mirror: POST /api/context-stress/live (Assistant Console footer LIVE line)", flush=True)

    stop_gpu = threading.Event()
    gpu_thread: threading.Thread | None = None
    if live_gpu:
        gpu_thread = threading.Thread(target=_gpu_watch, args=(stop_gpu, gpu_interval), daemon=True)
        gpu_thread.start()
        print(f"[live-gpu] background thread every {gpu_interval}s (Ctrl+C stops)", flush=True)

    publish_stress_state(
        base,
        insecure=insecure,
        enabled=publish,
        payload={
            "active": True,
            "step": 0,
            "max_steps": max_steps,
            "phase": "connected",
            "model": model[:96],
            "ctx_limit": ctx_lim if isinstance(ctx_lim, int) else None,
            "approx_prompt_tokens": None,
            "gpu_line": nvidia_smi_stress_line(gpu_index),
        },
    )

    sys_msg = (
        "Stress test harness. The user message contains padding. "
        "Reply with exactly one line: OK"
    )
    pad_unit = (
        "Lorem ipsum dolor sit amet 0123456789 αβγ δεζ ηθικ λμν ξοπ ρστ υφχψω "
        "code `const x = () => {}` and math E=mc². "
    )

    size_chars = initial_chars
    step_i = 0
    exit_code = 0
    last_pub_wall: dict[str, float] = {"t": 0.0}

    try:
        while step_i < max_steps:
            step_i += 1
            # Build padding of exact character length (high entropy-ish for tokenizers).
            if size_chars <= 0:
                size_chars = len(pad_unit)
            reps = max(1, size_chars // len(pad_unit))
            pad = (pad_unit * reps)[:size_chars]
            user_msg = f"STEP={step_i}\nPADDING_START\n{pad}\nPADDING_END\nSay OK."
            messages: list[dict[str, str]] = [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ]
            est = approx_prompt_tokens(messages)
            chars = sum(_msg_chars(m) for m in messages if isinstance(m, dict))
            ts = time.strftime("%H:%M:%S")
            print(
                f"\n[{ts}] step {step_i}/{max_steps}  approx_prompt_tokens≈{est:,}  chars≈{chars:,}  "
                f"(target pad_chars={size_chars:,})",
                flush=True,
            )

            publish_stress_state(
                base,
                insecure=insecure,
                enabled=publish,
                payload={
                    "active": True,
                    "step": step_i,
                    "max_steps": max_steps,
                    "phase": "before POST",
                    "model": model[:96],
                    "ctx_limit": ctx_lim if isinstance(ctx_lim, int) else None,
                    "approx_prompt_tokens": est,
                    "chars": chars,
                    "gpu_line": nvidia_smi_stress_line(gpu_index),
                },
            )

            stop_pulse = threading.Event()
            pulse_state: dict[str, Any] = {
                "active": True,
                "step": step_i,
                "max_steps": max_steps,
                "phase": "POST /api/text-chat/completions",
                "approx_prompt_tokens": est,
                "ctx_limit": ctx_lim if isinstance(ctx_lim, int) else None,
                "prompt_tokens": None,
                "model": model[:96],
            }

            def pulse_fn() -> None:
                t_req = time.perf_counter()
                while not stop_pulse.wait(pulse_interval):
                    elapsed = time.perf_counter() - t_req
                    pulse_state["elapsed_s"] = round(elapsed, 2)
                    pulse_state["gpu_line"] = nvidia_smi_stress_line(gpu_index)
                    if sys.stdout.isatty():
                        sys.stdout.write("\r\033[K" + _tty_live_line(pulse_state))
                        sys.stdout.flush()
                    if publish:
                        now = time.time()
                        if now - last_pub_wall["t"] >= 0.48:
                            last_pub_wall["t"] = now
                            publish_stress_state(
                                base,
                                insecure=insecure,
                                enabled=publish,
                                payload=dict(pulse_state),
                            )

            pulse_thread = threading.Thread(target=pulse_fn, daemon=True)
            pulse_thread.start()
            t0 = time.perf_counter()
            try:
                code, resp = _post_json(
                    chat_url,
                    {
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.0,
                        "text_chat_max_prompt_tokens": 900_000,
                        "text_chat_max_prompt_chars": 900_000,
                    },
                    timeout=request_timeout,
                    insecure=insecure,
                )
            finally:
                stop_pulse.set()
                pulse_thread.join(timeout=3.0)
            if sys.stdout.isatty():
                sys.stdout.write("\n")
                sys.stdout.flush()
            dt = time.perf_counter() - t0
            dt_ms = dt * 1000.0

            if code == 200 and isinstance(resp, dict) and not resp.get("error"):
                usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
                pt = usage.get("prompt_tokens")
                tt = usage.get("total_tokens")
                ct = usage.get("completion_tokens")
                print(
                    f"  HTTP 200 in {dt:.2f}s  usage: prompt_tokens={pt!r} "
                    f"completion_tokens={ct!r} total_tokens={tt!r}",
                    flush=True,
                )
                if isinstance(ctx_lim, int) and ctx_lim > 0 and isinstance(pt, int):
                    print(f"  live: prompt_tokens / configured_ctx ≈ {pt:,} / {ctx_lim:,}", flush=True)
                publish_stress_state(
                    base,
                    insecure=insecure,
                    enabled=publish,
                    payload={
                        "active": True,
                        "step": step_i,
                        "max_steps": max_steps,
                        "phase": "response OK",
                        "model": model[:96],
                        "ctx_limit": ctx_lim if isinstance(ctx_lim, int) else None,
                        "approx_prompt_tokens": est,
                        "prompt_tokens": pt,
                        "total_tokens": tt,
                        "last_latency_ms": dt_ms,
                        "http_code": code,
                        "gpu_line": nvidia_smi_stress_line(gpu_index),
                    },
                )
                # Grow for next step
                size_chars = max(size_chars + 1, int(size_chars * growth))
            else:
                detail = _failure_detail(code, resp)
                print(f"  FAIL http={code} after {dt:.2f}s", flush=True)
                print(f"  body: {detail}", flush=True)
                if is_context_or_oom(detail):
                    print("  (...classified as context / resource limit style error)", flush=True)
                publish_stress_state(
                    base,
                    insecure=insecure,
                    enabled=publish,
                    payload={
                        "active": True,
                        "step": step_i,
                        "max_steps": max_steps,
                        "phase": "response error",
                        "model": model[:96],
                        "ctx_limit": ctx_lim if isinstance(ctx_lim, int) else None,
                        "approx_prompt_tokens": est,
                        "last_latency_ms": dt_ms,
                        "http_code": code,
                        "error_hint": detail[:240],
                        "gpu_line": nvidia_smi_stress_line(gpu_index),
                    },
                )
                exit_code = 1
                break
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
        exit_code = 130
    finally:
        stop_gpu.set()
        if gpu_thread is not None:
            gpu_thread.join(timeout=2.0)
        publish_stress_state(
            base,
            insecure=insecure,
            enabled=publish,
            payload={"active": False, "phase": "idle", "gpu_line": nvidia_smi_stress_line(gpu_index)},
        )

    return exit_code


def main() -> None:
    ap = argparse.ArgumentParser(description="Stress text-chat context until error or step cap.")
    ap.add_argument("--base-url", default="http://127.0.0.1:7861", help="Bot HTTP(S) root (7861 HTTP or 7860 HTTPS).")
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verify for https:// URLs.")
    ap.add_argument("--max-tokens", type=int, default=32, help="Completion budget (keep small to isolate prompt KV).")
    ap.add_argument("--max-steps", type=int, default=40, help="Max growth iterations (safety).")
    ap.add_argument("--growth", type=float, default=1.35, help="Multiply pad length after each success.")
    ap.add_argument("--initial-chars", type=int, default=20_000, help="Initial user padding size (characters).")
    ap.add_argument("--timeout", type=float, default=600.0, help="Per-request timeout (seconds).")
    ap.add_argument("--live-gpu", action="store_true", help="Print nvidia-smi memory/util every --gpu-interval seconds.")
    ap.add_argument("--gpu-interval", type=float, default=2.0, help="Seconds between live GPU lines.")
    ap.add_argument(
        "--no-publish",
        action="store_true",
        help="Do not POST /api/context-stress/live (no Assistant Console footer mirror).",
    )
    ap.add_argument(
        "--pulse-interval",
        type=float,
        default=0.25,
        help="Seconds between TTY pulse lines and UI throttle baseline during each POST (default: 0.25).",
    )
    ap.add_argument(
        "--gpu-index",
        type=int,
        default=0,
        help="GPU device index to read in nvidia-smi snippets (default: 0).",
    )
    args = ap.parse_args()
    code = run_stress(
        args.base_url,
        insecure=args.insecure,
        max_tokens=args.max_tokens,
        max_steps=args.max_steps,
        growth=args.growth,
        initial_chars=args.initial_chars,
        request_timeout=args.timeout,
        live_gpu=args.live_gpu,
        gpu_interval=args.gpu_interval,
        publish=not args.no_publish,
        pulse_interval=args.pulse_interval,
        gpu_index=args.gpu_index,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
