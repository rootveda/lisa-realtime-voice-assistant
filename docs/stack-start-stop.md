# How to start / stop / restart the Lisa stack

The canonical bring-up script is **`offline_setup/start_current_stack.sh`** (ASR → XTTS → LLM route → `bot_vllm` → optional HTTPS on **7860**).

## Working directory (important)

All commands below assume your shell is in **`lisa/nvidia_voice`** — the directory that contains **`offline_setup/`**.

```text
/path/to/lisa/nvidia_voice/          ← run lisa_stack.sh from HERE (repo root)
    └── offline_setup/
        ├── lisa_stack.sh
        ├── lisa_admin_token.env
        └── app/                 ← bot code; NOT the stack root
            └── scripts/
```

| Your cwd | Command |
|----------|---------|
| **`lisa/nvidia_voice`** (correct) | `./offline_setup/lisa_stack.sh start` or `start --admin` |
| **`offline_setup/app`** (common mistake) | `cd ../..` first, then `./offline_setup/lisa_stack.sh start` |
| **Parent folder above repo root** | `cd lisa/nvidia_voice` first — there is no `offline_setup/` at that level |

If you see `No such file or directory` for `offline_setup/lisa_admin_token.env` or `lisa_stack.sh`, you are in the wrong directory.

For a **single command** that always does a **clean** cycle (stop stray processes first, then start everything), use:

## Control script

**Path:** `offline_setup/lisa_stack.sh`

| Command | What it does |
|--------|----------------|
| **`./offline_setup/lisa_stack.sh start`** | **Stops** any running stack pieces, then runs **`start_current_stack.sh`**. **Home dev mode**: loopback-only bind (`127.0.0.1`), admin actions from this machine **without** a token. Clears stale `LISA_ADMIN_TOKEN` from the shell. |
| **`./offline_setup/lisa_stack.sh start --admin`** | Same as **start**, but loads **`offline_setup/lisa_admin_token.env`** (LAN/public bind + **`LISA_ADMIN_TOKEN`** required for admin APIs). See [admin-token-and-security.md](admin-token-and-security.md). |
| **`./offline_setup/lisa_stack.sh start --network-proxy`** | After the stack is up, installs/reloads the **nginx** LAN site from the repo **`Network/`** folder (needs **sudo**). Does **not** stop nginx on **`stop`**. Full guide: **[network-proxy.md](network-proxy.md)**. |
| **`./offline_setup/lisa_stack.sh start --admin --network-proxy`** | Common LAN + phone setup: token-protected admin APIs **and** nginx on port **8088**. |
| **`./offline_setup/lisa_stack.sh stop`** | Stops **bot** (`bot_vllm`), **TLS proxy** (`tls_tcp_proxy.py`), **XTTS** container, **`lisa-llama-primary`**, and **Nemotron ASR** (`nemotron.sh stop`). |
| **`./offline_setup/lisa_stack.sh restart`** | Same as **stop**, then **start** (respects the same flags: `--admin`, `--network-proxy`). |
| **`./offline_setup/lisa_stack.sh status`** | Prints `/tmp/stack_mode.txt`, **pgrep** for bot/TLS, **docker** rows, HTTP probes, vision checks, GPU summary. If nginx site is enabled, probes **`https://127.0.0.1:8088/...`**. |

**Env equivalents:** `LISA_STACK_ADMIN=1` → `--admin`; `LISA_NETWORK_PROXY=1` → `--network-proxy`.

Use **`./offline_setup/lisa_stack.sh help`** for the same summary.

## Start modes (cheat sheet)

| Goal | Command | Console URL |
|------|---------|-------------|
| Same PC only, no token | `./offline_setup/lisa_stack.sh start` | `https://127.0.0.1:7860/assistant-console` |
| LAN / other devices + token | `./offline_setup/lisa_stack.sh start --admin` | `https://<LAN-IP>:7860/assistant-console` |
| LAN via nginx (port 8088) | `./offline_setup/lisa_stack.sh start --admin --network-proxy` | `https://<LAN-IP>:8088/assistant-console` |

Verify security mode:

```bash
curl -sk https://127.0.0.1:7860/api/admin/capabilities
# home dev:  admin_token_required: false
# --admin:   admin_token_required: true
```

### What is `--network-proxy`?

Optional **nginx reverse proxy** so LAN clients (especially **phones** with mic/camera) hit **`https://<LAN-IP>:8088`** instead of connecting directly to port **7860**.

