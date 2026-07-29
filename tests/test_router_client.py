from __future__ import annotations

import subprocess

import pytest
from astrill_lazy.astrill import AstrillConnectionSelection
from astrill_lazy.native_settings import NativeAstrillSettings
from astrill_lazy.router import (
    DOMAIN_REFRESH_TIMEOUT,
    CommandResult,
    RouterClient,
    RouterError,
    _parse_native_clients,
)


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

    def complete(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess:
        captured.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0, b"ready\n", b"")

    monkeypatch.setattr(subprocess, "run", complete)
    client = RouterClient(
        "192.168.1.1",
        user="root",
        port=2222,
        identity_file="~/.ssh/router-key",
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
    assert captured[captured.index("-i") + 1].endswith("/.ssh/router-key")
    assert "root@192.168.1.1" in captured


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
