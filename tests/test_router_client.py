from __future__ import annotations

import json
import subprocess
from pathlib import Path

import astrill_lazy.router as router_module
import pytest
from astrill_lazy.astrill import AstrillConnectionSelection
from astrill_lazy.native_settings import (
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
)
from astrill_lazy.router import (
    DOMAIN_REFRESH_TIMEOUT,
    CommandResult,
    RouterClient,
    RouterError,
    _openssh_config_path,
    _parse_native_clients,
)

WINDOWS_NO_WINDOW = 0x08000000


def test_refresh_allows_a_full_forced_domain_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        assert arguments == ["refresh", "--json"]
        assert input_bytes is None
        assert timeout == DOMAIN_REFRESH_TIMEOUT
        return CommandResult('{"health":"healthy"}\n', "", 0)

    monkeypatch.setattr(client, "_run_alctl", run_alctl)
    assert client.refresh() == {"health": "healthy"}


def test_remote_timeout_is_reported_as_a_router_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient(timeout=7)

    def time_out(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["ssh"], 7)

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(RouterError, match="timed out after 7 seconds"):
        client.status()


def test_remote_commands_use_stable_key_only_ssh_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    captured_options: dict[str, object] = {}

    def complete(
        arguments: list[str], **options: object
    ) -> subprocess.CompletedProcess:
        captured.extend(arguments)
        captured_options.update(options)
        return subprocess.CompletedProcess(arguments, 0, b"ready\n", b"")

    monkeypatch.setattr(subprocess, "run", complete)
    monkeypatch.setattr(
        router_module,
        "background_process_options",
        lambda: {"creationflags": WINDOWS_NO_WINDOW},
    )
    client = RouterClient(
        "192.168.1.1",
        user="root",
        port=2222,
        identity_file="~/.ssh/router-key",
        known_hosts_file="~/.ssh/astrill-lazy-known-hosts",
    )

    assert client.ping()
    assert captured[0] == "ssh"
    assert "BatchMode=yes" in captured
    assert "ConnectTimeout=8" in captured
    assert "ConnectionAttempts=2" in captured
    assert "ServerAliveInterval=15" in captured
    assert "ServerAliveCountMax=3" in captured
    assert "StrictHostKeyChecking=accept-new" in captured
    assert captured[captured.index("-p") + 1] == "2222"
    assert captured[captured.index("-i") + 1] == str(
        Path("~/.ssh/router-key").expanduser()
    )
    known_hosts = Path("~/.ssh/astrill-lazy-known-hosts").expanduser()
    assert f"UserKnownHostsFile={_openssh_config_path(known_hosts)}" in captured
    assert "root@192.168.1.1" in captured
    assert captured_options["creationflags"] == WINDOWS_NO_WINDOW


def test_astrill_payload_ssh_uses_background_process_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_options: dict[str, object] = {}

    def complete(
        arguments: list[str], **options: object
    ) -> subprocess.CompletedProcess:
        captured_options.update(options)
        return subprocess.CompletedProcess(arguments, 0, b"applet", b"")

    monkeypatch.setattr(subprocess, "run", complete)
    monkeypatch.setattr(
        router_module,
        "background_process_options",
        lambda: {"creationflags": WINDOWS_NO_WINDOW},
    )

    assert RouterClient().fetch_astrill_payload() == b"applet"
    assert captured_options["creationflags"] == WINDOWS_NO_WINDOW


def test_known_hosts_path_with_spaces_is_one_openssh_config_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def complete(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess:
        captured.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0, b"ready\n", b"")

    monkeypatch.setattr(subprocess, "run", complete)
    known_hosts = tmp_path / "Astrill Lazy Router" / "known hosts"
    client = RouterClient("192.168.1.1", known_hosts_file=known_hosts)

    assert client.ping()
    option = next(
        argument for argument in captured if argument.startswith("UserKnownHostsFile=")
    )
    encoded = known_hosts.as_posix().replace(" ", r"\ ")
    assert option == f"UserKnownHostsFile={encoded}"
    assert _openssh_config_path(known_hosts) == encoded


