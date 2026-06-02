# E2E tests: vision + text + voice

## Automated script

From **`offline_setup/app`** (with the stack running):

```bash
# HTTP bot port (default stack)
uv run python scripts/e2e_vision_voice_test.py

# Through HTTPS proxy (self-signed)
uv run python scripts/e2e_vision_voice_test.py --base-url https://127.0.0.1:7860 --insecure
```

Optional: `E2E_TEST_JPEG=/path/to/file.jpg` to force a known-good JPEG for frames + preview.

### What the script checks

| Step | Endpoint / action |
|------|---------------------|
| 1 | `GET /api/runtime-status` |
| 2 | `GET /api/mobile-voice-vision` |
| 3 | `POST /api/vision/preview` (multipart `image`) |
| 4 | `WebSocket /ws/mobile-voice-vision` — `client-ready` + binary JPEG frames |
| 5 | `POST /api/text-chat/completions` with `vision_session_id` + vision question |
| 6 | `POST /api/text-chat/completions` with `vision_session_id` + non-vision message (`pong`) |

Requires **Ollama** (or compatible) with a **vision-capable** model. Default caption model is **`gemma4:e2b-it-q4_K_M`** (unset `VISION_OLLAMA_MODEL`); use **`gemma4:26b-a4b-it-q4_K_M`** only if you have spare VRAM alongside XTTS and local llama. **`start_current_stack.sh`** sets **`VISION_CAPTION_OPENAI_COMPAT=0`** so the bot uses **native `/api/chat` only** for captions (same as the standalone latency test); discovery JSON exposes **`vision_caption_try_openai_compat_first`**.

**Voice vs text freshness:** Final STT often arrives just before the next JPEG from the browser (~500ms ticks). The server **`VISION_VOICE_CAPTION_SETTLE_SEC`** (default **0.42**) briefly polls for the **newest** frame before captioning on **voice** turns; text uses **`VISION_TEXT_CAPTION_SETTLE_SEC`** (default **0.12**). Short follow-ups like “eyes open?” / “now?” are matched by **`vision_intent`** so `[Camera context]` is still merged (not only “what do you see”).

**Richer `[Camera context]`:** The caption prompt lives in **`pipecat_bots/vision_policy/caption_instruction_base.txt`** (plus **`caption_instruction_recheck_suffix.txt`** when the user asked for a fresh look). Override paths with **`VISION_CAPTION_INSTRUCTION_BASE_FILE`** / **`VISION_CAPTION_INSTRUCTION_RECHECK_FILE`**. Tune **`VISION_CAPTION_MAX_TOKENS`** (default **512**), **`VISION_CAPTION_RECHECK_EXTRA_TOKENS`** (default **120**), and **`VISION_CAPTION_MAX_CHARS`** (default **3200**).

**Configurable policies and triggers:** Under **`pipecat_bots/vision_policy/`**, edit **`substantive.txt`**, **`unavailable.txt`**, **`refresh_note.txt`**, **`system_addon.txt`**, **`camera_context_template.txt`**, and regex triggers in **`vision_intents.json`** (or set **`VISION_INTENTS_FILE`** to your own copy). Use **`VISION_POLICY_DIR`** to point at a directory that contains those files. The packaged **`primary_regex`** also matches phrases like **“what we are doing”**, **“check the camera”**, and **“can you check … doing”** when **`VISION_AUGMENT_SESSION_LEVEL=0`**. The default in code is **`VISION_AUGMENT_SESSION_LEVEL=0`** (regex-only) for lower latency. Set **`VISION_AUGMENT_SESSION_LEVEL=1`** or use the Assistant Console checkbox **“Every reply uses live camera”** (sends **`vision_augment_every_turn: true`** on **`client-ready`** / text chat) to attach **`[Camera context]`** on every turn while vision is on (per–`frame_id` caption caching still limits repeat Ollama calls for the same frame). While **Voice+Video** is connected, toggling that checkbox sends **`{"type":"vision-prefs","vision_session_id":…,"vision_augment_every_turn":…}`** on **`/ws/mobile-voice`** so the server updates without reconnecting; text+camera still applies the preference on the next **`/api/text-chat/completions`** request.

