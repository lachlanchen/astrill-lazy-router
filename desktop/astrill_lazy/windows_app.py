from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .store import default_config_path

if TYPE_CHECKING:
    from PySide6.QtCore import QLockFile


def _single_instance_lock_path() -> Path:
    return default_config_path().with_name("windows-gui.lock")


def _acquire_single_instance_lock(
    path: Path | None = None,
) -> QLockFile | None:
    """Acquire the long-lived lock that owns UI startup reconciliation."""

    from PySide6.QtCore import QLockFile

    lock_path = path or _single_instance_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    # This lock lives for the whole GUI session. PID/process-name checks still
    # recover a crashed owner, without misclassifying a healthy long session.
    lock.setStaleLockTime(0)
    if lock.tryLock(0):
        return lock
    if lock.error() == QLockFile.LockError.LockFailedError:
        return None
    raise RuntimeError(f"could not acquire the Windows application lock: {lock_path}")


def run_application(argv: Sequence[str] | None = None) -> int:
    if sys.platform != "win32":
        raise RuntimeError(
            "the native Windows frontend is available only on Windows; "
            "run astrill-lazy-gui on Ubuntu"
        )
    try:
        instance_lock = _acquire_single_instance_lock()
    except ImportError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            raise RuntimeError(
                "PySide6 is required for the native Windows frontend; "
                "install astrill-lazy-router[windows]"
            ) from exc
        raise
    if instance_lock is None:
        # MainWindow schedules reconciliation during construction. Returning
        # before importing it ensures that only the lock owner can restore an
        # opted-in RAM overlay or display a second top-level window.
        return 0
    try:
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
    finally:
        instance_lock.unlock()


def main() -> int:
    return run_application()


if __name__ == "__main__":
    raise SystemExit(main())
