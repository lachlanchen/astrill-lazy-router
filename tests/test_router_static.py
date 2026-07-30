from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from astrill_lazy.router import _clean_ssh_stderr

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
def test_router_and_helper_scripts_parse_with_posix_shell() -> None:
    scripts = [
        ROOT / "router" / "alctl",
        ROOT / "router" / "alapi",
        ROOT / "router" / "alpage",
        ROOT / "router" / "bootstrap.sh",
        ROOT / "helpers" / "astrill-lazy-netns",
        ROOT / "helpers" / "astrill-lazy-profile-runner",
        ROOT / "scripts" / "install-desktop.sh",
        ROOT / "scripts" / "install-novnc-service.sh",
        ROOT / "scripts" / "run-novnc-debug.sh",
        ROOT / "scripts" / "uninstall-novnc-service.sh",
        ROOT / "contrib" / "macos" / "install-launcher.sh",
        ROOT / "contrib" / "macos" / "install-uuremote-route-reporter.sh",
        ROOT / "contrib" / "macos" / "uuremote-route-reporter.sh",
    ]
    for script in scripts:
        subprocess.run(["sh", "-n", str(script)], check=True)


def test_router_bootstrap_waits_for_the_old_controller_before_replacement() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    installer = (ROOT / "desktop" / "astrill_lazy" / "installer.py").read_text(
        encoding="utf-8"
    )

    stop_watchdog = bootstrap.index("for pid in $(watchdog_pids)")
    wait_for_lock = bootstrap.index('while [ -d "$BASE/controller.lock" ]')
    stop_controller = bootstrap.index('"$BASE/alctl" stop')
    extract_package = bootstrap.index('tar -xzf "$ARCHIVE"')
    assert stop_watchdog < wait_for_lock < stop_controller < extract_package
    assert 'kill -0 "$lock_pid"' in bootstrap
    assert '*" $BASE/alctl refresh "*)' in bootstrap
    assert 'kill -9 "$lock_pid"' in bootstrap
    assert 'rmdir "$BASE/controller.lock"' in bootstrap
    assert "timeout=300" in installer


