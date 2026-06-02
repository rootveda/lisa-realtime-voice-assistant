Assistant stack — automated regression scope
============================================

Why bugs slipped through before
---------------------------------
- Work prioritized shipping behavior in the browser; checks were often manual or API-only curls.
- UI logic lived only in HTML/JS until recently (face phase module + harness).
- Server and client can disagree unless both are asserted (merged vs strict frame, focus pin row).

What is covered now (run ./scripts/run_regression_tests.sh)
---------------------------------------------------------
Python (pytest, offline_setup/app):
  face_session_public_api   — session payload merged vs observed_now_*, focus pin name row
  person_registry_identity  — enrolled vs placeholder display names
  instructions_payload      — prompt/content contract (must match pipecat_offline_patch handlers)
  face_phase_parity         — same scenarios as static harness (no multiface on grace glitch)

Browser (optional):
  tests/e2e — Playwright: face_phase_harness.html (phase logic); text_chat_errors.spec.ts mocks /api/text-chat/completions and /api/chat/attachments for HTTP 400 JSON bodies (same helper as Assistant). Set PLAYWRIGHT_BASE_URL for live bot.

What still needs human or stack-up tests (document explicitly)
--------------------------------------------------------------
- Live microphone / WebRTC / ASR quality
- GPU model load and latency
- Real webcam enrollment quality and lighting
- Full 40m exploratory UX passes (complements automation, does not replace unit/API checks)

Policy
------
- Any change to face session JSON shape or face_phase JS must update: pytest + harness + parity test.
- Any change to instructions POST/PUT body rules must update test_instructions_payload_contract.
- Text chat context trimming: pipecat_bots/text_chat_truncate.py — env TEXT_CHAT_MAX_PROMPT_CHARS (default ~42k chars); tests/test_text_chat_truncate.py.
