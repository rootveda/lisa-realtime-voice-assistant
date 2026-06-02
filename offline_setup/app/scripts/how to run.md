# How to Run

This project uses one canonical startup script for the full current stack.

## Start all services

From the repo root (`lisa/nvidia_voice`):

```bash
./offline_setup/lisa_stack.sh start
```

Or use the underlying script directly:

```bash
cd offline_setup && ./start_current_stack.sh
```

This starts:
- Nemotron ASR (`:8080`)
- XTTS (`:80`, Andrew Chipper)
- Local llama-server primary (`:8000`) with Ollama fallback
- Bot app (`:7861`)
- HTTPS proxy (`:7860`)

## Stop all services

```bash
./offline_setup/lisa_stack.sh stop
```

Note: this does not stop the global Ollama daemon.

## Test endpoints

- Mobile voice page: `https://127.0.0.1:7860/mobile-voice-test`
- Client page: `https://127.0.0.1:7860/client`
- API info: `https://127.0.0.1:7860/api/mobile-voice`

## Quick health checks

```bash
curl -sf http://127.0.0.1:8080/health
curl -sf http://127.0.0.1:80/studio_speakers >/dev/null
curl -sf http://127.0.0.1:8000/health
curl -skf https://127.0.0.1:7860/mobile-voice-test >/dev/null
```