- **Use when:** accessing the Assistant Console from other devices on Wi‑Fi, or you want a single published port behind the firewall.
- **Skip when:** you only use `https://127.0.0.1:7860` on the host, or direct `:7860` LAN access with `--admin` is enough.
- **Requires:** `Network/nginx/lisa-bot.conf` in the checkout and **sudo** for nginx install/reload.

Details: **[network-proxy.md](network-proxy.md)**.

### LAN / reverse proxy (optional)

To reach the UI from other machines on the LAN, use **`start --admin`**. For nginx on port **8088**, add **`--network-proxy`**. See **[network-proxy.md](network-proxy.md)** (replaces the optional **`Network/README.md`** when that folder is present in your checkout).

From the **`lisa/nvidia_voice`** directory (not `offline_setup/app`):

```bash
chmod +x offline_setup/lisa_stack.sh   # once
./offline_setup/lisa_stack.sh status
./offline_setup/lisa_stack.sh start              # home dev
./offline_setup/lisa_stack.sh start --admin      # LAN + token
./offline_setup/lisa_stack.sh stop
```

## Environment

`start` passes through the same variables as **`start_current_stack.sh`** (e.g. `BOT_PORT`, `ENABLE_HTTPS`, `USE_LOCAL_LLAMA_PRIMARY`, `LOCAL_LLAMA_CTX_SIZE`, `LOCAL_LLAMA_N_GPU_LAYERS`, `HTTP_BOT_PORT`, `STACK_UNLOAD_OLLAMA_AT_START`, `NVIDIA_LLM_MODEL` for **Ollama fallback only**). Set them in your shell before calling `lisa_stack.sh start`.

### Local llama context size (`LOCAL_LLAMA_CTX_SIZE`)

Passed to **local llama** as **`--ctx-size`** when **`lisa-llama-primary`** starts. Larger values reserve **more KV cache → more VRAM**. Default **`49152`** in **`start_current_stack.sh`** (override with **`LOCAL_LLAMA_CTX_SIZE`**) also drives **`LLM_CONTEXT_SIZE`**, **`VISION_OLLAMA_NUM_CTX`** (Ollama caption **`num_ctx`**), and default **`TEXT_CHAT_MAX_PROMPT_TOKENS`** for the bot. If you hit **`exceed_context_size_error`** or VRAM OOM, **lower** context / prompt caps or **raise** VRAM headroom (e.g. fewer **`LOCAL_LLAMA_N_GPU_LAYERS`**, unload Ollama) and restart:

```bash
cd offline_setup   # repo-relative path to this bundle
export LOCAL_LLAMA_CTX_SIZE=49152   # or lower if OOM (e.g. 24576)
./lisa_stack.sh restart
```

### Local tools, RAG, and egress (Assistant Console text chat)

Offline stack tools run **inside the bot** or against **allowlisted loopback/LAN URLs only** — not the public internet.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOCAL_TOOLS_ENABLE` | `1` | When `1`, text chat may use the multi-step **agent** path if the UI sends `local_tools_enable` with tool toggles. Set `0` to force a single `chat/completions` round-trip (no `tools` in the body). |
| `LOCAL_AGENT_MAX_ROUNDS` | `8` | Max tool round-trips per user message (see `text_chat_agent.py`). |
| `TOOL_HTTP_ALLOW_HOSTS` | `127.0.0.1,localhost,::1` | Comma list of hostnames allowed for **optional** HTTP connector URLs (`rag_url`, `calendar_url`, `connector_url` from the UI). |
| `TOOL_HTTP_ALLOW_SCHEMES` | `http,https` | URL schemes permitted for those connectors. |
| `TOOL_HTTP_ALLOW_PRIVATE_LAN` | `0` | Set `1` to allow **private** IPv4/IPv6 ranges (e.g. a NAS at `192.168.x.x`) after DNS resolution. |
| `TOOL_HTTP_ALLOW_CIDRS` | *(empty)* | Extra comma-separated CIDRs (e.g. `10.0.0.0/8`) for connector URLs. |
| `LOCAL_RAG_DATA_DIR` | *(unset → `offline_setup/rag_data`)* | Override base directory for the FTS index and `documents/` ingest folder. |
| `LOCAL_RAG_DB_PATH` | *(unset)* | Override path to the SQLite FTS file. |
| `LOCAL_CALENDAR_DB_PATH` | *(unset → `…/rag_data/calendar.sqlite`)* | Local calendar events database. |
| `CHAT_ATTACH_MAX_BYTES` | `20971520` (20 MiB) | Per-file cap for `POST /api/chat/attachments`. |

**RAG index:** place `.md` / `.txt` under **`offline_setup/rag_data/documents/`**, then `curl -sk -X POST https://127.0.0.1:7860/api/rag/ingest -H 'Content-Type: application/json' -d '{"reindex_all":true}'`. Status: `GET /api/rag/status`. Discovery: `GET /api/local-tools`.

