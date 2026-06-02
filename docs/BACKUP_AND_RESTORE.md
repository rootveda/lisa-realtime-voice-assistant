# Backup and restore (local operator data)

Lisa keeps **runtime and personal data on disk only** — most of it is **gitignored**. Back up these paths before OS reinstall or machine migration.

## What to back up

| Path | Contents |
|------|----------|
| `offline_setup/lisa_admin_token.env` | Admin Bearer token (LAN mode) |
| `offline_setup/rag_data/` | RAG documents, attachments, SQLite FTS index |
| `offline_setup/app/assistant_console_*.v1.json` | Chat sessions, power stats |
| `offline_setup/app/rag_data/` | Face registry SQLite, face runtime JSON |
| `offline_setup/models/` | GGUF weights (large — optional if re-downloadable) |
| `offline_setup/hf_cache/`, `xtts_tts_data/` | Model caches (optional if re-downloadable) |
| `docs/instructions/*.md` | Custom instruction presets you added locally |

## What is in Git (no backup needed for code)

Application source, docs, Dockerfiles, **generic example presets**, dev TLS certs, empty `rag_data/` skeleton.

## Example backup command

```bash
cd /path/to/lisa/nvidia_voice
tar -czvf lisa-local-backup-$(date +%Y%m%d).tar.gz \
  offline_setup/lisa_admin_token.env \
  offline_setup/rag_data \
  offline_setup/app/assistant_console_chat_sessions.v1.json \
  offline_setup/app/assistant_console_power_stats.v1.json \
  offline_setup/app/rag_data \
  docs/instructions
```

Restore by extracting into a fresh clone, then `./offline_setup/lisa_stack.sh start`.

## Never commit

Secrets, RAG content, chat history, face enrollments — see [CONTRIBUTING.md](CONTRIBUTING.md).
