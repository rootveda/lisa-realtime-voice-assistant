# Links and endpoints (Lisa / offline stack)

Defaults assume the **canonical** stack from `offline_setup/start_current_stack.sh`: HTTPS on **7860** → HTTP bot on **7861** (when `ENABLE_HTTPS=1`). Replace `127.0.0.1` with your LAN IP when testing from other devices (e.g. **`<LAN-IP>`**).

**Remediation (May 2026):** all seven hardening phases are documented in **`docs/remediation-phases-2026-05.md`**. For LAN + admin token: **`./offline_setup/lisa_stack.sh start --admin`**.

**Start / stop / restart / status:** **`docs/stack-start-stop.md`**. One script: **`offline_setup/lisa_stack.sh`**.

| Mode | Command |
|------|---------|
| Home dev (localhost, no token) | `./offline_setup/lisa_stack.sh start` |
| Admin (LAN + token) | `./offline_setup/lisa_stack.sh start --admin` |
| Admin + nginx LAN proxy | `./offline_setup/lisa_stack.sh start --admin --network-proxy` |

**Docs index:** **`docs/README.md`**. **Admin auth:** **`docs/admin-token-and-security.md`**. **Network proxy:** **`docs/network-proxy.md`**. **Power/GPU stats:** **`docs/power-gpu-stats.md`**. **Context stress test:** **`docs/context-stress-test.md`**.

**Voice + live video:** **`docs/video_voice_build_plan.md`** (wire protocol). Test page **`/mobile-voice-vision-test`**, discovery **`/api/mobile-voice-vision`**. Short overview: **`docs/video_voice_realtime_plan.md`**. **Automated E2E script:** **`docs/e2e_vision_voice_tests.md`** (`scripts/e2e_vision_voice_test.py`). **Env + policy files on disk:** **`docs/stack-start-stop.md`** (vision Ollama vars, **`VISION_POLICY_DIR`**, **`VISION_INTENTS_FILE`**).

---

## User-facing pages (bot host)

| What | URL (HTTPS) | Notes |
|------|-------------|--------|
| Playground (Pipecat) | `https://127.0.0.1:7860/client` | Main WebRTC client |
| Mobile voice test | `https://127.0.0.1:7860/mobile-voice-test` | Browser test page |
| Mobile voice + vision | `https://127.0.0.1:7860/mobile-voice-vision-test` | Mic + camera JPEG + voice |
| Assistant Console | `https://127.0.0.1:7860/assistant-console` | `sci_fi_assistant.html` |
| Assistant Console (LAN) | `https://<LAN-IP>:7860/assistant-console` | Direct TLS when `LISA_BIND_PUBLIC=1` |
| Assistant Console (nginx) | `https://<LAN-IP>:8088/assistant-console` | If `Network/nginx/lisa-bot.conf` is enabled |
| Legacy redirect | `https://127.0.0.1:7860/sci-fi-assistant` | → `/assistant-console` |
| Diagnose | `https://127.0.0.1:7860/diagnose` | |
| Offline client | `https://127.0.0.1:7860/offline-client` | may redirect to `/client/` |
| WebSocket voice page | `https://127.0.0.1:7860/ws-voice` | HTML for raw WS client |

With HTTPS off (`ENABLE_HTTPS=0`), use `http://127.0.0.1:7861/...` (or whatever `BOT_PORT` / `HTTP_BOT_PORT` is).

---

## REST / JSON on the bot (same host as above)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/mobile-voice` | Discovery: ASR/TTS/LLM metadata, WebSocket usage |
| `GET` | `/api/mobile-voice/info` | Same payload as above |
| `GET` | `/api/mobile-voice-vision` | Vision WebSocket + session fields, Ollama model env |
| `GET` | `/api/mobile-voice-vision/info` | Same as above |
| `POST` | `/api/vision/preview` | Multipart field `image` (JPEG) → `{ ok, caption }` via Ollama |
| `GET` | `/api/runtime-status` | LLM provider, model, TTS, GPU summary |
| `GET` | `/api/ollama-models` | Names from `ollama list` |
| `GET` | `/api/models` | Static pipeline model blurb (ASR/LLM/TTS) |
| `GET` | `/api/host-ip` | Suggested LAN / dummy URLs for ICE |
| `GET` | `/api/instructions` | List saved presets: `{ default_id, items:[{id,label,filename}] }` from `docs/instructions/instruction*.md` |
| `GET` | `/api/instructions/{id}` | One preset body: `{ id, label, prompt }` (e.g. `instruction1`) |
| `GET` | `/api/default-instructions` | Default prompt; optional query `?id=instruction1`. Legacy `docs/Instructions.md` only if no `instruction*.md` exist |
| `POST` | `/api/llm-routing/apply` | Switch local vs Ollama; **restarts stack** (JSON: `provider`, `model`) |
| `POST` | `/api/text-chat/completions` | Same-origin proxy to active OpenAI-compatible LLM; optional `vision_session_id` / `vision_enable` for live camera (see **`docs/e2e_vision_voice_tests.md`**) |
| `GET` | `/sw.js` | Service worker (if present) |
| `GET` | `/static/call-machine-object-bundle.js` | Bundle asset |

WebRTC offer (from Pipecat / test client): **`POST /api/offer`** (on the bot base URL, e.g. `http://localhost:7860` in scripts).

