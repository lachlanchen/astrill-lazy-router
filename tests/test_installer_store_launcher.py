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
    STARTUP_LINE,
    RouterInstaller,
    build_router_package,
    find_router_root,
)
from astrill_lazy.launcher import parse_command
from astrill_lazy.router import RouterError
from astrill_lazy.store import ConfigStore, default_uu_rule

ROUTER_VERSION = (find_router_root() / "VERSION").read_text(encoding="ascii").strip()


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
    store.router_host = "192.168.50.1"
    store.router_user = "root"
    store.router_port = 2222
    store.router_identity = "~/.ssh/test-router"
    store.companion_enabled = False
    store.read_only = False
    store.save()
    assert path.stat().st_mode & 0o777 == 0o600

    loaded = ConfigStore(path)
    assert loaded.rules[0].selector == "uu-remote"
    assert loaded.enabled_extensions == ["core-catalog"]
    assert loaded.router_host == "192.168.50.1"
    assert loaded.router_user == "root"
    assert loaded.router_port == 2222
    assert loaded.router_identity == "~/.ssh/test-router"
    assert loaded.companion_enabled is False
    assert loaded.read_only is False
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["companion_enabled"] is False
    assert document["read_only"] is False
    assert "password" not in document


def test_fresh_config_store_starts_native_only_and_read_only(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "missing.json")

    assert store.router_host == "192.168.1.1"
    assert store.router_user == "root"
    assert store.router_port == 22
    assert store.router_identity == "~/.ssh/astrill_lazy_router_ed25519"
    assert store.companion_enabled is False
    assert store.read_only is True
    assert store.rules == []


def test_legacy_config_keeps_its_writable_companion_behavior(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "router_host": "legacy-router",
                "active_region": "active-astrill",
                "enabled_extensions": ["core-catalog"],
                "rules": [],
            }
        ),
        encoding="utf-8",
    )

    store = ConfigStore(path)

    assert store.companion_enabled is True
    assert store.read_only is False


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
                "version": ROUTER_VERSION,
                "jump_installed": True,
                "watchdog": True,
            }

    result = RouterInstaller(CurrentClient()).ensure()  # type: ignore[arg-type]
    assert result.action == "none"


def test_companion_check_requires_confirmation_when_not_installed() -> None:
    class MissingClient:
        def companion_presence(self) -> dict[str, object]:
            return {"installed": False, "version": None, "runtime": False}

    check = RouterInstaller(MissingClient()).check()  # type: ignore[arg-type]

    assert check.action == "install"
    assert check.installed_version is None
    assert "not installed" in check.reason


def test_companion_check_reuses_preloaded_healthy_snapshot() -> None:
    class SnapshotOnlyClient:
        def companion_presence(self) -> dict[str, object]:
            raise AssertionError("presence must come from the monitor snapshot")

        def status(self) -> dict[str, object]:
            raise AssertionError("status must come from the monitor snapshot")

    check = RouterInstaller(SnapshotOnlyClient()).check(  # type: ignore[arg-type]
        presence={
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
        },
        status={
            "version": ROUTER_VERSION,
            "jump_installed": True,
            "watchdog": True,
        },
    )

    assert check.action == "none"
    assert check.installed_version == ROUTER_VERSION


def test_automatic_reconcile_cannot_install_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingClient:
        def status(self) -> dict[str, object]:
            raise RouterError("runtime missing")

        def ping(self) -> bool:
            return True

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            assert timeout is None
            assert arguments[:2] == ["nvram", "get"]
            return ""

    monkeypatch.setattr("astrill_lazy.installer.time.sleep", lambda _seconds: None)
    installer = RouterInstaller(MissingClient())  # type: ignore[arg-type]
    monkeypatch.setattr(
        installer,
        "install",
        lambda: pytest.fail("automatic reconcile must not install"),
    )

    with pytest.raises(RouterError, match="requires Install / Upgrade confirmation"):
        installer.ensure(allow_install=False)


def test_router_reconcile_repairs_runtime_before_reinstalling() -> None:
    class RepairableClient:
        def __init__(self) -> None:
            self.repaired = False

        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
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
                "version": ROUTER_VERSION,
                "jump_installed": True,
                "watchdog": True,
            }

        def ping(self) -> bool:
            return True

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            assert timeout is None
            values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_version": ROUTER_VERSION,
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
                "version": ROUTER_VERSION,
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
                "astrill_lazy_version": ROUTER_VERSION,
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


def test_router_uninstall_audits_cleanup_and_preserves_native_state() -> None:
    native_status = {
        "vpn_state": "up",
        "astrill_server_id": 1109,
        "astrill_protocol": 2,
    }

    class InstalledClient:
        def __init__(self) -> None:
            self.script = ""

        def native_astrill_status(self) -> dict[str, object]:
            return dict(native_status)

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            assert timeout is None
            values = {
                "astrill_lazy_pkg_count": "2",
                "rc_startup": "original-command\n" + STARTUP_LINE,
                "mypage_scripts": (
                    "native-page /tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"
                ),
            }
            return values[arguments[-1]]

        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 45
            self.script = script
            return ""

    client = InstalledClient()
    result = RouterInstaller(client).uninstall()  # type: ignore[arg-type]
    assert result == native_status
    assert "astrill_lazy_pkg_0" in client.script
    assert "astrill_lazy_pkg_1" in client.script
    assert "rm -rf /tmp/astrill-lazy" in client.script
    assert "iptables -w 10 -t mangle -S" in client.script
    assert "lookup (212|213)" in client.script
    assert "native-page" in client.script
    assert "/tmp/astrill-lazy/alpage" not in next(
        line
        for line in client.script.splitlines()
        if line.startswith("nvram set mypage_scripts=")
    )
