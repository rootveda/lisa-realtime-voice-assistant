"""Resolve Lisa repo root (directory containing docs/ and offline_setup/)."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_repo_root() -> Path:
    """Prefer WORKSPACE_ROOT / REPO_ROOT; walk parents for markers; last resort parents[3]."""
    for key in ("WORKSPACE_ROOT", "REPO_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            p = Path(raw).expanduser().resolve()
            if (p / "docs" / "instructions").is_dir():
                return p
            if (p / "offline_setup" / "start_current_stack.sh").is_file():
                return p
    here = Path(__file__).resolve().parent
    for anc in (here, *here.parents):
        if (anc / "docs" / "instructions").is_dir():
            return anc
        if (anc / "offline_setup" / "start_current_stack.sh").is_file():
            return anc
    return Path(__file__).resolve().parents[3]
