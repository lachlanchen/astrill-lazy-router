from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from astrill_lazy import windows_app


def test_single_instance_lock_rejects_a_second_live_owner(
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6.QtCore")
    path = tmp_path / "windows-gui.lock"
    first = windows_app._acquire_single_instance_lock(path)
    assert first is not None
    try:
        assert windows_app._acquire_single_instance_lock(path) is None
    finally:
        first.unlock()

    replacement = windows_app._acquire_single_instance_lock(path)
    assert replacement is not None
    replacement.unlock()


def test_second_launch_exits_before_loading_the_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden_ui = ModuleType("astrill_lazy.windows_ui")

    def run_windows_application(_argv: object) -> int:
        pytest.fail("a second launch must not construct the Windows UI")

    forbidden_ui.run_windows_application = run_windows_application  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astrill_lazy.windows_ui", forbidden_ui)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        windows_app,
        "_acquire_single_instance_lock",
        lambda: None,
    )

    assert windows_app.run_application(["astrill-lazy-windows"]) == 0


def test_non_windows_rejection_happens_before_locking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        windows_app,
        "_acquire_single_instance_lock",
        lambda: pytest.fail("non-Windows startup must not import or acquire Qt"),
    )

    with pytest.raises(RuntimeError, match="available only on Windows"):
        windows_app.run_application()


def test_application_holds_the_lock_until_the_ui_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Lock:
        def unlock(self) -> None:
            events.append("unlock")

    fake_ui = ModuleType("astrill_lazy.windows_ui")

    def run_windows_application(argv: object) -> int:
        events.append(f"run:{argv!r}")
        return 17

    fake_ui.run_windows_application = run_windows_application  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astrill_lazy.windows_ui", fake_ui)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        windows_app,
        "_acquire_single_instance_lock",
        Lock,
    )

    arguments = ["astrill-lazy-windows", "--example"]
    assert windows_app.run_application(arguments) == 17
    assert events == [f"run:{arguments!r}", "unlock"]