---

## WebSockets (bot)

| Path | Use |
|------|-----|
| `/ws/mobile-voice` | RTVI + raw PCM; default UI builds `wss://<host>/ws/mobile-voice` |
| `/ws/mobile-voice-vision` | JPEG camera frames; query `session_id` + binary framing (see `docs/video_voice_build_plan.md`) |
| `/ws/voice` | Raw PCM WebSocket transport |

---

## Upstream services (not the bot — local processes / Docker)

| Service | Default base | Env / script |
|--------|----------------|--------------|
| **ASR (Nemotron / Parakeet)** | `http://127.0.0.1:8080/health` | `NVIDIA_ASR_URL` — WebSocket e.g. `ws://127.0.0.1:8080` |
| **XTTS (Coqui)** | `http://127.0.0.1:80` — `GET /studio_speakers`, `POST /tts` | `XTTS_TTS_URL` (stack uses port **80**; `start_xtts.sh` uses `XTTS_PORT`, default 80) |
| **Local llama.cpp server (Gemma4 GGUF)** | `http://127.0.0.1:8000/v1` | `NVIDIA_LLM_URL` when primary; health `http://127.0.0.1:8000/health` |
| **Ollama (OpenAI-compatible)** | `http://127.0.0.1:11434/v1` | `NVIDIA_LLM_URL` when Ollama route; native API on **11434** |

TTS **defaults in older docs / tests** sometimes use `http://127.0.0.1:8001` — the **current** stack pins XTTS to **80** via `XTTS_TTS_URL`.

---

## Models — local (files on disk)

| Role | Default path / name | Notes |
|------|---------------------|--------|
| Local LLM (GGUF) | `offline_setup/models/gemma-4-26B-A4B-it-Q4_K_M.gguf` | `LOCAL_LLAMA_MODEL_FILE` |
| API model id (local server) | `gemma4-26b-a4b-it-q4_K_M-local` | `LOCAL_LLAMA_MODEL_NAME` |
| Context window | `LOCAL_LLAMA_CTX_SIZE` / `LLM_CONTEXT_SIZE` (default **49152** in `start_current_stack.sh`; raise with care — VRAM) | llama-server `--ctx-size`; Ollama text + vision `num_ctx`; see `docs/stack-start-stop.md` |

HF cache roots used by the stack: `offline_setup/hf_cache`, `offline_setup/xtts_hf_cache` (not URLs — local dirs).

---

## Models — Ollama (tags)

These appear in scripts and routing defaults; your machine’s actual list is whatever `ollama list` / **`GET /api/ollama-models`** returns.

| Tag | Where used |
|-----|------------|
| `gemma4:e2b-it-q4_K_M` | Default Ollama dialogue + vision caption tag in stack / `vision_caption` (smaller VRAM) |
| `gemma4:26b-a4b-it-q4_K_M` | Heavier Ollama option; `context_limit_perf_test.py` may still reference 26b |
| `gemma4:31b` | `start_current_stack.sh` — `ollama stop` best-effort unload |

Routing apply defaults empty Ollama model to **`gemma4:e2b-it-q4_K_M`** (`pipecat_offline_patch.py`).

---

## Environment shortcuts (reference)

- **LLM:** `NVIDIA_LLM_URL`, `NVIDIA_LLM_MODEL`, `NVIDIA_LLM_API_KEY` (often `not-needed`)
- **Voice:** `NVIDIA_ASR_URL`, `TTS_PROVIDER`, `XTTS_TTS_URL`, `XTTS_VOICE_ID` (e.g. `Andrew Chipper`)
- **Ports:** `BOT_PORT` (7860), `HTTP_BOT_PORT` (7861), `ENABLE_HTTPS`
- **Routing:** `USE_LOCAL_LLAMA_PRIMARY`, `LOCAL_LLAMA_*` family in `start_current_stack.sh`
- **Security:** `LISA_BIND_PUBLIC`, `LISA_BIND_LOOPBACK_ONLY`, `LISA_ADMIN_TOKEN` — see **`docs/admin-token-and-security.md`** and **`docs/stack-start-stop.md`** (`--admin` flag)

---

## Placeholder URLs (Assistant Console UI only)

The console may store tool endpoints such as:

- RAG: e.g. `https://rag.local/query`
- Calendar: e.g. `https://calendar.local/events`
- Web tool: e.g. `https://search.local/run`

These are **examples** in the HTML placeholders unless you wire real services.

---

## Instruction presets (on disk)

- **Repo (preferred):** `docs/instructions/instruction1.md`, `assistant_personalized_example.md`, … — editable copies used first when the bot can resolve the repo root.
- **Bundled with the app:** `offline_setup/app/presets/instructions/instruction*.md` — same presets shipped beside `bot_vllm.py`, used when `docs/` is not mounted or not found at runtime. Same stem in **repo overrides** bundled.

The short file `docs/Instructions.md` is documentation only; it is **not** served as the live system prompt when it looks like a pointer.

---

## External references (images / upstream projects)

- XTTS Docker: `ghcr.io/coqui-ai/xtts-streaming-server` (see `scripts/start_xtts.sh` for tags)
- Ollama: [https://ollama.com](https://ollama.com) — API on host port **11434**
