# Offline patch for Pipecat runner: empty ICE, path rewriting, offline routes.
# Import this before calling pipecat.runner.run.main() so /client works without STUN/TURN.
# DO NOT override /client with a different UI; keep the full Playground at /client.
#
# PIPECAT_USE_TURN=1: Use local TURN server (run setup_turn_server.sh first) - fixes ICE when host candidates fail.
#
# Security:
#   LISA_BIND_LOOPBACK_ONLY (default "1"): when "1", mutating /api/* calls (POST/PUT/DELETE/PATCH)
#       are accepted only from a loopback peer (127.0.0.1, ::1, ::ffff:127.0.0.1). The host MUST
#       actually bind to 127.0.0.1 for this to be safe (do NOT trust this gate behind a reverse proxy).
#       Set to "0" together with LISA_ADMIN_TOKEN to allow non-loopback callers.
#   LISA_ADMIN_TOKEN: optional shared bearer token. When set, mutating /api/* calls are accepted from
#       any peer that supplies "Authorization: Bearer <token>". If LISA_BIND_LOOPBACK_ONLY is "1" the
#       token check is OR'd with loopback (loopback callers do not need to send the token).

from pathlib import Path
import asyncio
import csv
import hmac
import io
import json
import os
import re
import shlex
import subprocess
import threading
import time

from loguru import logger

from pipecat_bots.repo_root import resolve_repo_root

# TURN credentials path (created by setup_turn_server.sh)
_TURN_CREDS_DIR = Path(__file__).resolve().parent.parent.parent

# Filename stem / URL id for presets under docs/instructions (must be safe as a single path segment).
_INSTRUCTION_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")

_SKIP_INSTRUCTION_MD_NAMES = frozenset(
    {"readme.md", "changelog.md", "instructions.md", "license.md", "contributing.md"}
)


def _ollama_origin_from_llm_url(llm_url: str) -> str:
    """http://127.0.0.1:11434/v1 -> http://127.0.0.1:11434"""
    u = (llm_url or "").strip().rstrip("/")
    if u.endswith("/v1"):
        return u[:-3]
    return u


def _context_length_from_ollama_show(data: dict) -> int | None:
    """Read context length from Ollama /api/show JSON (model_info *context_length* keys)."""
    mi = data.get("model_info")
    if not isinstance(mi, dict):
        return None
    best: int | None = None
    for key, val in mi.items():
        if isinstance(key, str) and "context_length" in key.lower():
            try:
                n = int(val)
                if n > 0 and (best is None or n > best):
                    best = n
            except (TypeError, ValueError):
                continue
    return best


def _probe_llm_context_tokens(model: str, provider: str, llm_url: str) -> int | None:
    """Best-effort context window for the active stack (Ollama: /api/show; local llama: env)."""
    pl = (provider or "").lower().strip()
    if pl == "ollama" and model:
        origin = _ollama_origin_from_llm_url(llm_url)
        if origin.startswith("http"):
            try:
                import httpx

                r = httpx.post(f"{origin.rstrip('/')}/api/show", json={"name": model}, timeout=4.0)
                if r.status_code == 200:
                    ctx = _context_length_from_ollama_show(r.json())
                    if ctx and ctx > 0:
                        return ctx
            except Exception as e:
                logger.debug(f"[runtime-status] Ollama context probe failed: {e}")
    if pl in ("local-llama", "local_llama", "local") and model:
        for key in ("LOCAL_LLAMA_CTX_SIZE", "LLM_CONTEXT_SIZE", "LLAMA_CTX_SIZE"):
            v = os.environ.get(key)
            if v:
                try:
                    n = int(v)
                    if n > 0:
                        return n
                except ValueError:
                    pass
    return None


def _parse_human_size_to_bytes(text: str) -> int | None:
    s = str(text or "").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([kmgt]?b)$", s)
    if not m:
        return None
    n = float(m.group(1))
    u = m.group(2)
    mul = {
        "b": 1,
        "kb": 1024,
        "mb": 1024**2,
        "gb": 1024**3,
        "tb": 1024**4,
    }.get(u)
    if not mul:
        return None
    return int(n * mul)


def _ollama_model_sizes_bytes() -> dict[str, int]:
    out = {}
    try:
        r = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return out
        lines = (r.stdout or "").splitlines()
        for ln in lines[1:]:
            row = ln.strip()
            if not row:
                continue
            parts = re.split(r"\s{2,}", row)
            if len(parts) < 3:
                continue
            name = parts[0].strip()
            size_txt = parts[2].strip()
            b = _parse_human_size_to_bytes(size_txt)
            if name and isinstance(b, int) and b > 0:
                out[name] = b
    except Exception:
        pass
    return out


def _local_llama_model_file_size_bytes() -> int | None:
    candidates = []
    env_fp = (os.environ.get("LOCAL_LLAMA_MODEL_FILE") or "").strip()
    if env_fp:
        candidates.append(Path(env_fp))
    # Default used in offline_setup/start_current_stack.sh.
    candidates.append(resolve_repo_root() / "offline_setup" / "models" / "gemma-4-26B-A4B-it-Q4_K_M.gguf")
    for p in candidates:
        try:
            if p.is_file():
                st = p.stat()
                if st.st_size > 0:
                    return int(st.st_size)
        except Exception:
            continue
    return None


def _dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        if not path.exists():
            return 0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except Exception:
                continue
    except Exception:
        return 0
    return total


def _xtts_model_size_bytes() -> int | None:
    """Best-effort on-disk XTTS assets size from configured cache/data dirs."""
    candidates = []
    for env_key in ("XTTS_TTS_DATA_DIR", "XTTS_HF_CACHE_DIR"):
        raw = (os.environ.get(env_key) or "").strip()
        if raw:
            candidates.append(Path(raw))
    repo_default_data = resolve_repo_root() / "offline_setup" / "xtts_tts_data"
    repo_default_hf = resolve_repo_root() / "offline_setup" / "xtts_hf_cache"
    candidates.extend([repo_default_data, repo_default_hf])
    seen = set()
    total = 0
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        total += _dir_size_bytes(rp)
    return int(total) if total > 0 else None


def _nemotron_stt_model_size_bytes() -> int | None:
    """Best-effort on-disk size of Nemotron ASR model cache."""
    candidates = []
    root_override = (os.environ.get("HF_CACHE_ROOT_OVERRIDE") or "").strip()
    if root_override:
        candidates.append(Path(root_override) / "hub" / "models--nvidia--nemotron-speech-streaming-en-0.6b")
    # Common defaults in this repo/scripts.
    candidates.append(resolve_repo_root() / "offline_setup" / "hf_cache" / "hub" / "models--nvidia--nemotron-speech-streaming-en-0.6b")
    # General user cache fallback.
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub" / "models--nvidia--nemotron-speech-streaming-en-0.6b")
    seen = set()
    best = 0
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        sz = _dir_size_bytes(rp)
        if sz > best:
            best = sz
    return int(best) if best > 0 else None


def _app_bundled_instructions_dir() -> Path:
    """Bundled with `offline_setup/app/` — used when repo `docs/instructions/` is missing at runtime."""
    return Path(__file__).resolve().parent.parent / "presets" / "instructions"


def _instruction_directories() -> list:
    """Prefer repo `docs/instructions/` (editable), then app presets (always shipped next to the bot)."""
    dirs = []
    repo_dir = resolve_repo_root() / "docs" / "instructions"
    bundled = _app_bundled_instructions_dir()
    for d in (repo_dir, bundled):
        if d.is_dir():
            dirs.append(d)
    return dirs


def _repo_instructions_dir() -> Path:
    return resolve_repo_root() / "docs" / "instructions"


def _instruction_source(path: Path | None) -> str:
    if not path:
        return "missing"
    try:
        repo_dir = _repo_instructions_dir().resolve()
        p = path.resolve()
        if p.parent == repo_dir:
            return "repo"
    except Exception:
        pass
    return "bundled"


