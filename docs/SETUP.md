# Lisa — Real-time Voice Assistant (setup guide)

**Audience:** human operators and coding agents (LLMs) setting up a fresh machine from this repository.

**Repo root:** the directory that contains `offline_setup/` (clone path may vary; examples use `lisa/nvidia_voice`).

---

## Instructions for LLM agents

When helping a user set up this project:

1. **Working directory** — All stack commands run from the **repo root** (`./offline_setup/lisa_stack.sh`), not from `offline_setup/app`.
2. **Do not commit** — `offline_setup/lisa_admin_token.env`, `offline_setup/rag_data/*` (except `.gitkeep`), `offline_setup/models/`, `offline_setup/hf_cache/`, `offline_setup/bundle/`, `.venv/`, `.cache/`, chat session JSON, SQLite under `rag_data/`.
3. **Large artifacts are not in Git** — Models, Docker bundles, Hugging Face caches, and XTTS data must be downloaded or built locally (~80–100 GB total for a full offline install).
4. **Order of operations** — Install OS prerequisites → NVIDIA driver + Docker → Ollama → Python venv → download models → build/pull Docker images → configure admin token (LAN only) → `./offline_setup/lisa_stack.sh start`.
5. **Primary docs** — Operations: [stack-start-stop.md](stack-start-stop.md). Operator UI: [user-guide.md](user-guide.md). Security: [admin-token-and-security.md](admin-token-and-security.md).

---

## What this stack is

Lisa is an **offline, GPU-accelerated real-time voice assistant** on a single Linux host:

| Component | Technology | Default port |
|-----------|------------|--------------|
| Dialogue LLM | Local llama-server (Gemma 4 26B GGUF) or Ollama fallback | `8000` / `11434` |
| Vision captions | Ollama (vision-capable Gemma 4) | `11434` |
| ASR | NVIDIA Nemotron Speech (Docker, ASR-only) | `8080` |
| TTS | Coqui XTTS v2 (Docker) | `80` |
| Web UI + API | Pipecat bot (`bot_vllm`) behind TLS proxy | `7860` (HTTPS) |

Optional: local RAG (FTS SQLite), face recognition (stub or OpenCV), LAN admin token, nginx reverse proxy.

---

## Hardware requirements

### Minimum (Ollama-only LLM fallback, no local 26B GGUF)

| Resource | Requirement |
|----------|-------------|
| **GPU** | NVIDIA with **≥ 12 GB VRAM** (e.g. RTX 3080 12GB, RTX 4070) |
| **RAM** | **32 GB** system RAM recommended |
| **Disk** | **~50 GB** free for models + Docker images + caches (excluding user RAG docs) |
| **CPU** | 8+ cores recommended for Docker + bot |

Uses smaller Ollama tag `gemma4:e2b-it-q4_K_M` when `offline_setup/models/gemma-4-26B-A4B-it-Q4_K_M.gguf` is missing.

### Recommended (full stack: local 26B + XTTS + ASR + vision on one GPU)

| Resource | Requirement |
|----------|-------------|
| **GPU** | NVIDIA **≥ 24 GB VRAM** (RTX 4090 24GB, RTX 5090, DGX Spark class) |
| **RAM** | **64 GB** system RAM |
| **Disk** | **~100 GB** free (models ~40 GB, HF caches ~25 GB, Docker layers ~46 GB, XTTS data ~2 GB, headroom for RAG) |
| **CUDA** | Driver supporting **CUDA 12.x–13.x**; Blackwell (sm_120+) may need `xtts-gpu:blackwell` image build |

Typical VRAM at runtime (one GPU, local llama primary):

| Service | Approx. VRAM |
|---------|----------------|
| Dialogue LLM (26B Q4_K_M, partial GPU layers) | ~14–18 GiB |
| XTTS | ~2–3 GiB |
| Nemotron ASR | ~3 GiB |
| Ollama vision (when loaded) | ~7 GiB additional |

If you hit OOM: lower `LOCAL_LLAMA_N_GPU_LAYERS` or `LOCAL_LLAMA_CTX_SIZE`, use Ollama-only mode (`USE_LOCAL_LLAMA_PRIMARY=0`), or set `XTTS_CPU=1` / `XTTS_GPU_DEVICE=1`.

### Not supported as primary targets

- macOS / Windows native stack (Linux + NVIDIA Docker is the tested path)
- CPU-only inference for the full voice loop (XTTS CPU mode exists but is very slow)
- Cloud-free deployment without a local NVIDIA GPU for ASR/TTS/LLM

