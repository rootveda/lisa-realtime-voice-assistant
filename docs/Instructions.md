# Instructions files (not the live system prompt)

Editable presets live in **`docs/instructions/`**. Name files **`something.md`** (letters, digits, `_`, `-` only; must start with a letter). Examples: `instruction1.md`, `instruction_vision.md`, `my_preset.md`. The UI loads the list from **`GET /api/instructions`** — **restart the bot** after adding a file, then **hard-refresh** the Assistant page.

The running bot also ships copies under **`offline_setup/app/presets/instructions/`** so the Assistant Console still loads presets if `docs/` is not on the process path (Docker / minimal deploy).

The API serves presets from **repo `docs/instructions/` first**, then **bundled presets**. This file is documentation only and is **not** used as the assistant system prompt when that content looks like a short pointer.
