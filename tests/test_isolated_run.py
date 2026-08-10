from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from astrill_lazy.isolated_run import (
    IsolatedRunError,
    _interface_identity,
    _normalize_dns_servers,
    _resolve_allowed_domain_map,
    _resolve_allowed_domains,
    run_isolated_command,
)
from astrill_lazy.native_settings import NativeAstrillSettings
from astrill_lazy.router import RouterError


class FakeRouter:
    def __init__(
        self,
        *,
        connected: bool = False,
        native_device_mode: str = "1",
        native_devices: str = "",
    ) -> None:
        self.connected = connected
        self.flows: list[dict[str, object]] = []
        self.connection_changes: list[bool] = []
        self.settings = NativeAstrillSettings.from_dict(
            {
                "astrill_devmode": native_device_mode,
                "astrill_devices": native_devices,
            }
        )

    def status(self) -> dict[str, object]:
        return {"vpn_state": "up" if self.connected else "down"}

    def app_flows(self) -> dict[str, object]:
        return {"ok": True, "flows": list(self.flows)}

    def native_astrill_settings(self) -> NativeAstrillSettings:
        return self.settings

    def set_app_flow(
        self,
        flow_id: str,
        source: str,
        protocol: str,
        source_ports: str,
        target: str,
    ) -> dict[str, object]:
        self.flows = [item for item in self.flows if item["id"] != flow_id]
        self.flows.append(
            {
                "id": flow_id,
                "source": source,
                "protocol": protocol,
                "source_ports": source_ports,
                "target": target,
            }
        )
        return self.app_flows()

    def delete_app_flow(self, flow_id: str) -> dict[str, object]:
        self.flows = [item for item in self.flows if item["id"] != flow_id]
        return self.app_flows()

    def set_astrill_connection(
        self, connected: bool, *, companion_enabled: bool
    ) -> dict[str, object]:
        assert companion_enabled is True
        self.connection_changes.append(connected)
        self.connected = connected
        return self.status()


def test_interface_identity_reads_ipv4_and_mac_from_separate_ip_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "address" in arguments:
            output = json.dumps(
                [
                    {
                        "ifname": "enp0s31f6",
                        "addr_info": [
                            {
                                "family": "inet",
                                "local": "192.168.1.100",
                                "scope": "global",
                            }
                        ],
                    }
                ]
            )
        else:
            output = json.dumps(
                [
                    {
                        "ifname": "enp0s31f6",
                        "link_type": "ether",
                        "address": "D0:8E:79:0D:26:99",
                    }
                ]
            )
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)

    assert _interface_identity("enp0s31f6") == (
        "192.168.1.100",
        "d0:8e:79:0d:26:99",
    )
    assert calls == [
        [
            "/usr/sbin/ip",
            "-json",
            "-4",
            "address",
            "show",
            "dev",
            "enp0s31f6",
        ],
        [
            "/usr/sbin/ip",
            "-json",
            "link",
            "show",
            "dev",
            "enp0s31f6",
        ],
    ]


def test_interface_identity_rejects_missing_link_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "address" in arguments:
            output = '[{"addr_info":[{"family":"inet","local":"192.168.1.100","scope":"global"}]}]'
        else:
            output = '[{"ifname":"enp0s31f6","link_type":"ether"}]'
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(IsolatedRunError, match="Ethernet identity"):
        _interface_identity("enp0s31f6")


def test_explicit_dns_resolution_unions_valid_ipv4_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        output = (
            "example.com.\n93.184.216.34\n"
            if "@1.1.1.1" in arguments
            else "93.184.216.35\n"
        )
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)

    assert _resolve_allowed_domains(
        ("Example.COM",),
        dns_servers=("1.1.1.1", "8.8.8.8"),
    ) == (
        ("example.com",),
        ("93.184.216.34", "93.184.216.35"),
    )
    assert calls == [
        [
            "/usr/bin/dig",
            "+time=3",
            "+tries=1",
            "+short",
            "A",
            "example.com",
            "@1.1.1.1",
        ],
        [
            "/usr/bin/dig",
            "+time=3",
            "+tries=1",
            "+short",
            "A",
            "example.com",
            "@8.8.8.8",
        ],
    ]
    assert _resolve_allowed_domain_map(
        ("Example.COM",),
        dns_servers=("1.1.1.1", "8.8.8.8"),
    ) == (
        ("example.com",),
        (("example.com", ("93.184.216.34", "93.184.216.35")),),
    )


