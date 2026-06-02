# Contributing

## Never commit

- `offline_setup/lisa_admin_token.env` or any file containing `LISA_ADMIN_TOKEN=<hex>`
- **Personal instruction presets** (real people's names, private child profiles). Ship only generic `*_example.md` templates.
- Contents under `offline_setup/rag_data/` (documents, attachments, SQLite indexes) — only `.gitkeep` skeletons belong in Git
- `offline_setup/app/assistant_console_*.v1.json` (chat sessions, power stats)
- `offline_setup/app/rag_data/` except `.gitkeep`
- Large artifacts: `offline_setup/models/`, `hf_cache/`, `bundle/`, `xtts_tts_data/`, `.venv/`, `.cache/`

Run before push:

```bash
./scripts/pre_push_sanity.sh
```

## Setup for new machines

See **[SETUP.md](SETUP.md)** for hardware, dependencies, model downloads, and first start.

## Doc changes

Keep machine-specific paths out of tracked files (`/home/...`, LAN IPs, backup folder names). Use `/path/to/lisa/nvidia_voice` and `<LAN-IP>` placeholders.
