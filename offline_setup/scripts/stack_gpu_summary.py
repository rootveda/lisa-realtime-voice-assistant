#!/usr/bin/env python3
"""Print GPU totals + stack model table (dialogue LLM, vision, TTS, ASR).

Used by lisa_stack.sh status and end of start_current_stack.sh.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        return subprocess.check_output(cmd, text=True, timeout=timeout, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _curl_json(url: str, timeout: float = 3.0, ssl_ctx: ssl.SSLContext | None = None) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stack_gpu_summary/1"})
        kwargs: dict = {"timeout": timeout}
        if ssl_ctx is not None and url.lower().startswith("https"):
            kwargs["context"] = ssl_ctx
        with urllib.request.urlopen(req, **kwargs) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _nvidia_gpu_totals() -> list[tuple[int, str, int, int, str]]:
    """[(index, name, used_mib, total_mib, util_pct_str), ...]"""
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=5.0,
    )
    rows: list[tuple[int, str, int, int, str]] = []
    for line in out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            idx = int(float(parts[0]))
            used = int(parts[2])
            total = int(parts[3])
        except ValueError:
            continue
        util = parts[4].replace(" ", "")
        rows.append((idx, parts[1], used, total, util))
    return rows


def _nvidia_compute_apps() -> list[tuple[str, str, str]]:
    """[(pid, process_name, used_gpu_memory), ...]"""
    out = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader",
        ],
        timeout=5.0,
    )
    apps: list[tuple[str, str, str]] = []
    reader = csv.reader(io.StringIO(out))
    for row in reader:
        if len(row) >= 3:
            apps.append((row[0].strip(), row[1].strip(), row[2].strip()))
    return apps


def _docker_top_pids(name: str) -> set[str]:
    out = _run(["docker", "top", name, "-eo", "pid"], timeout=4.0)
    pids: set[str] = set()
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line.isdigit():
            pids.add(line)
    return pids


def _ollama_ps_rows() -> list[dict[str, str]]:
    out = _run(["ollama", "ps"], timeout=4.0)
    if not out.strip():
        return []
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    header = re.split(r"\s{2,}", lines[0].strip())
    rows_out: list[dict[str, str]] = []
    for ln in lines[1:]:
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) < len(header):
            continue
        rows_out.append({header[i]: parts[i] for i in range(min(len(header), len(parts)))})
    return rows_out


def _read_stack_mode(path: str = "/tmp/stack_mode.txt") -> dict[str, str]:
    kv: dict[str, str] = {}
    if not os.path.isfile(path):
        return kv
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if "=" in ln:
                k, v = ln.strip().split("=", 1)
                kv[k] = v
    return kv


def _fmt_mib_pair(used: int, total: int) -> str:
    if total <= 0:
        return f"{used} MiB"
    pct = 100.0 * used / total
    return f"{used} / {total} MiB ({pct:.1f}%)"


def _mib_from_nv_str(mem_s: str) -> int:
    m = re.search(r"(\d+)", mem_s)
    return int(m.group(1)) if m else 0


def main() -> int:
    bot_http = os.environ.get("STACK_STATUS_BOT_HTTP", "http://127.0.0.1:7861")
    bot_https = os.environ.get("STACK_STATUS_BOT_HTTPS", "https://127.0.0.1:7860")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    rt = _curl_json(f"{bot_http.rstrip('/')}/api/runtime-status")
    if rt is None:
        rt = _curl_json(f"{bot_https.rstrip('/')}/api/runtime-status", ssl_ctx=ssl_ctx)

    mv = _curl_json(f"{bot_http.rstrip('/')}/api/mobile-voice-vision")
    if mv is None:
        mv = _curl_json(f"{bot_https.rstrip('/')}/api/mobile-voice-vision", ssl_ctx=ssl_ctx)

    stack_mode = _read_stack_mode()
    models = (rt or {}).get("models") or {}
    llm_model = models.get("llm_model") or stack_mode.get("model", "(unknown)")
    llm_provider = models.get("llm_provider") or stack_mode.get("provider", "(unknown)")
    llm_url = models.get("llm_url") or stack_mode.get("url", "")
    llm_ctx = models.get("llm_context_tokens")

    tts_p = models.get("tts_provider", "")
    tts_v = models.get("tts_voice", "")

    vision_env = ((mv or {}).get("environment") or {}) if mv else {}
    vision_model = vision_env.get("VISION_OLLAMA_MODEL") or os.environ.get("VISION_OLLAMA_MODEL") or "gemma4:e2b-it-q4_K_M"
    vision_base = vision_env.get("VISION_OLLAMA_BASE") or os.environ.get("VISION_OLLAMA_BASE") or "http://127.0.0.1:11434/v1"

    containers = ["lisa-llama-primary", "xtts-tts", "nemotron"]
    c_pids: dict[str, set[str]] = {c: _docker_top_pids(c) for c in containers}

    apps = _nvidia_compute_apps()
    mem_by_container: dict[str, int] = {c: 0 for c in containers}
    mem_ollama_host = 0
    mem_other = 0
    other_procs: list[str] = []

    for pid, pname, mem_s in apps:
        mib = _mib_from_nv_str(mem_s)
        placed = False
        for c, ps in c_pids.items():
            if pid in ps:
                mem_by_container[c] += mib
                placed = True
                break
        if not placed:
            if "ollama" in pname.lower():
                mem_ollama_host += mib
            else:
                mem_other += mib
                other_procs.append(f"{pname} pid={pid} {mem_s}")

    gpu_rows = _nvidia_gpu_totals()
    ollama_rows = _ollama_ps_rows()

    print("")
    print("=== GPU (totals) ===")
    if not gpu_rows:
        print("  (nvidia-smi not available or no NVIDIA GPU)")
    else:
        for idx, name, used, total, util in gpu_rows:
            print(f"  GPU {idx}  {name}")
            print(f"    Memory: {_fmt_mib_pair(used, total)}")
            print(f"    Utilization: {util}%")

    # --- table ---
    table: list[tuple[str, str, str, str]] = []

    if llm_provider == "local-llama":
        where = "Docker lisa-llama-primary → " + (llm_url or "http://127.0.0.1:8000/v1")
        if llm_ctx is not None:
            where += f"  ctx={llm_ctx}"
        mib = mem_by_container.get("lisa-llama-primary", 0)
        gpu_mem = f"~{mib} MiB (compute-apps)" if mib else "—"
    else:
        where = "Ollama → " + (llm_url or "http://127.0.0.1:11434/v1")
        mib = mem_ollama_host
        gpu_mem = f"~{mib} MiB (compute-apps)" if mib else "—"

    table.append(("Dialogue LLM", str(llm_model), where, gpu_mem))

    where_v = f"Ollama {vision_base}"
    gpu_mem_v = "not loaded (ollama ps empty)"
    loaded_names: list[str] = []
    for r in ollama_rows:
        nm = r.get("NAME", r.get("name", ""))
        loaded_names.append(nm)
        if nm == vision_model:
            sz = r.get("SIZE", r.get("size", ""))
            proc = r.get("PROCESSOR", r.get("processor", ""))
            gpu_mem_v = f"{sz}  ({proc})" if sz else "—"
    if ollama_rows and gpu_mem_v.startswith("not loaded"):
        r0 = ollama_rows[0]
        gpu_mem_v = f"loaded: {r0.get('NAME', '')} {r0.get('SIZE', '')} (not same tag as VISION_*)"
    extra = ""
    if loaded_names:
        extra = f"  [ollama ps: {', '.join(loaded_names)}]"

    table.append(("Vision / captions", str(vision_model) + extra, where_v, gpu_mem_v))

    xtts_mib = mem_by_container.get("xtts-tts", 0)
    table.append(
        (
            "TTS",
            f"{tts_p or 'xtts'}  voice={tts_v or '—'}",
            "Docker xtts-tts → http://127.0.0.1:80",
            f"~{xtts_mib} MiB (compute-apps)" if xtts_mib else "—",
        )
    )

    asr_mib = mem_by_container.get("nemotron", 0)
    table.append(
        (
            "ASR",
            "Nemotron (ASR-only)",
            "Docker nemotron → http://127.0.0.1:8080",
            f"~{asr_mib} MiB (compute-apps)" if asr_mib else "—",
        )
    )

    if mem_other > 0:
        preview = "; ".join(other_procs[:4])
        if len(other_procs) > 4:
            preview += " …"
        table.append(("Other GPU (unmapped)", "—", preview or "misc", f"~{mem_other} MiB"))

    print("")
    print("=== Models in use & GPU memory (best-effort) ===")
    col_w = (22, 32, 40, 28)
    headers = ("Role", "Model / service", "Where", "GPU memory")
    print(
        f"| {headers[0]:<{col_w[0]}} | {headers[1]:<{col_w[1]}} | {headers[2]:<{col_w[2]}} | {headers[3]:<{col_w[3]}} |"
    )
    print("|" + "|".join("-" * (w + 2) for w in col_w) + "|")
    for role, model, where, gmem in table:
        where_s = where.replace("\n", " ")
        if len(where_s) > col_w[2]:
            where_s = where_s[: col_w[2] - 1] + "…"
        mod_s = str(model)
        if len(mod_s) > col_w[1]:
            mod_s = mod_s[: col_w[1] - 1] + "…"
        gm = str(gmem)
        if len(gm) > col_w[3]:
            gm = gm[: col_w[3] - 1] + "…"
        print(f"| {role:<{col_w[0]}} | {mod_s:<{col_w[1]}} | {where_s:<{col_w[2]}} | {gm:<{col_w[3]}} |")

    print("")
    print("  MiB on stack rows: nvidia-smi compute-apps matched to `docker top` PIDs.")
    print("  Ollama SIZE: from `ollama ps` when models are loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