---

## Software prerequisites

Install on **Ubuntu 22.04 / 24.04** (or similar Debian-based Linux):

| Tool | Version / notes |
|------|-----------------|
| **NVIDIA driver** | Recent driver for your GPU; verify with `nvidia-smi` |
| **Docker Engine** | 24+ with **NVIDIA Container Toolkit** (`docker run --gpus all` works) |
| **Ollama** | Latest; must serve on `http://127.0.0.1:11434` for vision (see below) |
| **Python** | **3.10–3.12** (3.12 used in development) |
| **uv** (recommended) or pip | [https://docs.astral.sh/uv/](https://docs.astral.sh/uv/) |
| **curl**, **git**, **openssl** | For health checks, clone, token generation |
| **Optional** | `nginx` + sudo — only if using `--network-proxy` for LAN phones |

Optional host packages for RAG attachments and face thumbnails (install into the project venv):

```bash
cd /path/to/lisa/nvidia_voice
uv venv .venv && source .venv/bin/activate
uv pip install -r offline_setup/app/requirements.txt
# pypdf, python-docx, openpyxl, Pillow, opencv-python-headless, etc.
```

Sync main Python dependencies (from repo root):

```bash
uv sync   # uses uv.lock at repo root
```

---

## Disk layout (not in Git)

After setup, expect these **local-only** paths (see `.gitignore`):

| Path | Purpose | Approx. size |
|------|---------|----------------|
| `offline_setup/models/` | GGUF weights (e.g. `gemma-4-26B-A4B-it-Q4_K_M.gguf`) | ~16 GB |
| `offline_setup/hf_cache/` | Hugging Face hub cache for Nemotron ASR | ~25 GB |
| `offline_setup/bundle/` | Pre-built Docker image tarballs (optional) | ~46 GB |
| `offline_setup/xtts_tts_data/` | XTTS speaker/model data | ~2 GB |
| `offline_setup/xtts_hf_cache/` | XTTS HF cache | varies |
| `.venv/` | Python virtualenv | ~8 GB |
| `.cache/huggingface/` | HF hub (if used at repo root) | varies |
| `offline_setup/rag_data/` | Your documents, attachments, SQLite indexes | user-dependent |

Skeleton dirs `offline_setup/rag_data/documents/` and `attachments/` exist in Git as `.gitkeep` only.

---

## First-time setup (step by step)

### 1. Clone and enter repo root

```bash
git clone https://github.com/rootveda/lisa-realtime-voice-assistant.git
cd lisa-realtime-voice-assistant
```

### 2. NVIDIA + Docker

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) if the test container fails.

### 3. Install Ollama and pull vision model

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama   # or run `ollama serve` manually
ollama pull gemma4:e2b-it-q4_K_M
```

Ensure loopback API works:

```bash
curl -s http://127.0.0.1:11434/api/tags
```

If Ollama binds only to a VPN interface, run:

```bash
sudo bash offline_setup/scripts/ollama_listen_localhost.sh
```

### 4. Python environment

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cd /path/to/lisa/nvidia_voice
uv sync
source .venv/bin/activate
uv pip install -r offline_setup/app/requirements.txt
```

### 5. Download models

**Primary dialogue LLM (recommended):**

```bash
mkdir -p offline_setup/models
# Place GGUF at offline_setup/models/gemma-4-26B-A4B-it-Q4_K_M.gguf
# Example: huggingface-cli download <your-gemma4-gguf-source> \
#   --local-dir offline_setup/models --include '*.gguf'
```

**ASR** — Downloaded automatically on first Nemotron container start into `offline_setup/hf_cache/` (requires network on first run; stack sets `HF_HUB_OFFLINE=1` after cache exists).

**XTTS** — First `start_xtts.sh` run pulls or uses pre-built image; data dirs `offline_setup/xtts_tts_data/` and `xtts_hf_cache/` are created at stack start.

**Optional: build heavy Docker images from source**

```bash
# Unified Nemotron image (2–3 hours; CUDA 13 / Blackwell)
docker build -f Dockerfile.unified -t nemotron-unified:cuda13 .

# Local Gemma llama runtime (built automatically on first stack start if missing)
docker build -f Dockerfile.llama-gemma4-runtime -t lisa-llama-gemma4:latest .

# XTTS for Blackwell (if pre-built cuda121 image fails)
docker build -f Dockerfile.xtts-gpu -t xtts-gpu:blackwell .
```

