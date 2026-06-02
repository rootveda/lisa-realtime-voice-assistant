# Context stress test

The **context stress test** pushes ever-larger prompts through `/api/text-chat/completions` until the backend hits a context limit, OOM, or HTTP error. Use it to validate local llama / Ollama context sizing, VRAM headroom, and Assistant Console live telemetry.

Related:

- [stack-start-stop.md](stack-start-stop.md) — env vars, start/stop
- [admin-token-and-security.md](admin-token-and-security.md) — admin token (needed for UI run on LAN mode)
- [power-gpu-stats.md](power-gpu-stats.md) — power/GPU counters during load

---

## What it does

| Behavior | Detail |
|----------|--------|
| **Target API** | `POST /api/text-chat/completions` |
| **Growth** | Padding characters multiply each step (default growth **1.35×**) |
| **Stop conditions** | HTTP error (e.g. `exceed_context_size_error`), `--max-steps` cap, timeout |
| **Live GPU** | Optional `nvidia-smi` lines in terminal (`--live-gpu`) |
| **UI mirror** | POSTs state to `/api/context-stress/live` → **LIVE** line in Assistant Console footer |
| **Log (UI spawn)** | `offline_setup/app/logs/context_stress_run.log` |

Typical failure on local llama with **49 152** ctx: step ~7 when prompt exceeds ~49k tokens.

---

## Paths (important)

The script lives under **`lisa/nvidia_voice`**, not the parent backup folder:

```text
lisa/nvidia_voice/offline_setup/app/scripts/context_max_stress_test.py
```

**Wrong** (will fail):

```bash
cd /path/to/parent   # parent of repo — no offline_setup here
cd offline_setup/app
```

**Correct:**

```bash
cd /path/to/lisa/nvidia_voice/offline_setup/app
```

---

## Option A — CLI (always available)

No extra env vars. Stack must be running.

```bash
cd lisa/nvidia_voice/offline_setup/app

# HTTPS (port 7860 — self-signed cert)
uv run python scripts/context_max_stress_test.py \
  --base-url https://127.0.0.1:7860 --insecure --live-gpu --max-steps 8

# Direct HTTP bot (port 7861)
uv run python scripts/context_max_stress_test.py \
  --base-url http://127.0.0.1:7861 --live-gpu --max-steps 25
```

### Useful flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--max-steps` | 40 | Safety cap on growth iterations |
| `--initial-chars` | 20000 | Starting padding size |
| `--growth` | 1.35 | Multiply pad length after each success |
| `--max-tokens` | 32 | Small completion budget (isolates prompt KV) |
| `--live-gpu` | off | Print VRAM/util every 2s during requests |
| `--no-publish` | off | Skip POST to `/api/context-stress/live` |
| `--timeout` | 600 | Per-request timeout (seconds) |

---

## Option B — Assistant Console (background spawn)

Requires **`ENABLE_CONTEXT_STRESS_RUN_API=1`** on the bot process.

### Enable

Add to shell or `offline_setup/lisa_admin_token.env`:

```bash
export ENABLE_CONTEXT_STRESS_RUN_API=1
# optional — required when triggering from non-loopback (LAN IP):
# export CONTEXT_STRESS_RUN_TOKEN=your-secret
# optional — override URL the spawned script uses:
# export CONTEXT_STRESS_BASE_URL=https://127.0.0.1:7860
```

Restart (with UI context stress enabled):

```bash
cd lisa/nvidia_voice
# optional: add ENABLE_CONTEXT_STRESS_RUN_API=1 to offline_setup/lisa_admin_token.env
./offline_setup/lisa_stack.sh restart --admin
```

Home dev only: `./offline_setup/lisa_stack.sh restart` (no `--admin`).

Verify:

```bash
curl -sk https://127.0.0.1:7860/api/context-stress/run/capabilities
# → {"run_api_enabled": true, "token_gate": false}
```

### Run from UI

1. Open `https://127.0.0.1:7860/assistant-console` (or LAN URL).
2. Expand **LLM Routing**.
3. If `admin_token_required`, paste **Admin token** (see [admin-token-and-security.md](admin-token-and-security.md)).
4. Set **Max steps** (1–80).
5. Click **Run context stress test (background)**.
6. Watch **LIVE** line in the chat footer; check log at `offline_setup/app/logs/context_stress_run.log`.

### Run status API

```bash
curl -sk https://127.0.0.1:7860/api/context-stress/run/status
```

---

## API reference

| Endpoint | Method | Admin token | Purpose |
|----------|--------|-------------|---------|
| `/api/context-stress/live` | GET | No | Read live stress state (footer poll) |
| `/api/context-stress/live` | POST | No | Script publishes progress |
| `/api/context-stress/run/capabilities` | GET | No | Is UI spawn enabled? |
| `/api/context-stress/run/status` | GET | Gate* | Active pid / log path |
| `/api/context-stress/run` | POST | Gate* | Spawn background script |

\* **Gate:** `ENABLE_CONTEXT_STRESS_RUN_API=1` required. If `CONTEXT_STRESS_RUN_TOKEN` is set, send header `X-Context-Stress-Token`. Without token, only loopback clients may POST. LAN + admin mode also requires **LISA admin token** for `/api/context-stress/run` (mutating admin route).

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_CONTEXT_STRESS_RUN_API` | unset | Must be `1` for UI/API background spawn |
| `CONTEXT_STRESS_RUN_TOKEN` | unset | Optional shared secret for non-loopback run POST |
| `CONTEXT_STRESS_BASE_URL` | bot HTTP port | URL passed to spawned script (`--base-url`) |

Propagated by `offline_setup/start_current_stack.sh` → `bot_vllm.py`.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `No such file or directory` for script | Use path under `lisa/nvidia_voice/offline_setup/app` |
| `run_api_enabled: false` | Set `ENABLE_CONTEXT_STRESS_RUN_API=1` and restart stack |
| `exceed_context_size_error` at ~step 7 | Expected with 49k ctx; raise `LOCAL_LLAMA_CTX_SIZE` or lower `--max-steps` |
| No **LIVE** footer line | Omit `--no-publish`; keep console open |
| Denied from LAN | Set `CONTEXT_STRESS_RUN_TOKEN` + header, or run CLI from the server |
| Power stats not moving | Stress alone does not update Wh — see [power-gpu-stats.md](power-gpu-stats.md) |

---

## Quick reference

```bash
# CLI smoke (8 steps)
cd lisa/nvidia_voice/offline_setup/app
uv run python scripts/context_max_stress_test.py \
  --base-url https://127.0.0.1:7860 --insecure --live-gpu --max-steps 8

# Enable UI spawn
export ENABLE_CONTEXT_STRESS_RUN_API=1
./offline_setup/lisa_stack.sh restart   # from lisa/nvidia_voice
```
