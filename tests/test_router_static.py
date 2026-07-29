from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from astrill_lazy.router import _clean_ssh_stderr

ROOT = Path(__file__).resolve().parents[1]


def test_router_and_helper_scripts_parse_with_posix_shell() -> None:
    scripts = [
        ROOT / "router" / "alctl",
        ROOT / "router" / "alapi",
        ROOT / "router" / "alpage",
        ROOT / "router" / "bootstrap.sh",
        ROOT / "helpers" / "astrill-lazy-netns",
        ROOT / "scripts" / "run-novnc-debug.sh",
    ]
    for script in scripts:
        subprocess.run(["sh", "-n", str(script)], check=True)


def test_policy_controller_never_evaluates_rule_content() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    helper = (ROOT / "helpers" / "astrill-lazy-netns").read_text(encoding="ascii")
    assert "eval " not in controller
    assert "eval " not in helper
    assert "--set-xmark" in controller
    assert "0xc000000" in controller
    assert "DIRECT_PREF=32000" in controller
    assert "VPN_PREF=32001" in controller
    assert "MAX_RULE_BYTES=6144" in controller
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
    assert "astrill-connect)" in controller
    assert "astrill-disconnect)" in controller
    assert "/dev/astrill/astrillvpn stop" in controller


def test_ddwrt_banner_is_removed_from_ssh_errors() -> None:
    output = (
        "DD-WRT v3.0-r62374 mega\n"
        "Release: 10/19/25\n"
        "Board: Linksys E4200\n"
        "actual failure\n"
    )
    assert _clean_ssh_stderr(output) == "actual failure"


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