def _instruction_title_line(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                return s.lstrip("#").strip()
            return (s[:117] + "…") if len(s) > 120 else s
    except Exception:
        pass
    return path.stem


def _list_instruction_items():
    """Collect *.md presets from docs/instructions then bundled presets (repo wins per id).

    IDs come from the basename without `.md` (e.g. ``instruction1.md`` → ``instruction1``).
    Skips common non-preset filenames (README, etc.).
    """
    items_by_id = {}
    for d in _instruction_directories():
        for p in sorted(d.glob("*.md")):
            if not p.is_file():
                continue
            if p.name.lower() in _SKIP_INSTRUCTION_MD_NAMES:
                continue
            stem = p.stem
            if not _INSTRUCTION_ID_RE.match(stem):
                continue
            if stem in items_by_id:
                continue
            items_by_id[stem] = {
                "id": stem,
                "label": _instruction_title_line(p),
                "filename": p.name,
            }
    return [items_by_id[k] for k in sorted(items_by_id.keys())]


def _default_instruction_id(items):
    ids = [x["id"] for x in items]
    if "instruction1" in ids:
        return "instruction1"
    return ids[0] if ids else ""


def _resolve_instruction_path(inst_id: str):
    if not inst_id or not _INSTRUCTION_ID_RE.match(inst_id):
        return None
    for d in _instruction_directories():
        p = d / f"{inst_id}.md"
        if p.is_file():
            return p
    return None


def _read_instruction_file(inst_id: str):
    """Returns (full_prompt_text, label). Empty prompt if not found."""
    p = _resolve_instruction_path(inst_id)
    if not p:
        return "", inst_id
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text, _instruction_title_line(p)
    except Exception as e:
        logger.warning(f"[offline-patch] Could not read instruction {inst_id}: {e}")
        return "", inst_id


def _read_instruction_bundled(inst_id: str):
    """Shipped presets under app/presets/instructions/ — used when repo docs are absent."""
    if not inst_id or not _INSTRUCTION_ID_RE.match(inst_id):
        return "", inst_id
    p = _app_bundled_instructions_dir() / f"{inst_id}.md"
    if not p.is_file():
        return "", inst_id
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text, _instruction_title_line(p)
    except Exception as e:
        logger.warning(f"[offline-patch] Could not read bundled instruction {inst_id}: {e}")
        return "", inst_id


def _legacy_instructions_md() -> Path:
    return resolve_repo_root() / "docs" / "Instructions.md"


def _is_instructions_md_stub(text: str) -> bool:
    """docs/Instructions.md may be a pointer only — never use as live system prompt."""
    raw = text or ""
    low = raw.lower()
    if len(raw) > 1200:
        return False
    if "not the live system prompt" in low:
        return True
    if "documentation only" in low and "docs/instructions" in low:
        return True
    if "legacy path" in low and "docs/instructions" in low:
        return True
    if "saved instructions" in low and "pick a preset" in low:
        return True
    return False


# aioice excludes 127.0.0.1 by default; add it for localhost connections
def _patch_aioice_loopback():
    try:
        from aioice import ice
        _orig = ice.get_host_addresses
        def _patched(use_ipv4, use_ipv6):
            addrs = _orig(use_ipv4, use_ipv6)
            if use_ipv4 and "127.0.0.1" not in addrs:
                addrs = ["127.0.0.1"] + addrs
            return addrs
        ice.get_host_addresses = _patched
        logger.info("[offline-patch] aioice: including 127.0.0.1 for localhost")
    except Exception as e:
        logger.warning(f"[offline-patch] aioice patch: {e}")


class _ReplayFirstBytesWebSocket:
    """Replay first binary frame so client-ready is parsed by transport."""

    def __init__(self, inner, first: bytes):
        self._inner = inner
        self._first = first

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def iter_bytes(self):
        yield self._first
        async for chunk in self._inner.iter_bytes():
            yield chunk


# Apply patch on import
_APPLIED_IN_PROCESS = False

_ASSISTANT_CONSOLE_POWER_LOCK = threading.Lock()

# Persistence guardrails (Phase 3): cap on disk + cap on inbound body size.
_ASSISTANT_CONSOLE_POWER_MAX_BYTES = 256 * 1024  # 256 KiB; the SPA payload is rolling samples
_ASSISTANT_CONSOLE_POWER_ALLOWED_KEYS = frozenset(
    {"powerStats", "powerSession", "gpuTimeStats", "gpuTimeSession", "version", "updatedAt"}
)


def _assistant_console_power_stats_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assistant_console_power_stats.v1.json"


def _coerce_power_stats_payload(body: object) -> tuple[dict, str]:
    """Return (payload, error). Drops unknown keys, rejects non-numeric leaf values for known keys."""
    if not isinstance(body, dict):
        return {}, "expected JSON object"
    payload: dict = {}
    for key in _ASSISTANT_CONSOLE_POWER_ALLOWED_KEYS:
        if key not in body:
            continue
        v = body[key]
        if key in ("version", "updatedAt"):
            if isinstance(v, (str, int, float)):
                payload[key] = v
            continue
        if not isinstance(v, dict):
            continue
        # Drop any nested dict that explodes JSON size (defense in depth).
        try:
            blob = json.dumps(v, default=str)
        except (TypeError, ValueError):
            continue
        if len(blob) > _ASSISTANT_CONSOLE_POWER_MAX_BYTES:
            return {}, f"section '{key}' exceeds {_ASSISTANT_CONSOLE_POWER_MAX_BYTES // 1024} KiB cap"
        payload[key] = v
    return payload, ""


def _load_assistant_console_power_stats() -> dict:
    p = _assistant_console_power_stats_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[assistant-console] power stats read failed: {}", e)
        return {}


def _save_assistant_console_power_stats(payload: dict) -> None:
    p = _assistant_console_power_stats_path()
    tmp = p.with_name(p.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if len(text.encode("utf-8")) > _ASSISTANT_CONSOLE_POWER_MAX_BYTES:
        raise ValueError(f"power stats payload exceeds {_ASSISTANT_CONSOLE_POWER_MAX_BYTES} bytes")
    with _ASSISTANT_CONSOLE_POWER_LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(p)


def _fin_power_num(x: object, default: float = 0.0) -> float:
    try:
        n = float(x)  # type: ignore[arg-type]
        return n if n == n else default  # NaN guard
    except (TypeError, ValueError):
        return default


def _merge_assistant_console_power_stats(incoming: dict, existing: dict) -> dict:
    """Merge client POST with on-disk totals using max() so restarts / stale tabs cannot wipe history."""
    out = dict(existing) if isinstance(existing, dict) else {}
    if not isinstance(incoming, dict):
        return out

    def _merge_section(section_key: str, merge_fn) -> None:
        inc = incoming.get(section_key)
        if not isinstance(inc, dict):
            return
        cur = out.get(section_key)
        if not isinstance(cur, dict):
            cur = {}
        out[section_key] = merge_fn(cur, inc)

    def _merge_power_stats(cur: dict, inc: dict) -> dict:
        merged = dict(cur)
        for k in ("dayKey", "monthKey", "yearKey"):
            if k in inc and isinstance(inc[k], str):
                merged[k] = inc[k]
        for k in ("dayWh", "monthWh", "yearWh", "totalWh"):
            if k in inc:
                merged[k] = max(_fin_power_num(cur.get(k)), _fin_power_num(inc.get(k)))
        return merged

    def _merge_power_session(cur: dict, inc: dict) -> dict:
        merged = dict(cur)
        if "startedAtSec" in inc:
            a = _fin_power_num(cur.get("startedAtSec"), default=float("inf"))
            b = _fin_power_num(inc.get("startedAtSec"), default=float("inf"))
            merged["startedAtSec"] = min(a, b) if a != float("inf") or b != float("inf") else b
        if "wh" in inc:
            merged["wh"] = max(_fin_power_num(cur.get("wh")), _fin_power_num(inc.get("wh")))
        if "lastSampleSec" in inc:
            a = cur.get("lastSampleSec")
            b = inc.get("lastSampleSec")
            if a is not None and b is not None:
                merged["lastSampleSec"] = max(_fin_power_num(a), _fin_power_num(b))
            elif b is not None:
                merged["lastSampleSec"] = _fin_power_num(b)
        return merged

    def _merge_gpu_time_stats(cur: dict, inc: dict) -> dict:
        merged = dict(cur)
        for k in ("dayKey", "monthKey", "yearKey"):
            if k in inc and isinstance(inc[k], str):
                merged[k] = inc[k]
        for k in ("dayBusySec", "monthBusySec", "yearBusySec", "totalBusySec"):
            if k in inc:
                merged[k] = max(_fin_power_num(cur.get(k)), _fin_power_num(inc.get(k)))
        return merged

    def _merge_gpu_time_session(cur: dict, inc: dict) -> dict:
        merged = dict(cur)
        if "busySec" in inc:
            merged["busySec"] = max(_fin_power_num(cur.get("busySec")), _fin_power_num(inc.get("busySec")))
        return merged

    _merge_section("powerStats", _merge_power_stats)
    _merge_section("powerSession", _merge_power_session)
    _merge_section("gpuTimeStats", _merge_gpu_time_stats)
    _merge_section("gpuTimeSession", _merge_gpu_time_session)
    return out


_CONTEXT_STRESS_LOCK = threading.Lock()
_CONTEXT_STRESS_LIVE: dict = {"active": False, "updated_at": 0.0}


def _merge_context_stress_live(patch: dict) -> None:
    with _CONTEXT_STRESS_LOCK:
        for k, v in patch.items():
            if k == "updated_at":
                continue
            if v is None and k not in ("gpu_line", "error_hint"):
                continue
            _CONTEXT_STRESS_LIVE[k] = v
        _CONTEXT_STRESS_LIVE["updated_at"] = time.time()


_CONTEXT_STRESS_RUN_LOCK = threading.Lock()
_CONTEXT_STRESS_RUN_PROC: subprocess.Popen | None = None
_CONTEXT_STRESS_RUN_LOG_PATH: Path | None = None
_CONTEXT_STRESS_RUN_STDOUT: object | None = None


def _stress_run_cleanup_nolock() -> None:
    global _CONTEXT_STRESS_RUN_PROC, _CONTEXT_STRESS_RUN_STDOUT
    if _CONTEXT_STRESS_RUN_PROC is None:
        return
    if _CONTEXT_STRESS_RUN_PROC.poll() is None:
        return
    try:
        if _CONTEXT_STRESS_RUN_STDOUT is not None:
            fh = _CONTEXT_STRESS_RUN_STDOUT
            close = getattr(fh, "close", None)
            if callable(close):
                close()
    except Exception:
        pass
    _CONTEXT_STRESS_RUN_STDOUT = None
    _CONTEXT_STRESS_RUN_PROC = None


def _context_stress_run_client_host(request) -> str:
    """Direct peer only (do not trust X-Forwarded-For for loopback gating)."""
    return (request.client.host if request.client else "") or ""


def _context_stress_cleanup_if_done() -> None:
    with _CONTEXT_STRESS_RUN_LOCK:
        _stress_run_cleanup_nolock()


def _context_stress_run_allowed(request) -> tuple[bool, str]:
    if os.environ.get("ENABLE_CONTEXT_STRESS_RUN_API") != "1":
        return False, "Context stress run API is disabled. Set ENABLE_CONTEXT_STRESS_RUN_API=1 on the bot process."
    token = (os.environ.get("CONTEXT_STRESS_RUN_TOKEN") or "").strip()
    if token:
        if request.headers.get("X-Context-Stress-Token") != token:
            return False, "Missing or invalid X-Context-Stress-Token."
        return True, ""
    host = _context_stress_run_client_host(request)
    if host in ("127.0.0.1", "::1", "localhost"):
        return True, ""
    if host.startswith("::ffff:127.0.0.1"):
        return True, ""
    if host == "testclient" and os.environ.get("LISA_ALLOW_TEST_CLIENT_HOST") == "1":
        return True, ""
    return (
        False,
        "Refused: client is not loopback. Set CONTEXT_STRESS_RUN_TOKEN and send header X-Context-Stress-Token, or use from 127.0.0.1.",
    )


def _context_stress_run_base_url() -> tuple[str, bool]:
    raw = (os.environ.get("CONTEXT_STRESS_BASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw, raw.lower().startswith("https://")
    port = (
        os.environ.get("BOT_LISTEN_PORT")
        or os.environ.get("HTTP_BOT_PORT")
        or os.environ.get("PIPECAT_PORT")
        or "7861"
    ).strip()
    if not port.isdigit():
        port = "7861"
    return f"http://127.0.0.1:{port}", False


def _spawn_context_stress_subprocess(max_steps: int, initial_chars: int) -> tuple[bool, str, int | None]:
    global _CONTEXT_STRESS_RUN_PROC, _CONTEXT_STRESS_RUN_LOG_PATH, _CONTEXT_STRESS_RUN_STDOUT
    app_dir = Path(__file__).resolve().parent.parent
    script = app_dir / "scripts" / "context_max_stress_test.py"
    if not script.is_file():
        return False, f"Stress script not found: {script}", None
    base_url, use_https = _context_stress_run_base_url()
    cmd: list[str] = [
        "uv",
        "run",
        "python",
        str(script),
        "--base-url",
        base_url,
        "--max-steps",
        str(max_steps),
        "--initial-chars",
        str(initial_chars),
    ]
    if use_https:
        cmd.append("--insecure")
    logs_dir = app_dir / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Cannot create logs dir: {e}", None
    log_path = logs_dir / "context_stress_run.log"
    with _CONTEXT_STRESS_RUN_LOCK:
        _stress_run_cleanup_nolock()
        if _CONTEXT_STRESS_RUN_PROC is not None and _CONTEXT_STRESS_RUN_PROC.poll() is None:
            return False, "A context stress run is already in progress.", _CONTEXT_STRESS_RUN_PROC.pid
        lf = None
        try:
            lf = open(log_path, "a", encoding="utf-8", buffering=1)
            lf.write(f"\n==== context stress {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n")
            lf.write(f"cmd: {' '.join(shlex.quote(c) for c in cmd)}\n")
            lf.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=str(app_dir),
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            lf.write(f"pid: {proc.pid}\n")
            lf.flush()
            _CONTEXT_STRESS_RUN_PROC = proc
            _CONTEXT_STRESS_RUN_LOG_PATH = log_path
            _CONTEXT_STRESS_RUN_STDOUT = lf  # ownership transferred to globals
            lf = None
            logger.info("[context-stress] spawned stress test pid={} log={}", proc.pid, log_path)
            return True, str(log_path), proc.pid
        except Exception as e:
            logger.warning("[context-stress] spawn failed: {}", e)
            return False, str(e), None
        finally:
            if lf is not None:
                try:
                    lf.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Security: admin auth gate + response security headers (Phase 1)
# ---------------------------------------------------------------------------

_LISA_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})

# Methods that mutate state. /api/* with these methods passes through the auth gate.
_LISA_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# Public read-only path prefixes that the SPA needs without auth.
# (These are GET-only by route definition; the gate only fires on mutating methods anyway.)
_LISA_PUBLIC_API_PREFIXES = (
    "/api/mobile-voice",            # GET only
    "/api/mobile-voice-vision",     # GET only
    "/api/host-ip",                 # GET only
    "/api/runtime-status",          # GET only
)

# Console user operations (chat, attachments, power-stats mirror, vision preview) — no admin
# token even when LISA_BIND_LOOPBACK_ONLY=0. Admin-only routes (routing apply, stack restart,
# RAG ingest/remove/upload) still require Bearer LISA_ADMIN_TOKEN.
_LISA_USER_API_PREFIXES = (
    "/api/text-chat/completions",
    "/api/chat/attachments",
    "/api/assistant-console/power-stats",
    "/api/assistant-console/chat-sessions",
    "/api/vision/preview",
    "/api/context-stress/live",
)


def _lisa_admin_token() -> str:
    return (os.environ.get("LISA_ADMIN_TOKEN") or "").strip()


def _lisa_loopback_only() -> bool:
    val = (os.environ.get("LISA_BIND_LOOPBACK_ONLY") or "1").strip().lower()
    return val not in ("0", "false", "no", "off", "")


def _lisa_is_loopback_peer(request) -> bool:
    host = (request.client.host if request.client else "") or ""
    if host in _LISA_LOOPBACK_HOSTS:
        return True
    if host.startswith("::ffff:127."):
        return True
    # Starlette TestClient reports peer host as "testclient"; allow only when explicitly
    # opted in (CI/test fixtures set LISA_ALLOW_TEST_CLIENT_HOST=1).
    if host == "testclient" and os.environ.get("LISA_ALLOW_TEST_CLIENT_HOST") == "1":
        return True
    return False


def _lisa_bearer_token_from_headers(request) -> str:
    auth = request.headers.get("authorization") or ""
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    direct = request.headers.get("x-lisa-admin-token") or ""
    return direct.strip()


def _lisa_admin_auth_check(request) -> tuple[bool, str, int]:
    """Return (ok, error_message, status_code) for a mutating /api/* call.

    Defense in depth: route-level checks (e.g. `_context_stress_run_allowed`) still run after
    this gate passes.
    """
    token = _lisa_admin_token()
    loopback_ok = _lisa_loopback_only() and _lisa_is_loopback_peer(request)
    if loopback_ok:
        return True, "", 200
    if token:
        supplied = _lisa_bearer_token_from_headers(request)
        if supplied and hmac.compare_digest(supplied, token):
            return True, "", 200
        if not supplied:
            return False, "Missing Authorization: Bearer <token> header.", 401
        return False, "Invalid admin token.", 403
    if _lisa_loopback_only():
        return False, "Refused: this endpoint is loopback-only. Connect from 127.0.0.1.", 403
    return (
        False,
        "Refused: LISA_BIND_LOOPBACK_ONLY=0 but no LISA_ADMIN_TOKEN configured. Set a token to enable remote admin.",
        503,
    )


def _lisa_should_gate(path: str, method: str) -> bool:
    if method.upper() not in _LISA_MUTATING_METHODS:
        return False
    if not path.startswith("/api/"):
        return False
    for prefix in _LISA_PUBLIC_API_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return False
    for prefix in _LISA_USER_API_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return False
    return True


def _lisa_admin_token_required() -> bool:
    """True when mutating /api/* admin routes need Authorization: Bearer."""
    return bool(_lisa_admin_token()) and not _lisa_loopback_only()


# Strict-but-pragmatic CSP: keeps inline <script>/<style> working (the SPA depends on them)
# while blocking framing, plugins, mixed-origin script loads, and base-tag hijacks.
_LISA_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: data:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:; "
    "frame-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_LISA_CSP_EMBEDDABLE = _LISA_CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")

# Permissions used by the SPA: camera/mic/screen-capture for the assistant console.
_LISA_PERMISSIONS_POLICY = (
    "camera=(self), microphone=(self), display-capture=(self), "
    "geolocation=(), payment=(), usb=(), bluetooth=()"
)


def _lisa_apply_security_headers(response, *, is_html: bool, embeddable: bool = False) -> None:
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    if is_html:
        headers.setdefault(
            "Content-Security-Policy",
            _LISA_CSP_EMBEDDABLE if embeddable else _LISA_CSP,
        )
        headers.setdefault("X-Frame-Options", "SAMEORIGIN" if embeddable else "DENY")
        headers.setdefault("Permissions-Policy", _LISA_PERMISSIONS_POLICY)
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")


def _apply():
    global _APPLIED_IN_PROCESS
    _patch_aioice_loopback()
    if _APPLIED_IN_PROCESS:
        # Already applied in this Python process (e.g. multiple imports).
        # Do not use env var checks here: env is inherited across process restarts.
        return
    _APPLIED_IN_PROCESS = True

    import pipecat.runner.run as run_mod
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler
    from fastapi import File, Form, UploadFile
    from starlette.requests import Request
    from starlette.responses import Response

    # Patch: wait for server ICE gathering so answer includes candidates (fixes no-audio for remote clients)
    _orig_handle_web_request = SmallWebRTCRequestHandler.handle_web_request

    async def _patched_handle_web_request(self, request, webrtc_connection_callback):
        result = await _orig_handle_web_request(self, request, webrtc_connection_callback)
        if result and (pc_id := result.get("pc_id")):
            conn = self._pcs_map.get(pc_id)
            if conn and hasattr(conn, "pc"):
                pc = conn.pc
                if pc.iceGatheringState != "complete":
                    loop = asyncio.get_running_loop()
                    fut = loop.create_future()

                    def on_gathering():
                        if not fut.done() and pc.iceGatheringState == "complete":
                            loop.call_soon_threadsafe(fut.set_result, None)
                    pc.on("icegatheringstatechange", on_gathering)
                    if pc.iceGatheringState != "complete":
                        try:
                            await asyncio.wait_for(asyncio.shield(fut), timeout=8.0)
                        except asyncio.TimeoutError:
                            logger.debug("[offline-patch] ICE gathering timeout, returning answer anyway")
                ld = pc.localDescription
                if ld:
                    result = {"sdp": ld.sdp, "type": ld.type, "pc_id": pc_id}
                    logger.debug("[offline-patch] Answer refreshed with ICE candidates")
        return result

    SmallWebRTCRequestHandler.handle_web_request = _patched_handle_web_request

    # Patch: normalize ICE candidate string (browser may send "candidate:xxx", aiortc expects "xxx")
    from pipecat.transports.smallwebrtc.request_handler import (
        SmallWebRTCPatchRequest,
        IceCandidate,
    )

    _orig_handle_patch = SmallWebRTCRequestHandler.handle_patch_request

    async def _patched_handle_patch(self, request: SmallWebRTCPatchRequest):
        normalized = []
        for c in request.candidates:
            raw = c.candidate or ""
            if isinstance(raw, str) and raw.strip().lower().startswith("candidate:"):
                raw = raw.split(":", 1)[1].strip()
            normalized.append(IceCandidate(candidate=raw, sdp_mid=c.sdp_mid, sdp_mline_index=c.sdp_mline_index))
        request = SmallWebRTCPatchRequest(pc_id=request.pc_id, candidates=normalized)
        return await _orig_handle_patch(self, request)

    SmallWebRTCRequestHandler.handle_patch_request = _patched_handle_patch

    # Patch handler to use TURN when PIPECAT_USE_TURN=1 (fixes ICE when host candidates fail)
    from pipecat.transports.smallwebrtc.connection import IceServer as RTCIceServer

    _orig_handler_init = SmallWebRTCRequestHandler.__init__

    def _patched_handler_init(self, ice_servers=None, **kwargs):
        if ice_servers is None and os.environ.get("PIPECAT_USE_TURN") == "1":
            user_file = _TURN_CREDS_DIR / ".turn_user"
            pass_file = _TURN_CREDS_DIR / ".turn_pass"
            if user_file.exists() and pass_file.exists():
                ice_servers = [
                    RTCIceServer(
                        urls=["turn:127.0.0.1:3478"],
                        username=user_file.read_text().strip(),
                        credential=pass_file.read_text().strip(),
                    )
                ]
                logger.info("[offline-patch] Using local TURN server for ICE")
        _orig_handler_init(self, ice_servers=ice_servers, **kwargs)

    SmallWebRTCRequestHandler.__init__ = _patched_handler_init

    _orig_setup = run_mod._setup_webrtc_routes
    _static_dir = Path(__file__).resolve().parent.parent / "static"

    async def _offline_dispatch(request: Request, call_next):
        scope = request.scope
        path = scope.get("path") or ""
        method = (scope.get("method") or "").upper()
        original_host = request.headers.get("host") or ""
        # Path rewrite: /client/start -> /start, /client/sessions/... -> /sessions/...
        if path == "/client/start" or path.startswith("/client/start?"):
            scope["path"] = "/start" + (path[len("/client/start"):] if len(path) > len("/client/start") else "")
        elif path.startswith("/client/sessions/"):
            scope["path"] = "/sessions/" + path[len("/client/sessions/"):]
        # Normalize trailing slash on session proxy path (e.g. api/offer/ -> api/offer)
        if "/sessions/" in scope["path"]:
            p = scope["path"]
            if p.endswith("/") and "api/offer" in p:
                scope["path"] = p.rstrip("/")
        request._offline_was_start = (scope["path"] == "/start" and scope.get("method") == "POST")

        # Admin auth gate for mutating /api/* (defense in depth; per-route gates still run).
        gate_path = scope.get("path") or path
        if _lisa_should_gate(gate_path, method):
            ok, msg, status = _lisa_admin_auth_check(request)
            if not ok:
                return Response(
                    content=json.dumps({"error": msg}).encode(),
                    status_code=status,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="lisa-admin"'} if status == 401 else None,
                )

        response = await call_next(request)

        # Override /start response: TURN, STUN, or empty ICE
        if getattr(request, "_offline_was_start", False) and response.status_code == 200:
            try:
                body = b""
                if hasattr(response, "body"):
                    body = response.body
                elif hasattr(response, "content"):
                    body = response.content
                elif hasattr(response, "body_iterator"):
                    body = b"".join([c async for c in response.body_iterator if isinstance(c, bytes)])
                if body:
                    data = json.loads(body)
                    use_turn = os.environ.get("PIPECAT_USE_TURN", "0") == "1"
                    use_stun = os.environ.get("PIPECAT_USE_STUN", "0") == "1"
                    if use_turn:
                        user_file = _TURN_CREDS_DIR / ".turn_user"
                        pass_file = _TURN_CREDS_DIR / ".turn_pass"
                        if user_file.exists() and pass_file.exists():
                            data["iceConfig"] = {
                                "iceServers": [{
                                    "urls": ["turn:127.0.0.1:3478"],
                                    "username": user_file.read_text().strip(),
                                    "credential": pass_file.read_text().strip(),
                                }],
                                "iceTransportPolicy": "relay",
                            }
                            logger.info("[offline-patch] POST /start: returning TURN")
                        else:
                            data["iceConfig"] = {"iceServers": [], "iceTransportPolicy": "all"}
                            logger.warning("[offline-patch] TURN requested but .turn_user/.turn_pass not found")
                    elif use_stun:
                        data["iceConfig"] = {
                            "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
                            "iceTransportPolicy": "all",
                        }
                        logger.info("[offline-patch] POST /start: returning STUN")
                    else:
                        data["iceConfig"] = {"iceServers": [], "iceTransportPolicy": "all"}
                        logger.debug("[offline-patch] POST /start: returning empty iceServers")
                    return Response(
                        content=json.dumps(data).encode(),
                        status_code=response.status_code,
                        media_type="application/json",
                    )
            except Exception as e:
                logger.warning(f"[offline-patch] Could not patch /start response: {e}")
        # If backend emits absolute http redirect (e.g. /client -> /client/),
        # rewrite same-host redirects to https for TLS-terminated clients.
        try:
            location = response.headers.get("location")
            if (
                location
                and original_host
                and location.startswith(f"http://{original_host}/")
                and ":7860" in original_host
            ):
                response.headers["location"] = f"https://{original_host}/" + location.split(f"http://{original_host}/", 1)[1]
        except Exception:
            pass
        # Prevent stale UI/API cache for interactive manager flows.
        try:
            no_store_paths = (
                "/assistant-console",
                "/face-manager",
                "/instructions-manager",
                "/session-manager",
                "/user-guide",
                "/session-memory/store.js",
            )
            no_store_prefixes = (
                "/api/face",
                "/api/instructions",
                "/api/assistant-console/",
                "/api/context-stress/",
                "/api/chat/attachments",
            )
            req_path = path or (request.url.path if request and request.url else "")
            if req_path in no_store_paths or any(req_path.startswith(p) for p in no_store_prefixes):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
        except Exception:
            pass
        try:
            ctype = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            req_path = path or (request.url.path if request and request.url else "")
            embed_diagram = (
                req_path == "/rag-flow"
                and request is not None
                and request.query_params.get("embed") == "diagram"
            )
            _lisa_apply_security_headers(
                response,
                is_html=(ctype == "text/html"),
                embeddable=embed_diagram,
            )
        except Exception:
            pass
        return response

    def _patched_setup(app, *args, **kwargs):
        _orig_setup(app, *args, **kwargs)
        from starlette.middleware.base import BaseHTTPMiddleware
        app.add_middleware(BaseHTTPMiddleware, dispatch=_offline_dispatch)
        token_set = bool(_lisa_admin_token())
        loopback_only = _lisa_loopback_only()
        if loopback_only and not token_set:
            mode = "loopback-only (default; no remote admin)"
        elif loopback_only and token_set:
            mode = "loopback-only OR token (LISA_ADMIN_TOKEN set)"
        elif (not loopback_only) and token_set:
            mode = "token-required (LISA_BIND_LOOPBACK_ONLY=0, LISA_ADMIN_TOKEN set)"
        else:
            mode = "FAIL-CLOSED (LISA_BIND_LOOPBACK_ONLY=0 with no LISA_ADMIN_TOKEN — mutating /api/* blocked)"
        logger.info("[lisa-security] mutating /api/* gate: {}", mode)
        # Serve offline routes from app static dir
        if _static_dir.exists():
            from fastapi.responses import FileResponse, RedirectResponse

            @app.get("/offline-voice", include_in_schema=False)
            async def offline_voice():
                f = _static_dir / "offline_voice_client.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return Response(content="offline_voice_client.html not found", status_code=404)

            @app.get("/diagnose", include_in_schema=False)
            async def diagnose():
                f = _static_dir / "diagnose.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return Response(content="diagnose.html not found", status_code=404)

            @app.get("/api/models", include_in_schema=False)
            async def api_models():
                """Return models used by the voice pipeline with params and VRAM."""
                tts_provider = os.environ.get("TTS_PROVIDER", "xtts").lower()
                tts_info = {
                    "xtts": {"name": "XTTS v2", "detail": "Coqui XTTS multilingual", "params_b": 1.8, "vram_gb": "~2.8"},
                    "magpie": {"name": "Magpie", "detail": "NVIDIA Magpie streaming TTS", "params_b": 0.5, "vram_gb": "~2"},
                    "kokoro": {"name": "Kokoro", "detail": "Kokoro-FastAPI", "params_b": 0.1, "vram_gb": "~1"},
                    "csm": {"name": "CSM-1B", "detail": "Sesame CSM-1B", "params_b": 1.0, "vram_gb": "~2"},
                }.get(tts_provider, {"name": tts_provider, "detail": "", "params_b": None, "vram_gb": "—"})
                # Live GPU memory if nvidia-smi available
                vram_used = None
                try:
                    r = __import__("subprocess").run(
                        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        mb = int(r.stdout.strip().split("\n")[0].strip().split()[0])
                        vram_used = round(mb / 1024, 1)
                except Exception:
                    pass
                return Response(
                    content=json.dumps({
                        "asr": {"name": "Nemotron Parakeet", "detail": "NVIDIA streaming ASR", "params_b": 0.6, "vram_gb": "~2"},
                        "llm": {"name": "Nemotron-3-Nano-30B", "detail": "30B total, 3.5B active (MoE) Q4", "params_b": 30, "vram_gb": "~18"},
                        "tts": tts_info,
                        "vram_used_gb": vram_used,
                    }).encode(),
                    media_type="application/json",
                )

            def _collect_host_urls(port: int) -> list[str]:
                urls: list[str] = []
                try:
                    r = subprocess.run(
                        ["ip", "-4", "addr", "show", "dummy0"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if r.returncode == 0 and "169.254.2" in r.stdout:
                        urls.append(f"http://169.254.2.1:{port}")
                except Exception:
                    pass
                try:
                    r = subprocess.run(
                        ["hostname", "-I"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        ips = [
                            x.strip()
                            for x in r.stdout.split()
                            if x.strip() and not x.strip().startswith("127.")
                        ]
                        if ips:
                            urls.append(f"http://{ips[0]}:{port}")
                except Exception:
                    pass
                return urls

            @app.get("/api/host-ip", include_in_schema=False)
            async def host_ip(request: Request):
                """Return host IPs for browser fallback when 127.0.0.1 ICE fails."""
                port = request.scope.get("server", (None, 7860))[1] or 7860
                urls = await asyncio.to_thread(_collect_host_urls, port)
                return Response(
                    content=json.dumps({
                        "urls": urls,
                        "dummy_url": urls[0] if urls and "169.254" in urls[0] else None,
                        "lan_url": next((u for u in urls if "169.254" not in u), urls[0] if urls else None),
                    }).encode(),
                    media_type="application/json",
                )

            @app.get("/offline-client", include_in_schema=False)
            async def offline_client():
                f = _static_dir / "offline_client.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return RedirectResponse(url="/client/")

            @app.get("/sw.js", include_in_schema=False)
            async def sw_js():
                f = _static_dir / "sw_offline.js"
                if f.exists():
                    return FileResponse(f, media_type="application/javascript")
                return Response(content="", status_code=404)

            @app.get("/static/call-machine-object-bundle.js", include_in_schema=False)
            async def static_bundle():
                f = _static_dir / "call-machine-object-bundle.js"
                if f.exists():
                    return FileResponse(f, media_type="application/javascript")
                return Response(content="", status_code=404)

            @app.get("/ws-voice", include_in_schema=False)
            async def ws_voice_page():
                """WebSocket voice client - bypasses WebRTC/ICE entirely."""
                f = _static_dir / "ws_voice_client.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return Response(content="ws_voice_client.html not found", status_code=404)

            @app.get("/mobile-voice-test", include_in_schema=False)
            async def mobile_voice_test_page():
                f = _static_dir / "mobile_voice_browser_test.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return Response(content="mobile_voice_browser_test.html not found", status_code=404)

            @app.get("/mobile-voice-vision-test", include_in_schema=False)
            async def mobile_voice_vision_test_page():
                f = _static_dir / "mobile_voice_vision_test.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return Response(content="mobile_voice_vision_test.html not found", status_code=404)

            @app.get("/assistant-console", include_in_schema=False)
            async def assistant_console_page():
                f = _static_dir / "sci_fi_assistant.html"
                if f.exists():
                    return FileResponse(f, media_type="text/html")
                return Response(content="sci_fi_assistant.html not found", status_code=404)

            @app.get("/sci-fi-assistant", include_in_schema=False)
            async def sci_fi_assistant_legacy_redirect():
                return RedirectResponse(url="/assistant-console")

        # Always register (not gated on static/ dir): same host may serve APIs while static was omitted.
        from fastapi.responses import FileResponse as _FileResponse

        @app.get("/architecture-flow", include_in_schema=False)
        @app.get("/architecture_flow", include_in_schema=False)
        async def architecture_flow_page():
            """Animated architecture diagram (looping SVG/CSS); suitable for screen recording."""
            f = _static_dir / "architecture_flow.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="architecture_flow.html not found", status_code=404)

        @app.get("/rag-arena", include_in_schema=False)
        async def rag_arena_page():
            """Full-page offline RAG management (upload, rebuild, stats)."""
            f = _static_dir / "rag_arena.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="rag_arena.html not found", status_code=404)

        @app.get("/rag-flow", include_in_schema=False)
        async def rag_flow_page():
            """RAG-only architecture flow diagram."""
            f = _static_dir / "rag_flow.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="rag_flow.html not found", status_code=404)

        @app.get("/session-manager", include_in_schema=False)
        async def session_manager_page():
            """Session memory management console."""
            f = _static_dir / "session_memory" / "session_manager.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="session_manager.html not found", status_code=404)

        @app.get("/instructions-manager", include_in_schema=False)
        async def instructions_manager_page():
            """Instruction presets management console."""
            f = _static_dir / "instructions_manager.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="instructions_manager.html not found", status_code=404)

        @app.get("/user-guide", include_in_schema=False)
        async def user_guide_page():
            """Operator guide: stack control, components, models, admin, persistence."""
            f = _static_dir / "user_guide.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="user_guide.html not found", status_code=404)

        @app.get("/session-memory/store.js", include_in_schema=False)
        async def session_store_js():
            f = _static_dir / "session_memory" / "session_store.js"
            if f.is_file():
                return _FileResponse(f, media_type="application/javascript")
            return Response(content="session_store.js not found", status_code=404)

        @app.get("/session-memory/face_session_phase.js", include_in_schema=False)
        async def session_face_phase_js():
            f = _static_dir / "session_memory" / "face_session_phase.js"
            if f.is_file():
                return _FileResponse(f, media_type="application/javascript")
            return Response(content="face_session_phase.js not found", status_code=404)

        @app.get("/session-memory/face_phase_harness.html", include_in_schema=False)
        async def session_face_phase_harness_html():
            f = _static_dir / "session_memory" / "face_phase_harness.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="face_phase_harness.html not found", status_code=404)

        @app.get("/session-memory/http_error_detail.js", include_in_schema=False)
        async def session_http_error_detail_js():
            f = _static_dir / "session_memory" / "http_error_detail.js"
            if f.is_file():
                return _FileResponse(f, media_type="application/javascript")
            return Response(content="http_error_detail.js not found", status_code=404)

        @app.get("/lisa-admin-ui.js", include_in_schema=False)
        async def lisa_admin_ui_js():
            f = _static_dir / "lisa_admin_ui.js"
            if f.is_file():
                return _FileResponse(f, media_type="application/javascript")
            return Response(content="lisa_admin_ui.js not found", status_code=404)

        @app.get("/session-memory/text_chat_error_harness.html", include_in_schema=False)
        async def session_text_chat_error_harness_html():
            f = _static_dir / "session_memory" / "text_chat_error_harness.html"
            if f.is_file():
                return _FileResponse(f, media_type="text/html")
            return Response(content="text_chat_error_harness.html not found", status_code=404)

        logger.info(
            "[offline-patch] Static diagram: GET /architecture-flow and /architecture_flow → %s",
            _static_dir / "architecture_flow.html",
        )

        @app.get("/api/assistant-console/power-stats", include_in_schema=False)
        async def assistant_console_power_stats_get():
            data = await asyncio.to_thread(_load_assistant_console_power_stats)
            return Response(
                content=json.dumps(data).encode(),
                media_type="application/json",
            )

        @app.post("/api/assistant-console/power-stats", include_in_schema=False)
        async def assistant_console_power_stats_post(request: Request):
            try:
                raw = await request.body()
            except Exception:
                return Response(
                    content=json.dumps({"error": "could not read body"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            if len(raw) > _ASSISTANT_CONSOLE_POWER_MAX_BYTES * 2:
                return Response(
                    content=json.dumps({
                        "error": f"payload exceeds {_ASSISTANT_CONSOLE_POWER_MAX_BYTES * 2} bytes",
                    }).encode(),
                    status_code=413,
                    media_type="application/json",
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(
                    content=json.dumps({"error": "invalid json"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            payload, err = _coerce_power_stats_payload(body)
            if err:
                return Response(
                    content=json.dumps({"error": err}).encode(),
                    status_code=413 if "cap" in err else 400,
                    media_type="application/json",
                )
            try:
                existing = await asyncio.to_thread(_load_assistant_console_power_stats)
                merged = _merge_assistant_console_power_stats(payload, existing)
                await asyncio.to_thread(_save_assistant_console_power_stats, merged)
            except ValueError as e:
                return Response(
                    content=json.dumps({"error": str(e)}).encode(),
                    status_code=413,
                    media_type="application/json",
                )
            except OSError as e:
                logger.warning("[assistant-console] power stats save failed: {}", e)
                return Response(
                    content=json.dumps({"error": "save failed"}).encode(),
                    status_code=500,
                    media_type="application/json",
                )
            return Response(content=b'{"ok":true}', media_type="application/json")

        @app.get("/api/assistant-console/chat-sessions", include_in_schema=False)
        async def assistant_console_chat_sessions_get():
            from pipecat_bots.assistant_console_chat_sessions import load_chat_sessions

            data = await asyncio.to_thread(load_chat_sessions)
            return Response(
                content=json.dumps(data, default=str).encode(),
                media_type="application/json",
            )

        @app.put("/api/assistant-console/chat-sessions", include_in_schema=False)
        async def assistant_console_chat_sessions_put(request: Request):
            from pipecat_bots.assistant_console_chat_sessions import (
                coerce_chat_sessions_payload,
                load_chat_sessions,
                save_chat_sessions,
            )

            try:
                raw = await request.body()
            except Exception:
                return Response(
                    content=json.dumps({"error": "could not read body"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            if len(raw) > 16 * 1024 * 1024:
                return Response(
                    content=json.dumps({"error": "payload too large"}).encode(),
                    status_code=413,
                    media_type="application/json",
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(
                    content=json.dumps({"error": "invalid json"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            payload, err = coerce_chat_sessions_payload(body)
            if err:
                return Response(
                    content=json.dumps({"error": err}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            try:
                existing = await asyncio.to_thread(load_chat_sessions)
                if int(existing.get("updatedAt") or 0) > int(payload.get("updatedAt") or 0):
                    return Response(
                        content=json.dumps({"ok": True, "skipped": "stale_client"}).encode(),
                        media_type="application/json",
                    )
                await asyncio.to_thread(save_chat_sessions, payload)
            except ValueError as e:
                return Response(
                    content=json.dumps({"error": str(e)}).encode(),
                    status_code=413,
                    media_type="application/json",
                )
            except OSError as e:
                logger.warning("[assistant-console] chat sessions save failed: {}", e)
                return Response(
                    content=json.dumps({"error": "save failed"}).encode(),
                    status_code=500,
                    media_type="application/json",
                )
            return Response(content=b'{"ok":true}', media_type="application/json")

        @app.get("/api/context-stress/live", include_in_schema=False)
        async def context_stress_live_get():
            with _CONTEXT_STRESS_LOCK:
                snap = dict(_CONTEXT_STRESS_LIVE)
            return Response(
                content=json.dumps(snap, default=str).encode(),
                media_type="application/json",
            )

        @app.post("/api/context-stress/live", include_in_schema=False)
        async def context_stress_live_post(request: Request):
            try:
                raw = await request.body()
            except Exception:
                return Response(
                    content=json.dumps({"error": "could not read body"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            # Cap at 64 KiB; the publisher only sends small status snapshots.
            if len(raw) > 64 * 1024:
                return Response(
                    content=json.dumps({"error": "payload exceeds 64 KiB"}).encode(),
                    status_code=413,
                    media_type="application/json",
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(
                    content=json.dumps({"error": "invalid json"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            if not isinstance(body, dict):
                return Response(
                    content=json.dumps({"error": "expected object"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            _merge_context_stress_live(body)
            return Response(content=b'{"ok":true}', media_type="application/json")

        @app.get("/api/admin/capabilities", include_in_schema=False)
        async def admin_capabilities():
            return Response(
                content=json.dumps(
                    {
                        "admin_token_required": _lisa_admin_token_required(),
                        "token_configured": bool(_lisa_admin_token()),
                        "bind_loopback_only": _lisa_loopback_only(),
                    }
                ).encode(),
                media_type="application/json",
            )

        @app.get("/api/context-stress/run/capabilities", include_in_schema=False)
        async def context_stress_run_capabilities():
            tok = (os.environ.get("CONTEXT_STRESS_RUN_TOKEN") or "").strip()
            return Response(
                content=json.dumps(
                    {
                        "run_api_enabled": os.environ.get("ENABLE_CONTEXT_STRESS_RUN_API") == "1",
                        "token_gate": bool(tok),
                    }
                ).encode(),
                media_type="application/json",
            )

        @app.get("/api/context-stress/run/status", include_in_schema=False)
        async def context_stress_run_status(request: Request):
            ok, msg = _context_stress_run_allowed(request)
            if not ok:
                return Response(
                    content=json.dumps({"error": msg}).encode(),
                    status_code=403,
                    media_type="application/json",
                )
            _context_stress_cleanup_if_done()
            with _CONTEXT_STRESS_RUN_LOCK:
                proc = _CONTEXT_STRESS_RUN_PROC
                running = proc is not None and proc.poll() is None
                pid = proc.pid if proc else None
                log_path = str(_CONTEXT_STRESS_RUN_LOG_PATH) if _CONTEXT_STRESS_RUN_LOG_PATH else None
            return Response(
                content=json.dumps({"running": running, "pid": pid, "log_path": log_path}).encode(),
                media_type="application/json",
            )

        @app.post("/api/context-stress/run", include_in_schema=False)
        async def context_stress_run_post(request: Request):
            ok, msg = _context_stress_run_allowed(request)
            if not ok:
                return Response(
                    content=json.dumps({"error": msg}).encode(),
                    status_code=403,
                    media_type="application/json",
                )
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            try:
                max_steps = int(body.get("max_steps", 8))
            except (TypeError, ValueError):
                max_steps = 8
            try:
                initial_chars = int(body.get("initial_chars", 20_000))
            except (TypeError, ValueError):
                initial_chars = 20_000
            max_steps = max(1, min(max_steps, 80))
            initial_chars = max(1_000, min(initial_chars, 900_000))
            ok2, info, pid = await asyncio.to_thread(
                _spawn_context_stress_subprocess, max_steps, initial_chars
            )
            if not ok2:
                if pid is not None and "already in progress" in (info or ""):
                    return Response(
                        content=json.dumps({"error": info, "pid": pid}).encode(),
                        status_code=409,
                        media_type="application/json",
                    )
                return Response(
                    content=json.dumps({"error": info}).encode(),
                    status_code=500,
                    media_type="application/json",
                )
            return Response(
                content=json.dumps({"ok": True, "pid": pid, "log_path": info}).encode(),
                media_type="application/json",
            )

        @app.get("/api/mobile-voice", include_in_schema=False)
        @app.get("/api/mobile-voice/info", include_in_schema=False)
        async def mobile_voice_info(request: Request):
            from pipecat_bots.mobile_voice_endpoint_info import build_mobile_voice_payload

            return Response(
                content=json.dumps(build_mobile_voice_payload(request), indent=2, default=str).encode(),
                media_type="application/json",
            )

        @app.get("/api/mobile-voice-vision", include_in_schema=False)
        @app.get("/api/mobile-voice-vision/info", include_in_schema=False)
        async def mobile_voice_vision_info(request: Request):
            from pipecat_bots.mobile_voice_vision_endpoint_info import build_mobile_voice_vision_payload

            return Response(
                content=json.dumps(build_mobile_voice_vision_payload(request), indent=2, default=str).encode(),
                media_type="application/json",
            )

        @app.get("/api/instructions", include_in_schema=False)
        async def instructions_index():
            items = _list_instruction_items()
            for x in items:
                p = _resolve_instruction_path(x.get("id") or "")
                x["source"] = _instruction_source(p)
                x["writable"] = x["source"] == "repo"
            default_id = _default_instruction_id(items)
            return Response(
                content=json.dumps({"default_id": default_id, "items": items}).encode(),
                media_type="application/json",
            )

        @app.get("/api/instructions/{inst_id}", include_in_schema=False)
        async def instructions_get_one(inst_id: str):
            prompt, label = _read_instruction_file(inst_id)
            source = _instruction_source(_resolve_instruction_path(inst_id))
            if not prompt:
                prompt, label = _read_instruction_bundled(inst_id)
                source = "bundled" if prompt else "missing"
            if not prompt:
                return Response(
                    content=json.dumps({"error": "instruction not found"}).encode(),
                    status_code=404,
                    media_type="application/json",
                )
            return Response(
                content=json.dumps(
                    {
                        "id": inst_id,
                        "label": label,
                        "prompt": prompt,
                        "source": source,
                        "writable": source == "repo",
                    }
                ).encode(),
                media_type="application/json",
            )

        @app.post("/api/instructions", include_in_schema=False)
        async def instructions_create(request: Request):
            payload = await request.json()
            inst_id = str(payload.get("id") or "").strip()
            title = str(payload.get("title") or "").strip()
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                prompt = str(payload.get("content") or "").strip()
            overwrite = bool(payload.get("overwrite"))
            if not inst_id or not _INSTRUCTION_ID_RE.match(inst_id):
                return Response(
                    content=json.dumps(
                        {"ok": False, "error": "id must match ^[a-zA-Z][a-zA-Z0-9_-]{0,127}$"}
                    ).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            if not prompt:
                return Response(
                    content=json.dumps({"ok": False, "error": "prompt is required"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            repo_dir = _repo_instructions_dir()
            repo_dir.mkdir(parents=True, exist_ok=True)
            out = repo_dir / f"{inst_id}.md"
            if out.exists() and not overwrite:
                return Response(
                    content=json.dumps(
                        {"ok": False, "error": "instruction id already exists; set overwrite=true to replace"}
                    ).encode(),
                    status_code=409,
                    media_type="application/json",
                )
            body = prompt
            if title:
                body = f"# {title}\n\n{prompt}"
            out.write_text(body.strip() + "\n", encoding="utf-8")
            return Response(
                content=json.dumps({"ok": True, "id": inst_id, "path": str(out)}).encode(),
                media_type="application/json",
            )

        @app.put("/api/instructions/{inst_id}", include_in_schema=False)
        async def instructions_update(inst_id: str, request: Request):
            if not inst_id or not _INSTRUCTION_ID_RE.match(inst_id):
                return Response(
                    content=json.dumps({"ok": False, "error": "invalid instruction id"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            payload = await request.json()
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                prompt = str(payload.get("content") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not prompt:
                return Response(
                    content=json.dumps({"ok": False, "error": "prompt is required"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            repo_dir = _repo_instructions_dir()
            repo_dir.mkdir(parents=True, exist_ok=True)
            out = repo_dir / f"{inst_id}.md"
            body = prompt
            if title:
                body = f"# {title}\n\n{prompt}"
            out.write_text(body.strip() + "\n", encoding="utf-8")
            return Response(
                content=json.dumps({"ok": True, "id": inst_id, "path": str(out)}).encode(),
                media_type="application/json",
            )

        @app.delete("/api/instructions/{inst_id}", include_in_schema=False)
        async def instructions_delete(inst_id: str):
            if not inst_id or not _INSTRUCTION_ID_RE.match(inst_id):
                return Response(
                    content=json.dumps({"ok": False, "error": "invalid instruction id"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            repo_path = _repo_instructions_dir() / f"{inst_id}.md"
            if not repo_path.is_file():
                return Response(
                    content=json.dumps(
                        {
                            "ok": False,
                            "error": "Only repo-backed instructions can be deleted (bundled presets are read-only).",
                        }
                    ).encode(),
                    status_code=404,
                    media_type="application/json",
                )
            repo_path.unlink(missing_ok=True)
            return Response(content=json.dumps({"ok": True, "id": inst_id}).encode(), media_type="application/json")

        @app.get("/api/default-instructions", include_in_schema=False)
        async def default_instructions(request: Request):
            inst_id = (request.query_params.get("id") or "").strip()
            items = _list_instruction_items()
            if not inst_id:
                inst_id = _default_instruction_id(items)
            prompt, label = _read_instruction_file(inst_id)
            if not prompt and not items:
                try:
                    legacy = _legacy_instructions_md()
                    if legacy.is_file():
                        raw = legacy.read_text(encoding="utf-8").strip()
                        if raw and not _is_instructions_md_stub(raw):
                            prompt = raw
                            label = "Instructions.md (legacy)"
                except Exception as e:
                    logger.warning(f"[offline-patch] Could not read legacy Instructions.md: {e}")
            if not prompt and items:
                inst_id = items[0]["id"]
                prompt, label = _read_instruction_file(inst_id)
            if prompt and _is_instructions_md_stub(prompt):
                prompt, label = "", inst_id
            if not prompt:
                fb, lb = _read_instruction_bundled(inst_id)
                if fb:
                    prompt, label = fb, lb
                else:
                    fb, lb = _read_instruction_bundled("instruction1")
                    if fb:
                        prompt, label = fb, lb
                        inst_id = "instruction1"
            return Response(
                content=json.dumps({"prompt": prompt, "id": inst_id, "label": label}).encode(),
                media_type="application/json",
            )

        def _runtime_status_payload_sync() -> dict:
            def _run(cmd):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                    if r.returncode != 0:
                        return ""
                    return (r.stdout or "").strip()
                except Exception:
                    return ""

            stack_mode = {}
            try:
                mode_path = Path("/tmp/stack_mode.txt")
                if mode_path.exists():
                    for ln in mode_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if "=" in ln:
                            k, v = ln.split("=", 1)
                            stack_mode[k.strip()] = v.strip()
            except Exception:
                pass

            gpus = []
            smi_out = _run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw",
                    "--format=csv,noheader,nounits",
                ]
            )
            if smi_out:
                for parts in csv.reader(io.StringIO(smi_out)):
                    parts = [p.strip() for p in parts]
                    if len(parts) >= 8:
                        gpus.append(
                            {
                                "index": parts[0],
                                "name": parts[1],
                                "temp_c": parts[2],
                                "util_gpu_pct": parts[3],
                                "util_mem_pct": parts[4],
                                "mem_used_mb": parts[5],
                                "mem_total_mb": parts[6],
                                "power_w": parts[7],
                            }
                        )

            model = stack_mode.get("model") or os.environ.get("NVIDIA_LLM_MODEL", "")
            provider = stack_mode.get("provider") or "unknown"
            llm_url = stack_mode.get("url") or os.environ.get("NVIDIA_LLM_URL", "")
            llm_context_tokens = _probe_llm_context_tokens(model, provider, llm_url)
            if not llm_context_tokens or llm_context_tokens <= 0:
                try:
                    from pipecat_bots.llm_context_size import effective_num_ctx

                    llm_context_tokens = effective_num_ctx()
                except Exception:
                    llm_context_tokens = None
            vision_ollama_num_ctx = None
            try:
                from pipecat_bots.llm_context_size import effective_num_ctx

                raw_v = (os.environ.get("VISION_OLLAMA_NUM_CTX") or "").strip()
                if raw_v:
                    vision_ollama_num_ctx = max(2048, int(raw_v))
                else:
                    vision_ollama_num_ctx = effective_num_ctx()
            except (TypeError, ValueError):
                try:
                    from pipecat_bots.llm_context_size import effective_num_ctx

                    vision_ollama_num_ctx = effective_num_ctx()
                except Exception:
                    vision_ollama_num_ctx = None
            except Exception:
                vision_ollama_num_ctx = None
            vision_model = (
                os.environ.get("VISION_OLLAMA_MODEL") or "gemma4:e2b-it-q4_K_M"
            ).strip() or "gemma4:e2b-it-q4_K_M"
            ollama_sizes = _ollama_model_sizes_bytes()
            llm_model_size_bytes = None
            if provider == "local-llama":
                llm_model_size_bytes = _local_llama_model_file_size_bytes()
            elif provider == "ollama":
                llm_model_size_bytes = ollama_sizes.get(model)
            vision_model_size_bytes = ollama_sizes.get(vision_model)
            tts_model_size_bytes = _xtts_model_size_bytes()
            stt_model_size_bytes = _nemotron_stt_model_size_bytes()

            return {
                "models": {
                    "llm_model": model,
                    "llm_provider": provider,
                    "llm_url": llm_url,
                    "llm_context_tokens": llm_context_tokens,
                    "vision_ollama_num_ctx": vision_ollama_num_ctx,
                    "llm_model_size_bytes": llm_model_size_bytes,
                    "vision_model": vision_model,
                    "vision_model_size_bytes": vision_model_size_bytes,
                    "stt_provider": os.environ.get("STT_PROVIDER", "nemotron-websocket"),
                    "stt_model": os.environ.get(
                        "NVIDIA_ASR_MODEL", "nvidia/nemotron-speech-streaming-en-0.6b"
                    ),
                    "stt_model_size_bytes": stt_model_size_bytes,
                    "tts_provider": os.environ.get("TTS_PROVIDER", "xtts"),
                    "tts_voice": os.environ.get("XTTS_VOICE_ID", "Andrew Chipper"),
                    "tts_model_size_bytes": tts_model_size_bytes,
                },
                "gpu": gpus,
            }

        @app.get("/api/runtime-status", include_in_schema=False)
        async def runtime_status():
            payload = await asyncio.to_thread(_runtime_status_payload_sync)
            return Response(
                content=json.dumps(payload).encode(),
                media_type="application/json",
            )

        def _ollama_models_sync() -> list[str]:
            models: list[str] = []
            try:
                r = subprocess.run(
                    ["ollama", "list"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                if r.returncode == 0:
                    lines = (r.stdout or "").splitlines()
                    for ln in lines[1:]:
                        ln = ln.strip()
                        if not ln:
                            continue
                        name = ln.split()[0]
                        if name and name.lower() != "name":
                            models.append(name)
            except Exception:
                pass
            return models

        @app.get("/api/ollama-models", include_in_schema=False)
        async def ollama_models():
            models = await asyncio.to_thread(_ollama_models_sync)
            return Response(
                content=json.dumps({"models": models}).encode(),
                media_type="application/json",
            )

        @app.get("/api/ollama-model-sizes", include_in_schema=False)
        async def ollama_model_sizes():
            sizes = await asyncio.to_thread(_ollama_model_sizes_bytes)
            return Response(
                content=json.dumps({"sizes_bytes": sizes}).encode(),
                media_type="application/json",
            )

        def _apply_llm_routing_sync(provider: str, model: str) -> tuple[int, dict]:
            if provider == "ollama":
                show = subprocess.run(["ollama", "show", model], capture_output=True, text=True, timeout=10)
                if show.returncode != 0:
                    pull = subprocess.run(["ollama", "pull", model], capture_output=True, text=True, timeout=1800)
                    if pull.returncode != 0:
                        return 500, {"ok": False, "error": f"ollama pull failed for {model}"}

            start_script = resolve_repo_root() / "offline_setup" / "start_current_stack.sh"
            env = os.environ.copy()
            if provider == "ollama":
                env["USE_LOCAL_LLAMA_PRIMARY"] = "0"
                env["NVIDIA_LLM_MODEL"] = model
                env["NVIDIA_LLM_URL"] = "http://127.0.0.1:11434/v1"
            else:
                env.pop("NVIDIA_LLM_MODEL", None)
                env.pop("NVIDIA_LLM_URL", None)
                env["USE_LOCAL_LLAMA_PRIMARY"] = "1"

            restarted = subprocess.run(
                [str(start_script)],
                capture_output=True,
                text=True,
                timeout=900,
                cwd=str(start_script.parent),
                env=env,
            )
            if restarted.returncode != 0:
                return 500, {
                    "ok": False,
                    "error": "stack restart failed",
                    "stdout": (restarted.stdout or "")[-1200:],
                    "stderr": (restarted.stderr or "")[-1200:],
                }
            return 200, {
                "ok": True,
                "provider": provider,
                "model": model if provider == "ollama" else "",
            }

        @app.post("/api/llm-routing/apply", include_in_schema=False)
        async def apply_llm_routing(request: Request):
            payload = await request.json()
            provider = str(payload.get("provider") or "").strip().lower()
            model = str(payload.get("model") or "").strip()
            if provider not in {"local", "ollama"}:
                return Response(
                    content=json.dumps({"ok": False, "error": "provider must be local or ollama"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )

            if provider == "ollama" and not model:
                model = "gemma4:e2b-it-q4_K_M"

            status, payload_out = await asyncio.to_thread(_apply_llm_routing_sync, provider, model)
            return Response(
                content=json.dumps(payload_out).encode(),
                status_code=status,
                media_type="application/json",
            )

        @app.post("/api/stack/restart", include_in_schema=False)
        async def stack_restart_full():
            """Schedule offline_setup/start_current_stack.sh in the background (this process exits when script kills bot_vllm)."""
            script = resolve_repo_root() / "offline_setup" / "start_current_stack.sh"
            if not script.is_file():
                return Response(
                    content=json.dumps({"ok": False, "error": "start_current_stack.sh not found"}).encode(),
                    status_code=404,
                    media_type="application/json",
                )
            bundle = script.parent
            delay_sec = 3
            cmd = (
                f"sleep {delay_sec} && cd {shlex.quote(str(bundle))} "
                f"&& exec bash {shlex.quote(script.name)}"
            )
            try:
                subprocess.Popen(
                    ["/bin/bash", "-c", cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    cwd=str(bundle),
                    env=os.environ.copy(),
                )
            except Exception as e:
                logger.exception("[stack-restart] failed to spawn installer")
                return Response(
                    content=json.dumps({"ok": False, "error": str(e)}).encode(),
                    status_code=500,
                    media_type="application/json",
                )
            logger.info("[stack-restart] Scheduled full stack via start_current_stack.sh")
            return Response(
                content=json.dumps(
                    {
                        "ok": True,
                        "message": (
                            f"Full stack restart scheduled (starts in ~{delay_sec}s). "
                            "Reload this page after a few minutes when ASR/XTTS/bot are healthy."
                        ),
                    }
                ).encode(),
                status_code=202,
                media_type="application/json",
            )

        @app.get("/api/local-tools", include_in_schema=False)
        async def api_local_tools():
            from pipecat_bots import local_calendar, local_rag
            from pipecat_bots.local_tool_executor import (
                tool_http_allow_hosts,
                tool_http_allow_private_lan,
                tool_http_allow_schemes,
            )

            from pipecat_bots.asyncio_helpers import run_db_blocking

            le = os.environ.get("LOCAL_TOOLS_ENABLE", "1").strip().lower()
            rag_payload = await run_db_blocking(local_rag.rag_status)
            calendar_payload = await asyncio.to_thread(local_calendar.calendar_status)
            return Response(
                content=json.dumps(
                    {
                        "local_tools_enable_default": le not in ("0", "false", "no"),
                        "tool_http_allow_hosts": sorted(tool_http_allow_hosts()),
                        "tool_http_allow_schemes": sorted(tool_http_allow_schemes()),
                        "tool_http_allow_private_lan": tool_http_allow_private_lan(),
                        "rag": rag_payload,
                        "calendar": calendar_payload,
                    },
                    indent=2,
                ).encode(),
                media_type="application/json",
            )

        @app.get("/api/rag/status", include_in_schema=False)
        async def rag_status_endpoint():
            from pipecat_bots import local_rag
            from pipecat_bots.asyncio_helpers import run_db_blocking

            status = await run_db_blocking(local_rag.rag_status)
            return Response(
                content=json.dumps(status, indent=2).encode(),
                media_type="application/json",
            )

        @app.post("/api/rag/ingest", include_in_schema=False)
        async def rag_ingest_endpoint(request: Request):
            from pipecat_bots import local_rag
            from pipecat_bots.asyncio_helpers import run_db_blocking

            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict) and body.get("reindex_all"):
                out = await run_db_blocking(local_rag.ingest_documents_dir)
                return Response(content=json.dumps(out, default=str).encode(), media_type="application/json")
            return Response(
                content=json.dumps(
                    {"ok": False, "error": 'POST JSON {"reindex_all": true} to ingest offline_setup/rag_data/documents'}
                ).encode(),
                status_code=400,
                media_type="application/json",
            )

        @app.get("/api/rag/files", include_in_schema=False)
        async def rag_files_endpoint(request: Request):
            from pipecat_bots import local_rag
            from pipecat_bots.asyncio_helpers import run_db_blocking

            try:
                limit = int((request.query_params.get("limit") or "500").strip())
            except (TypeError, ValueError, AttributeError):
                limit = 500
            out = await run_db_blocking(local_rag.rag_files_detail, limit=limit)
            return Response(content=json.dumps(out, default=str).encode(), media_type="application/json")

        @app.post("/api/rag/remove", include_in_schema=False)
        async def rag_remove_endpoint(request: Request):
            from pipecat_bots import local_rag
            from pipecat_bots.asyncio_helpers import run_db_blocking

            try:
                body = await request.json()
            except Exception:
                body = None
            p = ""
            if isinstance(body, dict):
                p = str(body.get("path") or "").strip()
            if not p:
                return Response(
                    content=json.dumps({"ok": False, "error": 'POST JSON {"path": "..."}'}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            out = await run_db_blocking(local_rag.remove_rag_file, p)
            code = 200 if out.get("ok") else 400
            return Response(content=json.dumps(out, default=str).encode(), status_code=code, media_type="application/json")

        @app.post("/api/rag/remove-many", include_in_schema=False)
        async def rag_remove_many_endpoint(request: Request):
            from pipecat_bots import local_rag
            from pipecat_bots.asyncio_helpers import run_db_blocking

            try:
                body = await request.json()
            except Exception:
                body = None
            paths = []
            if isinstance(body, dict) and isinstance(body.get("paths"), list):
                for p in body.get("paths"):
                    s = str(p or "").strip()
                    if s:
                        paths.append(s)
            if not paths:
                return Response(
                    content=json.dumps({"ok": False, "error": 'POST JSON {"paths": ["..."]}'}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            seen = set()
            uniq = []
            for p in paths:
                if p not in seen:
                    seen.add(p)
                    uniq.append(p)
            removed = []
            failed = []
            for p in uniq:
                out = await run_db_blocking(local_rag.remove_rag_file, p)
                if out.get("ok"):
                    removed.append(out)
                else:
                    failed.append(out)
            ok = len(failed) == 0
            code = 200 if ok else 207
            return Response(
                content=json.dumps(
                    {
                        "ok": ok,
                        "requested": len(uniq),
                        "removed_count": len(removed),
                        "failed_count": len(failed),
                        "removed": removed[:200],
                        "failed": failed[:200],
                    },
                    default=str,
                ).encode(),
                status_code=code,
                media_type="application/json",
            )

        @app.post("/api/rag/upload", include_in_schema=False)
        async def rag_upload_endpoint(request: Request):
            """Multipart upload into documents/ or from_chat/arena_uploads; indexes each ingestable file."""
            from pipecat_bots import local_rag

            try:
                form = await request.form()
            except Exception as e:
                return Response(
                    content=json.dumps({"ok": False, "error": str(e)}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            dest = str(form.get("dest") or "documents").strip().lower()
            if dest not in ("documents", "from_chat"):
                dest = "documents"
            if dest == "documents":
                base = local_rag.rag_documents_dir()
            else:
                base = local_rag.rag_from_chat_dir() / "arena_uploads"
                base.mkdir(parents=True, exist_ok=True)

            saved = []
            candidates = list(form.getlist("file"))
            if not candidates:
                for key in form:
                    if key in ("dest", "session_id"):
                        continue
                    uf = form.get(key)
                    if hasattr(uf, "read"):
                        candidates.append(uf)

            for uf in candidates:
                if not hasattr(uf, "read"):
                    continue
                raw = await uf.read()
                fname = getattr(uf, "filename", None) or "upload.bin"
                safe = (re.sub(r"[^\w.\-]", "_", Path(fname).name)[:180] or "upload.bin")
                out = base / safe
                if out.is_file():
                    stem = out.stem
                    suf = out.suffix
                    n = 1
                    while out.is_file():
                        out = base / f"{stem}_{n}{suf}"
                        n += 1
                suf = Path(out.name).suffix.lower() or ".bin"
                from pipecat_bots.asyncio_helpers import run_blocking, run_db_blocking
                await run_blocking(local_rag.write_dedup_linked_file, out, raw, suffix=suf)
                ing = await run_db_blocking(local_rag.ingest_file, out)
                saved.append({"path": str(out), "ingest": ing})

            return Response(content=json.dumps({"ok": True, "dest": dest, "saved": saved}, default=str).encode(), media_type="application/json")

        @app.get("/api/chat/attachments/limits", include_in_schema=False)
        async def chat_attachments_limits():
            from pipecat_bots.chat_attachments import (
                chat_attach_max_bytes,
                chat_attach_pdf_page_cap,
                chat_attach_text_max_chars,
            )
            return Response(
                content=json.dumps(
                    {
                        "max_bytes_per_file": chat_attach_max_bytes(),
                        "pdf_page_cap": chat_attach_pdf_page_cap(),
                        "text_max_chars": chat_attach_text_max_chars(),
                    }
                ).encode(),
                media_type="application/json",
            )

        @app.post("/api/chat/attachments", include_in_schema=False)
        async def chat_attachments_endpoint(request: Request):
            from pipecat_bots.chat_attachments import chat_attach_max_bytes, save_uploads

            try:
                form = await request.form()
            except Exception as e:
                return Response(
                    content=json.dumps({"ok": False, "error": str(e)}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            sid = str(form.get("session_id") or "").strip()
            max_b = chat_attach_max_bytes()
            flist: list[tuple[str, bytes, str]] = []
            rejections: list[dict] = []
            # multi_items() preserves duplicate keys (multiple "file" parts).
            for key, uf in form.multi_items():
                if key == "session_id":
                    continue
                if not hasattr(uf, "read"):
                    continue
                fname = getattr(uf, "filename", None) or key
                ctype = getattr(uf, "content_type", "") or ""
                # Stream-read with an early cap so we 413 a 1 GiB upload before
                # buffering it all into RAM.
                buf = bytearray()
                oversized = False
                try:
                    while True:
                        chunk = await uf.read(1024 * 1024)
                        if not chunk:
                            break
                        if len(buf) + len(chunk) > max_b:
                            oversized = True
                            break
                        buf.extend(chunk)
                except Exception as e:
                    rejections.append({"name": fname, "error": f"read failed: {e}"})
                    continue
                if oversized:
                    rejections.append({"name": fname, "error": f"file too large (>{max_b} bytes)"})
                    continue
                flist.append((fname, bytes(buf), ctype))

            manifest = await save_uploads(sid, flist) if flist else {"session_id": sid, "items": []}
            if rejections:
                manifest.setdefault("items", []).extend(rejections)
                manifest["partial_413"] = True
            status = 413 if rejections and not flist else 200
            return Response(
                content=json.dumps(manifest, default=str).encode(),
                status_code=status,
                media_type="application/json",
            )

        @app.post("/api/text-chat/completions", include_in_schema=False)
        async def text_chat_completions_proxy(request: Request):
            """Same-origin proxy for Assistant Console text chat.

            Extension fields (stripped before upstream): ``vision_*``, ``attachment_*``,
            ``local_tools_enable``, ``tool_rag``, ``tool_calendar``, ``tool_connector``,
            ``rag_url``, ``calendar_url``, ``connector_url``.
            """
            import copy

            import httpx

            from pipecat_bots import chat_attachments
            from pipecat_bots.text_chat_agent import run_agent_chat_completions
            from pipecat_bots.vision_augment import augment_chat_messages_for_vision
            from pipecat_bots.vision_client_prefs import set_vision_augment_every_turn

            raw_body = await request.body()
            forward_body = raw_body
            payload = None
            try:
                payload = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            truncate_tw: int | None = None
            truncate_tc: int | None = None
            if isinstance(payload, dict):
                stream_on = payload.get("stream") is True
                _raw_tw = payload.pop("text_chat_max_prompt_tokens", None)
                _raw_tc = payload.pop("text_chat_max_prompt_chars", None)
                if _raw_tw is not None:
                    try:
                        truncate_tw = max(512, min(int(_raw_tw), 500_000))
                    except (TypeError, ValueError):
                        truncate_tw = None
                if _raw_tc is not None:
                    try:
                        truncate_tc = max(4096, min(int(_raw_tc), 500_000))
                    except (TypeError, ValueError):
                        truncate_tc = None
                att_sid = payload.pop("attachment_session_id", None)
                att_ids = payload.pop("attachment_ids", None)
                msgs0 = payload.get("messages")
                if (
                    not stream_on
                    and att_sid
                    and isinstance(att_ids, list)
                    and att_ids
                    and isinstance(msgs0, list)
                ):
                    payload["messages"] = chat_attachments.augment_last_user_with_attachments(
                        msgs0,
                        str(att_sid),
                        [str(x) for x in att_ids],
                    )

                vsid = payload.pop("vision_session_id", None)
                venable = payload.pop("vision_enable", True)
                va_every = payload.pop("vision_augment_every_turn", None)
                if not isinstance(venable, bool):
                    venable = True
                if isinstance(vsid, str) and vsid.strip() and isinstance(va_every, bool):
                    set_vision_augment_every_turn(vsid.strip(), va_every)
                if (
                    not stream_on
                    and isinstance(vsid, str)
                    and vsid.strip()
                    and venable
                ):
                    msgs = payload.get("messages")
                    if isinstance(msgs, list):
                        payload["messages"] = await augment_chat_messages_for_vision(
                            msgs, vsid.strip(), True
                        )

                if isinstance(payload.get("messages"), list):
                    from pipecat_bots.text_chat_truncate import truncate_messages_for_upstream

                    payload["messages"], _trim_meta = truncate_messages_for_upstream(
                        payload["messages"],
                        max_prompt_tokens=truncate_tw,
                        max_chars=truncate_tc,
                    )

                from pipecat_bots.llm_context_size import inject_ollama_options_num_ctx

                inject_ollama_options_num_ctx(payload)

                base = (os.environ.get("NVIDIA_LLM_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
                upstream = f"{base}/chat/completions"
                timeout = httpx.Timeout(180.0, connect=15.0)
                ct = request.headers.get("content-type") or "application/json"

                local_ok = os.environ.get("LOCAL_TOOLS_ENABLE", "1").strip().lower() not in (
                    "0",
                    "false",
                    "no",
                )
                use_agent = local_ok and not stream_on and bool(payload.get("local_tools_enable"))
                if use_agent:
                    try:

                        async def _post(body_bytes: bytes):
                            async with httpx.AsyncClient(timeout=timeout) as client:
                                return await client.post(
                                    upstream,
                                    content=body_bytes,
                                    headers={"Content-Type": "application/json"},
                                )

                        body_b, st, ct_out = await run_agent_chat_completions(
                            copy.deepcopy(payload),
                            upstream_post=_post,
                            truncate_max_tokens=truncate_tw,
                            truncate_max_chars=truncate_tc,
                        )
                        return Response(
                            content=body_b,
                            status_code=st,
                            media_type=ct_out,
                        )
                    except ValueError as e:
                        logger.debug(f"[text-chat] agent fallback: {e}")
                    except Exception as e:
                        logger.warning(f"[text-chat] agent error: {e}")

                for k in (
                    "local_tools_enable",
                    "tool_rag",
                    "tool_calendar",
                    "tool_connector",
                    "rag_url",
                    "calendar_url",
                    "connector_url",
                ):
                    payload.pop(k, None)
                forward_body = json.dumps(payload).encode()

            base = (os.environ.get("NVIDIA_LLM_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
            upstream = f"{base}/chat/completions"
            ct = request.headers.get("content-type") or "application/json"
            timeout = httpx.Timeout(180.0, connect=15.0)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    r = await client.post(upstream, content=forward_body, headers={"Content-Type": ct})
            except httpx.RequestError as e:
                logger.warning(f"[offline-patch] text-chat proxy to {upstream}: {e}")
                return Response(
                    content=json.dumps({"error": str(e), "upstream": upstream}).encode(),
                    status_code=502,
                    media_type="application/json",
                )
            return Response(
                content=r.content,
                status_code=r.status_code,
                media_type=r.headers.get("content-type") or "application/json",
            )

        @app.post("/api/vision/preview", include_in_schema=False)
        async def vision_preview(request: Request):
            """Multipart JPEG → Ollama caption (validates VLM without voice). Field name: image."""
            from pipecat_bots.vision_caption import caption_jpeg

            try:
                form = await request.form()
            except Exception as e:
                return Response(
                    content=json.dumps({"ok": False, "error": str(e)}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            f = form.get("image")
            if f is None:
                return Response(
                    content=json.dumps({"ok": False, "error": "missing form field 'image'"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            try:
                raw = await f.read()
            except Exception as e:
                return Response(
                    content=json.dumps({"ok": False, "error": str(e)}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            if not raw:
                return Response(
                    content=json.dumps({"ok": False, "error": "empty upload"}).encode(),
                    status_code=400,
                    media_type="application/json",
                )
            try:
                cap = await caption_jpeg(raw)
            except Exception as e:
                logger.exception("[vision/preview] caption failed")
                return Response(
                    content=json.dumps({"ok": False, "error": str(e)}).encode(),
                    status_code=502,
                    media_type="application/json",
                )
            return Response(
                content=json.dumps({"ok": True, "caption": cap}).encode(),
                media_type="application/json",
            )

        @app.get("/face-manager", include_in_schema=False)
        async def face_manager_page():
            from fastapi.responses import FileResponse as _FR

            f = _static_dir / "face_manager.html"
            if f.is_file():
                # Avoid stale JS (old confirm/alert) after deploy — browsers cache HTML aggressively.
                return _FR(
                    f,
                    media_type="text/html",
                    headers={
                        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                        "Pragma": "no-cache",
                    },
                )
            return Response(content="face_manager.html not found", status_code=404)

        @app.get("/api/face/runtime", include_in_schema=False)
        async def face_runtime_get():
            from pipecat_bots.face_recog.api import get_face_runtime_status

            return await get_face_runtime_status()

        @app.post("/api/face/runtime", include_in_schema=False)
        async def face_runtime_post(request: Request):
            from pipecat_bots.face_recog.api import post_face_runtime

            return await post_face_runtime(request)

        @app.get("/api/face/pending-quality", include_in_schema=False)
        async def face_pending_quality_get():
            from pipecat_bots.face_recog.api import get_face_pending_quality

            return await get_face_pending_quality()

        @app.post("/api/face/pending-quality", include_in_schema=False)
        async def face_pending_quality_post(request: Request):
            from pipecat_bots.face_recog.api import post_face_pending_quality

            return await post_face_pending_quality(request)

        @app.post("/api/face/voice-bind", include_in_schema=False)
        async def face_voice_bind(request: Request):
            from pipecat_bots.face_recog.api import post_voice_face_bind

            return await post_voice_face_bind(request)

        @app.post("/api/face/person", include_in_schema=False)
        async def face_person_create(request: Request):
            from pipecat_bots.face_recog.api import post_face_person

            return await post_face_person(request)

        @app.get("/api/face/persons", include_in_schema=False)
        async def face_persons_list(include_legacy_unknown: bool = False):
            from pipecat_bots.face_recog.api import get_face_persons

            return await get_face_persons(include_legacy_unknown=include_legacy_unknown)

        @app.delete("/api/face/person/{person_id}", include_in_schema=False)
        async def face_person_delete(person_id: str):
            from pipecat_bots.face_recog.api import delete_face_person

            return await delete_face_person(person_id)

        @app.get("/api/face/person/{person_id}/thumbnail", include_in_schema=False)
        async def face_person_thumbnail(person_id: str):
            from pipecat_bots.face_recog.api import get_face_person_thumbnail

            return await get_face_person_thumbnail(person_id)

        @app.get("/api/face/person/{person_id}/turns", include_in_schema=False)
        async def face_person_turns(person_id: str, limit: int = 40):
            from pipecat_bots.face_recog.api import get_face_person_turns

            return await get_face_person_turns(person_id, limit=limit)

        @app.get("/api/face/session/{vision_session_id}", include_in_schema=False)
        async def face_session_get(vision_session_id: str):
            from pipecat_bots.face_recog.api import get_face_session

            return await get_face_session(vision_session_id)

        @app.post("/api/face/focus", include_in_schema=False)
        async def face_focus_post(request: Request):
            from pipecat_bots.face_recog.api import post_face_focus

            return await post_face_focus(request)

        @app.post("/api/face/capture", include_in_schema=False)
        async def face_capture_post(
            vision_session_id: str = Form(...),
            image: UploadFile = File(...),
        ):
            from pipecat_bots.face_recog.api import post_face_capture

            return await post_face_capture(vision_session_id=vision_session_id, image=image)

        @app.post("/api/face/extract-upload", include_in_schema=False)
        async def face_extract_upload_post(
            images: list[UploadFile] = File(...),
            vision_session_id: str | None = Form(None),
        ):
            from pipecat_bots.face_recog.api import post_face_extract_upload

            return await post_face_extract_upload(images=images, vision_session_id=vision_session_id)

        # WebSocket voice endpoint - raw PCM, no ICE
        try:
            from fastapi import WebSocket
            from pipecat.transports.websocket.fastapi import (
                FastAPIWebsocketParams,
                FastAPIWebsocketTransport,
            )
            from pipecat_bots.raw_pcm_serializer import RawPCMFrameSerializer

            def _ws_voice_params():
                from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
                from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
                from pipecat.audio.vad.silero import SileroVADAnalyzer
                from pipecat.audio.vad.vad_analyzer import VADParams
                return FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    audio_out_sample_rate=16000,
                    add_wav_header=False,
                    serializer=RawPCMFrameSerializer(),
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
                    turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
                )

            def _select_ws_bot_module():
                from pipecat_bots import bot_vllm
                return bot_vllm

            @app.websocket("/ws/voice")
            async def ws_voice_endpoint(websocket: WebSocket):
                await websocket.accept()
                try:
                    transport = FastAPIWebsocketTransport(
                        websocket=websocket,
                        params=_ws_voice_params(),
                    )
                    from pipecat.runner.types import RunnerArguments

                    bot_module = _select_ws_bot_module()
                    runner_args = RunnerArguments(body={})
                    await bot_module.run_bot(transport, runner_args)
                except Exception as e:
                    logger.exception(f"[ws-voice] Bot error: {e}")
                    try:
                        await websocket.close()
                    except Exception:
                        pass

            @app.websocket("/ws/mobile-voice")
            async def ws_mobile_voice_endpoint(websocket: WebSocket):
                await websocket.accept()
                try:
                    msg = await websocket.receive()
                    if msg.get("type") != "websocket.receive":
                        await websocket.close(code=4400)
                        return
                    if msg.get("bytes") is not None:
                        raw = msg["bytes"]
                    elif msg.get("text"):
                        raw = msg["text"].encode("utf-8")
                    else:
                        await websocket.close(code=4400)
                        return
                    try:
                        obj = json.loads(raw.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        await websocket.close(code=4400)
                        return
                    if obj.get("type") != "client-ready":
                        await websocket.close(code=4401)
                        return
                    messages = obj.get("messages")
                    if not isinstance(messages, list) or len(messages) == 0:
                        await websocket.close(code=4402)
                        return

                    from pipecat.runner.types import RunnerArguments
                    bot_module = _select_ws_bot_module()

                    transport = FastAPIWebsocketTransport(
                        websocket=websocket,
                        params=_ws_voice_params(),
                    )
                    body = {"client_ready_messages": messages}
                    lp = obj.get("llm_params")
                    if isinstance(lp, dict) and lp:
                        body["llm_params"] = lp
                    vsid = obj.get("vision_session_id")
                    if isinstance(vsid, str) and vsid.strip():
                        body["vision_session_id"] = vsid.strip()
                    if "vision_enable" in obj and isinstance(obj["vision_enable"], bool):
                        body["vision_enable"] = obj["vision_enable"]
                    if "vision_mode" in obj and isinstance(obj["vision_mode"], bool):
                        body["vision_mode"] = obj["vision_mode"]
                    if "vision_augment_every_turn" in obj and isinstance(obj["vision_augment_every_turn"], bool):
                        body["vision_augment_every_turn"] = obj["vision_augment_every_turn"]
                    if "face_recognition" in obj and isinstance(obj["face_recognition"], bool):
                        body["face_recognition"] = obj["face_recognition"]
                    fp = obj.get("face_profile")
                    if isinstance(fp, str) and fp.strip():
                        body["face_profile"] = fp.strip()
                    runner_args = RunnerArguments(body=body)
                    await bot_module.run_bot(transport, runner_args)
                except Exception as e:
                    logger.exception(f"[ws-mobile-voice] Bot error: {e}")
                    try:
                        await websocket.close()
                    except Exception:
                        pass

            @app.websocket("/ws/mobile-voice-vision")
            async def ws_mobile_voice_vision_endpoint(websocket: WebSocket):
                from pipecat_bots.vision_ws import run_mobile_voice_vision_ws

                await run_mobile_voice_vision_ws(websocket)

            logger.info(
                "[offline-patch] Applied: /ws/voice, /ws/mobile-voice, /ws/mobile-voice-vision "
                "(WebSocket voice + vision JPEG, no ICE)"
            )
        except ImportError as e:
            logger.warning(f"[offline-patch] WebSocket voice not available: {e}")

        logger.info("[offline-patch] Applied: empty ICE, path rewrite, ICE wait for answer, offline routes")

    run_mod._setup_webrtc_routes = _patched_setup


_apply()
