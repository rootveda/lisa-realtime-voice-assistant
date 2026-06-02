#!/usr/bin/env python3
"""Stress local llama vs Ollama: context-ish payload + max_tokens, latency + usage.

Uses the live Assistant stack HTTP API (same paths as the console):
  GET  /api/runtime-status
  POST /api/llm-routing/apply   (restarts stack — slow)
  POST /api/text-chat/completions

See also ``context_max_stress_test.py`` for exponential prompt growth until
context/OOM errors with optional live ``nvidia-smi`` lines.

Run from repo with bot listening (default http://127.0.0.1:7861).

Examples:
  uv run python scripts/context_limit_perf_test.py
  uv run python scripts/context_limit_perf_test.py --base-url http://127.0.0.1:7861
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


def _get_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url: str, payload: dict[str, Any], timeout: float = 920.0) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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


def nvidia_smi_snapshot() -> str:
    try:
        p = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        return (p.stdout or "").strip() or "(nvidia-smi empty)"
    except Exception as e:
        return f"(nvidia-smi failed: {e})"


def wait_runtime(base: str, want: str, timeout: float = 420.0) -> dict[str, Any]:
    """Poll until llm_provider contains `want` (substring, lowercased)."""
    url = f"{base.rstrip('/')}/api/runtime-status"
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        try:
            last = _get_json(url, timeout=15.0)
            prov = str(last.get("models", {}).get("llm_provider", "")).lower()
            if want.lower() in prov:
                return last
        except Exception:
            pass
        time.sleep(2.0)
    raise TimeoutError(f"runtime-status never matched {want!r} within {timeout}s; last={last!r}")


def apply_routing(base: str, provider: str, model: str = "") -> dict[str, Any]:
    url = f"{base.rstrip('/')}/api/llm-routing/apply"
    body: dict[str, Any] = {"provider": provider}
    if model:
        body["model"] = model
    code, resp = _post_json(url, body, timeout=920.0)
    if code != 200 or not isinstance(resp, dict) or not resp.get("ok"):
        raise RuntimeError(f"apply {provider} failed: http={code} resp={resp!r}")
    return resp


@dataclass
class CaseResult:
    name: str
    ok: bool
    latency_s: float | None = None
    http_status: int | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    prompt_chars: int = 0
    completion_preview: str = ""
    gpu_before: str = ""


def chat_case(
    base: str,
    model: str,
    name: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float = 0.2,
) -> CaseResult:
    url = f"{base.rstrip('/')}/api/text-chat/completions"
    prompt_chars = sum(len(m.get("content") or "") for m in messages)
    gpu_before = nvidia_smi_snapshot()
    t0 = time.perf_counter()
    code, resp = _post_json(
        url,
        {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=600.0,
    )
    dt = time.perf_counter() - t0
    if code != 200 or not isinstance(resp, dict):
        return CaseResult(
            name=name,
            ok=False,
            latency_s=dt,
            http_status=code,
            error=str(resp)[:800],
            prompt_chars=prompt_chars,
            gpu_before=gpu_before,
        )
    err = resp.get("error")
    if err:
        return CaseResult(
            name=name,
            ok=False,
            latency_s=dt,
            http_status=code,
            error=str(err)[:800],
            prompt_chars=prompt_chars,
            gpu_before=gpu_before,
        )
    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    text = str(msg.get("content") or msg.get("reasoning") or "")[:200]
    usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    return CaseResult(
        name=name,
        ok=True,
        latency_s=dt,
        http_status=code,
        usage=usage,
        prompt_chars=prompt_chars,
        completion_preview=text.replace("\n", " "),
        gpu_before=gpu_before,
    )


def rough_tok_estimate(chars: int) -> float:
    return chars / 4.0


def run_suite(base: str, label: str, model: str) -> list[CaseResult]:
    results: list[CaseResult] = []
    sys_msg = "You follow instructions exactly. Be concise when asked."

    # 1) Baseline
    results.append(
        chat_case(
            base,
            model,
            f"{label}_baseline",
            [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": "Reply with exactly: OK"},
            ],
            max_tokens=32,
        )
    )

    # 2) Requested large completion (may stop early; measures throughput under load)
    results.append(
        chat_case(
            base,
            model,
            f"{label}_maxtok_3072",
            [
                {"role": "system", "content": sys_msg},
                {
                    "role": "user",
                    "content": (
                        "Write a markdown bullet list. "
                        "Each line: '- item N' for N from 1 upward. "
                        "Produce as many lines as you can until you stop."
                    ),
                },
            ],
            max_tokens=3072,
            temperature=0.3,
        )
    )

    # 3) Large prompt + small completion (context / KV pressure)
    pad_unit = "tokpad " * 50  # 350 chars ~ 87 tok per repeat
    target_chars = 120_000
    repeats = max(1, target_chars // len(pad_unit))
    big = pad_unit * repeats
    results.append(
        chat_case(
            base,
            model,
            f"{label}_prompt_{len(big)}c_max256",
            [
                {"role": "system", "content": sys_msg + "\n\nReference material:\n" + big},
                {"role": "user", "content": "Only reply with the single word: FIN"},
            ],
            max_tokens=256,
            temperature=0.0,
        )
    )

    # 4) Extreme requested max (short answer expected; validates server accepts param)
    results.append(
        chat_case(
            base,
            model,
            f"{label}_maxtok_45000_short",
            [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": "Reply with exactly one word: pong"},
            ],
            max_tokens=45000,
            temperature=0.0,
        )
    )

    return results


def print_result(provider_label: str, r: CaseResult) -> None:
    u = r.usage or {}
    pt = u.get("prompt_tokens")
    ct = u.get("completion_tokens")
    tt = u.get("total_tokens")
    tps = None
    if r.ok and ct is not None and r.latency_s and r.latency_s > 0:
        tps = float(ct) / r.latency_s
    print(f"\n--- {provider_label} :: {r.name} ---")
    print(f"  ok={r.ok}  http={r.http_status}  latency_s={r.latency_s:.3f}" if r.latency_s else f"\n  ok={r.ok}")
    if r.error:
        print(f"  error: {r.error[:500]}")
    print(f"  prompt_chars={r.prompt_chars}  ~est_input_tok≈{rough_tok_estimate(r.prompt_chars):.0f}")
    print(f"  usage: prompt={pt} completion={ct} total={tt}")
    if tps is not None:
        print(f"  completion tok/s (wall): {tps:.1f}")
    if r.completion_preview:
        print(f"  completion_head: {r.completion_preview!r}")
    print(f"  gpu_before: {r.gpu_before[:200]}..." if len(r.gpu_before) > 200 else f"  gpu_before: {r.gpu_before}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base-url",
        default="http://127.0.0.1:7861",
        help="Bot HTTP base (plain HTTP port behind TLS proxy).",
    )
    ap.add_argument(
        "--ollama-model",
        default="gemma4:26b-a4b-it-q4_K_M",
        help="Ollama model name for routing apply.",
    )
    ap.add_argument("--skip-restore", action="store_true", help="Do not switch back to initial provider.")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    print("nvidia-smi (initial):")
    print(nvidia_smi_snapshot())

    initial = _get_json(f"{base}/api/runtime-status", timeout=20.0)
    init_prov = str(initial.get("models", {}).get("llm_provider", ""))
    init_model = str(initial.get("models", {}).get("llm_model", ""))
    print(f"\nInitial runtime: provider={init_prov!r} model={init_model!r}")

    all_results: dict[str, list[CaseResult]] = {}

    # ---- Local llama ----
    print("\n" + "=" * 60)
    print("Switching to LOCAL llama (stack restart, can take several minutes)...")
    apply_routing(base, "local")
    st = wait_runtime(base, "local-llama")
    model = str(st.get("models", {}).get("llm_model") or init_model)
    print(f"Ready local: model={model!r}")
    all_results["local"] = run_suite(base, "local", model)
    for r in all_results["local"]:
        print_result("LOCAL", r)

    # ---- Ollama ----
    print("\n" + "=" * 60)
    print(f"Switching to OLLAMA model={args.ollama_model!r} (stack restart)...")
    apply_routing(base, "ollama", args.ollama_model)
    st = wait_runtime(base, "ollama")
    model = str(st.get("models", {}).get("llm_model") or args.ollama_model)
    print(f"Ready ollama: model={model!r}")
    all_results["ollama"] = run_suite(base, "ollama", model)
    for r in all_results["ollama"]:
        print_result("OLLAMA", r)

    # ---- Restore ----
    if not args.skip_restore:
        print("\n" + "=" * 60)
        print("Restoring initial routing...")
        if "ollama" in init_prov.lower():
            apply_routing(base, "ollama", init_model or args.ollama_model)
            wait_runtime(base, "ollama")
        else:
            apply_routing(base, "local")
            wait_runtime(base, "local-llama")
        print("Restored.")

    out_path = "/tmp/context_limit_perf_test.json"
    serializable = {
        k: [
            {
                "name": x.name,
                "ok": x.ok,
                "latency_s": x.latency_s,
                "http_status": x.http_status,
                "error": x.error,
                "usage": x.usage,
                "prompt_chars": x.prompt_chars,
                "completion_preview": x.completion_preview,
            }
            for x in v
        ]
        for k, v in all_results.items()
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"initial": {"provider": init_prov, "model": init_model}, "results": serializable}, f, indent=2)
    print(f"\nWrote {out_path}")

    failed = [r for rows in all_results.values() for r in rows if not r.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