def test_explicit_dns_servers_are_bounded_and_safe() -> None:
    assert _normalize_dns_servers(("1.1.1.1", "1.1.1.1", "8.8.8.8")) == (
        "1.1.1.1",
        "8.8.8.8",
    )
    with pytest.raises(IsolatedRunError, match="usable IPv4"):
        _normalize_dns_servers(("127.0.0.1",))
    with pytest.raises(IsolatedRunError, match="at most 3"):
        _normalize_dns_servers(("1.1.1.1", "8.8.8.8", "9.9.9.9", "208.67.222.222"))


def test_explicit_dns_is_preserved_only_for_namespace_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    router = FakeRouter(connected=True)
    prepare_seen = False
    pin_seen = False

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal pin_seen, prepare_seen
        if "prepare" in arguments:
            prepare_seen = True
            assert "--preserve-env=ASTRILL_LAZY_PROFILE_DNS" in arguments
            assert kwargs["env"]["ASTRILL_LAZY_PROFILE_DNS"] == "1.1.1.1 8.8.8.8"
            return subprocess.CompletedProcess(
                arguments,
                0,
                '{"profile":"taskvpn","namespace":"al-taskvpn",'
                '"address":"192.168.1.244"}',
                "",
            )
        if "pin-hosts" in arguments:
            pin_seen = True
            assert arguments[-2:] == ["example.com", "93.184.216.34"]
        if "execute" in arguments:
            assert "--preserve-env=ASTRILL_LAZY_PROFILE_DNS" not in arguments
            assert kwargs.get("env") is None
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._resolve_allowed_domain_map",
        lambda _domains, *, dns_servers: (
            ("example.com",),
            (("example.com", ("93.184.216.34",)),),
        ),
    )

    assert run_isolated_command(
        router,
        ["/bin/true"],
        helper=helper,
        parent_interface="eth0",
        allowed_domains=("example.com",),
        dns_servers=("1.1.1.1", "8.8.8.8"),
    ) == 0
    assert prepare_seen is True
    assert pin_seen is True
    assert router.flows == []


def test_isolated_run_scopes_flows_and_restores_disconnected_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    router = FakeRouter()
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "prepare" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "profile": "taskvpn",
                        "namespace": "al-taskvpn",
                        "address": "192.168.1.240",
                        "mac": "02:41:4c:00:00:01",
                    }
                ),
                "",
            )
        if "execute" in arguments:
            assert router.connected is True
            assert router.flows == [
                {
                    "id": "isolated-taskvpn-host-tcp",
                    "source": "192.168.1.100",
                    "protocol": "tcp",
                    "source_ports": "1024:65535",
                    "target": "direct",
                },
                {
                    "id": "isolated-taskvpn-host-udp",
                    "source": "192.168.1.100",
                    "protocol": "udp",
                    "source_ports": "1024:65535",
                    "target": "direct",
                },
                {
                    "id": "isolated-taskvpn-tcp",
                    "source": "192.168.1.240",
                    "protocol": "tcp",
                    "source_ports": "1024:65535",
                    "target": "vpn",
                },
            ]
            return subprocess.CompletedProcess(arguments, 7, "", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._resolve_allowed_domains",
        lambda _domains: (("example.com",), ("93.184.216.34",)),
    )

    result = run_isolated_command(
        router,
        ["--", "/bin/true", "argument"],
        helper=helper,
        parent_interface="eth0",
        allowed_domains=("example.com",),
    )

    assert result == 7
    assert router.flows == []
    assert router.connection_changes == [True, False]
    assert any("cleanup" in call for call in calls)


def test_isolated_run_preserves_preexisting_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    router = FakeRouter(connected=True)

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "prepare" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                '{"profile":"taskvpn","namespace":"al-taskvpn",'
                '"address":"192.168.1.241"}',
                "",
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._resolve_allowed_domains",
        lambda _domains: (("example.com",), ("93.184.216.34",)),
    )

    assert (
        run_isolated_command(
            router,
            ["/bin/true"],
            helper=helper,
            parent_interface="eth0",
            allowed_domains=("example.com",),
        )
        == 0
    )
    assert router.connected is True
    assert router.connection_changes == []
    assert router.flows == []