def test_remote_commands_can_require_a_preverified_host_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def complete(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess:
        captured.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0, b"ready\n", b"")

    monkeypatch.setattr(subprocess, "run", complete)
    client = RouterClient("192.168.1.1", host_key_policy="yes")

    assert client.ping()
    assert "StrictHostKeyChecking=yes" in captured
    assert "StrictHostKeyChecking=accept-new" not in captured
    with pytest.raises(ValueError, match="host-key policy"):
        RouterClient(host_key_policy="no")


def test_monitor_snapshot_combines_status_settings_and_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    calls = 0

    def tagged(name: str, value: str) -> str:
        return f"{name}\t{value.encode().hex()}\n"

    settings = {key: "" for key in SAFE_NATIVE_ASTRILL_KEYS}
    settings.update(
        {
            "astrill_serverid": "1109",
            "astrill_protocol": "2",
            "astrill_status": "3",
        }
    )
    companion = {
        "version": "0.2.3",
        "health": "healthy",
        "vpn_state": "up",
        "jump_installed": True,
        "watchdog": True,
    }
    output = "".join(
        (
            "meta:vpn_state\tup\n",
            "meta:applet\ttrue\n",
            "presence:runtime\ttrue\n",
            tagged("meta:wan_iface", "vlan2"),
            tagged("presence:astrill_lazy_installed", "1"),
            tagged("presence:astrill_lazy_version", "0.2.3"),
            tagged("presence:astrill_lazy_pkg_md5", "abc123"),
            *(tagged(f"setting:{key}", value) for key, value in settings.items()),
            tagged("companion_status", json.dumps(companion)),
        )
    )

    def run_script(script: str, *, timeout: int = 60) -> str:
        nonlocal calls
        calls += 1
        assert timeout == 30
        assert "/tmp/astrill-lazy/alctl status --json" in script
        return output

    monkeypatch.setattr(client, "run_script", run_script)
    snapshot = client.monitor_snapshot(include_companion=True)

    assert calls == 1
    assert snapshot.native_status["health"] == "healthy"
    assert snapshot.native_status["vpn_state"] == "up"
    assert snapshot.native_status["astrill_server_id"] == 1109
    assert snapshot.native_status["astrill_protocol"] == 2
    assert snapshot.settings.get("astrill_status") == "3"
    assert snapshot.companion_presence == {
        "installed": True,
        "version": "0.2.3",
        "runtime": True,
        "package_md5": "abc123",
    }
    assert snapshot.companion_status == companion
    assert snapshot.selected_status(companion_enabled=True) == companion
    assert snapshot.selected_status(companion_enabled=False) == (snapshot.native_status)


def test_monitor_snapshot_can_skip_companion_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()

    def tagged(name: str, value: str) -> str:
        return f"{name}\t{value.encode().hex()}\n"

    output = "".join(
        (
            "meta:vpn_state\tdown\n",
            "meta:applet\ttrue\n",
            "presence:runtime\tfalse\n",
            tagged("meta:wan_iface", "vlan2"),
            tagged("presence:astrill_lazy_installed", ""),
            tagged("presence:astrill_lazy_version", ""),
            tagged("presence:astrill_lazy_pkg_md5", ""),
            *(tagged(f"setting:{key}", "") for key in SAFE_NATIVE_ASTRILL_KEYS),
        )
    )

    def run_script(script: str, *, timeout: int = 60) -> str:
        assert timeout == 30
        assert "alctl status --json" not in script
        return output

    monkeypatch.setattr(client, "run_script", run_script)
    snapshot = client.monitor_snapshot(include_companion=False)

    assert snapshot.companion_status is None
    assert snapshot.native_status["vpn_state"] == "down"