def test_application_profile_has_a_stable_locally_administered_mac() -> None:
    helper = ROOT / "helpers" / "astrill-lazy-netns"
    helper_source = helper.read_text(encoding="ascii")
    result = subprocess.run(
        ["sh", str(helper), "identity", "uuremote"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "profile": "uuremote",
        "mac": "02:41:4c:de:39:3a",
    }
    invalid = subprocess.run(
        ["sh", str(helper), "identity", "../invalid"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0
    assert "invalid profile" in invalid.stderr
    assert "deconfig|bound|renew) dhcp_hook" in helper_source
    assert "dhcp_pid_for" in helper_source
    assert 'grep -Fxq "$pid_file"' in helper_source


def test_application_profile_system_service_is_fixed_and_restartable() -> None:
    unit = (
        ROOT
        / "data"
        / "io.github.lachlanchen.AstrillLazyRouter.ApplicationProfile@.service"
    ).read_text(encoding="ascii")
    runner = (ROOT / "helpers" / "astrill-lazy-profile-runner").read_text(
        encoding="ascii"
    )

    assert "EnvironmentFile=/etc/astrill-lazy/profiles/%i.conf" in unit
    assert "ExecStopPost=-/usr/local/libexec/astrill-lazy-netns cleanup" in unit
    assert "Restart=always" in unit
    assert "pgrep -u" in runner
    assert "DBUS_SESSION_BUS_ADDRESS" in runner
    assert "eval " not in runner


def test_novnc_service_is_rendered_from_the_checked_out_repository() -> None:
    unit = (
        ROOT / "data" / "io.github.lachlanchen.AstrillLazyRouter.NoVNC.service"
    ).read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install-novnc-service.sh").read_text(
        encoding="utf-8"
    )

    assert "@ROOT@" in unit
    assert "Projects/astrill-lazy" not in unit
    assert "EnvironmentFile=-%h/.config/astrill-lazy/novnc.env" in unit
    assert "Restart=always" in unit
    assert 'sed "s|@ROOT@|$escaped_root|g"' in installer
    assert "--install-only" in installer
    assert "DBUS_SESSION_BUS_ADDRESS" in installer

    runner = (ROOT / "scripts" / "run-novnc-debug.sh").read_text(encoding="utf-8")
    for component in ("Xvfb", "openbox", "x11vnc", "websockify", "Astrill Lazy GUI"):
        assert f':{component}"' in runner
    assert "restarting the noVNC stack" in runner


def test_router_upgrade_cleans_existing_runtime_before_extraction() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    guarded_stop = '"$BASE/alctl" stop >/dev/null 2>&1 || exit 1'
    extraction = 'tar -xzf "$ARCHIVE" -C /tmp || exit 1'

    assert guarded_stop in bootstrap
    assert bootstrap.index("for pid in $(watchdog_pids)") < bootstrap.index(
        'while [ -d "$BASE/controller.lock" ]'
    )
    assert bootstrap.index('while [ -d "$BASE/controller.lock" ]') < bootstrap.index(
        guarded_stop
    )
    assert bootstrap.index(guarded_stop) < bootstrap.index(extraction)


def test_desktop_installer_selects_a_supported_python_and_opt_in_autostart() -> None:
    installer = (ROOT / "scripts" / "install-desktop.sh").read_text(encoding="utf-8")

    assert "python3.12 python3.11 python3" in installer
    assert "sys.version_info < (3, 11)" in installer
    assert "ASTRILL_LAZY_PYTHON" in installer
    assert "ASTRILL_LAZY_ENABLE_AUTOSTART" in installer


def test_remote_novnc_launchers_prefer_the_current_ubuntu_host() -> None:
    macos = (ROOT / "contrib" / "macos" / "open-astrill-lazy.applescript").read_text(
        encoding="utf-8"
    )
    windows = (ROOT / "contrib" / "windows" / "Open-AstrillLazyRouter.ps1").read_text(
        encoding="utf-8"
    )

    assert '"glassagent-ubuntu"' in macos
    for launcher in (macos, windows):
        assert "lachlan@192.168.1.100" in launcher
        assert "127.0.0.1:" in launcher
        assert "BatchMode=yes" in launcher
        assert "password" not in launcher.casefold()


def test_policy_controller_never_evaluates_rule_content() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    helper = (ROOT / "helpers" / "astrill-lazy-netns").read_text(encoding="ascii")
    runner = (ROOT / "helpers" / "astrill-lazy-profile-runner").read_text(
        encoding="ascii"
    )
    assert "eval " not in controller
    assert "eval " not in helper
    assert "eval " not in runner
    assert "APP_CHAIN=AL_LAZY_APP" in controller
    assert "MAX_APP_FLOWS=16" in controller
    assert "--sport" in controller
    assert "app-flow)" in controller
    assert "astrill_lazy_app_flows" not in controller
    assert "--set-xmark" in controller
    assert "0xc000000" in controller
    assert "RPDB_PREF_FLOOR=100" in controller
    assert "VPN_BLACKHOLE_METRIC=32767" in controller
    assert "RPDB_RECONCILE_ATTEMPTS" not in controller
    assert "ASTRILL_NATIVE_STABLE_ATTEMPTS=12" in controller
    assert "ASTRILL_NATIVE_STABLE_SAMPLES=2" in controller
    assert "select_owned_prefs" in controller
    assert "wait_for_native_rules_stable" in controller
    assert "record_owned_prefs" in controller
    assert "remove_exact_ip_rule" in controller
    assert "DIRECT_PREF=" not in controller
    assert "VPN_PREF=" not in controller
    assert "WATCHDOG_INTERVAL=60" in controller
    assert "WATCHDOG_REFRESH_CYCLES=30" in controller
    assert "ASTRILL_CONNECT_ATTEMPTS=60" in controller
    assert "MAX_RULE_BYTES=6144" in controller
    assert "MIN_NVRAM_FREE_BYTES=2048" in controller
    assert 'nvram unset "$PREVIOUS_RULES_KEY"' in controller
    assert "CURRENT_RULES_GZ_KEY=astrill_lazy_rules_gz" in controller
    assert "persistent_rule_bytes" in controller
    assert "persist_rule_document" in controller
    assert '"rollback_persistent":%s' in controller
    assert controller.count("iptables -w 10") >= 15
    assert "insufficient NVRAM headroom" in controller
    assert "watchdog_pids | grep -qx" in controller
    assert "cleanup_watchdog_pid" in controller
    assert "refresh_mode=${3:-0}" in controller
    assert 'apply_runtime "$CURRENT" 1' in controller
    assert '"$RESOLVED"' in controller
    assert 'kill -9 "$pid"' in controller
    assert 'wait "$watchdog_sleep_pid"' in controller
    assert '[ "$watchdog" = true ] || health=degraded' in controller
    assert '"policy_health":"%s","precedence_ok":%s' in controller
    assert '"native_min_pref":%s,"direct_pref":%s,"vpn_pref":%s' in controller
    assert '"table_readiness":{"direct":%s,"vpn":%s,"native":%s}' in controller
    assert '"last_reconcile_error":null' in controller
    assert '"rebase_required":%s' in controller
    assert "VPN_FAIL_CHAIN=AL_LAZY_VPN_FAIL" in controller
    assert '-m mark --mark "$VPN_MARK/$MARK_MASK" -j DROP' in controller
    assert "astrill-connect)" in controller
    assert "astrill-disconnect)" in controller
    assert "/dev/astrill/astrillvpn stop" in controller


def test_macos_uu_reporter_is_change_driven_and_process_scoped() -> None:
    reporter = (ROOT / "contrib" / "macos" / "uuremote-route-reporter.sh").read_text(
        encoding="ascii"
    )
    installer = (
        ROOT / "contrib" / "macos" / "install-uuremote-route-reporter.sh"
    ).read_text(encoding="ascii")
    template = (
        ROOT / "contrib" / "macos" / "com.lachlan.astrill-lazy-uuremote-route.plist.in"
    ).read_text(encoding="ascii")

    assert "/Applications/UURemote\\.app/" in reporter
    assert "lsof -nP -a" in reporter
    assert "-iUDP" in reporter
    assert "app-flow" in reporter
    assert "PasswordAuthentication=no" in reporter
    assert 'nc -z -G 1 "$router_address" 22' in reporter
    assert "eval " not in reporter
    assert 'SUPPORT_DIR="$HOME/Library/Application Support/Astrill Lazy Router"' in (
        installer
    )
    assert 'PROGRAM="$SUPPORT_DIR/uuremote-route-reporter"' in installer
    assert "<integer>30</integer>" in template
    assert "ASTRILL_LAZY_ROUTER_ADDRESS" in template
    assert "ASTRILL_LAZY_HEARTBEAT_SECONDS" in template


def test_failed_astrill_switch_restores_settings_and_original_tunnel_state() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    match = re.search(
        r"^switch_astrill\(\) \{\n(?P<body>.*?)"
        r"^\}\n\nrestart_astrill_for_managed_rebase\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    switch = match["body"]

    state_snapshot = "initially_connected=false"
    state_up = "initially_connected=true"
    requested_start = '/dev/astrill/astrillvpn start > "$BASE/astrill-switch.log"'
    restore_settings = 'done < "$rollback_file"'
    rollback_stop = '/dev/astrill/astrillvpn stop >> "$BASE/astrill-switch.log"'
    state_branch = 'if [ "$initially_connected" = true ]; then'
    rollback_start = '/dev/astrill/astrillvpn start >> "$BASE/astrill-switch.log"'
    original_failure = (
        'fail "new Astrill server did not connect; previous settings were restored"'
    )
    unverified_failure = (
        'fail "new Astrill server did not connect; '
        'previous connection state could not be verified"'
    )

    assert switch.index(state_snapshot) < switch.index(state_up)
    assert switch.index(state_up) < switch.index(requested_start)
    assert switch.index(requested_start) < switch.index(restore_settings)
    assert switch.index(restore_settings) < switch.index(rollback_stop)
    assert switch.index(rollback_stop) < switch.index(state_branch)
    assert switch.index(state_branch) < switch.index(rollback_start)
    assert switch.index(rollback_start) < switch.index(original_failure)
    assert switch.index(original_failure) < switch.index(unverified_failure)

    disconnected_rollback = switch[
        switch.index(rollback_stop) : switch.index(state_branch)
    ]
    assert "astrillvpn start" not in disconnected_rollback
    assert "rollback_stopped=false" in disconnected_rollback
    assert 'while [ "$attempts" -lt 65 ]' in disconnected_rollback
    assert "astrill/openvpn.conf" in disconnected_rollback

    connected_rollback = switch[
        switch.index(state_branch) : switch.index("ensure_routes >/dev/null 2>&1")
    ]
    assert rollback_start in connected_rollback
    assert "rollback_connected=false" in connected_rollback
    assert "rollback_connected=true" in connected_rollback
    assert "rollback_verified=false" in connected_rollback
    assert 'while [ "$attempts" -lt "$ASTRILL_CONNECT_ATTEMPTS" ]' in connected_rollback
    assert "rollback_verified=true" in connected_rollback
    assert '[ "$rollback_stopped" = true ]' in connected_rollback
    assert '[ "$rollback_connected" = true ]' in connected_rollback

    assert "rollback_verified=$rollback_stopped" in switch
    assert switch.count('rm -f "$rollback_file"') == 4
    assert "could not verify the original connection state" in switch
    assert switch.index("ensure_routes >/dev/null 2>&1") < switch.index(
        original_failure
    )


def test_router_policy_precedence_follows_astrill_lifecycle() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")

    prepare = re.search(
        r"^prepare_for_astrill_start\(\) \{\n(?P<body>.*?)^\}\n",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert prepare is not None
    assert prepare["body"].index("ensure_vpn_fail_closed") < prepare["body"].index(
        "remove_owned_ip_rules"
    )
    assert "clear_rebase_required" in prepare["body"]

    switch = re.search(
        r"^switch_astrill\(\) \{\n(?P<body>.*?)"
        r"^\}\n\nrestart_astrill_for_managed_rebase\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert switch is not None
    assert switch["body"].index("prepare_for_astrill_start") < switch["body"].index(
        "/dev/astrill/astrillvpn start"
    )
    assert switch["body"].count("prepare_for_astrill_start") == 2
    assert switch["body"].index("prepare_for_astrill_stop") < switch["body"].index(
        "/dev/astrill/astrillvpn stop"
    )

    connection = re.search(
        r"^set_astrill_connection\(\) \{\n(?P<body>.*?)^\}\n\nusage\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert connection is not None
    already_connected = connection["body"].index("ensure_runtime")
    prepare_start = connection["body"].index("prepare_for_astrill_start")
    native_start = connection["body"].index("/dev/astrill/astrillvpn start")
    assert already_connected < prepare_start < native_start
    assert "ensure_runtime >/dev/null 2>&1 || true" not in connection["body"]

    routes = re.search(
        r"^ensure_routes\(\) \{\n(?P<body>.*?)^\}\n\napply_runtime\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert routes is not None
    assert "if ! tunnel_is_up; then" in routes["body"]
    assert routes["body"].index("ensure_vpn_fail_closed") < routes["body"].index(
        "remove_owned_ip_rules"
    )
    assert "wait_for_native_rules_stable" in routes["body"]
    assert routes["body"].count("install_owned_overlay") == 1
    assert "disable_vpn_fail_closed" in routes["body"]
    assert "rebase_is_required" in routes["body"]
    assert "mark_recorded_overlay_for_rebase" in routes["body"]

    down_branch = connection["body"][connection["body"].index("down)") :]
    assert down_branch.index("prepare_for_astrill_stop") < down_branch.index(
        "/dev/astrill/astrillvpn stop"
    )
    assert "restart_astrill_for_managed_rebase" in connection["body"]


def test_router_removes_only_exact_companion_rules_and_idles_when_ready() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")

    delete_lines = [
        line.strip() for line in controller.splitlines() if "ip rule del" in line
    ]
    assert delete_lines == [
        'ip rule del pref "$pref" fwmark "$mark/$MARK_MASK" lookup "$table" ||'
    ]
    assert "lookup (110|111|112|113|114)" in controller
    assert 'ip rule del pref "$pref" 2>/dev/null' not in controller

    fail_closed = re.search(
        r"^ensure_vpn_fail_closed\(\) \{\n(?P<body>.*?)^\}\n",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert fail_closed is not None
    body = fail_closed["body"].lstrip()
    assert body.startswith("vpn_fail_closed_is_ready && return 0")
    assert '-I FORWARD 1 -j "$VPN_FAIL_CHAIN"' in body

    overlay = re.search(
        r"^owned_overlay_is_valid\(\) \{\n(?P<body>.*?)^\}\n",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert overlay is not None
    assert "load_owned_pref_state" in overlay["body"]
    assert '[ "$owned_vpn_pref" -lt "$current_native_pref" ]' in overlay["body"]

    stop = controller[controller.index("    stop)") :]
    stop = stop[: stop.index("    watchdog-loop)")]
    assert "cleanup_policy ||" in stop
    assert 'fail "could not completely remove the companion policy runtime"' in stop


def test_ddwrt_banner_is_removed_from_ssh_errors() -> None:
    output = (
        "DD-WRT v3.0-r62374 mega\n"
        "Release: 10/19/25\n"
        "Board: Linksys E4200\n"
        "actual failure\n"
    )
    assert _clean_ssh_stderr(output) == "actual failure"


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
def test_router_clients_merge_lan_sources_and_exclude_wan(tmp_path: Path) -> None:
    leases = tmp_path / "dnsmasq.leases"
    leases.write_text(
        "2000000000 AA:BB:CC:DD:EE:01 192.168.1.10 * client-id\n",
        encoding="ascii",
    )
    arp = tmp_path / "arp"
    arp.write_text(
        "IP address HW type Flags HW address Mask Device\n"
        "192.168.1.10 0x1 0x2 aa:bb:cc:dd:ee:01 * br0\n"
        "192.168.1.30 0x1 0x2 aa:bb:cc:dd:ee:03 * br0\n"
        "192.168.2.1 0x1 0x2 aa:bb:cc:dd:ee:ff * vlan2\n",
        encoding="ascii",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nvram = bin_dir / "nvram"
    nvram.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        "  lan_ifname) printf 'br0' ;;\n"
        "  static_leases) "
        "printf 'AA:BB:CC:DD:EE:01=laptop=192.168.1.10=1440' ;;\n"
        "  dhcp_staticlist) "
        "printf '<AA:BB:CC:DD:EE:02=printer=192.168.1.20=1440>' ;;\n"
        "esac\n",
        encoding="ascii",
    )
    nvram.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ASTRILL_LAZY_BASE": str(tmp_path / "runtime"),
        "ASTRILL_LAZY_LEASES_FILE": str(leases),
        "ASTRILL_LAZY_ARP_FILE": str(arp),
    }
    result = subprocess.run(
        ["sh", str(ROOT / "router" / "alctl"), "clients", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    clients = json.loads(result.stdout)
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