def test_isolated_run_disconnects_after_a_failed_connection_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    router = FakeRouter()
    original_connection = router.set_astrill_connection
    status_calls = 0

    def status_with_late_tunnel() -> dict[str, object]:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 2:
            router.connected = True
            return {"vpn_state": "down"}
        return {"vpn_state": "up" if router.connected else "down"}

    def connect_then_time_out(
        connected: bool, *, companion_enabled: bool
    ) -> dict[str, object]:
        if connected:
            router.connection_changes.append(True)
            raise RouterError("late tunnel")
        return original_connection(connected, companion_enabled=companion_enabled)

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "prepare" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                '{"profile":"taskvpn","namespace":"al-taskvpn",'
                '"address":"192.168.1.242"}',
                "",
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    router.set_astrill_connection = connect_then_time_out  # type: ignore[method-assign]
    router.status = status_with_late_tunnel  # type: ignore[method-assign]
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._resolve_allowed_domains",
        lambda _domains: (("example.com",), ("93.184.216.34",)),
    )

    with pytest.raises(RouterError, match="late tunnel"):
        run_isolated_command(
            router,
            ["/bin/true"],
            helper=helper,
            parent_interface="eth0",
            allowed_domains=("example.com",),
        )

    assert router.connected is False
    assert router.connection_changes == [True, False]
    assert router.flows == []


def test_isolated_run_cleans_a_late_tunnel_after_interrupted_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    router = FakeRouter()
    disconnect_attempts = 0

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "prepare" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                '{"profile":"taskvpn","namespace":"al-taskvpn",'
                '"address":"192.168.1.243"}',
                "",
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def interrupted_connection(
        connected: bool, *, companion_enabled: bool
    ) -> dict[str, object]:
        nonlocal disconnect_attempts
        assert companion_enabled is True
        router.connection_changes.append(connected)
        if connected:
            raise KeyboardInterrupt
        disconnect_attempts += 1
        if disconnect_attempts == 1:
            router.connected = True
            raise RouterError("controller is busy")
        router.connected = False
        return router.status()

    router.set_astrill_connection = interrupted_connection  # type: ignore[method-assign]
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("astrill_lazy.isolated_run.time.sleep", lambda _delay: None)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._resolve_allowed_domains",
        lambda _domains: (("example.com",), ("93.184.216.34",)),
    )

    with pytest.raises(KeyboardInterrupt):
        run_isolated_command(
            router,
            ["/bin/true"],
            helper=helper,
            parent_interface="eth0",
            allowed_domains=("example.com",),
        )

    assert disconnect_attempts == 2
    assert router.connected is False
    assert router.flows == []


@pytest.mark.parametrize("profile", ["", "UPPER", "too-long-id", "bad_id"])
def test_isolated_run_rejects_unsafe_profile(profile: str) -> None:
    with pytest.raises(IsolatedRunError, match="profile"):
        run_isolated_command(FakeRouter(), ["/bin/true"], profile=profile)


def test_isolated_run_refuses_native_vpn_host_before_router_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    router = FakeRouter(
        native_devices=(
            "D0:8E:79:0D:26:99/192.168.1.100/optiplex-7090"
        )
    )
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._resolve_allowed_domains",
        lambda _domains: (("example.com",), ("93.184.216.34",)),
    )

    with pytest.raises(IsolatedRunError, match="Codex remains Direct"):
        run_isolated_command(
            router,
            ["/bin/true"],
            helper=helper,
            parent_interface="eth0",
            allowed_domains=("example.com",),
        )

    assert router.flows == []
    assert router.connection_changes == []


def test_isolated_run_requires_destination_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "astrill-lazy-netns"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    helper.chmod(0o755)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(
        "astrill_lazy.isolated_run._interface_identity",
        lambda _interface: ("192.168.1.100", "d0:8e:79:0d:26:99"),
    )

    with pytest.raises(IsolatedRunError, match="--allow-domain"):
        run_isolated_command(
            FakeRouter(),
            ["/bin/true"],
            helper=helper,
            parent_interface="eth0",
        )