def test_native_clients_merge_read_only_router_sources() -> None:
    def tagged(name: str, value: str) -> str:
        return f"{name}\t{value.encode().hex()}\n"

    output = "".join(
        (
            tagged(
                "leases",
                "2000000000 AA:BB:CC:DD:EE:01 192.168.1.10 * client-id\n",
            ),
            tagged(
                "arp",
                "IP address HW type Flags HW address Mask Device\n"
                "192.168.1.10 0x1 0x2 aa:bb:cc:dd:ee:01 * br0\n"
                "192.168.1.30 0x1 0x2 aa:bb:cc:dd:ee:03 * br0\n"
                "192.168.2.1 0x1 0x2 aa:bb:cc:dd:ee:ff * vlan2\n",
            ),
            tagged(
                "nvram:static_leases",
                "AA:BB:CC:DD:EE:01=laptop=192.168.1.10=1440",
            ),
            tagged(
                "nvram:dhcp_staticlist",
                "<AA:BB:CC:DD:EE:02=printer=192.168.1.20=1440>",
            ),
            tagged("nvram:dhcpd_static", ""),
            tagged("nvram:lan_ifname", "br0"),
        )
    )

    clients = _parse_native_clients(output)
    by_mac = {client["mac"]: client for client in clients}

    assert set(by_mac) == {
        "aa:bb:cc:dd:ee:01",
        "aa:bb:cc:dd:ee:02",
        "aa:bb:cc:dd:ee:03",
    }
    assert by_mac["aa:bb:cc:dd:ee:01"] == {
        "address": "192.168.1.10",
        "mac": "aa:bb:cc:dd:ee:01",
        "hostname": "laptop",
        "expires": 2000000000,
        "source": "dhcp,static,arp",
        "active": True,
    }
    assert by_mac["aa:bb:cc:dd:ee:02"]["source"] == "static"
    assert by_mac["aa:bb:cc:dd:ee:02"]["active"] is False
    assert by_mac["aa:bb:cc:dd:ee:03"]["source"] == "arp"
    assert by_mac["aa:bb:cc:dd:ee:03"]["active"] is True


def test_native_client_inventory_does_not_use_companion_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    captured = ""

    def run_script(script: str, *, timeout: int = 60) -> str:
        nonlocal captured
        assert timeout == 60
        captured = script
        return ""

    monkeypatch.setattr(client, "run_script", run_script)

    assert client.native_clients() == []
    assert "/tmp/astrill-lazy" not in captured
    assert "nvram get" in captured
    assert "/tmp/dnsmasq.leases" in captured
    assert "/proc/net/arp" in captured


def test_connection_apply_verifies_selection_and_native_settings() -> None:
    selection = AstrillConnectionSelection(1109, 1109, 536872021, "443", 1, 1, 6)

    class FakeConnectionRouter(RouterClient):
        def __init__(self) -> None:
            super().__init__("unused")
            self.values = {
                "astrill_cipher": "default",
                "astrill_wanmtu": "1446",
            }
            self.writes: list[dict[str, str]] = []

        def native_astrill_settings(self) -> NativeAstrillSettings:
            return NativeAstrillSettings.from_dict(self.values)

        def _write_native_astrill_values(
            self, values: dict[str, str]
        ) -> NativeAstrillSettings:
            self.writes.append(values)
            self.values.update(values)
            return self.native_astrill_settings()

        def switch_astrill(self, **values: int | str) -> dict[str, object]:
            self.values.update(selection.native_values())
            return {"health": "healthy", "values": values}

    router = FakeConnectionRouter()
    result = router.apply_astrill_connection(
        selection,
        {"astrill_cipher": "AES-256-CBC", "astrill_wanmtu": "1400"},
    )

    assert result.settings.get("astrill_serverid") == "1109"
    assert result.settings.get("astrill_protocol") == "1"
    assert result.settings.get("astrill_cipher") == "AES-256-CBC"
    assert router.writes == [
        {"astrill_cipher": "AES-256-CBC", "astrill_wanmtu": "1400"}
    ]


