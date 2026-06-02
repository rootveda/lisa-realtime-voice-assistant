# Lisa stack remediation — May 2026 (Phases 1–7)

This document records the **seven-phase remediation plan** applied to the Lisa offline voice stack (`offline_setup/`), verification results, and the **current deployment profile** after optional post-remediation steps (local llama primary, LAN/public bind, admin token).

**Status:** All seven phases **complete**. Final smoke tests passed (security headers, 413/400 validation, async `/api/runtime-status`, RAG off thread pool, vision intent v2, pidfiles + readiness probe).

---

## Phase summary

| Phase | Focus | Key deliverables |
|-------|--------|------------------|
| **1** | Security | Loopback default bind; `LISA_ADMIN_TOKEN` Bearer gate for mutating `/api/*`; CSP + security headers on HTML; TLS proxy defaults to `127.0.0.1`, cert validation, TLS 1.2 min, SIGTERM; SPA WebSocket same-origin lock |
| **2** | SPA + backend regressions | Instruction preset restore; poll in-flight guards + backoff + visibility pause; attachment upload guards; double-click guards; log handle leak fix; safe `CHAT_ATTACH_MAX_BYTES` parsing; TestClient host opt-in |
| **3** | Persistence & validation | Power-stats schema whitelist + size cap + atomic write + `fsync`; streaming attachment upload with 413; unified PDF/text caps via env; iterative context trim in `text_chat_truncate.py` |
| **4** | Async / concurrency | `asyncio_helpers.run_blocking` / `run_db_blocking`; `/api/runtime-status` and RAG/SQLite/subprocess paths off event loop; SQLite WAL + `busy_timeout=30000` |
| **5** | Voice + UX | Vision session TTL eviction; vision intent regex v2 (fewer false positives); STT WebSocket connect retry/backoff; attachment dedup in SPA; periodic limits refresh; `llmRouteToggleBtn` → status badge |
| **6** | Orchestration | Bot + TLS pidfiles (`/tmp/lisa_bot.pid`, `/tmp/lisa_tls_proxy.pid`); readiness probe before “Ready:”; graceful pidfile-based stop |
| **7** | Code health | Log rotation on restart (>5 MiB → `.prev`); syntax/lint clean; final end-to-end smoke |

---

## Phase 1 — Security

### Backend (`pipecat_offline_patch.py`)

- **`LISA_BIND_LOOPBACK_ONLY`** (default `1`): when `1`, mutating `/api/*` from loopback peers is allowed without a token.
- **`LISA_ADMIN_TOKEN`**: when set and loopback-only is off (`LISA_BIND_LOOPBACK_ONLY=0`), mutating `/api/*` requires `Authorization: Bearer <token>` (or `X-Lisa-Admin-Token`).
- **Security headers** on `text/html`: CSP, `X-Frame-Options: DENY`, Permissions-Policy, `Referrer-Policy`, `X-Content-Type-Options`, COOP.
- **Startup log** reports active security mode.

### TLS proxy (`scripts/tls_tcp_proxy.py`)

- Default listen **`127.0.0.1`** unless `--bind-public` or explicit `--listen-host`.
- Cert/key existence + readability checks; minimum TLS **1.2**; SIGINT/SIGTERM graceful shutdown.

### Startup script (`start_current_stack.sh`)

- **`BOT_HOST`** defaults to `127.0.0.1`; opt-in public bind via **`LISA_BIND_PUBLIC=1`** → `0.0.0.0`.
- Propagates `LISA_BIND_LOOPBACK_ONLY`, `LISA_ADMIN_TOKEN`, `CHAT_ATTACH_MAX_BYTES`, `BOT_PORT`, `HTTP_BOT_PORT`.

### SPA (`sci_fi_assistant.html`)

- WebSocket URLs forced same-origin via `_safeWsUrl()` helpers.

---

## Phase 2 — Regressions

### SPA

- Restore **`INSTRUCTION_ID_KEY`** from `localStorage` on load.
- Context-stress footer poll: in-flight guard, exponential backoff, `AbortController`, pause when tab hidden.
- Runtime status poll: in-flight guard; cleanup on `pagehide`.
- Attachment uploads: in-flight flag, abort on `pagehide`, disable Send during upload.
- Context-stress run + full-stack restart: double-click guards, token validation.

### Backend

- Context-stress subprocess: log file handle closed in `finally` (no leak).
- `CHAT_ATTACH_MAX_BYTES`: invalid env → warning + 20 MiB default (no crash).
- `TestClient` host `"testclient"` accepted only when `LISA_ALLOW_TEST_CLIENT_HOST=1`.
- Context stress test: GPU index in `nvidia-smi`, publish error logging, correct exit codes.

