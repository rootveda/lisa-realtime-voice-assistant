# Lisa — Real-time Voice Assistant (Operator Guide)

End-to-end reference for the offline real-time voice assistant stack: starting services, using each console and manager page, models and GPU roles, persistence, and admin security.

Run all shell commands from `lisa/nvidia_voice` (the directory that contains `offline_setup/`).

**Live page (when stack is running):** `https://127.0.0.1:7860/user-guide`

---

## Contents

- [Quick start](#quick-start)
- [Stack control — lisa_stack.sh](#stack-control--lisa_stacksh)
  - [Commands](#commands)
  - [Flags & environment equivalents](#flags--environment-equivalents)
  - [What status shows](#what-status-shows)
- [URLs & ports](#urls--ports)
- [Application components — how to use each](#application-components--how-to-use-each)
- [Models & GPU services](#models--gpu-services)
- [Security model — what we protect and how safe this is](#security-model--what-we-protect-and-how-safe-this-is)
  - [Security layers we enforce](#security-layers-we-enforce)
  - [How secure is this in practice?](#how-secure-is-this-in-practice)
  - [What we do not protect (honest limits)](#what-we-do-not-protect-honest-limits)
  - [Security best practices](#security-best-practices)
- [Admin role & security token](#admin-role--security-token)
  - [Setup & usage](#setup--usage)
  - [Features requiring admin token](#features-requiring-admin-token-lan--admin-mode)
- [Data persistence](#data-persistence)
  - [Complete reference — how, where, why](#complete-reference--how-where-why)
- [Logs & troubleshooting](#logs--troubleshooting)

---

## Quick start

### Home dev (this PC only, no token)

```bash
cd lisa/nvidia_voice
./offline_setup/lisa_stack.sh start
```

Open `https://127.0.0.1:7860/assistant-console`. Admin mutations work from localhost without a Bearer token.

### LAN + admin token (phones, other PCs)

```bash
cp offline_setup/lisa_admin_token.env.example offline_setup/lisa_admin_token.env
# Edit LISA_ADMIN_TOKEN — generate: openssl rand -hex 24
./offline_setup/lisa_stack.sh start --admin
```

Paste the token in the UI: **LLM Routing → Admin token** (Assistant Console), or the admin field on Face Manager, Instructions Manager, and RAG Arena.

### LAN via nginx (port 8088)

```bash
./offline_setup/lisa_stack.sh start --admin --network-proxy
```

Requires `Network/nginx/lisa-bot.conf` in the checkout and sudo for nginx install. Console: `https://<LAN-IP>:8088/assistant-console`

> **Verify security mode anytime:**
> `curl -sk https://127.0.0.1:7860/api/admin/capabilities`
> → `admin_token_required: false` (home dev) or `true` (admin mode).

---

## Stack control — lisa_stack.sh

**Script path:** `offline_setup/lisa_stack.sh`

Single entrypoint for the full offline voice stack: Nemotron ASR → XTTS TTS → dialogue LLM (local llama or Ollama) → bot (`bot_vllm.py`) → optional HTTPS proxy on port **7860**.

`start` and `restart` always run a **clean stop** first so duplicate bots, proxies, or containers do not linger. Ollama as a system service is *not* stopped; on start, loaded Ollama runners are unloaded to free GPU VRAM for XTTS and local llama (`STACK_UNLOAD_OLLAMA_AT_START=1` default).

### Commands

| Command | What it does |
|---------|--------------|
| `start` | Stop any running stack pieces, then run `start_current_stack.sh`. Default: **home dev** — loopback bind, no admin token required locally. |
| `start --admin` | Sources `offline_setup/lisa_admin_token.env`: public/LAN bind + `LISA_ADMIN_TOKEN` required for admin API mutations. |
| `start --network-proxy` | After stack is up, installs/reloads nginx LAN site (port **8088**). Does not stop nginx on `stop`. |
| `start --admin --network-proxy` | Common phone/LAN setup: token-protected admin APIs + nginx entry on 8088. |
| `stop` | Stops bot, TLS proxy, Docker `xtts-tts`, `lisa-llama-primary`, Nemotron ASR container. |
| `restart` | Same as `stop` then `start`; respects `--admin` and `--network-proxy` flags. |
| `status` | Prints stack mode file, processes, Docker rows, HTTP health probes, vision/Ollama checks, GPU summary, and **models in use** table. |
| `help` | Usage summary (same as running with no args). |

### Flags & environment equivalents

| Flag / env | Effect |
|------------|--------|
| `--admin` / `LISA_STACK_ADMIN=1` | Load `lisa_admin_token.env`; set `LISA_BIND_PUBLIC=1`, `LISA_BIND_LOOPBACK_ONLY=0`, require Bearer token for admin mutations. |
| `--network-proxy` / `LISA_NETWORK_PROXY=1` | Run nginx install helper after successful start. |
| `USE_LOCAL_LLAMA_PRIMARY=1` | Start Docker `lisa-llama-primary` on `:8000` (default when GGUF file exists). Set `0` to use Ollama for dialogue. |
| `LOCAL_LLAMA_CTX_SIZE` | Context window for local llama (default **49152**). Larger = more VRAM. Lower if OOM. |
| `LOCAL_LLAMA_N_GPU_LAYERS` | GPU layers for local llama (default **28**). Reduce to leave VRAM for XTTS/vision. |
| `NVIDIA_LLM_MODEL` | Ollama tag when local llama is not primary (default `gemma4:e2b-it-q4_K_M`). |
| `VISION_OLLAMA_MODEL` | Ollama vision model for camera captions (default `gemma4:e2b-it-q4_K_M`). |
| `STACK_UNLOAD_OLLAMA_AT_START=0` | Skip `ollama stop` before XTTS (keep models hot; may cause VRAM pressure). |
| `ENABLE_HTTPS=0` | Bot only on HTTP (no TLS proxy). Use `http://127.0.0.1:7861/...`. |

Set env vars in the shell *before* `lisa_stack.sh start`, or in `lisa_admin_token.env` for admin mode.

### What status shows

- **Stack mode file** — `/tmp/stack_mode.txt` (provider, URL, model from last start).
- **Processes** — `bot_vllm.py`, `tls_tcp_proxy.py`.
- **Docker** — `xtts-tts`, `lisa-llama-primary`, `nemotron`.
- **HTTP checks** — ASR :8080, XTTS :80, bot :7861, HTTPS :7860, optional nginx :8088.
- **Vision** — Ollama tags, vision model presence, `POST /api/vision/preview` smoke test.
- **GPU & models table** — see [Models & GPU services](#models--gpu-services) below.

---

## URLs & ports

| Port | Service | Notes |
|------|---------|-------|
| **7860** | HTTPS (TLS proxy) | Primary browser entry. Use `https://`, not `http://`. |
| **7861** | Bot HTTP | Direct bot API; used internally by proxy. |
| **8088** | nginx LAN proxy | Optional; separate browser origin from :7860. |
| **8000** | Local llama-server | OpenAI-compatible `/v1` when primary. |
| **11434** | Ollama | Dialogue (fallback) + vision captions. |
| **8080** | Nemotron ASR | Speech-to-text WebSocket. |
| **80** | XTTS TTS | Text-to-speech (Coqui XTTS v2). |

### Main pages

| Page | Path | Purpose |
|------|------|---------|
| Assistant Console | `/assistant-console` | Primary UI: text chat, voice, live camera, RAG, LLM routing, tools. |
| Session Manager | `/session-manager` | Rename, delete, clear histories; open session in console. |
| Instructions Manager | `/instructions-manager` | Edit system-prompt presets (markdown). |
| Face Manager | `/face-manager` | Face recognition toggle, enroll, pending identities, session focus. |
| RAG Arena | `/rag-arena` | Upload documents, rebuild FTS index, inspect chunks. |
| Mobile voice test | `/mobile-voice-test` | Browser mic + voice WebSocket test. |
| Mobile voice + vision | `/mobile-voice-vision-test` | Mic + camera JPEG + voice/vision WebSockets. |
| Playground (WebRTC) | `/client` | Pipecat WebRTC client. |
| Diagnose | `/diagnose` | Connectivity diagnostics. |
| This guide | `/user-guide` | Operator reference (live page). |

> **Separate origins:** `https://127.0.0.1:7860` and `https://127.0.0.1:8088` use different browser storage. Pick one URL and stay on it for sessions and settings.

Full URLs (HTTPS default): prefix with `https://127.0.0.1:7860` (or your LAN IP / nginx port).

---

## Application components — how to use each

### Assistant Console

The main workspace for offline voice and text AI.

- **Text chat** — Select or create a session; messages persist (see [Data persistence](#data-persistence)). Toggle RAG per session.
- **Instructions** — Pick a preset from the dropdown (loaded from `docs/instructions/`). Edit presets in Instructions Manager.
- **Voice** — Start voice mode; uses Nemotron ASR + active LLM + XTTS. With camera enabled, JPEG frames feed vision captions via Ollama.
- **Live camera** — Vision augment adds `[Camera context]` to matching utterances (or every turn if opted in).
- **Attachments** — Upload files per chat session; stored on disk and available to RAG/tools.
- **LLM Routing** — Switch local llama vs Ollama; **Apply Routing** restarts the stack. Requires admin token in LAN mode.
- **Local tools** — Optional agent path: RAG search, calendar, HTTP connectors (allowlisted hosts only).
- **Power & GPU time** — Tracks Wh and GPU utilization while the page is open; syncs to server file.
- **RAG panel** — Status, rebuild index (admin), open RAG Arena.

### Session Manager

- Create, rename, or delete chat sessions.
- **Clear all histories** — Wipes messages but keeps session names and settings.
- Syncs from server on load (merges with browser copy).
- **Open selected in Assistant** — Sets active session and navigates to the console.

### Instructions Manager

- Lists presets from repo (`docs/instructions/*.md`) and bundled copies.
- **Repo** badges are editable/deletable; **bundled** are read-only until you Save to create a repo override.
- Save / Save As / Delete require admin token when `admin_token_required` is true.

### Face Manager

- **Turn ON/OFF** — Enables server face pipeline; persisted to `facerecog_runtime.json`.
- **Camera capture** — Detect faces, add pending `stub_vs_*` identities.
- **Extract from files** — Batch enroll from uploaded images.
- **Enroll / rename** — Assign display names to person IDs.
- **Vision session focus** (advanced) — Pin auto / multiface / person for a `vision_session_id`.
- All mutations require admin token in admin mode.

### RAG Arena

- Upload `.md` / `.txt` (and supported types) into the offline FTS index.
- **Rebuild index** — Ingests `offline_setup/rag_data/documents/` (admin token in LAN mode).
- Remove files, inspect chunk stats, test queries.

### Mobile test pages

- **`/mobile-voice-test`** — Raw voice WebSocket without full console UI.
- **`/mobile-voice-vision-test`** — Validates mic + camera + vision pipeline end-to-end.

---

## Models & GPU services

Run `./offline_setup/lisa_stack.sh status` for live GPU memory and the **Models in use** table. Typical roles:

| Role | Model / service | Where it runs | GPU / memory |
|------|-----------------|---------------|--------------|
| **Dialogue LLM** | `gemma4-26b-a4b-it-q4_K_M-local` (primary) or `gemma4:e2b-it-q4_K_M` (Ollama fallback) | Docker `lisa-llama-primary` → `http://127.0.0.1:8000/v1` or Ollama → `http://127.0.0.1:11434/v1` | Largest consumer (~16+ GiB on GPU when local llama loaded). GGUF: `offline_setup/models/gemma-4-26B-A4B-it-Q4_K_M.gguf` |
| **Vision / captions** | `gemma4:e2b-it-q4_K_M` (default) | Ollama on `http://127.0.0.1:11434/v1` | Loads on first vision question (`ollama ps`). Separate from dialogue LLM when using local llama primary. |
| **TTS** | Coqui XTTS v2, voice: `Andrew Chipper` | Docker `xtts-tts` → `http://127.0.0.1:80` | ~2–3 GiB GPU typical. Data: `offline_setup/xtts_tts_data/` |
| **ASR** | Nemotron streaming EN, `nvidia/nemotron-speech-streaming-en-0.6b` | Docker `nemotron` → `http://127.0.0.1:8080` | ~3 GiB GPU typical. ASR-only mode (no LLM/TTS in container). |

Example **Models in use & GPU memory** output from `lisa_stack.sh status`:

```text
=== Models in use & GPU memory (best-effort) ===
| Role                   | Model / service                  | Where                                    | GPU memory                   |
|------------------------|----------------------------------|------------------------------------------|------------------------------|
| Dialogue LLM           | gemma4-26b-a4b-it-q4_K_M-local   | Docker lisa-llama-primary → http://127.… | ~16634 MiB (compute-apps)    |
| Vision / captions      | gemma4:e2b-it-q4_K_M             | Ollama http://127.0.0.1:11434/v1         | not loaded (ollama ps empty) |
| TTS                    | xtts  voice=Andrew Chipper       | Docker xtts-tts → http://127.0.0.1:80    | ~2336 MiB (compute-apps)     |
| ASR                    | Nemotron (ASR-only)              | Docker nemotron → http://127.0.0.1:8080  | ~3136 MiB (compute-apps)     |
```

### Routing & switching models

- **Assistant Console → LLM Routing** — Choose *local llama* or *Ollama*, then Apply Routing (restarts stack).
- **Env before start** — `USE_LOCAL_LLAMA_PRIMARY=0 NVIDIA_LLM_MODEL=gemma4:e2b-it-q4_K_M ./offline_setup/lisa_stack.sh start`
- **Vision model** — Set `VISION_OLLAMA_MODEL` before start; pull with `ollama pull <tag>`.

### VRAM tips

- Stack unloads Ollama runners at start to free VRAM for XTTS + local llama.
- If OOM: lower `LOCAL_LLAMA_N_GPU_LAYERS` or `LOCAL_LLAMA_CTX_SIZE`; use smaller Ollama tags; run `ollama stop` after vision sessions.
- Check `nvidia-smi` and the status table after start.

---

## Security model — what we protect and how safe this is

Lisa is an **offline, self-hosted real-time voice assistant** for a trusted home or lab network — not a multi-user cloud product. Security is built around **defense in depth for local/LAN deployment**: safe defaults, gated admin actions, browser hardening, and input size limits.

It is **appropriate for personal use on your own machine or LAN with a shared admin token**; it is **not** designed as internet-facing SaaS with per-user accounts, RBAC, or audit trails.

> **Default posture (home dev):** the stack binds to `127.0.0.1` only — not reachable from other devices unless you explicitly start with `--admin`. Mutating admin APIs are allowed from localhost without a token in that mode.

### Security layers we enforce

| Layer | What it does | Where |
|-------|--------------|-------|
| **Network bind** | Default `127.0.0.1` (loopback). LAN/public only with `--admin` + `LISA_BIND_PUBLIC=1`. | `lisa_stack.sh`, `start_current_stack.sh` |
| **HTTPS (TLS)** | Browser UI on port **7860** via local TLS proxy (self-signed cert). Mic/camera require secure context on many browsers. | `tls_tcp_proxy.py`, `offline_setup/certs/` |
| **Admin auth gate** | All mutating `POST/PUT/PATCH/DELETE` under `/api/*` pass through a gate. In LAN mode: `Authorization: Bearer <LISA_ADMIN_TOKEN>` (constant-time compare). Fail-closed if public bind without token configured. | `pipecat_offline_patch.py` |
| **User vs admin API split** | Chat, attachments, session sync, vision preview, power-stats POST — allowed without admin token. Stack restart, RAG ingest/delete, instructions save, face mutations — admin only in LAN mode. | `_LISA_USER_API_PREFIXES` allowlist |
| **Content-Security-Policy** | HTML pages get CSP: `default-src 'self'`, no external scripts, `connect-src 'self' ws: wss:`, `frame-ancestors 'none'`, `object-src 'none'`. Inline script/style allowed (SPA requirement). | Response headers on HTML |
| **Other browser headers** | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`, Permissions-Policy (camera/mic self-only). | All HTML responses |
| **WebSocket same-origin** | Assistant Console forces voice/vision WebSocket URLs to same host — blocks arbitrary remote WS endpoints from UI config. | `sci_fi_assistant.html` |
| **Offline tool egress** | Local agent HTTP connectors restricted to allowlisted hosts (`127.0.0.1`, `localhost` by default). No open internet proxy from tools unless you expand `TOOL_HTTP_ALLOW_*`. | `local_tool_executor.py` |
| **Upload & body limits** | Attachment streaming cap (`CHAT_ATTACH_MAX_BYTES`, default 20 MiB) with early 413. Power-stats and context-stress payloads schema-whitelisted and size-capped. PDF/text extraction caps. | `chat_attachments.py`, power-stats API |
| **Secrets hygiene** | Keep the token in `lisa_admin_token.env` on this machine. UI stores it in browser `localStorage` for admin API calls (not on server disk). | Local + browser |
| **Cache control** | Manager pages and sensitive API paths send `Cache-Control: no-store` to reduce stale UI/token confusion. | HTTP middleware |

### How secure is this in practice?

| Scenario | Rating | Notes |
|----------|--------|-------|
| Solo dev on same PC (`start`, loopback) | **Good** | Not exposed to LAN; admin actions local-only. Main risk is local malware or shared user account on the machine. |
| Home LAN with `--admin` + strong token | **Good for trusted home Wi‑Fi** | Admin mutations require Bearer token. Chat/vision still usable without token — anyone on LAN who can reach the URL can use the assistant (by design). |
| LAN without admin token (public bind, no secret) | **Poor — avoid** | Server returns 503 fail-closed for admin routes if misconfigured; always set `LISA_ADMIN_TOKEN` for LAN. |
| Exposed to the public internet | **Not supported** | No rate limiting, no user accounts, no WAF, self-signed TLS, shared secret auth. Use VPN or reverse proxy with proper auth if you must reach it remotely. |

### What we do not protect (honest limits)

- **No per-user login** — one shared admin token, not individual accounts or roles.
- **Chat is not admin-gated** — on LAN, anyone who can open the console can send messages and use voice/vision (user-facing APIs intentionally open).
- **Self-signed HTTPS** — browsers will warn on first visit; you must trust your own cert or install it locally.
- **Camera, mic, face data** — processed locally; you are responsible for physical access and who you enroll in Face Manager.
- **RAG and attachments** — content you upload is stored on disk under `rag_data/`; protect filesystem permissions on shared machines.
- **Ollama / Docker** — separate services; keep them on loopback and firewall LAN ports you do not need.
- **nginx :8088** — optional second origin; same security rules apply, but separate browser storage (pick one URL consistently).

### Security best practices

1. Use `./offline_setup/lisa_stack.sh start` for solo local work; only use `--admin` when you need LAN/phone access.
2. Generate a long random token: `openssl rand -hex 24`. Keep it only in `lisa_admin_token.env`.
3. Keep the stack on a trusted home network; do not port-forward 7860/8088 to the internet without a proper VPN or authenticated reverse proxy.
4. Verify mode after start: `curl -sk https://127.0.0.1:7860/api/admin/capabilities`
5. Clear stale tokens in the browser if you rotate `LISA_ADMIN_TOKEN` (LLM Routing field or manager sidebar).
6. Restrict `TOOL_HTTP_ALLOW_HOSTS` unless you explicitly need LAN connector URLs.

Full remediation history (Phases 1–7): `docs/remediation-phases-2026-05.md`. Admin token operational details: [Admin role & security token](#admin-role--security-token) below.

---

## Admin role & security token

`LISA_ADMIN_TOKEN` is a shared secret (not a user login). It protects **mutating** admin APIs when the stack is reachable beyond trusted localhost-only use.

### Bind profiles

| Profile | Command | Bind | Token required? |
|---------|---------|------|-----------------|
| Home dev | `lisa_stack.sh start` | `127.0.0.1` only | No — loopback bypass for admin mutations |
| Admin / LAN | `lisa_stack.sh start --admin` | `0.0.0.0` (7860/7861) | Yes — Bearer on all gated admin routes, even from this PC |

### Setup & usage

1. Copy `offline_setup/lisa_admin_token.env.example` → `lisa_admin_token.env`.
2. Set `LISA_ADMIN_TOKEN` — generate: `openssl rand -hex 24`
3. Start with `./offline_setup/lisa_stack.sh start --admin`
4. Paste token in UI fields (saved to browser `localStorage`):
   - Assistant Console → **LLM Routing → Admin token**
   - Face Manager, Instructions Manager, RAG Arena — sidebar admin fields
5. Sent as header: `Authorization: Bearer <token>`

### Features requiring admin token (LAN / admin mode)

| Feature / API | UI location |
|---------------|-------------|
| `POST /api/llm-routing/apply` | LLM Routing → Apply Routing (Restart Stack) |
| `POST /api/stack/restart` | Full stack restart actions |
| `POST /api/rag/ingest` | RAG panel Rebuild; RAG Arena rebuild |
| `POST /api/rag/upload`, `/remove`, `/remove-many` | RAG Arena upload/delete |
| `POST/PUT/DELETE /api/instructions` | Instructions Manager save/delete |
| `POST/DELETE /api/face/*` (mutations) | Face Manager: ON/OFF, enroll, capture, delete, focus, quality |
| `POST /api/context-stress/run` | Context stress UI (when `ENABLE_CONTEXT_STRESS_RUN_API=1`) |
| Other mutating `/api/*` not in user allowlist | Various admin endpoints |

### Does NOT require admin token (user-facing)

- Text chat — `POST /api/text-chat/completions`
- Chat attachments — `POST /api/chat/attachments`
- Chat session sync — `GET/PUT /api/assistant-console/chat-sessions`
- Vision preview — `POST /api/vision/preview`
- Power stats mirror — `POST /api/assistant-console/power-stats`
- Read-only GET — `/api/runtime-status`, `/api/instructions`, `/api/face/persons`, etc.

> **Warning:** After editing `lisa_admin_token.env`, use `restart --admin` — a plain `start` without `--admin` resets to home dev and clears the token from the process environment.

---

## Data persistence

Understanding what survives browser refresh, stack restart, and which storage is per-origin vs shared on disk.

### Complete reference — how, where, why

| Content | How it is saved | Where it lives | Why / notes |
|---------|-----------------|----------------|-------------|
| Chat sessions & messages | Browser `localStorage` on edit; debounced `PUT` to server; merge on load by `updatedAt` | Browser: `assistantConsole.sessions.v1`; Server: `offline_setup/app/assistant_console_chat_sessions.v1.json` | Survive refresh and stack restart. Separate per browser origin (:7860 vs :8088). |
| Active chat session id | Set when switching sessions; synced with server payload | `assistantConsole.activeSession.v1` (browser) + server `activeId` | Restore last open session on page load. |
| LLM generation settings | On change in LLM Routing panel | `assistantConsole.llmGen.v1` (browser localStorage) | Temperature, top-p, etc. persist across tabs (migrated from sessionStorage). |
| Admin token (UI) | Paste in admin field; blur/change persists | `assistantConsole.lisaAdminToken.v1` (browser) | Convenience only — server reads env at process start, not this value. |
| Instruction presets | Instructions Manager POST/PUT writes markdown files | `docs/instructions/*.md` (repo, preferred); `offline_setup/app/presets/instructions/` (bundled fallback) | System prompts for chat/voice. Bundled copies read-only until saved as repo override. |
| Face registry (people) | Enroll/delete via Face Manager API | `offline_setup/app/rag_data/face_registry.sqlite` | Stable person IDs, display names, turn history metadata. |
| Face recognition ON/OFF | Face Manager toggle or env `FACERECOG_ENABLED=1` | `offline_setup/app/rag_data/facerecog_runtime.json` | Runtime enable without restarting shell env. |
| RAG document index | Ingest/rebuild via API or RAG Arena | `offline_setup/rag_data/` (FTS SQLite + `documents/`) | Offline full-text search for tool RAG and arena. |
| Chat file attachments | Upload in Assistant Console | `offline_setup/rag_data/attachments/{session_id}/` | Per chat session files on disk; tied to chat session UUID. |
| Power & GPU time stats | Console polls runtime; POST merges with `max()` | Server: `offline_setup/app/assistant_console_power_stats.v1.json`; Browser: `assistantConsole.powerStats.v1`, related keys | Cumulative Wh/GPU time; server totals shared across origins after sync. |
| Last vision session id | Set when starting voice with camera | `assistantConsole.lastVisionSessionId.v1` (browser) | Face Manager can paste this for session focus continuity. |
| Stack mode (provider/model) | Written at each successful start | `/tmp/stack_mode.txt` | Shown in `status` and `/api/runtime-status`. |
| Vision / face session state | In-memory during live WebSocket sessions | Server RAM (`vision_session_store`) | **Not** persisted across stack restart — reconnect camera/voice after restart. |
| Process logs | Append on run; rotate >5 MiB on start | `/tmp/bot.log`, `/tmp/https_proxy.log` | Debugging; rotated to `.prev`. |

---

## Logs & troubleshooting

| Issue | Check |
|-------|-------|
| Stack not responding | `./offline_setup/lisa_stack.sh status` then `restart` |
| Wrong directory | Must be in `lisa/nvidia_voice`, not `offline_setup/app` |
| `This site can't be reached` on :7860 | Use `https://` not `http://`. Or try `http://127.0.0.1:7861` |
| Missing admin token error | Start with `--admin`; paste token in UI; verify `/api/admin/capabilities` |
| Vision captions fail | Ollama on :11434; `ollama pull gemma4:e2b-it-q4_K_M`; see status vision section |
| VRAM OOM | Lower ctx/layers; `ollama stop`; check status GPU table |
| Sessions missing after restart | Same URL origin; check server file `assistant_console_chat_sessions.v1.json` |

### Log files

- `/tmp/bot.log` — Bot and API errors
- `/tmp/https_proxy.log` — TLS proxy
- `tail -f /tmp/bot.log` — Live tail during debugging
- Vision debug: `grep vision_augment /tmp/bot.log | tail`

Extended documentation in the repo: `docs/stack-start-stop.md`, `docs/admin-token-and-security.md`, `docs/links.md`, `docs/power-gpu-stats.md`.
