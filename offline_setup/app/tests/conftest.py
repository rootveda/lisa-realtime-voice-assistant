"""Pytest: resolve pipecat_bots imports from offline_setup/app."""

from __future__ import annotations

import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