def test_favorite_replacement_compares_current_value_and_reads_back() -> None:
    values = {key: "" for key in SAFE_NATIVE_ASTRILL_KEYS}
    expected = "998:402654182:1-65535:1:6:998"
    replacement = expected + ",1109:402654293:1-65535:1:6:1109"
    values["astrill_favlist"] = expected

    class FavoriteRouter(RouterClient):
        def __init__(self) -> None:
            super().__init__("unused")
            self.write_script = ""

        def run_script(self, script: str, *, timeout: int = 60) -> str:
            if "hexdump" in script:
                return "\n".join(
                    f"{key}\t{(value + chr(10)).encode().hex()}"
                    for key, value in values.items()
                )
            assert timeout == 30
            self.write_script = script
            values["astrill_favlist"] = replacement
            return ""

    router = FavoriteRouter()
    settings = router.replace_astrill_favorites(expected, replacement)

    assert settings.get("astrill_favlist") == replacement
    assert f"expected={expected}" in router.write_script
    assert 'current="$(nvram get astrill_favlist)"' in router.write_script
    assert 'if [ "$current" != "$expected" ]; then' in router.write_script
    assert f"nvram set astrill_favlist={replacement}" in router.write_script
    assert router.write_script.count("nvram commit") == 1
    assert router.write_script.index("if [") < router.write_script.index("nvram set")


