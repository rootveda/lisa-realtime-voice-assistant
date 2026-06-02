# Speaker diarization / active speaker (Phase 4 backlog)

Visual face detection alone cannot reliably determine **who is speaking** when multiple people are present.

Planned direction:

1. Run **audio speaker diarization or separation** on the PCM stream from `/ws/mobile-voice` (async, bounded latency).
2. Correlate active speaker segments with **clock time** and optional **lip/movement** cues from JPEG timestamps.
3. Expose `active_speaker_person_id` in [`FaceSessionState`](../offline_setup/app/pipecat_bots/face_recog/session_state.py) when confidence exceeds a threshold.
4. Until then, the stack uses the **visual primary** person from face observations for context injection (see `speaker_focus_note` in `face_recognition_instructions.yaml`).

No production code depends on this document; it records scope boundaries.
