# Face recognition environment flags

All face features are **off by default** so existing stacks behave unchanged.

| Variable | Default | Meaning |
|----------|---------|---------|
| `FACERECOG_ENABLED` | `0` | Master switch when `1`. Also: **Assistant Console → Voice+Video+Face** POSTs `enabled:true` to `/api/face/runtime`, persisted as `offline_setup/app/rag_data/facerecog_runtime.json`, so the pipeline can run **without** shell env. |
| `FACERECOG_DATA_DIR` | (auto under offline app `rag_data`) | Directory for `face_registry.sqlite`. |
| `FACERECOG_INSTRUCTIONS_PATH` | unset | Optional override YAML path; else bundled defaults + repo `docs/face_recognition_instructions.yaml`. |
| `FACERECOG_STUB_SIMPLE` | `1` | When `1`, use deterministic stub identity per `vision_session_id` (no ML deps). Set `0` when a real analyzer backend is wired. |
| `FACE_EMOTION_ENABLED` | `0` | Reserved for expression/emotion classification on face crops (future). |

Privacy: embeddings and transcripts stay **local** in SQLite unless you copy the database elsewhere.
