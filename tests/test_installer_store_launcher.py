from __future__ import annotations

import json
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
from astrill_lazy.autostart import (
    autostart_path,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from astrill_lazy.installer import (
    RouterInstaller,
    build_router_package,
    find_router_root,
)
from astrill_lazy.launcher import parse_command
from astrill_lazy.router import RouterError
from astrill_lazy.store import ConfigStore, default_uu_rule


def test_router_package_is_deterministic_and_contains_only_runtime_files() -> None:
    root = find_router_root()
    first = build_router_package(root)
    second = build_router_package(root)
    assert first == second
    assert len(first) < 20_000

    with tarfile.open(fileobj=BytesIO(first), mode="r:gz") as archive:
        names = archive.getnames()
        assert names == [
            "astrill-lazy/alctl",
            "astrill-lazy/alapi",
            "astrill-lazy/alpage",
            "astrill-lazy/VERSION",
        ]
        assert all("key" not in name and "backup" not in name for name in names)


def test_config_store_round_trip_is_private(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.rules = [default_uu_rule()]
    store.enabled_extensions = ["core-catalog"]
    store.save()
    assert path.stat().st_mode & 0o777 == 0o600

    loaded = ConfigStore(path)
    assert loaded.rules[0].selector == "uu-remote"
    assert loaded.enabled_extensions == ["core-catalog"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1


def test_parse_application_command() -> None:
    executable, arguments = parse_command("/usr/bin/printf '%s' hello")
    assert executable == "/usr/bin/printf"
    assert arguments == ["%s", "hello"]


def test_parse_application_command_rejects_missing_file() -> None:
    with pytest.raises(ValueError, match="not found"):
        parse_command("/does/not/exist")


def test_desktop_autostart_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    executable = tmp_path / "Astrill Lazy GUI"
    executable.write_text("#!/bin/sh\n", encoding="ascii")

    installed = enable_autostart(executable)
    assert installed == autostart_path()
    assert installed.stat().st_mode & 0o777 == 0o644
    assert is_autostart_enabled()
    document = installed.read_text(encoding="utf-8")
    assert f'Exec="{executable}"' in document
    assert "X-GNOME-Autostart-enabled=true" in document

    disable_autostart()
    assert not is_autostart_enabled()


def test_router_reconcile_skips_current_runtime() -> None:
    class CurrentClient:
        def status(self) -> dict[str, object]:
            return {
                "version": "0.1.0",
                "jump_installed": True,
                "watchdog": True,
            }

    result = RouterInstaller(CurrentClient()).ensure()  # type: ignore[arg-type]
    assert result.action == "none"


def test_router_reconcile_repairs_runtime_before_reinstalling() -> None:
    class RepairableClient:
        def __init__(self) -> None:
            self.repaired = False

        def status(self) -> dict[str, object]:
            return {
                "version": "0.1.0",
                "jump_installed": self.repaired,
                "watchdog": self.repaired,
            }

        def raw(self, _arguments: list[str], *, timeout: int) -> str:
            assert timeout == 30
            self.repaired = True
            return ""

    client = RepairableClient()
    result = RouterInstaller(client).ensure()  # type: ignore[arg-type]
    assert result.action == "repaired"
    assert client.repaired


def test_router_reconcile_reconstructs_current_stored_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StoredClient:
        def __init__(self) -> None:
            self.reconstructed = False
            self.expected_md5 = ""

        def status(self) -> dict[str, object]:
            if not self.reconstructed:
                raise RouterError("runtime is not ready")
            return {
                "version": "0.1.0",
                "jump_installed": True,
                "watchdog": True,
            }

        def ping(self) -> bool:
            return True

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            assert timeout is None
            values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_version": "0.1.0",
                "astrill_lazy_pkg_md5": self.expected_md5,
            }
            return values[arguments[-1]]

        def run_script(self, script: str, *, timeout: int) -> str:
            assert script == "nvram get astrill_lazy_bootstrap | sh\n"
            assert timeout == 45
            self.reconstructed = True
            return ""

    monkeypatch.setattr("astrill_lazy.installer.time.sleep", lambda _seconds: None)
    client = StoredClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    result = installer.ensure()
    assert result.action == "repaired"
    assert client.reconstructed


def test_router_reconcile_does_not_rewrite_identical_broken_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenClient:
        def __init__(self) -> None:
            self.expected_md5 = ""

        def status(self) -> dict[str, object]:
            return {
                "version": "0.1.0",
                "jump_installed": False,
                "watchdog": False,
            }

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            if arguments == ["/tmp/astrill-lazy/alctl", "start"]:
                assert timeout == 30
                raise RouterError("start failed")
            assert timeout is None
            values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_version": "0.1.0",
                "astrill_lazy_pkg_md5": self.expected_md5,
            }
            return values[arguments[-1]]

    client = BrokenClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    monkeypatch.setattr(
        installer,
        "install",
        lambda: pytest.fail("identical package must not be rewritten"),
    )
    with pytest.raises(RouterError, match="explicit rewrite"):
        installer.ensure()