def test_favorite_replacement_cas_conflict_stops_before_commit_or_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "998:402654182:1-65535:1:6:998"
    replacement = expected + ",1109:402654293:1-65535:1:6:1109"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def conflict(
        arguments: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((arguments, options))
        script = bytes(options["input"]).decode("utf-8")
        assert script.index("exit 75") < script.index("nvram commit")
        return subprocess.CompletedProcess(
            arguments,
            75,
            b"",
            b"router favorite endpoints changed before this save; reload and try again\n",
        )

    monkeypatch.setattr(subprocess, "run", conflict)
    monkeypatch.setattr(
        router_module,
        "background_process_options",
        dict,
    )

    with pytest.raises(
        RouterError,
        match="favorite endpoints changed before this save",
    ):
        RouterClient("router").replace_astrill_favorites(expected, replacement)

    # One failed compare-and-swap SSH call means the readback SSH call was
    # never attempted. The simulated exit occurs before the commit line.
    assert len(calls) == 1
    assert calls[0][0][-1] == "/bin/sh -s"


def test_favorite_replacement_validates_both_values_before_router_write() -> None:
    class NoWriteRouter(RouterClient):
        def run_script(self, _script: str, *, timeout: int = 60) -> str:
            pytest.fail("invalid favorites must not reach the router")

    router = NoWriteRouter()
    valid = "998:402654182:1-65535:1:6:998"

    with pytest.raises(ValueError, match="favorite record"):
        router.replace_astrill_favorites("invalid", valid)
    with pytest.raises(ValueError, match="favorite record"):
        router.replace_astrill_favorites(valid, "invalid")


def test_favorite_replacement_requires_exact_readback() -> None:
    expected = "998:402654182:1-65535:1:6:998"
    replacement = expected + ",1109:402654293:1-65535:1:6:1109"

    class MismatchedRouter(RouterClient):
        def run_script(self, _script: str, *, timeout: int = 60) -> str:
            assert timeout == 30
            return ""

        def native_astrill_settings(self) -> NativeAstrillSettings:
            return NativeAstrillSettings.from_dict({"astrill_favlist": expected})

    with pytest.raises(
        RouterError,
        match="did not persist native Astrill settings: astrill_favlist",
    ):
        MismatchedRouter().replace_astrill_favorites(expected, replacement)


def test_connection_apply_restores_native_settings_after_switch_failure() -> None:
    selection = AstrillConnectionSelection(1109, 1109, 536872021, "443", 1, 1, 6)

    class FailingConnectionRouter(RouterClient):
        def __init__(self) -> None:
            super().__init__("unused")
            self.values = {"astrill_cipher": "default"}
            self.writes: list[dict[str, str]] = []

        def native_astrill_settings(self) -> NativeAstrillSettings:
            return NativeAstrillSettings.from_dict(self.values)

        def _write_native_astrill_values(
            self, values: dict[str, str]
        ) -> NativeAstrillSettings:
            self.writes.append(values)
            self.values.update(values)
            return self.native_astrill_settings()

        def switch_astrill(self, **_values: int | str) -> dict[str, object]:
            raise RouterError("connection failed")

    router = FailingConnectionRouter()
    with pytest.raises(RouterError, match="connection failed"):
        router.apply_astrill_connection(selection, {"astrill_cipher": "AES-256-CBC"})

    assert router.writes == [
        {"astrill_cipher": "AES-256-CBC"},
        {"astrill_cipher": "default"},
    ]
    assert router.native_astrill_settings().get("astrill_cipher") == "default"


def test_native_connection_apply_restarts_an_active_tunnel() -> None:
    selection = AstrillConnectionSelection(1109, 1109, 536872021, "443", 1, 1, 6)

    class NativeConnectionRouter(RouterClient):
        def __init__(self) -> None:
            super().__init__("unused")
            self.values: dict[str, str] = {}
            self.connected = True
            self.connection_calls: list[bool] = []

        def native_astrill_settings(self) -> NativeAstrillSettings:
            return NativeAstrillSettings.from_dict(self.values)

        def native_astrill_status(self) -> dict[str, object]:
            return {"vpn_state": "up" if self.connected else "down"}

        def _write_native_astrill_values(
            self, values: dict[str, str]
        ) -> NativeAstrillSettings:
            self.values.update(values)
            return self.native_astrill_settings()

        def set_astrill_connection(
            self, connected: bool, *, companion_enabled: bool
        ) -> dict[str, object]:
            assert companion_enabled is False
            self.connection_calls.append(connected)
            self.connected = connected
            return self.native_astrill_status()

    router = NativeConnectionRouter()
    result = router.apply_astrill_connection(
        selection,
        {"astrill_wanmtu": "1400"},
        companion_enabled=False,
    )

    assert router.connection_calls == [False, True]
    assert result.status["vpn_state"] == "up"
    assert result.settings.get("astrill_serverid") == "1109"
    assert result.settings.get("astrill_wanmtu") == "1400"


def test_native_connection_apply_restores_and_reconnects_after_failure() -> None:
    selection = AstrillConnectionSelection(1109, 1109, 536872021, "443", 1, 1, 6)
    original = AstrillConnectionSelection(458, 458, 536871370, "80", 0, 0, 5)

    class FailingNativeConnectionRouter(RouterClient):
        def __init__(self) -> None:
            super().__init__("unused")
            self.values = {
                **original.native_values(),
                "astrill_wanmtu": "1446",
            }
            self.connected = True
            self.connect_attempts = 0
            self.connection_calls: list[bool] = []
            self.writes: list[dict[str, str]] = []

        def native_astrill_settings(self) -> NativeAstrillSettings:
            return NativeAstrillSettings.from_dict(self.values)

        def native_astrill_status(self) -> dict[str, object]:
            return {"vpn_state": "up" if self.connected else "down"}

        def _write_native_astrill_values(
            self, values: dict[str, str]
        ) -> NativeAstrillSettings:
            self.writes.append(values)
            self.values.update(values)
            return self.native_astrill_settings()

        def set_astrill_connection(
            self, connected: bool, *, companion_enabled: bool
        ) -> dict[str, object]:
            assert companion_enabled is False
            self.connection_calls.append(connected)
            if connected:
                self.connect_attempts += 1
                if self.connect_attempts == 1:
                    raise RouterError("new endpoint failed")
            self.connected = connected
            return self.native_astrill_status()

    router = FailingNativeConnectionRouter()
    with pytest.raises(RouterError, match="new endpoint failed"):
        router.apply_astrill_connection(
            selection,
            {"astrill_wanmtu": "1400"},
            companion_enabled=False,
        )

    assert router.connection_calls == [False, True, False, True]
    assert router.connected is True
    assert router.native_astrill_settings().get("astrill_serverid") == "458"
    assert router.native_astrill_settings().get("astrill_wanmtu") == "1446"
