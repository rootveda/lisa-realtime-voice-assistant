"""
Phase 4 — speaker diarization / active-speaker correlation (planned).

PCM-stream diarization (e.g. pyannote-style) correlated with video speaking activity
would set `active_speaker_person_id` in session state for FaceTranscriptionProcessor.

Until wired: `FaceTranscriptionProcessor` uses optional client `face_profile` / registry primary only.
See docs/face_recognition_speaker_diarization.md.
"""

SPEAKER_DIARIZATION_PLANNED = True