**VRAM defaults:** local llama uses **`LOCAL_LLAMA_CTX_SIZE` default 49152** and **`LOCAL_LLAMA_N_GPU_LAYERS` default 28** (reduce layers or ctx if XTTS + vision OOM). Ollama-only dialogue defaults to **`gemma4:e2b-it-q4_K_M`** (~5B, vision-capable) with caption **`num_ctx`** aligned to **`LLM_CONTEXT_SIZE`** unless **`VISION_OLLAMA_NUM_CTX`** is set.

**Voice + camera (vision captions via Ollama, separate from dialogue LLM when using local llama):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `VISION_OLLAMA_BASE` | `http://127.0.0.1:11434/v1` | OpenAI-compatible base for image caption requests |
| `VISION_OLLAMA_MODEL` | `gemma4:e2b-it-q4_K_M` (stack + app default) | Ollama vision model; set `gemma4:26b-a4b-it-q4_K_M` for heavier captions if VRAM allows |
| `NVIDIA_LLM_LOCAL_OPENAI_MODEL` | *(unset → `LOCAL_LLAMA_MODEL_NAME`)* | When using **local llama primary**, OpenAI `model` string sent to llama-server (do not set this to an Ollama tag) |
| `VISION_OLLAMA_TIMEOUT` | `45` | Caption HTTP timeout (seconds) |
| `VISION_FRAME_TTL_SEC` | `5` | Drop camera frames whose **last receive time** is older than this (seconds). Lower (e.g. `2`) for stricter “live only”; raise if you see spurious `[Camera: no recent frame]` on slow links or voice lag |
| `VISION_CAPTION_MAX_TOKENS` | `512` | Hard-clamped **120–1200** in code. Lower (**180–280**) for faster captions but more mid-sentence cutoffs if the model uses long preambles. |
| `VISION_CAPTION_OPENAI_COMPAT` | **`1`** in code if unset; **`start_current_stack.sh` exports `0`** | **`0`** / **`false`**: skip `/v1/chat/completions` and use **only** Ollama native **`/api/chat`** (one GPU round-trip; lower latency). Set **`1`** to try OpenAI compat first (legacy). Manual `uv run … bot_vllm.py` without the start script follows the **code** default (**compat first**) unless you export this var. |
| `VISION_CAPTION_TEMPERATURE` | *(unset)* | Optional sampling temperature for caption requests (OpenAI compat + native `options`); unset leaves server default |
| `VISION_AUGMENT_SESSION_LEVEL` | `0` | **`0`** (default when unset in code): only utterances matching **`vision_intents.json`** get `[Camera context]` (lower latency for non-visual chat). **`1`**: while vision is enabled, **every** user message gets camera context (regex still accepted). The **Assistant Console** checkbox **“Every reply uses live camera”** sends **`vision_augment_every_turn`** on **`client-ready`** / text chat to override this per session (opt-in to every-turn captions). Same-frame captions are **cached** so Ollama is not called again for the same JPEG `frame_id` unless the user asks for a refresh/recheck. |
| `VISION_POLICY_DIR` | *(unset)* | Directory containing `substantive.txt`, `unavailable.txt`, `refresh_note.txt`, `system_addon.txt`, `camera_context_template.txt`, `caption_instruction_*.txt`, and packaged `vision_intents.json` when overriding as a set |
| `VISION_INTENTS_FILE` | *(unset)* | Path to `vision_intents.json` (regex groups for vision / refresh / color-probe triggers); defaults to `pipecat_bots/vision_policy/vision_intents.json` |
| `VISION_CAPTION_INSTRUCTION_BASE_FILE` / `VISION_CAPTION_INSTRUCTION_RECHECK_FILE` | *(unset)* | Override caption prompt files (see `docs/e2e_vision_voice_tests.md`) |
| `VISION_POLICY_SUBSTANTIVE_FILE`, `VISION_POLICY_UNAVAILABLE_FILE`, `VISION_POLICY_REFRESH_NOTE_FILE`, `VISION_SYSTEM_ADDON_FILE`, `VISION_CAMERA_CONTEXT_TEMPLATE_FILE` | *(unset)* | Override individual policy snippets |