**“Check again” and color checks:** Phrases like “Can you check that again?” match **`vision_refresh_intent`**; questions about specific colors (“any green?”, “what colors?”) match **`vision_color_probe_intent`** and use the same **fresh frame + recheck** path. The server waits up to **`VISION_FRESH_FRAME_WAIT_SEC`** (default **1.25s**) for a **new** JPEG `frame_id` from the client when a refresh or color probe is detected, then runs a **recheck** caption and appends **`refresh_note.txt`** so the assistant prefers the new observation over older transcript text.

### Not automated here

- **Voice + live video end-to-end** (mic → ASR → caption merge → TTS): needs real audio and is covered manually via **`/mobile-voice-vision-test`** or **Assistant Console** with **“Live camera for vision”** checked and voice enabled.

## Troubleshooting: “I cannot see the camera”

1. **Wait for video before speaking** — JPEG encoding only starts after the browser has video dimensions (`videoWidth` > 0). The test page and Assistant Console now open the vision WebSocket **after** the camera preview is ready and send an **immediate** first frame.
2. **Vision model output** — Some Gemma builds return chain-of-thought (“review against constraints…”) or list fragments like `*   The` instead of a scene caption. The server **sanitizes** thin or junk captions, **retries** with a short delay, and falls back to **`[Camera: caption unavailable]`** when needed. For a lighter model, **`ollama pull moondream`** and set **`VISION_OLLAMA_MODEL=moondream:latest`**. Default **`VISION_FRAME_TTL_SEC`** is **5** (balance of freshness vs. voice/STT timing). Use **`2`** for stricter live-only, or **`10`** if a slow client drops frames and you see false “no recent frame”.
3. **Instruction preset** — use **`instruction_vision`** (or merge its camera section into your main preset) when testing vision from the API / dropdown.
4. **Thinking on caption calls** — Default is `think: false` on the Ollama caption request. Set **`VISION_CHAT_THINK=1`** only if you need the model’s reasoning channel (not recommended for captions).

## Manual cases (browser)

1. **Text while video is live**  
   Assistant Console: enable **Camera for text chat** (left panel), type a normal message → expect normal reply.  
   Ask **“What do you see?”** → reply should reflect caption / scene.

2. **Voice + live video**  
   Same page: use **Voice+Video** (mic + camera + `/ws/mobile-voice-vision`) or enable text camera and reconnect voice if you combine paths; ask aloud **“What do you see?”** → reply should use `[Camera context]`.

3. **Dedicated test page**  
   `https://127.0.0.1:7860/mobile-voice-vision-test` — mic + camera + both WebSockets (no Assistant UI).

## API extension (text chat)

`POST /api/text-chat/completions` accepts extra JSON fields (removed before the upstream LLM):

- `vision_session_id` (string): must match `?session_id=` on `/ws/mobile-voice-vision`
- `vision_enable` (boolean, default `true`)

When the **last user** message matches vision intent, the proxy injects **`[Camera context]`** using the latest JPEG for that session (same rules as `bot_vllm` voice).

Streaming requests (`"stream": true`) do **not** apply vision merge; extension fields are still stripped.

### Local tools, RAG, attachments (manual)

1. **Discovery**  
   `curl -sk https://127.0.0.1:7860/api/local-tools` — expect `rag`, `calendar`, and allowlist fields.

2. **Ingest RAG (offline)**  
   Add a small `.md` or `.txt` under `offline_setup/rag_data/documents/`, then:  
   `curl -sk -X POST https://127.0.0.1:7860/api/rag/ingest -H 'Content-Type: application/json' -d '{"reindex_all":true}'`  
   `curl -sk https://127.0.0.1:7860/api/rag/status` — `chunks` should be &gt; 0.

3. **Text chat with tools**  
   In Assistant Console, enable **Local RAG** (and optionally calendar / connector with a **loopback** URL only). Send a message that needs the index. The first completion may return `tool_calls` (visible only in server logs / verbose mode); the final answer should use retrieved snippets. Set `LOCAL_TOOLS_ENABLE=0` and restart the bot to confirm plain proxy behavior.

4. **Egress**  
   Put a **non-allowlisted** URL in a connector field (e.g. `https://example.com`) — the server should reject it when building tools or return an error on the tool path. Connectors to `http://127.0.0.1:...` are allowed if the host is in `TOOL_HTTP_ALLOW_HOSTS`.

5. **Attachments**  
   Use **Attach** on the console, then send. Text extracts appear under `[Attachments]` in the user turn (see `chat_attachments.py`). PDF text requires optional **`pypdf`** in the same environment as the bot.