### 6. Admin token (LAN / multi-device only)

Skip for same-machine dev (`start` without `--admin`).

```bash
cp offline_setup/lisa_admin_token.env.example offline_setup/lisa_admin_token.env
# Edit: LISA_ADMIN_TOKEN=$(openssl rand -hex 24)
```

### 7. Start the stack

**Home dev (localhost only, no token):**

```bash
./offline_setup/lisa_stack.sh start
```

**LAN + admin token:**

```bash
./offline_setup/lisa_stack.sh start --admin
```

**Check status:**

```bash
./offline_setup/lisa_stack.sh status
```

### 8. Open the UI

| URL | When |
|-----|------|
| `https://127.0.0.1:7860/assistant-console` | Default home dev |
| `https://127.0.0.1:7860/user-guide` | Operator documentation (in-app) |
| `https://<LAN-IP>:7860/assistant-console` | `--admin` mode |
| `https://<LAN-IP>:8088/assistant-console` | `--admin --network-proxy` |

Accept the self-signed certificate warning (dev certs in `offline_setup/certs/`).

### 9. Stop

```bash
./offline_setup/lisa_stack.sh stop
```

---

## Environment variables (common)

| Variable | Default | Purpose |
|----------|---------|---------|
| `USE_LOCAL_LLAMA_PRIMARY` | `1` | Use GGUF llama-server on :8000 if model file exists |
| `LOCAL_LLAMA_MODEL_FILE` | `offline_setup/models/gemma-4-26B-A4B-it-Q4_K_M.gguf` | Path to GGUF |
| `LOCAL_LLAMA_N_GPU_LAYERS` | `28` | GPU layers (lower if OOM) |
| `LOCAL_LLAMA_CTX_SIZE` | `49152` | Context size (lower if OOM) |
| `NVIDIA_LLM_MODEL` | Ollama tag when fallback | e.g. `gemma4:e2b-it-q4_K_M` |
| `VISION_OLLAMA_MODEL` | `gemma4:e2b-it-q4_K_M` | Vision caption model |
| `STACK_UNLOAD_OLLAMA_AT_START` | `1` | Free VRAM before XTTS/llama |
| `XTTS_CPU` | unset | Force CPU XTTS |
| `LISA_ADMIN_TOKEN` | from env file | Required for admin APIs in `--admin` mode |

Full list: [stack-start-stop.md](stack-start-stop.md).

---

## Service ports (quick reference)

| Port | Service |
|------|---------|
| 7860 | HTTPS front door (TLS proxy → bot) |
| 7861 | Bot HTTP (internal) |
| 8000 | Local llama OpenAI-compatible API |
| 8080 | Nemotron ASR WebSocket/HTTP |
| 80 | XTTS HTTP |
| 11434 | Ollama API |

---

## Verification checklist

```bash
./scripts/install_git_hooks.sh      # once per clone — blocks unsafe git push
./offline_setup/lisa_stack.sh status  # GPU table + health probes
curl -sk https://127.0.0.1:7860/api/admin/capabilities
curl -sk https://127.0.0.1:8080/health
curl -sf http://127.0.0.1:80/studio_speakers
curl -sf http://127.0.0.1:8000/health   # when local llama primary is up
```

---

## Troubleshooting

| Symptom | Action |
|---------|--------|
| `No such file or directory` for `lisa_stack.sh` | `cd` to repo root (parent of `offline_setup/`) |
| ASR health timeout | Check `docker logs nemotron`; ensure `hf_cache` populated |
| XTTS CUDA error on RTX 5090 | Build `xtts-gpu:blackwell` or `XTTS_CPU=1` |
| LLM OOM | Lower `LOCAL_LLAMA_N_GPU_LAYERS` / ctx; or `USE_LOCAL_LLAMA_PRIMARY=0` |
| Vision never captions | `ollama pull gemma4:e2b-it-q4_K_M`; fix loopback bind |
| Admin actions fail from phone | Use `start --admin` and paste token in UI |

More: [user-guide.md](user-guide.md), [README.md](../README.md) (upstream Nemotron sample), [docs/README.md](README.md) (doc index).

---

## Related documentation

- [stack-start-stop.md](stack-start-stop.md) — start/stop/restart, env vars, RAG ingest
- [user-guide.md](user-guide.md) — consoles, managers, persistence, security
- [admin-token-and-security.md](admin-token-and-security.md) — `--admin`, tokens, LAN
- [CONTRIBUTING.md](CONTRIBUTING.md) — what never to commit
