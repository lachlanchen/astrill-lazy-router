from __future__ import annotations

import base64
import hashlib
import json
import shlex
import shutil
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
    _COMPANION_INTEGRITY_SHELL,
    DOMAIN_REFRESH_TIMEOUT,
    HYBRID_POLICY_TIMEOUT,
    CommandResult,
    RouterClient,
    RouterError,
    _openssh_config_path,
    _parse_native_clients,
)
from astrill_lazy.subprocess_support import background_process_options

WINDOWS_NO_WINDOW = 0x08000000


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
@pytest.mark.parametrize(
    ("package_payload", "bootstrap_digest", "expected_package", "expected_bootstrap"),
    [
        (b"package", None, "true", "true"),
        (b"corrupt-package", None, "false", "true"),
        (b"package", "0" * 32, "true", "false"),
    ],
)
def test_companion_integrity_probe_hashes_actual_nvram_bytes(
    package_payload: bytes,
    bootstrap_digest: str | None,
    expected_package: str,
    expected_bootstrap: str,
) -> None:
    expected_payload = b"package"
    package_md5 = hashlib.md5(
        expected_payload,
        usedforsecurity=False,
    ).hexdigest()
    package_chunk = base64.b64encode(package_payload).decode("ascii")
    bootstrap = "#!/bin/sh\nexit 0\n"
    expected_bootstrap_md5 = hashlib.md5(
        bootstrap.encode("ascii"),
        usedforsecurity=False,
    ).hexdigest()
    stored_bootstrap_md5 = bootstrap_digest or expected_bootstrap_md5
    script = f"""
uudecode() {{
    [ "$1" = -o ] || return 1
    output=$2
    sed '1d;$d' | base64 --decode > "$output"
}}
nvram() {{
    case $2 in
        astrill_lazy_bootstrap) printf '%s' {shlex.quote(bootstrap)} ;;
        astrill_lazy_bootstrap_md5)
            printf '%s' {shlex.quote(stored_bootstrap_md5)} ;;
        astrill_lazy_pkg_count) printf 1 ;;
        astrill_lazy_pkg_md5) printf '%s' {shlex.quote(package_md5)} ;;
        astrill_lazy_pkg_0) printf '%s' {shlex.quote(package_chunk)} ;;
        *) return 1 ;;
    esac
}}
{_COMPANION_INTEGRITY_SHELL}
"""
    result = subprocess.run(
        ["sh", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        **background_process_options(),
    )
    values = dict(line.split("\t", 1) for line in result.stdout.splitlines())

    assert values == {
        "integrity:package": expected_package,
        "integrity:bootstrap": expected_bootstrap,
    }


def test_hybrid_policy_mutations_stage_helper_and_use_extended_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    expected_version = "test-version"
    helper_calls: list[RouterClient] = []
    commands: list[list[str]] = []

    monkeypatch.setattr(
        "astrill_lazy.installer.RouterInstaller.ensure_hybrid_helper",
        lambda installer: helper_calls.append(installer.client),
    )
    monkeypatch.setattr(
        client,
        "_policy_identity_args",
        lambda: (expected_version, "a" * 32, "b" * 32),
    )

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        commands.append(arguments)
        if arguments[0] in {"apply", "core-apply", "overlay-put"}:
            assert input_bytes == b"# astrill-lazy-rules-v1\n"
        else:
            assert input_bytes is None
        assert timeout == HYBRID_POLICY_TIMEOUT
        return CommandResult('{"health":"healthy"}\n', "", 0)

    monkeypatch.setattr(client, "_run_alctl", run_alctl)

    assert client.apply_rules("# astrill-lazy-rules-v1\n")["health"] == "healthy"
    assert client.rollback()["health"] == "healthy"
    assert client.core_apply(4, "# astrill-lazy-rules-v1\n")["health"] == "healthy"
    assert client.core_rollback(5)["health"] == "healthy"
    assert (
        client.overlay_put(
            "controller-abc",
            6,
            "192.168.1.10/32",
            "# astrill-lazy-rules-v1\n",
        )["health"]
        == "healthy"
    )
    assert client.overlay_remove("controller-abc", 7)["health"] == "healthy"
    assert helper_calls == [client] * 6
    assert commands == [
        ["apply", expected_version, "a" * 32, "b" * 32, "-"],
        ["rollback", expected_version, "a" * 32, "b" * 32, "--json"],
        ["core-apply", expected_version, "a" * 32, "b" * 32, "4", "-"],
        [
            "core-rollback",
            expected_version,
            "a" * 32,
            "b" * 32,
            "5",
            "--json",
        ],
        [
            "overlay-put",
            expected_version,
            "a" * 32,
            "b" * 32,
            "controller-abc",
            "6",
            "192.168.1.10/32",
            "-",
            "-",
            "-",
        ],
        [
            "overlay-remove",
            expected_version,
            "a" * 32,
            "b" * 32,
            "controller-abc",
            "7",
        ],
    ]


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


def test_transient_application_flow_commands_use_structured_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    calls: list[list[str]] = []

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        calls.append(arguments)
        assert input_bytes is None
        assert timeout is None
        return CommandResult('{"ok":true,"count":1,"flows":[]}\n', "", 0)

    monkeypatch.setattr(client, "_run_alctl", run_alctl)

    assert client.app_flows()["ok"] is True
    assert (
        client.set_app_flow(
            "mac-uuremote",
            "192.168.1.99",
            "udp",
            "64479",
            "direct",
        )["count"]
        == 1
    )
    assert client.delete_app_flow("mac-uuremote")["ok"] is True
    assert calls == [
        ["app-flow", "list"],
        [
            "app-flow",
            "set",
            "mac-uuremote",
            "192.168.1.99",
            "udp",
            "64479",
            "direct",
        ],
        ["app-flow", "delete", "mac-uuremote"],
    ]


def test_astrill_switch_timeout_covers_verified_failure_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        assert arguments == [
            "astrill-switch",
            "998",
            "998",
            "123",
            "443",
            "0",
            "3",
            "6",
            "--json",
        ]
        assert input_bytes is None
        assert timeout == 210
        return CommandResult('{"health":"healthy"}\n', "", 0)

    monkeypatch.setattr(client, "_run_alctl", run_alctl)

    assert client.switch_astrill(
        server_id=998,
        sid=998,
        encoded_ip=123,
        port="443",
        port_index=0,
        protocol=3,
        vpn_mode=6,
    ) == {"health": "healthy"}


def test_companion_connect_allows_reconcile_and_preserves_degraded_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        assert arguments == ["astrill-connect"]
        assert input_bytes is None
        assert timeout == 210
        return CommandResult(
            json.dumps(
                {
                    "ok": True,
                    "partial": True,
                    "status": {
                        "vpn_state": "up",
                        "health": "degraded",
                        "policy_health": "degraded",
                        "precedence_ok": "false",
                        "native_min_pref": "27998",
                        "direct_pref": "27996",
                        "vpn_pref": 27997,
                        "enabled_origin_count": "2",
                        "table_readiness": {
                            "direct": "true",
                            "vpn": 1,
                            "native": False,
                        },
                        "last_reconcile_error": "native rules did not stabilize",
                    },
                }
            ),
            "",
            0,
        )

    monkeypatch.setattr(client, "_run_alctl", run_alctl)

    status = client.set_astrill_connection(True, companion_enabled=True)

    assert status["ok"] is True
    assert status["partial"] is True
    assert status["vpn_state"] == "up"
    assert status["policy_health"] == "degraded"
    assert status["precedence_ok"] is False
    assert status["native_min_pref"] == 27998
    assert status["direct_pref"] == 27996
    assert status["vpn_pref"] == 27997
    assert status["enabled_origin_count"] == 2
    assert status["table_readiness"] == {
        "direct": True,
        "vpn": True,
        "native": False,
    }


def test_companion_disconnect_keeps_shorter_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        assert arguments == ["astrill-disconnect"]
        assert input_bytes is None
        assert timeout == 80
        return CommandResult(
            '{"vpn_state":"down","policy_health":"ready"}\n',
            "",
            0,
        )

    monkeypatch.setattr(client, "_run_alctl", run_alctl)
    status = client.set_astrill_connection(False, companion_enabled=True)

    assert status == {"vpn_state": "down", "policy_health": "ready"}


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
            tagged("presence:astrill_lazy_bootstrap_md5", "def456"),
            tagged("presence:rc_startup", "bootstrap-launcher"),
            tagged("presence:mypage_scripts", "/tmp/astrill-lazy/alpage"),
            "integrity:package\ttrue\n",
            "integrity:bootstrap\ttrue\n",
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
        "bootstrap_md5": "def456",
        "rc_startup": "bootstrap-launcher",
        "mypage_scripts": "/tmp/astrill-lazy/alpage",
        "package_integrity": True,
        "bootstrap_integrity": True,
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
            tagged("presence:astrill_lazy_bootstrap_md5", ""),
            tagged("presence:rc_startup", ""),
            tagged("presence:mypage_scripts", ""),
            "integrity:package\tfalse\n",
            "integrity:bootstrap\tfalse\n",
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


def test_nvram_get_exact_removes_only_the_cli_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    captured = ""

    def run_script(script: str, *, timeout: int = 60) -> str:
        nonlocal captured
        assert timeout == 60
        captured = script
        return "value\t6162630a0a\n"

    monkeypatch.setattr(client, "run_script", run_script)

    assert client.nvram_get_exact("astrill_lazy_bootstrap") == "abc\n"
    assert "nvram get astrill_lazy_bootstrap" in captured


def test_nvram_get_exact_rejects_an_unsafe_key() -> None:
    client = RouterClient()

    with pytest.raises(ValueError, match="invalid NVRAM key"):
        client.nvram_get_exact("key; reboot")


@pytest.mark.parametrize(("output", "expected"), [("true\n", True), ("false\n", False)])
def test_nvram_is_set_distinguishes_empty_from_unset(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    expected: bool,
) -> None:
    client = RouterClient()
    captured = ""

    def run_script(script: str, *, timeout: int = 60) -> str:
        nonlocal captured
        captured = script
        return output

    monkeypatch.setattr(client, "run_script", run_script)

    assert client.nvram_is_set("rc_startup") is expected
    assert "grep -q '^rc_startup='" in captured


def test_nvram_is_set_rejects_invalid_results_and_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    monkeypatch.setattr(client, "run_script", lambda _script: "maybe\n")

    with pytest.raises(RouterError, match="invalid NVRAM presence"):
        client.nvram_is_set("rc_startup")
    with pytest.raises(ValueError, match="invalid NVRAM key"):
        client.nvram_is_set("key; reboot")


def test_companion_presence_includes_stored_package_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()
    captured = ""
    output = "".join(
        (
            "astrill_lazy_installed\t31\n",
            "astrill_lazy_version\t302e322e3131\n",
            "astrill_lazy_pkg_md5\t" + ("61" * 32) + "\n",
            "astrill_lazy_bootstrap_md5\t" + ("62" * 32) + "\n",
            "rc_startup\t6c61756e63686572\n",
            "mypage_scripts\t7061676573\n",
            "integrity:package\ttrue\n",
            "integrity:bootstrap\ttrue\n",
            "runtime\ttrue\n",
        )
    )

    def run_script(script: str) -> str:
        nonlocal captured
        captured = script
        return output

    monkeypatch.setattr(client, "run_script", run_script)

    assert client.companion_presence() == {
        "installed": True,
        "version": "0.2.11",
        "package_md5": "a" * 32,
        "bootstrap_md5": "b" * 32,
        "rc_startup": "launcher",
        "mypage_scripts": "pages",
        "package_integrity": True,
        "bootstrap_integrity": True,
        "runtime": True,
    }
    assert "uudecode -o" in captured
    assert "md5sum" in captured
    assert "integrity:package" in captured


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
