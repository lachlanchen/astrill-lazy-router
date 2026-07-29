from __future__ import annotations

import sys
from collections.abc import Sequence


def run_application(argv: Sequence[str] | None = None) -> int:
    if sys.platform != "win32":
        raise RuntimeError(
            "the native Windows frontend is available only on Windows; "
            "run astrill-lazy-gui on Ubuntu"
        )
    try:
        from .windows_ui import run_windows_application
    except ImportError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            raise RuntimeError(
                "PySide6 is required for the native Windows frontend; "
                "install astrill-lazy-router[windows]"
            ) from exc
        raise
    return run_windows_application(argv)


def main() -> int:
    return run_application()


if __name__ == "__main__":
    raise SystemExit(main())