---

## Phase 3 — Persistence & validation

### Power stats (`/api/assistant-console/power-stats`)

- Whitelisted keys: `powerStats`, `powerSession`, `gpuTimeStats`, `gpuTimeSession`.
- Per-section + total payload caps; atomic write (temp + `replace`) + `os.fsync`.
- POST: raw body read; **413** oversize; **400** malformed JSON; **500** disk errors.
- **May 2026:** POST merges with on-disk file using `max()`; SPA hydrates before GPU polling. User guide: **[power-gpu-stats.md](power-gpu-stats.md)**.

### Context stress test

- Script: `offline_setup/app/scripts/context_max_stress_test.py`. User guide: **[context-stress-test.md](context-stress-test.md)**.

### Attachments (`/api/chat/attachments`)

- Stream-read with early byte cap (413 before buffering huge files).
- Limits endpoint returns `max_bytes_per_file`, `pdf_page_cap`, `text_max_chars`.
- Shared helpers in `chat_attachments.py`: `chat_attach_max_bytes()`, `chat_attach_pdf_page_cap()`, `chat_attach_text_max_chars()`.

### Context trim (`text_chat_truncate.py`)

- Iterative tail shrink until under char + token budget.
- `default_max_prompt_chars` derived from token budget (not hard-capped at 24k).

### Context stress live POST

- 64 KiB body cap with **413**.

---

## Phase 4 — Async / concurrency

### New module: `asyncio_helpers.py`

- `run_blocking()` — general thread-pool offload.
- `run_db_blocking()` — single-worker executor for SQLite serialization.

### Offloaded paths (representative)

- `/api/runtime-status` (nvidia-smi, subprocess, httpx) — **~20 ms** vs prior multi-second event-loop block.
- RAG endpoints: `rag_status`, `ingest`, `files`, `remove`, upload ingest.
- Power-stats load/save; context-stress spawn; ollama listing; apply-llm-routing; host-ip collection.

### SQLite (`local_rag.py`)

- `timeout=30`, `PRAGMA journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL`.

---

## Phase 5 — Voice + UX

### Vision

- **`vision_session_store.py`**: session TTL eviction (`VISION_SESSION_TTL_SEC`, default ≥60s).
- **`vision_policy/vision_intents.json` v2**: removed bare `\b(see|seeing|picture|image|objects)\b` alternation; verified 0/3 false positives on colloquial “I see …” phrases.

### STT (`nvidia_stt.py`)

- WebSocket connect: up to **3 attempts** with exponential backoff (0.5s, 1s, 2s).

### SPA

- Attachment dedup by `name + size + lastModified`.
- Refresh attachment limits every **60s**.
- **`llmRouteToggleBtn`**: changed from inert button to `role="status"` (display-only route indicator).

---

## Phase 6 — Orchestration

### Pidfiles

| File | Process |
|------|---------|
| `/tmp/lisa_bot.pid` | `bot_vllm.py` |
| `/tmp/lisa_tls_proxy.pid` | `tls_tcp_proxy.py` |

### Readiness probe

- After starting bot + TLS proxy, `start_current_stack.sh` waits up to **60s** for `GET http://127.0.0.1:<HTTP_BOT_PORT>/api/runtime-status` before printing **Ready:**.

### Stop (`lisa_stack.sh`)

- Prefers pidfile SIGTERM → 3s grace → SIGKILL; falls back to `pkill`.

---

## Phase 7 — Code health

