# Lisa admin token, bind modes, and routing

This guide covers **who can change the stack** (admin auth), **how to run in simpler home-dev mode**, **LLM routing (Apply Routing)**, correct URLs, and troubleshooting common errors including **HTTP 422**.

Related docs:

- [README.md](README.md) — documentation index
- [stack-start-stop.md](stack-start-stop.md) — **`lisa_stack.sh`**, **`--admin`**, **`--network-proxy`**
- [network-proxy.md](network-proxy.md) — nginx LAN reverse proxy (when to use `--network-proxy`)
- [power-gpu-stats.md](power-gpu-stats.md) — Wh / GPU persistence
- [context-stress-test.md](context-stress-test.md) — context limit stress test
- [remediation-phases-2026-05.md](remediation-phases-2026-05.md) — full security remediation history
- [links.md](links.md) — HTTPS/LAN URLs

---

## What is `LISA_ADMIN_TOKEN`?

`LISA_ADMIN_TOKEN` is a **shared secret** (not a user login). The bot checks it on **mutating** HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`) under `/api/*` when the stack is configured to require it.

| Property | Detail |
|----------|--------|
| Format | Any string; generate with `openssl rand -hex 24` (48 hex chars) |
| Sent as | `Authorization: Bearer <token>` |
| Stored | Server: env var at process start. Browser: optional password field in **LLM Routing** panel → `localStorage` key `assistantConsole.lisaAdminToken.v1` |
| On disk | `offline_setup/lisa_admin_token.env` (local only; copy from `.example`) |

### How auth is decided (server)

Logic lives in `pipecat_offline_patch.py`:

1. **Read-only GET** on public discovery paths (e.g. `/api/runtime-status`, `/api/mobile-voice`) — no token.
2. **User-facing mutating paths** (text chat, attachments, vision preview, power-stats POST, context-stress live) — no admin token.
3. **Everything else mutating under `/api/*`** — admin gate applies:
   - If `LISA_BIND_LOOPBACK_ONLY=1` **and** the request peer is loopback → **allow without token**
   - Else if `LISA_ADMIN_TOKEN` is set **and** Bearer token matches (constant-time compare) → **allow**
   - Else → **401/403/503**

The UI asks the server whether a token is required:

```bash
curl -sk https://127.0.0.1:7860/api/admin/capabilities
# → {"admin_token_required":true,"token_configured":true,"bind_loopback_only":false}
```

When `admin_token_required` is true, the **Admin token** field appears in **LLM Routing**, and a **security banner** at the **bottom of Workspace configuration** shows **Admin mode** (amber) vs **Home dev mode** (green).

---

## Bind modes (three profiles)

`lisa_stack.sh` applies bind mode on every **start** / **restart**:

| Profile | Command | Banner in UI |
|---------|---------|--------------|
| **Home dev** (default) | `./offline_setup/lisa_stack.sh start` | Green — “Home dev mode” |
| **Admin / LAN** | `./offline_setup/lisa_stack.sh start --admin` | Amber — “Admin mode” |

Equivalent env: `LISA_STACK_ADMIN=1` for `--admin`.

### 1. Home dev — same machine only (recommended for local work)

Use when you only open the console on the **same PC** that runs the stack. No LAN access, no token paste.

```bash
./offline_setup/lisa_stack.sh start
```

| Setting | Value | Effect |
|---------|-------|--------|
| `LISA_BIND_LOOPBACK_ONLY` | `1` (forced by script) | Admin `/api/*` mutations from `127.0.0.1` work **without** Bearer token |
| `LISA_BIND_PUBLIC` | unset / `0` | Bot and TLS proxy bind **127.0.0.1** only |
| `LISA_ADMIN_TOKEN` | unset (cleared by script) | No shared secret required |

**URLs:** `https://127.0.0.1:7860/assistant-console`

**Trade-off:** Phones or other machines on your LAN **cannot** reach the UI unless you use **`start --admin`** (and optionally **`--network-proxy`**).

---

### 2. LAN / public bind + admin token (phones, other PCs)

Use when you want **HTTPS on all interfaces** and **mutations protected** by a token.

**File:** `offline_setup/lisa_admin_token.env` (create from template):

```bash
export LISA_BIND_PUBLIC=1
export LISA_BIND_LOOPBACK_ONLY=0
export USE_LOCAL_LLAMA_PRIMARY=1
export LISA_ADMIN_TOKEN=<your-secret>
```

**Start:**

```bash
./offline_setup/lisa_stack.sh start --admin
```

Optional nginx LAN proxy (phones, port **8088**): add **`--network-proxy`** — see [network-proxy.md](network-proxy.md).

**Advanced (equivalent):** source the env file yourself before restart:

```bash
set -a && source offline_setup/lisa_admin_token.env && set +a
./offline_setup/lisa_stack.sh restart --admin
```

Note: plain **`restart`** without **`--admin`** resets to home dev unless you exported admin vars manually.

| Setting | Value | Effect |
|---------|-------|--------|
| `LISA_BIND_PUBLIC` | `1` | Listen on `0.0.0.0:7860` (HTTPS) and `0.0.0.0:7861` (HTTP bot) |
| `LISA_BIND_LOOPBACK_ONLY` | `0` | Loopback bypass **off** — admin routes need Bearer token even from this machine |
| `LISA_ADMIN_TOKEN` | set | Required on admin mutations |

**Why loopback bypass is off on LAN:** The TLS proxy terminates HTTPS and forwards to the bot; without `LISA_BIND_LOOPBACK_ONLY=0`, the bot would see clients as loopback and **skip** token checks for anyone who can reach the proxy.

**URLs:**

- This machine: `https://127.0.0.1:7860/assistant-console`
- LAN: `https://<your-LAN-IP>:7860/assistant-console`
- Optional nginx: `https://<your-LAN-IP>:8088/assistant-console` (with **`start --admin --network-proxy`** — [network-proxy.md](network-proxy.md))

**UI:** Open **LLM Routing** → paste token from `lisa_admin_token.env` into **Admin token** → Apply Routing / Full stack restart will send `Authorization: Bearer …`.

---

### 3. Insecure LAN (not recommended)

Binding public **without** a token and with loopback-only still `1` effectively leaves admin routes open to anyone on the LAN who hits the proxy. **Do not use** for anything beyond a trusted isolated network. Prefer profile 2.

---

## LLM routing (Apply Routing)

**UI:** Assistant Console → **LLM Routing** → choose **local llama** or **Ollama** → **Apply Routing (Restart Stack)**.

**API:**

```http
POST /api/llm-routing/apply
Authorization: Bearer <LISA_ADMIN_TOKEN>
Content-Type: application/json

{"provider":"local","model":""}
```

or for Ollama:

```json
{"provider":"ollama","model":"gemma4:e2b-it-q4_K_M"}
```

**What it does:**

1. Validates `provider` is `local` or `ollama`
2. For Ollama: `ollama show` / `ollama pull` if needed
3. Runs `offline_setup/start_current_stack.sh` with updated env (`USE_LOCAL_LLAMA_PRIMARY`, `NVIDIA_LLM_MODEL`, etc.)
4. Returns `{"ok":true,"provider":"…","model":"…"}` on success

The UI then polls `/api/runtime-status` until the active LLM provider matches (can take 1–2 minutes while Docker/models restart).

**curl example (LAN mode):**

```bash
set -a && source offline_setup/lisa_admin_token.env && set +a
curl -sk -X POST "https://127.0.0.1:7860/api/llm-routing/apply" \
  -H "Authorization: Bearer ${LISA_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"provider":"local","model":""}'
```

---

## Troubleshooting

### "No such file or directory" for lisa_admin_token.env or lisa_stack.sh

You are not in **`lisa/nvidia_voice`**. Common case: shell is in **`offline_setup/app`**.

```bash
cd /path/to/lisa/nvidia_voice
./offline_setup/lisa_stack.sh start --admin
```

See [stack-start-stop.md](stack-start-stop.md#working-directory-important).

### "This site can't be reached"

| Mistake | Fix |
|---------|-----|
| `http://127.0.0.1:7860` | Port **7860 is HTTPS only**. Use `https://127.0.0.1:7860` or `http://127.0.0.1:7861` |
| Stack not running | `./offline_setup/lisa_stack.sh status` then `restart` |
| Loopback-only bind from phone | Use LAN profile + `https://<LAN-IP>:7860` |

### "Routing switch failed: Missing Authorization: Bearer token"

You are in **LAN + token** mode (`LISA_BIND_LOOPBACK_ONLY=0`). Paste `LISA_ADMIN_TOKEN` into the **Admin token** field in LLM Routing (or export it in your shell for curl).

In **simpler home-dev mode**, this error should not appear for localhost use.

### "Routing switch failed: HTTP 422"

**Cause (fixed May 2026):** The route decorator was accidentally attached to the internal sync helper `_apply_llm_routing_sync(provider, model)` instead of the async handler that reads JSON. FastAPI treated `provider` and `model` as **required query parameters**, so a JSON body `{ "provider": "local", "model": "" }` returned:

```json
{"detail":[
  {"type":"missing","loc":["query","provider"],"msg":"Field required"},
  {"type":"missing","loc":["query","model"],"msg":"Field required"}
]}
```

**Fix:** Restart the stack after updating `pipecat_offline_patch.py` so `/api/llm-routing/apply` is registered on `apply_llm_routing(request)`.

**Verify after restart:**

```bash
curl -sS -X POST "http://127.0.0.1:7861/api/llm-routing/apply" \
  -H "Authorization: Bearer ${LISA_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"provider":"local","model":""}'
# Expect HTTP 200 and {"ok":true,...} (or 500 if restart script fails — check /tmp/bot.log)
```

**Note:** A full routing apply **restarts the stack**; a brief connection drop or "Invalid HTTP request" during restart is normal. The UI retries via `/api/runtime-status`.

### "Invalid HTTP request received" (HTTP 400) with curl

Usually a **malformed Authorization header**, e.g. pasting the comment line from the env file:

```bash
# Wrong — grep picked up the comment
Authorization: Bearer # must send: Authorization: Bearer $LISA_ADMIN_TOKEN

# Right — source the file or use the value only
set -a && source offline_setup/lisa_admin_token.env && set +a
```

### HTTP 401 / 403 with token present

- Token mismatch (regenerate in env, restart stack, re-paste in UI)
- Stale token in `localStorage` — clear field, paste fresh, blur/change to persist
- `LISA_ADMIN_TOKEN` not loaded — use **`./offline_setup/lisa_stack.sh start --admin`** (or `restart --admin`), not a plain `start` after editing the env file

---

## Endpoints and token requirements

| Endpoint | Method | Admin token when LAN mode |
|----------|--------|---------------------------|
| `/api/runtime-status` | GET | No |
| `/api/admin/capabilities` | GET | No |
| `/api/text-chat/completions` | POST | No |
| `/api/chat/attachments` | POST | No |
| `/api/vision/preview` | POST | No |
| `/api/llm-routing/apply` | POST | **Yes** |
| `/api/stack/restart` | POST | **Yes** |
| `/api/rag/ingest` | POST | **Yes** |
| Most other `/api/*` mutations | * | **Yes** |

---

## Quick reference

| Goal | Command |
|------|---------|
| Home dev, no token | `./offline_setup/lisa_stack.sh start` |
| LAN + token | `./offline_setup/lisa_stack.sh start --admin` |
| LAN + token + nginx :8088 | `./offline_setup/lisa_stack.sh start --admin --network-proxy` |
| Generate token | `openssl rand -hex 24` → put in `offline_setup/lisa_admin_token.env` |
| Check auth mode | `curl -sk https://127.0.0.1:7860/api/admin/capabilities` |
| Stack status | `./offline_setup/lisa_stack.sh status` |
| Bot log | `tail -f /tmp/bot.log` |
