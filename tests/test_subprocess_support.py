from __future__ import annotations

import astrill_lazy.subprocess_support as support
import pytest


def test_background_processes_hide_their_windows_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(support.sys, "platform", "win32")
    monkeypatch.setattr(
        support.subprocess,
        "CREATE_NO_WINDOW",
        0x08000000,
        raising=False,
    )

    assert support.background_process_options() == {"creationflags": 0x08000000}


def test_background_processes_need_no_special_posix_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(support.sys, "platform", "linux")

    assert support.background_process_options() == {}
