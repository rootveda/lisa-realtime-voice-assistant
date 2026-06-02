# Lisa offline stack — documentation index

Start here for operations, security, and testing.

## First-time setup

| Doc | Topics |
|-----|--------|
| **[SETUP.md](SETUP.md)** | **Hardware, software deps, models, Docker, first start (users + LLMs)** |
| [screenshots/README.md](../screenshots/README.md) | **UI screenshots & feature guide for every page** |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Secrets hygiene, pre-push sanity, what not to commit |

## Operations

| Doc | Topics |
|-----|--------|
| [stack-start-stop.md](stack-start-stop.md) | **`lisa_stack.sh`**, **`--admin`**, **`--network-proxy`**, env vars, vision/RAG, logs |
| [network-proxy.md](network-proxy.md) | nginx LAN reverse proxy — when to use `--network-proxy` |
| [links.md](links.md) | URLs, ports, API endpoints |
| [architecture-overview.md](architecture-overview.md) | Runtime diagram, data flow |
| [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md) | Local operator data backup (RAG, sessions, tokens) |

## Security & admin

| Doc | Topics |
|-----|--------|
| [admin-token-and-security.md](admin-token-and-security.md) | **`--admin`**, `LISA_ADMIN_TOKEN`, home dev vs LAN mode, routing, HTTP 422 |
| [remediation-phases-2026-05.md](remediation-phases-2026-05.md) | Seven-phase hardening history and verification |

## Assistant Console

| Doc | Topics |
|-----|--------|
| [power-gpu-stats.md](power-gpu-stats.md) | Wh / GPU util·time persistence, server file, restart behavior |
| [context-stress-test.md](context-stress-test.md) | Context limit stress test (CLI + UI) |

## Testing

| Doc | Topics |
|-----|--------|
| [e2e_vision_voice_tests.md](e2e_vision_voice_tests.md) | Automated vision/voice E2E |
| [offline_setup/docs/RAG_ENTERPRISE_REGRESSION.md](../offline_setup/docs/RAG_ENTERPRISE_REGRESSION.md) | Offline RAG FTS regression |

---

## Quick start

**Must run from `lisa/nvidia_voice`** (see [stack-start-stop.md](stack-start-stop.md#working-directory-important)).

### Home dev (one machine, no admin token)

Loopback-only. Admin actions (RAG delete, routing, stack restart) work from **this PC** without pasting a token.

```bash
cd /path/to/lisa/nvidia_voice
./offline_setup/lisa_stack.sh start
```

Console: `https://127.0.0.1:7860/assistant-console`

### Admin mode (LAN + token)

Phones/other PCs can reach the stack; mutating admin APIs require **`LISA_ADMIN_TOKEN`**.

```bash
cd /path/to/lisa/nvidia_voice
cp offline_setup/lisa_admin_token.env.example offline_setup/lisa_admin_token.env   # first time only
# edit lisa_admin_token.env — set LISA_ADMIN_TOKEN (openssl rand -hex 24)
./offline_setup/lisa_stack.sh start --admin
```

Paste the token in the UI: **LLM Routing** → **Admin token** (or RAG Arena toolbar).

LAN: `https://<your-LAN-IP>:7860/assistant-console`

### Admin + nginx LAN proxy (phones, port 8088)

When you want nginx in front of the bot (see [network-proxy.md](network-proxy.md)):

```bash
./offline_setup/lisa_stack.sh start --admin --network-proxy
```

LAN via nginx: `https://<your-LAN-IP>:8088/assistant-console`

### Status / stop

```bash
./offline_setup/lisa_stack.sh status
./offline_setup/lisa_stack.sh stop
./offline_setup/lisa_stack.sh help
```

If your shell is in **`offline_setup/app`**: `cd ../..` then run the commands above.
