# Power & GPU time stats (Assistant Console)

The **Power & GPU time** card in the Assistant Console tracks cumulative energy (Wh) and weighted GPU busy-time from `nvidia-smi` data exposed on `/api/runtime-status`.

Related:

- [stack-start-stop.md](stack-start-stop.md) — start/stop
- [context-stress-test.md](context-stress-test.md) — heavy GPU load for testing counters

---

## What is tracked

| Display label | Meaning |
|---------------|---------|
| **Session Wh** | Cumulative energy since first sample (long-lived; **not** “since last page refresh”) |
| **Total Wh** | All-time total (plus day/month/year in **full** view) |
| **GPU util·time** | Integral of GPU utilization × elapsed time (Session and Total increment together) |
| **Tariff / cost** | From browser `localStorage` only — **not** server-backed |

**Wh source:** `power.draw` from `nvidia-smi` on each poll.  
**GPU util·time:** blend of GPU %, memory %, and power headroom vs session minimum draw.

---

## Where data is stored (dual layer)

| Layer | Location | Scope |
|-------|----------|--------|
| **Browser** | `localStorage` keys `assistantConsole.powerStats.v1`, `powerSession.v1`, `gpuTimeStats.v1`, `gpuTimeSession.v1` | Per origin (`127.0.0.1` ≠ LAN IP) |
| **Server** | `offline_setup/app/assistant_console_power_stats.v1.json` | Shared across browsers on this machine |

Sync API:

```http
GET  /api/assistant-console/power-stats
POST /api/assistant-console/power-stats
```

POST does **not** require admin token (user-facing mirror endpoint).

On page load the console **hydrates** from the server, then takes the **maximum** of local vs server counters. Server POST **merges with max()** so stale tabs or empty localStorage cannot wipe higher on-disk totals.

---

## When counters increase

Stats increase only while something polls **`/api/runtime-status`** and accumulates samples (~every **2.5 s**):

| Source | Updates stats? |
|--------|----------------|
| **Assistant Console open** (any tab on that origin) | Yes |
| **CLI stress test alone** | No (loads GPU but does not write Wh) |
| **Stack restart** | No reset — file persists |
| **Bot process restart** | No reset |

To see Wh move during headless load testing, keep the console open or use a poller that mirrors the SPA (POST merged payloads to `/api/assistant-console/power-stats`).

---

## Persistence across restart

Verified behavior:

- **Stack restart** (`lisa_stack.sh restart`) — JSON file unchanged; GET returns same totals.
- **Browser refresh** — hydrates from server + localStorage merge.
- **Session start date** (`startedAtSec`) — preserved (earliest of local/server).

File path:

```text
lisa/nvidia_voice/offline_setup/app/assistant_console_power_stats.v1.json
```

Example check:

```bash
curl -sk https://127.0.0.1:7860/api/assistant-console/power-stats | python3 -m json.tool
```

---

## UI views

| View | Shows |
|------|--------|
| **session_total** (default) | Session + Total Wh and GPU util·time |
| **full** | Day / month / year / total breakdown |

Tariff and currency: **Assistant Console → Power & GPU time** inputs → stored in browser only.

Footer line:

```text
Storage: https://… · server-backed totals
```

---

## Common misconceptions

| Misconception | Reality |
|---------------|---------|
| “Session resets on stack restart” | No — session Wh keeps accumulating (since first use or earliest `startedAtSec`) |
| “GPU Session ≠ GPU Total” | Often **equal** — both increment on the same poll; day/month differ in **full** view |
| “Stress test updates Wh automatically” | Only if console (or poller) is running |
| `127.0.0.1` vs LAN IP share stats | **Different** `localStorage`; server file is shared after sync |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Counters stuck at 0 | Open Assistant Console; wait for GPU Live card to show power/util |
| Totals dropped after new browser | Should not happen after merge fix — refresh; check JSON file on disk |
| LAN vs localhost differ | Same server totals after hydrate; localStorage may differ until sync |
| `Invalid HTTP request` on POST | Malformed `Authorization` header — unrelated to power-stats POST (no auth needed) |

---

## Implementation notes (reference)

| Piece | File |
|-------|------|
| SPA accumulation | `offline_setup/app/static/sci_fi_assistant.html` |
| Server load/save/merge | `offline_setup/app/pipecat_bots/pipecat_offline_patch.py` |
| GPU fields in status | `/api/runtime-status` → `gpu[0].power_w`, `util_gpu_pct`, etc. |

Load order (May 2026): hydrate from server **before** runtime-status polling starts, preventing a race that could POST stale zeros.

---

## Quick reference

```bash
# Read current totals
curl -sk https://127.0.0.1:7860/api/assistant-console/power-stats

# On-disk file
cat offline_setup/app/assistant_console_power_stats.v1.json

# Keep counters moving: leave Assistant Console open while stack runs
open https://127.0.0.1:7860/assistant-console
```