- Log rotation: `/tmp/bot.log` and `/tmp/https_proxy.log` renamed to `.prev` when > **5 MiB** on restart.
- Final smoke (May 2026): all checks green (see [Verification](#verification-may-2026)).

---

## Verification (May 2026)

| Check | Result |
|-------|--------|
| `/api/runtime-status` latency | ~20 ms (3 sequential); 10 parallel ~0.10 s wall |
| Security headers on `/assistant-console` | CSP, X-Frame-Options, Permissions-Policy, etc. |
| Power-stats oversize POST | **413** |
| Malformed JSON POST | **400** |
| Attachment limits JSON | includes `pdf_page_cap`, `text_max_chars` |
| `/api/rag/status` | **200** via `run_db_blocking` |
| Vision intent v2 | 0/3 false positives, 3/3 legit triggers |
| Pidfiles | created and alive after restart |
| `bot.log` errors | none in tail |

---

## Current deployment (after optional steps)

Applied **2026-05-29** via `offline_setup/lisa_admin_token.env`:

| Setting | Value |
|---------|--------|
| `USE_LOCAL_LLAMA_PRIMARY` | `1` |
| `LISA_BIND_PUBLIC` | `1` → bind **`0.0.0.0`** (bot + TLS on 7861 / 7860) |
| `LISA_BIND_LOOPBACK_ONLY` | `0` → **token required** for mutating `/api/*` even from localhost |
| `LISA_ADMIN_TOKEN` | in `offline_setup/lisa_admin_token.env` (**gitignored**) |
| LLM mode | **local-llama** primary (`gemma4-26b-a4b-it-q4_K_M-local` @ `http://127.0.0.1:8000/v1`) |

### URLs

| Where | Assistant Console |
|-------|-------------------|
| This machine | `https://127.0.0.1:7860/assistant-console` |
| LAN (direct TLS) | `https://<LAN-IP>:7860/assistant-console` |
| LAN (nginx proxy) | `https://<LAN-IP>:8088/assistant-console` |

Accept the **self-signed certificate** warning in the browser (required for mic/camera on HTTPS).

### Admin token usage

Mutating **admin** API calls (Apply Routing, full stack restart, RAG ingest/remove, etc.) require:

```http
Authorization: Bearer <LISA_ADMIN_TOKEN>
```

**Assistant Console:** when `admin_token_required` is true (`GET /api/admin/capabilities`), expand **LLM Routing** and paste the token into **Admin token** — it is saved in the browser (`localStorage`).

User-facing console calls (text chat, attachments, power-stats mirror, vision preview) do **not** require the admin token even on LAN.

Example (after **`start --admin`**, token is in the env file):

```bash
# For curl only — load token into shell:
set -a && source offline_setup/lisa_admin_token.env && set +a
curl -sk -X POST "https://127.0.0.1:7860/api/rag/ingest" \
  -H "Authorization: Bearer ${LISA_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"reindex_all": true}'
```

### Restart with this profile

```bash
cd lisa/nvidia_voice
./offline_setup/lisa_stack.sh restart --admin
./offline_setup/lisa_stack.sh status
```

To return to **home dev** (loopback-only, no token for local admin calls):

```bash
./offline_setup/lisa_stack.sh restart
```

Full bind-mode comparison, routing, and troubleshooting: **[admin-token-and-security.md](admin-token-and-security.md)**.

### Post-remediation fix: HTTP 422 on Apply Routing

**Symptom:** After pasting the admin token, **Apply Routing** failed with `HTTP 422` and FastAPI `detail` complaining about missing `query.provider` / `query.model`.

**Cause:** `@app.post("/api/llm-routing/apply")` was mistakenly placed on the internal sync helper `_apply_llm_routing_sync(provider, model)` instead of the async handler that reads JSON from the request body.

**Fix:** Move the route decorator to `apply_llm_routing(request)` in `pipecat_offline_patch.py` (May 2026). Verified: `POST` with JSON body returns **200** `{"ok":true,...}`.

---

## Files touched (reference)

| Area | Primary files |
|------|----------------|
| Security / routes | `app/pipecat_bots/pipecat_offline_patch.py` |
| TLS | `app/scripts/tls_tcp_proxy.py` |
| Async | `app/pipecat_bots/asyncio_helpers.py`, `local_rag.py` |
| Attachments | `app/pipecat_bots/chat_attachments.py` |
| Context trim | `app/pipecat_bots/text_chat_truncate.py` |
| Vision | `app/pipecat_bots/vision_session_store.py`, `vision_policy/vision_intents.json` |
| STT | `app/pipecat_bots/nvidia_stt.py` |
| SPA | `app/static/sci_fi_assistant.html` |
| Orchestration | `start_current_stack.sh`, `lisa_stack.sh` |
| Secrets (local) | `offline_setup/lisa_admin_token.env` (gitignored) |

---

## Related docs

- [README.md](README.md) — documentation index
- [admin-token-and-security.md](admin-token-and-security.md) — token, simpler home-dev mode, routing, HTTP 422 FAQ
- [power-gpu-stats.md](power-gpu-stats.md) — Assistant Console Wh / GPU persistence
- [context-stress-test.md](context-stress-test.md) — context limit stress test
- [stack-start-stop.md](stack-start-stop.md) — start/stop/status, env vars, security bind modes
- [links.md](links.md) — URLs and API endpoints
- [e2e_vision_voice_tests.md](e2e_vision_voice_tests.md) — automated vision/voice checks
