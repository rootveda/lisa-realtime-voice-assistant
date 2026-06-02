# Voice + live video — architecture overview

This is a **short** architecture snapshot. The full **implementation build plan** (wire protocol, milestones, file map, API table, GPU rules) is in **`video_voice_build_plan.md`**.

## Summary

- **Goal:** Mic + camera in one session; questions like *“what do you see?”* use **vision + voice** with low perceived latency.
- **Realtime:** Audio stays streaming; **video** is **sampled** (not full 30 fps VLM on the same GPU as a large LLM + XTTS + ASR).
- **v1 transport:** second WebSocket **`/ws/mobile-voice-vision`** for **JPEG** frames; existing **`/ws/mobile-voice`** unchanged for PCM.
- **Vision strategy:** **Tier A** — small VLM for caption/objects, merged into dialogue LLM context on vision-intent turns.

→ **Continue reading:** [`video_voice_build_plan.md`](./video_voice_build_plan.md)
