from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from astrill_lazy.router import (
    DOMAIN_REFRESH_TIMEOUT,
    CommandResult,
    RouterClient,
    RouterError,
    _openssh_config_path,
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