**Vision caption latency (typical multi‑second bottleneck):** Most round-trip time is **Ollama VLM inference**, not Python. To reduce it: set **`VISION_CAPTION_OPENAI_COMPAT=0`** if you only need native **`/api/chat`** (avoids a second full inference when compat runs first), use a **smaller/faster** vision model (`VISION_OLLAMA_MODEL`, e.g. lighter tags than full Gemma26), lower **`VISION_CAPTION_MAX_TOKENS`**, smaller JPEGs from the client (narrow **max width** / quality in the Assistant Console or the standalone vision test page), and keep the model **loaded** (avoid cold start). Shorter, task-specific **caption instruction** files also help. **Do not** set `VISION_CHAT_THINK=1` for captions unless you need the model’s thinking channel (adds work).

Policy files ship under **`offline_setup/app/pipecat_bots/vision_policy/`**; no extra start step is required. Set the env vars **before** `lisa_stack.sh start` / `start_current_stack.sh` if you use a custom directory; the bot process inherits the shell environment (including optional `VISION_*` overrides not listed explicitly in `start_current_stack.sh`).

Ollama itself is **not** started or stopped by `lisa_stack.sh`. On each **`start_current_stack.sh`** run, **`ollama stop`** is applied to every runner reported by **`ollama ps`** (plus common Gemma tags) **before XTTS starts**, so leftover VRAM from a prior session does not block XTTS or local llama. Opt out with **`STACK_UNLOAD_OLLAMA_AT_START=0`**. Keep **`ollama serve`** running and **`ollama pull`** a vision-capable model if needed.

**`ollama` HTTP listen address:** The stack and **`VISION_OLLAMA_BASE`** default to **`http://127.0.0.1:11434/v1`**. If **`curl http://127.0.0.1:11434/api/tags`** fails, vision captions and optional startup unload cannot talk to Ollama. A common cause is **`/etc/systemd/system/ollama.service.d/override.conf`** setting **`OLLAMA_HOST`** to a **host IP that is not up** (e.g. WireGuard **`10.x`** only), which makes **`ollama serve`** exit with *bind: cannot assign requested address* and nothing listens on loopback.

- **Fix (recommended):** from **`offline_setup`**, run **`sudo bash scripts/ollama_listen_localhost.sh`** for **`127.0.0.1:11434`**, or add **`--all-interfaces`** for **`0.0.0.0:11434`**. The script writes **`zzz-ollama-bind.conf`** so it sorts **after** **`override.conf`** and overrides a stale **`OLLAMA_HOST`** there.
- **`start_current_stack.sh`** sets **`OLLAMA_HOST`** for the **`ollama`** CLI to match **`VISION_OLLAMA_BASE`** (with a loopback fallback when the configured root is down but **`127.0.0.1`** answers). If Ollama is still unreachable, it **skips** GPU unload and prints the same **`ollama_listen_localhost.sh`** hint instead of failing the whole stack.

## What is not stopped

- **Ollama** as a **service** is not stopped by `lisa_stack.sh` (you may still unload models with `ollama stop <name>` if you need VRAM).
- Other unrelated Docker containers or processes are untouched.

## URLs after a successful start

