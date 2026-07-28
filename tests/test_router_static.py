from __future__ import annotations

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
    assert "MAX_RULE_BYTES=6144" in controller
    assert "insufficient NVRAM headroom" in controller
    assert "watchdog_pids | grep -qx" in controller
    assert "cleanup_watchdog_pid" in controller
    assert 'kill -9 "$pid"' in controller
    assert 'wait "$watchdog_sleep_pid"' in controller
    assert '[ "$watchdog" = true ] || health=degraded' in controller


def test_ddwrt_banner_is_removed_from_ssh_errors() -> None:
    output = (
        "DD-WRT v3.0-r62374 mega\n"
        "Release: 10/19/25\n"
        "Board: Linksys E4200\n"
        "actual failure\n"
    )
    assert _clean_ssh_stderr(output) == "actual failure"
