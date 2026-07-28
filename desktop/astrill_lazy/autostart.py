from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

AUTOSTART_FILENAME = "io.github.lachlanchen.AstrillLazyRouter.desktop"


def autostart_path() -> Path:
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ).expanduser()
    return config_home / "autostart" / AUTOSTART_FILENAME


def is_autostart_enabled() -> bool:
    path = autostart_path()
    if not path.is_file():
        return False
    try:
        document = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        "X-GNOME-Autostart-enabled=true" in document and "Hidden=true" not in document
    )


def enable_autostart(executable: Path | None = None) -> Path:
    gui_executable = executable or find_gui_executable()
    if not gui_executable.is_file():
        raise FileNotFoundError(f"GUI executable was not found: {gui_executable}")

    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Astrill Lazy Router\n"
        "Comment=Control direct and Astrill policy routing\n"
        f"Exec={_desktop_quote(gui_executable)}\n"
        f"TryExec={_desktop_quote(gui_executable)}\n"
        "Icon=network-vpn\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "StartupNotify=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=5\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{AUTOSTART_FILENAME}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def disable_autostart() -> None:
    autostart_path().unlink(missing_ok=True)


def find_gui_executable() -> Path:
    installed = shutil.which("astrill-lazy-gui")
    if installed:
        return Path(installed).resolve()
    candidate = Path(sys.prefix) / "bin" / "astrill-lazy-gui"
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError("astrill-lazy-gui is not installed")


def _desktop_quote(path: Path) -> str:
    value = str(path)
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'