See **`docs/links.md`** for HTTPS client URLs (default **https://127.0.0.1:7860** → bot on **7861**).

After **`start_current_stack.sh`** completes, the console also prints:

- **Mobile voice + vision test:** `https://127.0.0.1:<BOT_PORT>/mobile-voice-vision-test` (mic + camera JPEG + `/ws/mobile-voice` + `/ws/mobile-voice-vision`)
- **Vision discovery JSON:** `https://127.0.0.1:<BOT_PORT>/api/mobile-voice-vision`
- **Image caption smoke test (no WebSocket):** `POST /api/vision/preview` with multipart field **`image`** (JPEG)

## Testing voice + vision

| Check | What it proves |
|-------|----------------|
| `curl -sk https://127.0.0.1:7860/api/mobile-voice-vision` | Bot serves vision discovery |
| `curl -sk -X POST -F "image=@photo.jpg" https://127.0.0.1:7860/api/vision/preview` | Ollama vision path + model (`VISION_OLLAMA_MODEL`) work |
| Open **`/mobile-voice-vision-test`** in a browser | Live **camera** → JPEG over **`/ws/mobile-voice-vision`**, voice over **`/ws/mobile-voice`**; say e.g. “what do you see?” |

Automated checks in CI/agent runs typically use **preview + discovery**; **full live camera + STT + caption + TTS** is validated in the browser on a machine with mic, camera, GPU, and Ollama.

**Automated E2E (text + vision WebSocket + preview):** from `offline_setup/app` run  
`uv run python scripts/e2e_vision_voice_test.py`  
(documented in **`docs/e2e_vision_voice_tests.md`**).

## Logs

- Bot: `/tmp/bot.log`
- HTTPS proxy: `/tmp/https_proxy.log`
- Pidfiles: `/tmp/lisa_bot.pid`, `/tmp/lisa_tls_proxy.pid`
- Log rotation: on each start, logs **> 5 MiB** are renamed to `.prev` (`LISA_LOG_KEEP_BYTES` override)
- Context stress (UI spawn): `offline_setup/app/logs/context_stress_run.log`

## Power & GPU time stats

Assistant Console **Power & GPU time** (Wh, GPU util·time) persists to:

- **Server:** `offline_setup/app/assistant_console_power_stats.v1.json`
- **Browser:** `localStorage` (per origin)

Counters increase while the console is open and polling `/api/runtime-status`. Stack restart does **not** reset server totals.

Full guide: **[power-gpu-stats.md](power-gpu-stats.md)**.

## Chat sessions (Assistant Console)

Chat session names, messages, and the active session id persist in two places:

- **Browser:** `localStorage` keys `assistantConsole.sessions.v1` and `assistantConsole.activeSession.v1` (per origin — `https://127.0.0.1:7860` and `https://127.0.0.1:8088` are separate)
- **Server:** `offline_setup/app/assistant_console_chat_sessions.v1.json` via `GET`/`PUT /api/assistant-console/chat-sessions` (no admin token; user chat data)

On load the console **syncs from the server** and keeps whichever copy has the newer `updatedAt`. Edits debounce-sync back to disk. LLM generation settings use `localStorage` key `assistantConsole.llmGen.v1` (migrated from tab-only `sessionStorage` on first read).

Instructions presets remain on disk under `docs/instructions/*.md`. In **`start --admin`** mode, save/delete in **Instructions Manager** requires the admin token (same as Face Manager and RAG Arena).

## Context stress test

CLI (no extra env):

```bash
cd offline_setup/app
uv run python scripts/context_max_stress_test.py \
  --base-url https://127.0.0.1:7860 --insecure --live-gpu --max-steps 8
```

UI background run: set `ENABLE_CONTEXT_STRESS_RUN_API=1` before start (optional in `lisa_admin_token.env`).

Full guide: **[context-stress-test.md](context-stress-test.md)**.

## Security bind modes (Phase 1 remediation)

`lisa_stack.sh` sets bind mode automatically:

| Command | Bind | Admin token |
|---------|------|-------------|
| **`start`** (default) | Loopback only (`127.0.0.1`) | Not required from this machine |
| **`start --admin`** | Loads `lisa_admin_token.env` → usually `LISA_BIND_PUBLIC=1`, all interfaces | **Required** for admin mutations (RAG delete, routing, stack restart) |

Manual env overrides still work if set **before** `start` (advanced). Prefer the flags.

**First-time admin setup:**

```bash
cp offline_setup/lisa_admin_token.env.example offline_setup/lisa_admin_token.env
# edit LISA_ADMIN_TOKEN — openssl rand -hex 24
./offline_setup/lisa_stack.sh start --admin
```

**Return to home dev:**

```bash
./offline_setup/lisa_stack.sh start
```

(`start` clears `LISA_BIND_PUBLIC` and `LISA_ADMIN_TOKEN` and sets `LISA_BIND_LOOPBACK_ONLY=1`.)

See **[admin-token-and-security.md](admin-token-and-security.md)** for token usage in the UI, curl examples, and troubleshooting.

Read-only GET endpoints (`/api/runtime-status`, `/api/mobile-voice`, etc.) stay public. Mutating POST/PUT/PATCH/DELETE on `/api/*` require the Bearer token when loopback-only is off.

Full phase-by-phase remediation notes: **[remediation-phases-2026-05.md](remediation-phases-2026-05.md)**.

**Admin token, simpler home-dev mode, routing, and HTTP 422 troubleshooting:** **[admin-token-and-security.md](admin-token-and-security.md)**.

**Documentation index:** **[README.md](README.md)** (power stats, context stress, security, links).

## Remediation plan status (May 2026)

**All seven phases complete:** security/auth/CSP, SPA regressions, persistence/413 caps, async offloads, voice/UX, pidfiles + readiness probe, log rotation, final smoke — all passed. See **[remediation-phases-2026-05.md](remediation-phases-2026-05.md)** for details and verification table.
