# Lisa stack — nginx LAN reverse proxy (`--network-proxy`)

Optional **nginx** site in front of the Lisa bot so phones, tablets, and other PCs on your LAN can open the Assistant Console over **HTTPS** without changing app code.

Related:

- [stack-start-stop.md](stack-start-stop.md) — `lisa_stack.sh` commands and bind modes
- [admin-token-and-security.md](admin-token-and-security.md) — `--admin` and `LISA_ADMIN_TOKEN`
- [links.md](links.md) — URLs and ports

---

## What `--network-proxy` does

After the stack is up, `lisa_stack.sh` runs **`Network/scripts/lisa-network-proxy.sh install`** (needs **sudo**):

1. Copies/enables the nginx site from **`Network/nginx/lisa-bot.conf`** in your checkout
2. Runs **`nginx -t`**
3. **Reloads nginx**

The proxy listens on **`LISA_NGINX_LAN_PORT`** (default **8088**) and forwards to the bot TLS proxy on **`127.0.0.1:7860`**.

```text
Phone / laptop on LAN
        │
        ▼
  nginx :8088  (HTTPS, self-signed cert)
        │
        ▼
  tls_tcp_proxy :7860  →  bot_vllm :7861
```

**`lisa_stack.sh stop` does not stop nginx.** The site may keep listening until you disable it manually (see below).

---

## When you need it

| Situation | Use `--network-proxy`? |
|-----------|-------------------------|
| Open Assistant Console **only on the host PC** (`https://127.0.0.1:7860`) | **No** |
| Open from **phone/tablet on Wi‑Fi** with mic + camera | **Often yes** — stable LAN URL on **8088** |
| You already use **`start --admin`** and **`https://<LAN-IP>:7860`** works from other devices | **Optional** — direct bind may be enough |
| You want **one published port** (8088) in the firewall instead of 7860 | **Yes** |
| No nginx installed, or no sudo on the host | **No** — use direct LAN bind (`--admin`) or localhost only |

**Typical LAN setup:**

```bash
cd lisa/nvidia_voice
./offline_setup/lisa_stack.sh start --admin --network-proxy
```

Then on another device: **`https://<host-LAN-IP>:8088/assistant-console`** (accept the self-signed certificate warning — required for microphone/camera on HTTPS).

---

## When you do **not** need it

- **Home dev** on one machine: `./offline_setup/lisa_stack.sh start` (loopback only).
- You are fine hitting **`https://<LAN-IP>:7860`** directly after **`start --admin`** (`LISA_BIND_PUBLIC=1`).
- The checkout has **no `Network/` folder** (see prerequisite below) — the flag prints a warning and skips install.

---

## Prerequisite: `Network/` folder in checkout

The installer walks **upward** from `offline_setup/` looking for:

```text
Network/nginx/lisa-bot.conf
Network/scripts/lisa-network-proxy.sh
```

If those files are missing (some backup trees omit `Network/`), `--network-proxy` logs a warning and continues — the bot still runs.

Manual retry after install:

```bash
sudo bash /path/to/checkout/Network/scripts/lisa-network-proxy.sh install /path/to/checkout
```

---

## Commands

| Command | Effect |
|---------|--------|
| `./offline_setup/lisa_stack.sh start --network-proxy` | Start stack + install/reload nginx site |
| `./offline_setup/lisa_stack.sh start --admin --network-proxy` | LAN bind + admin token + nginx (common for phones) |
| `LISA_NETWORK_PROXY=1 ./offline_setup/lisa_stack.sh restart` | Same as `--network-proxy` |
| `./offline_setup/lisa_stack.sh status` | If `lisa-bot.conf` is enabled, probes **`https://127.0.0.1:8088/api/runtime-status`** |

### Disable nginx site (does not stop the bot)

```bash
sudo rm /etc/nginx/sites-enabled/lisa-bot.conf
sudo nginx -t && sudo systemctl reload nginx
```

Or stop nginx entirely: `sudo systemctl stop nginx`

---

## Ports summary

| Port | Service | Reachable from |
|------|---------|----------------|
| **7860** | Bot HTTPS (TLS proxy) | Loopback by default; all interfaces with `--admin` |
| **7861** | Bot HTTP | Same as 7860 |
| **8088** | nginx → bot (when proxy enabled) | LAN (nginx bind) |

See [links.md](links.md) for full URL table.

---

## Admin token with nginx

nginx terminates HTTPS and forwards to the bot. With **`--admin`**, mutating admin APIs still require **`LISA_ADMIN_TOKEN`** in the browser or curl — same as direct LAN access on 7860.

Paste the token in **LLM Routing** or **RAG Arena** on the client device. See [admin-token-and-security.md](admin-token-and-security.md).

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `WARN: could not find Network/nginx/lisa-bot.conf` | Checkout has no `Network/` tree — use direct `:7860` or restore `Network/` from full repo |
| `nginx reverse-proxy setup failed (need sudo?)` | Re-run install with sudo (see manual command above) |
| Phone cannot connect to `:8088` | Host firewall, wrong LAN IP, or nginx site not enabled — run `./offline_setup/lisa_stack.sh status` |
| Mic/camera blocked | Must use **HTTPS**; accept self-signed cert on the phone |
| Proxy up but bot DOWN | Fix stack first: `./offline_setup/lisa_stack.sh status` |
