from __future__ import annotations

import subprocess
import sys


def background_process_options() -> dict[str, int]:
    """Return platform options that keep background tools non-interactive."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}
